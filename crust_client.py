"""Crustdata client: auth, disk cache, retry on 429/5xx, cost ledger, budget stop.

Every response is cached in cache/ with its retrieval time, so:
- repeat calls cost 0 credits,
- a restart after a kill doesn't re-spend,
- each fact can cite when its record was retrieved.
"""
import hashlib
import json
import os
import time
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

import store

load_dotenv()
HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "config.json"), encoding="utf-8") as _f:
    CFG = json.load(_f)
KEY = os.getenv("CRUSTDATA_API_KEY")
CACHE_DIR = os.path.join(store.DATA_DIR, "cache")
os.makedirs(CACHE_DIR, exist_ok=True)


class BudgetExceeded(Exception):
    pass


RUN_SPENT = {}  # credits spent per job in this process (one run)
DEADLINE = None  # epoch seconds; set by run.py so retries never push a run past its time limit
DEFAULT_TIMEOUT = 30
TIMEOUTS = {"/web/search/live": 30, "/web/enrich/live": 45}  # slow live endpoints; human_mode fetches need room


def _cache_path(endpoint, body):
    h = hashlib.sha256((endpoint + json.dumps(body, sort_keys=True)).encode()).hexdigest()[:20]
    return os.path.join(CACHE_DIR, f"{endpoint.strip('/').replace('/', '_')}_{h}.json")


def _estimate(endpoint, body):
    """Pre-call estimate, used only for the budget check. Per-URL/per-profile endpoints scale."""
    base = CFG["estimated_cost"].get(endpoint, 1)
    n = len(body.get("urls") or body.get("professional_network_profile_urls") or [None])
    return base * n


def _actual_credits(resp, fallback):
    """Crustdata reports the exact charge in the X-Credits-Used response header."""
    raw = resp.headers.get("x-credits-used")
    try:
        return float(raw), "header"
    except (TypeError, ValueError):
        return fallback, "estimate"


def _write_atomic(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp, path)  # a kill mid-write leaves only the .tmp, never a half-written cache file


# The cache lives in files locally and in the database when deployed (functions keep no files).
def cache_load(path):
    if store.USE_POSTGRES:
        return store.cache_get(os.path.basename(path))
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None


def cache_save(path, rec):
    if store.USE_POSTGRES:
        store.cache_put(os.path.basename(path), rec)
    else:
        _write_atomic(path, rec)


def cache_exists(path):
    return store.cache_has(os.path.basename(path)) if store.USE_POSTGRES else os.path.exists(path)


def cache_drop(path):
    if store.USE_POSTGRES:
        store.cache_delete(os.path.basename(path))
    else:
        try:
            os.remove(path)
        except OSError:
            pass


def call(endpoint, body, job_id="adhoc", method="POST"):
    """Returns {"data": <json>, "retrieved_at": iso, "endpoint": str, "cached": bool}."""
    path = _cache_path(endpoint, body)
    rec = cache_load(path)
    if rec is not None:
        store.log_cost(job_id, endpoint, 0, True, 200)
        rec["cached"] = True
        return rec

    est = _estimate(endpoint, body)
    # Per run, as the config key says. Counting the job's lifetime spend instead would leave a job
    # that once hit the cap unable ever to resume; the ledger still keeps the job total.
    if RUN_SPENT.get(job_id, 0) + est > CFG["cost_limit_credits_per_run"]:
        raise BudgetExceeded(
            f"{endpoint} would push this run of job {job_id} over {CFG['cost_limit_credits_per_run']} credits"
        )

    headers = {
        "authorization": f"Bearer {KEY}",
        "x-api-version": CFG["api_version"],
        "content-type": "application/json",
    }
    url = CFG["base_url"] + endpoint
    attempts, delay, reason, r = 5, 2, None, None
    for attempt in range(1, attempts + 1):
        t_out = TIMEOUTS.get(endpoint, DEFAULT_TIMEOUT)
        if DEADLINE is not None:
            left = DEADLINE - time.time()
            if left < 3:
                reason = (reason + "; " if reason else "") + "no time left in this run"
                store.log_retry(job_id, endpoint, attempt, reason, 0, gave_up=True)
                print(f"    {reason} on {endpoint}; giving up")
                break
            t_out = min(t_out, left)
        try:
            r = (requests.post(url, headers=headers, json=body, timeout=t_out)
                 if method == "POST" else requests.get(url, headers=headers, timeout=t_out))
        except requests.RequestException as e:
            reason = f"{type(e).__name__}: {str(e)[:200]}"
        else:
            if r.status_code != 429 and r.status_code < 500:
                break
            reason = f"HTTP {r.status_code}"
        # A retry that couldn't finish before the run's deadline isn't started: the source is
        # "unavailable" (CANNOT_VERIFY), which beats overrunning the time limit.
        no_time = DEADLINE is not None and time.time() + delay + 5 > DEADLINE
        last = attempt == attempts or no_time
        if no_time and attempt < attempts:
            reason += "; no time left in this run for another try"
        wait = 0 if last else delay
        # Every retry is logged, and so is giving up, so "slow" is never confused with "missing".
        store.log_retry(job_id, endpoint, attempt, reason, wait, gave_up=last)
        print(f"    {reason} on {endpoint} (attempt {attempt}/{attempts})"
              + ("; giving up" if last else f"; retry in {wait}s"))
        if last:
            r = None
            break
        time.sleep(wait)
        delay *= 2
    if r is None or (r.status_code == 429 or r.status_code >= 500):
        store.log_cost(job_id, endpoint, 0, False, -1)
        return {"data": None, "error": "source unavailable after retries",
                "unavailable_reason": f"{reason} after {attempt} attempt(s)",
                "endpoint": endpoint, "retrieved_at": None, "cached": False}

    retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ok = r.status_code == 200
    credits, credits_source = _actual_credits(r, est if ok else 0)
    store.log_cost(job_id, endpoint, credits, False, r.status_code)
    RUN_SPENT[job_id] = RUN_SPENT.get(job_id, 0) + credits
    try:
        data = r.json()
    except ValueError:
        data = {"raw": r.text[:2000]}
    rec = {"data": data if ok else None, "error": None if ok else data,
           "status_code": r.status_code, "endpoint": endpoint,
           "request": body, "retrieved_at": retrieved_at,
           "credits": credits, "credits_source": credits_source}
    if ok:
        cache_save(path, rec)
    rec["cached"] = False
    return rec


# ---- convenience wrappers ----
def enrich_company_by_domain(domain, job_id="adhoc"):
    return call("/company/enrich", {"domains": [domain]}, job_id)


def enrich_company_by_name(name, job_id="adhoc"):
    return call("/company/enrich", {"names": [name]}, job_id)


if __name__ == "__main__":
    # Smoke test: second call should say cached=True and cost 0.
    store.create_job("smoke", {"domain": "retool.com"})
    for i in range(2):
        rec = enrich_company_by_domain("retool.com", "smoke")
        m = rec["data"][0]["matches"][0]["company_data"]["basic_info"]
        print(i + 1, "cached=", rec["cached"], "|", m["name"], m["status"], m["year_founded"],
              "| retrieved", rec["retrieved_at"])
    print("credits logged for job 'smoke':", store.job_cost("smoke"))

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
with open("config.json", encoding="utf-8") as _f:
    CFG = json.load(_f)
KEY = os.getenv("CRUSTDATA_API_KEY")
CACHE_DIR = "cache"
os.makedirs(CACHE_DIR, exist_ok=True)


class BudgetExceeded(Exception):
    pass


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


def call(endpoint, body, job_id="adhoc", method="POST"):
    """Returns {"data": <json>, "retrieved_at": iso, "endpoint": str, "cached": bool}."""
    path = _cache_path(endpoint, body)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            rec = json.load(f)
        store.log_cost(job_id, endpoint, 0, True, 200)
        rec["cached"] = True
        return rec

    est = _estimate(endpoint, body)
    if store.job_cost(job_id) + est > CFG["cost_limit_credits_per_run"]:
        raise BudgetExceeded(
            f"{endpoint} would push job {job_id} over {CFG['cost_limit_credits_per_run']} credits"
        )

    headers = {
        "authorization": f"Bearer {KEY}",
        "x-api-version": CFG["api_version"],
        "content-type": "application/json",
    }
    url = CFG["base_url"] + endpoint
    attempts, delay, reason = 5, 2, None
    for attempt in range(1, attempts + 1):
        try:
            r = (requests.post(url, headers=headers, json=body, timeout=60)
                 if method == "POST" else requests.get(url, headers=headers, timeout=60))
        except requests.RequestException as e:
            reason = f"{type(e).__name__}: {str(e)[:200]}"
        else:
            if r.status_code != 429 and r.status_code < 500:
                break
            reason = f"HTTP {r.status_code}"
        last = attempt == attempts
        wait = 0 if last else delay
        # Every retry is logged, and so is giving up, so "slow" is never confused with "missing".
        store.log_retry(job_id, endpoint, attempt, reason, wait, gave_up=last)
        print(f"    {reason} on {endpoint} (attempt {attempt}/{attempts})"
              + ("; giving up" if last else f"; retry in {wait}s"))
        if not last:
            time.sleep(wait)
            delay *= 2
    else:
        store.log_cost(job_id, endpoint, 0, False, -1)
        return {"data": None, "error": "source unavailable after retries",
                "unavailable_reason": f"{reason} after {attempts} attempts",
                "endpoint": endpoint, "retrieved_at": None, "cached": False}

    retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ok = r.status_code == 200
    credits, credits_source = _actual_credits(r, est if ok else 0)
    store.log_cost(job_id, endpoint, credits, False, r.status_code)
    try:
        data = r.json()
    except ValueError:
        data = {"raw": r.text[:2000]}
    rec = {"data": data if ok else None, "error": None if ok else data,
           "status_code": r.status_code, "endpoint": endpoint,
           "request": body, "retrieved_at": retrieved_at,
           "credits": credits, "credits_source": credits_source}
    if ok:
        _write_atomic(path, rec)
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

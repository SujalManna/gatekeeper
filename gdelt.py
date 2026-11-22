"""GDELT DOC 2.0 client (free, no key): news articles with direct URLs and a seendate.

    https://api.gdeltproject.org/api/v2/doc/doc?query=...&mode=artlist&format=json&maxrecords=25

Same contract as crust_client.call: disk cache, every retry logged, and a failure comes back
as {"data": None, "error": "source unavailable after retries"}; it is never read as "no articles".
GDELT allows one request every 5 seconds, so calls are spaced across processes too.
"""
import hashlib
import json
import os
import time
from datetime import datetime, timezone

import requests

import store

URL = "https://api.gdeltproject.org/api/v2/doc/doc"
ENDPOINT = "gdelt /api/v2/doc/doc"
CACHE_DIR = "cache"
LAST_CALL = os.path.join(CACHE_DIR, ".gdelt_last_call")
MIN_GAP = 5.5        # GDELT: "Please limit requests to one every 5 seconds"
ATTEMPTS = 2         # a slow GDELT must not eat the run's time budget; Crustdata search is the fallback
TIMEOUT = 25
os.makedirs(CACHE_DIR, exist_ok=True)


def _cache_path(params):
    h = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:20]
    return os.path.join(CACHE_DIR, f"gdelt_doc_{h}.json")


def _throttle():
    try:
        with open(LAST_CALL) as f:
            last = float(f.read().strip() or 0)
    except (OSError, ValueError):
        last = 0
    wait = last + MIN_GAP - time.time()
    if wait > 0:
        time.sleep(wait)
    with open(LAST_CALL, "w") as f:
        f.write(str(time.time()))


def seendate_iso(s):
    """'20261115T123000Z' -> '2026-11-15T12:30:00+00:00'."""
    try:
        return datetime.strptime(s, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return s


_DOWN = None  # set after GDELT gives up once in this process, so later searches don't wait on it again


def search(query, job_id, maxrecords=25):
    global _DOWN
    params = {"query": query, "mode": "artlist", "format": "json", "maxrecords": maxrecords}
    path = _cache_path(params)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            rec = json.load(f)
        store.log_cost(job_id, ENDPOINT, 0, True, 200)
        rec["cached"] = True
        return rec
    if _DOWN:
        return {"data": None, "error": "source unavailable after retries",
                "unavailable_reason": f"skipped: GDELT already failed earlier in this run ({_DOWN})",
                "endpoint": ENDPOINT, "retrieved_at": None, "cached": False}

    reason, delay = None, 6
    for attempt in range(1, ATTEMPTS + 1):
        _throttle()
        try:
            r = requests.get(URL, params=params, timeout=TIMEOUT)
            if r.status_code == 200:
                try:
                    # GDELT answers {} when nothing matches, and plain text for a query it rejects.
                    data = r.json() if r.text.strip() else {}
                except ValueError:
                    reason = f"HTTP 200 but not JSON: {r.text[:150]!r}"
                else:
                    store.log_cost(job_id, ENDPOINT, 0, False, 200)
                    rec = {"data": data, "error": None, "status_code": 200, "endpoint": ENDPOINT,
                           "request": params, "url": r.url,
                           "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
                    tmp = path + ".tmp"
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(rec, f, indent=2)
                    os.replace(tmp, path)
                    rec["cached"] = False
                    return rec
            else:
                reason = f"HTTP {r.status_code}: {r.text[:120]!r}"
        except requests.RequestException as e:
            reason = f"{type(e).__name__}: {str(e)[:200]}"
        last = attempt == ATTEMPTS
        wait = 0 if last else delay
        store.log_retry(job_id, ENDPOINT, attempt, reason, wait, gave_up=last)
        print(f"    {reason[:90]} on GDELT (attempt {attempt}/{ATTEMPTS})"
              + ("; giving up" if last else f"; retry in {wait}s"))
        if not last:
            time.sleep(wait)
            delay *= 2
    store.log_cost(job_id, ENDPOINT, 0, False, -1)
    _DOWN = reason[:120]
    return {"data": None, "error": "source unavailable after retries",
            "unavailable_reason": f"{reason} after {ATTEMPTS} attempts",
            "endpoint": ENDPOINT, "retrieved_at": None, "cached": False}

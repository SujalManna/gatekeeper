"""Human approval for the one irreversible action.

    python approve.py <job_id>            approve 'mark_vendor_cleared'
    python approve.py <job_id> --reject   reject it

run.py only ever *requests* approval (and only for REAL_AND_TRADING). Nothing marks a vendor
cleared except this command, and only for a request that is still pending.
"""
import argparse
import json
import os
import sys

import store

ACTION = "mark_vendor_cleared"


def main():
    for s_ in (sys.stdout, sys.stderr):
        try:
            s_.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("job_id")
    ap.add_argument("--reject", action="store_true")
    a = ap.parse_args()

    status = store.approval_status(a.job_id, ACTION)
    if status is None:
        print(f"No approval request for {a.job_id}. Only REAL_AND_TRADING verdicts request one.")
        return 1
    if status != "pending":
        print(f"{a.job_id}: '{ACTION}' is already {status}; nothing changed.")
        return 1

    path = os.path.join("output", f"{a.job_id}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            res = json.load(f)
        if res.get("verdict") != "REAL_AND_TRADING":
            print(f"Refusing: saved verdict is {res.get('verdict')}, not REAL_AND_TRADING.")
            return 1
        print(f"{res['input']['name']} ({res['input']['website']}): {res['verdict']}")
        for k, v in res["summary"].items():
            print(f"  {k}: {v}")

    store.decide_approval(a.job_id, ACTION, approved=not a.reject)
    new = store.approval_status(a.job_id, ACTION)
    if os.path.exists(path):
        res["approval"]["status"] = new
        with open(path, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=2, ensure_ascii=False)
    print(f"{a.job_id}: '{ACTION}' -> {new}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

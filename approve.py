"""Human approval for the one irreversible action.

    python approve.py <job_id>                 approve 'mark_vendor_cleared'
    python approve.py <job_id> --reject        reject it
    python approve.py <job_id> --by "A. Name"  record who decided (default: your OS user name)

run.py only ever *requests* approval (and only for REAL_AND_TRADING). Nothing marks a vendor
cleared except decide() below, which this command and the web API both call, and only for a
request that is still pending.
"""
import argparse
import getpass
import json
import os
import sys

import store

ACTION = "mark_vendor_cleared"


def _output_path(job_id):
    return os.path.join(store.DATA_DIR, "output", f"{job_id}.json")


def _load_result(job_id):
    res = store.get_result(job_id)
    if res is None and os.path.exists(_output_path(job_id)):
        with open(_output_path(job_id), encoding="utf-8") as f:
            res = json.load(f)
    return res


def decide(job_id, approve=True, decided_by=None):
    """Approve or reject a pending clearance request.
    Returns (ok, message, status, result); ok=False means nothing changed."""
    status = store.approval_status(job_id, ACTION)
    if status is None:
        return False, f"No approval request for {job_id}. Only REAL_AND_TRADING verdicts request one.", None, None
    if status != "pending":
        return False, f"{job_id}: '{ACTION}' is already {status}; nothing changed.", status, _load_result(job_id)

    res = _load_result(job_id)
    if res is not None and res.get("verdict") != "REAL_AND_TRADING":
        return False, f"Refusing: saved verdict is {res.get('verdict')}, not REAL_AND_TRADING.", status, res

    store.decide_approval(job_id, ACTION, approved=approve, decided_by=decided_by)
    new = store.approval_status(job_id, ACTION)
    if res is not None:
        rec = store.approval_record(job_id, ACTION) or {}
        res["approval"].update({"status": new, "decided_at": rec.get("decided_at"),
                                "decided_by": rec.get("decided_by")})
        store.save_result(job_id, res)
        if os.path.exists(_output_path(job_id)):
            with open(_output_path(job_id), "w", encoding="utf-8") as f:
                json.dump(res, f, indent=2, ensure_ascii=False)
    who = f" by {decided_by}" if decided_by else ""
    return True, f"{job_id}: '{ACTION}' -> {new}{who}", new, res


def main():
    for s_ in (sys.stdout, sys.stderr):
        try:
            s_.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("job_id")
    ap.add_argument("--reject", action="store_true")
    ap.add_argument("--by", default=None, help="who is deciding (default: your OS user name)")
    a = ap.parse_args()

    res = _load_result(a.job_id)
    if res is not None and store.approval_status(a.job_id, ACTION) == "pending":
        print(f"{res['input']['name']} ({res['input']['website']}): {res['verdict']}")
        for k, v in res["summary"].items():
            print(f"  {k}: {v}")
    ok, msg, _, _ = decide(a.job_id, approve=not a.reject, decided_by=a.by or getpass.getuser())
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

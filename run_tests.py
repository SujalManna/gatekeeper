"""Test runner for tests/cases.json.

    python run_tests.py                 unit cases only (offline, no credits)
    python run_tests.py --e2e           also run the live end-to-end vendor cases (uses the cache)
    python run_tests.py --e2e --recheck re-evaluate e2e cases from cached responses
    python run_tests.py -k shadowfax    only cases whose id contains the text

Exit code 0 when every selected case passes, 1 otherwise.
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)

import checks  # noqa: E402
import run  # noqa: E402
import store  # noqa: E402


# ---------- unit ----------
def _decide_case(a):
    """Build the minimal step outputs step_decide reads; everything verified unless the case says otherwise."""
    def out(data=None, checks_=None, flags=None, unavailable=None):
        return {"data": data or {}, "checks": checks_ or [], "red_flags": flags or [],
                "evidence": [], "unavailable": unavailable or []}
    hits = [{"verdict": v} for v in a.get("hits", [])]
    outs = {
        "identify": out(checks_=[{"check": "identify", "status": "verified"}]),
        "company_profile": out({"verified": True}),
        "who_runs_it": out({"verified": True, "people": [{"name": "x"}]}),
        "website_check": out({"verified": True}),
        "operating_signals": out({"verified": a.get("ops_verified", True)}),
        "entity_resolution": out({"status": a.get("er_status", "linked"), "match_confidence": 1.0}),
        "adverse_media": out({"complete": True, "leaders_screened": 1, "hits": hits},
                             flags=a.get("flags", []),
                             unavailable=[{"source": "test", "reason": "simulated outage"}]
                             if a.get("unavailable") else []),
    }
    return run.step_decide(outs)["data"]["verdict"]


def unit(case):
    fn, a, exp = case["fn"], case["args"], case["expect"]
    if fn == "triage":
        got = checks.triage(a["text"], a["name"], a["company_names"])[0]
        return got == exp["needs_read"], f"needs_read={got}"
    if fn == "classify_hit":
        v, reason, _ = checks.classify_hit(a["text"], a["person"], a["company_names"])
        return v == exp["verdict"], f"{v} ({reason})"
    if fn == "is_social":
        got = checks.is_social(a["url"], a.get("title"))
        return got == exp["value"], str(got)
    if fn == "is_interstitial":
        got = checks.is_interstitial(a["title"], a["text"])
        return got == exp["value"], str(got)
    if fn == "is_abbreviated":
        got = checks.is_abbreviated(a["name"])
        return got == exp["value"], str(got)
    if fn == "html_to_text":
        got = checks.html_to_text(a["html"])[0]
        return exp["contains"] in got, repr(got)
    if fn == "name_similarity":
        got = checks.name_similarity(a["a"], a["b"])
        return got >= exp["min"], str(got)
    if fn == "role_rank":
        got = run._role_rank(a["title"])
        return got == exp["value"], str(got)
    if fn == "decide":
        got = _decide_case(a)
        return got == exp["verdict"], got
    raise ValueError(f"unknown fn {fn}")


# ---------- e2e ----------
def e2e(case, recheck):
    cmd = [sys.executable, "run.py", "--name", case["name"], "--website", case["website"]]
    if recheck:
        cmd.append("--recheck")
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    job = run.job_id_for(case["name"], checks.normalize_url(case["website"]))
    path = os.path.join("output", f"{job}.json")
    if not os.path.exists(path):
        return False, f"no output (exit {p.returncode}): {p.stdout[-300:]} {p.stderr[-300:]}"
    with open(path, encoding="utf-8") as f:
        r = json.load(f)
    exp, problems = case["expect"], []
    if r["verdict"] != exp["verdict"]:
        problems.append(f"verdict {r['verdict']} != {exp['verdict']} (missing: {r.get('missing')})")
    flags = {f["flag"] for f in r["red_flags"]}
    for fl in exp.get("red_flags_include", []):
        if fl not in flags:
            problems.append(f"red flag {fl} not raised (got {sorted(flags)})")
    if "approval_status" in exp and r["approval"]["status"] != exp["approval_status"]:
        problems.append(f"approval {r['approval']['status']} != {exp['approval_status']}")
    if p.returncode not in (0,):
        problems.append(f"run.py exit code {p.returncode}")
    return not problems, "; ".join(problems) or f"{r['verdict']}, {r['cost_credits']} credits (job total)"


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--e2e", action="store_true")
    ap.add_argument("--recheck", action="store_true")
    ap.add_argument("-k", default="")
    a = ap.parse_args()
    with open(os.path.join("tests", "cases.json"), encoding="utf-8") as f:
        cases = json.load(f)

    results = []
    for c in cases["unit"]:
        if a.k in c["id"]:
            try:
                ok, info = unit(c)
            except Exception as e:  # a crash is a failure, not a stop
                ok, info = False, f"{type(e).__name__}: {e}"
            results.append(("unit", c["id"], ok, info))
    if a.e2e:
        for c in cases["e2e"]:
            if a.k in c["id"]:
                print(f"  running e2e {c['id']} ...", flush=True)
                ok, info = e2e(c, a.recheck)
                results.append(("e2e", c["id"], ok, info))

    for kind, cid, ok, info in results:
        print(f"{'PASS' if ok else 'FAIL'}  {kind:4}  {cid:50}  {info if not ok or kind == 'e2e' else ''}")
    failed = [r for r in results if not r[2]]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed" + ("" if a.e2e else "  (unit only; add --e2e for live cases)"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

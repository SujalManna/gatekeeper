"""Test runner for tests/cases.json.

    python run_tests.py                 unit + mock cases (offline, no credits)
    python run_tests.py --e2e           also run the live end-to-end vendor cases (uses the cache)
    python run_tests.py --e2e --recheck re-evaluate e2e cases from cached responses
    python run_tests.py -k shadowfax    only cases whose id contains the text

Exit code 0 when every selected case passes, 1 otherwise.
"""
import argparse
import json
import os
import shutil
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
    unavailable = ([{"source": "test", "reason": "simulated outage", "kind": a.get("unavailable_kind", "source")}]
                   if a.get("unavailable") else [])
    outs = {
        "identify": out(checks_=[{"check": "identify", "status": "verified"}]),
        "company_profile": out({"verified": True}),
        "who_runs_it": out({"verified": True, "people": [{"name": "x"}]}),
        "website_check": out({"verified": True}),
        "operating_signals": out({"verified": a.get("ops_verified", True)}),
        "entity_resolution": out({"status": a.get("er_status", "linked"), "match_confidence": 1.0}),
        "adverse_media": out({"complete": True, "leaders_screened": 1, "hits": hits},
                             flags=a.get("flags", []), unavailable=unavailable),
        "company_adverse_media": out({"complete": a.get("company_complete", True),
                                      "hits": [{"verdict": v} for v in a.get("company_hits", [])]}),
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
        return got >= exp.get("min", 0) and got <= exp.get("max", 100), str(got)
    if fn == "company_triage":
        got = checks.company_triage(a["text"], a["company_names"])
        return got[0] == exp["needs_read"], f"{got[0]} ({got[1]})"
    if fn == "classify_company_hit":
        v, reason, _ = checks.classify_company_hit(a["text"], a["company_names"])
        return v == exp["verdict"], f"{v} ({reason})"
    if fn == "classify_doh":
        got = checks.classify_doh(a["answer"])
        return got == exp["value"], got
    if fn == "role_rank":
        got = run._role_rank(a["title"])
        return got == exp["value"], str(got)
    if fn == "is_lookalike":
        got = checks.is_lookalike(a["domain"], a["real_domains"])
        return got[0] == exp["value"], f"{got[0]} (closest {got[1]}, similarity {got[2]})"
    if fn == "decide":
        got = _decide_case(a)
        return got == exp["verdict"], got
    raise ValueError(f"unknown fn {fn}")


# ---------- e2e ----------
def check_result(r, exp):
    """Compare a run.py result with a case's expectations. Returns a list of problems."""
    problems = []
    if "verdict" in exp and r["verdict"] != exp["verdict"]:
        problems.append(f"verdict {r['verdict']} != {exp['verdict']} (missing: {r.get('missing')})")
    if "verdict_in" in exp and r["verdict"] not in exp["verdict_in"]:
        problems.append(f"verdict {r['verdict']} not in {exp['verdict_in']}")
    if r["verdict"] in exp.get("verdict_not", []):
        problems.append(f"verdict {r['verdict']} is forbidden here")
    flags = {f["flag"] for f in r["red_flags"]}
    for fl in exp.get("red_flags_include", []):
        if fl not in flags:
            problems.append(f"red flag {fl} not raised (got {sorted(flags)})")
    if "approval_status" in exp and r["approval"]["status"] != exp["approval_status"]:
        problems.append(f"approval {r['approval']['status']} != {exp['approval_status']}")
    degraded = " | ".join(d["source"] for d in r.get("sources_degraded", []))
    for s in exp.get("sources_degraded_include", []):
        if s not in degraded:
            problems.append(f"{s!r} not listed under sources_degraded (got: {degraded or 'nothing'})")
    if len(r.get("retries", [])) < exp.get("retries_min", 0):
        problems.append(f"{len(r.get('retries', []))} retries logged, expected at least {exp['retries_min']}")
    ev_src = " ".join(str(e["source"]) for e in r.get("evidence", []))
    if exp.get("evidence_source_contains") and exp["evidence_source_contains"] not in ev_src:
        problems.append(f"no evidence row sourced from {exp['evidence_source_contains']!r}")
    if exp.get("company_adverse_evidence"):
        # an article we actually read accuses the company, and its passage is in the evidence
        adverse = [h for h in r.get("company_adverse_media", []) if h["verdict"] == "adverse" and h["basis"] == "page"]
        want = exp["company_adverse_evidence"].lower()
        cited = [e for e in r.get("evidence", []) if e["field_or_passage"] == "passage"
                 and want in str(e["value"]).lower() and "via crustdata /web/enrich/live" in str(e["source"])]
        if not adverse:
            problems.append("no read article accusing the company (company_adverse_media)")
        if not cited:
            problems.append(f"no evidence passage from a read page naming {exp['company_adverse_evidence']!r}")
    return problems


def e2e(case, recheck):
    """Returns (status, info) with status PASS, FAIL or XFAIL (safe, but a stated target not yet reached)."""
    if case.get("mock"):
        # Offline: the real pipeline in a scratch dir with every HTTP call timing out.
        cmd = [sys.executable, os.path.join("tests", "mock_run.py"), case["mock"],
               "--name", case["name"], "--website", case["website"]]
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        try:
            meta = json.loads(p.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return "FAIL", f"mock run crashed: {p.stdout[-300:]} {p.stderr[-500:]}"
        base, code = meta["dir"], meta["exit_code"]
    else:
        cmd = [sys.executable, "run.py", "--name", case["name"], "--website", case["website"]]
        if recheck:
            cmd.append("--recheck")
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        base, code = ".", p.returncode
    job = run.job_id_for(case["name"], checks.normalize_url(case["website"]))
    path = os.path.join(base, "output", f"{job}.json")
    if not os.path.exists(path):
        return "FAIL", f"no output (exit {code}): {p.stdout[-300:]} {p.stderr[-300:]}"
    with open(path, encoding="utf-8") as f:
        r = json.load(f)
    if case.get("mock"):
        shutil.rmtree(base, ignore_errors=True)  # the scratch dir held only this simulated run
    problems = check_result(r, case["expect"])
    if code != 0:
        problems.append(f"run.py exit code {code}")
    if problems:
        return "FAIL", "; ".join(problems)
    info = f"{r['verdict']}, {r['cost_credits']} credits (job total)"
    if case.get("target"):
        missed = check_result(r, case["target"])
        if missed:
            return "XFAIL", f"{info}; target not reached: {'; '.join(missed)}. Why: {case.get('xfail', '')}"
        info += "; target reached"
    return "PASS", info


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
            results.append(("unit", c["id"], "PASS" if ok else "FAIL", info))
    # Mock cases are offline and free, so they always run; live cases need --e2e.
    for c in cases["e2e"]:
        if a.k in c["id"] and (a.e2e or c.get("mock")):
            print(f"  running {'mock' if c.get('mock') else 'e2e'} {c['id']} ...", flush=True)
            status, info = e2e(c, a.recheck)
            results.append(("mock" if c.get("mock") else "e2e", c["id"], status, info))

    for kind, cid, status, info in results:
        print(f"{status:5} {kind:4}  {cid:50}  {info if status != 'PASS' or kind != 'unit' else ''}")
    failed = [r for r in results if r[2] == "FAIL"]
    xfail = [r for r in results if r[2] == "XFAIL"]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed"
          + (f" ({len(xfail)} xfail: safe, but a stated target is not reached yet)" if xfail else "")
          + ("" if a.e2e else "  (unit + mock only; add --e2e for live cases)"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

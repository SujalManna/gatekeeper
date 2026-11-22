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
import crust_client as cc  # noqa: E402
import run  # noqa: E402
import store  # noqa: E402

with open("config.json", encoding="utf-8") as _f:
    TIME_LIMIT = json.load(_f).get("time_limit_seconds_per_run", 180)


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
        "website_check": out(a.get("website_data", {"verified": True}), flags=a.get("website_flags", [])),
        "operating_signals": out({"verified": a.get("ops_verified", True)}),
        "entity_resolution": out({"status": a.get("er_status", "linked"), "match_confidence": 1.0}),
        "adverse_media": out({"complete": True, "leaders_screened": 1, "hits": hits},
                             flags=a.get("flags", []), unavailable=unavailable),
        "company_adverse_media": out({"complete": a.get("company_complete", True),
                                      "hits": [{"verdict": v} for v in a.get("company_hits", [])]}),
    }
    if a.get("return_missing"):
        return run.step_decide(outs)["data"]
    return run.step_decide(outs)["data"]["verdict"]


def _website_cert_case(a):
    """step_website_check when Crustdata answers but the page won't load, with a given certificate."""
    import dnscheck, tlscheck
    saved = run._fetch, dnscheck.lookup, tlscheck.inspect
    try:
        run._fetch = lambda urls, job, human=False: ({"data": [], "retrieved_at": "2026-11-22T00:00:00+00:00"},
                                                    {}, [{"success": False, "url": u} for u in urls])
        dnscheck.lookup = lambda host, job: {"status": "exists", "host": host, "resolver": "test", "rcode": 0,
                                             "retrieved_at": "2026-11-22T00:00:00+00:00"}
        tlscheck.inspect = lambda host: {"host": host, "checked_at": "2026-11-22T00:00:00+00:00", **a["tls"]}
        return run.step_website_check("unit-test", a["website"], a["name"], {})
    finally:
        run._fetch, dnscheck.lookup, tlscheck.inspect = saved


def _google_link_case(a):
    """_read_pages on a Google redirect link that won't open: found again by headline, read directly."""
    saved = run._fetch, run.api, dict(run._LOOKUPS)
    goto = "https://www.google.com/goto?url=CAESabc&gsessionid=x"

    def fake_fetch(urls, job, human=False):
        items = [{"success": u == a["direct_url"], "url": u, "title": a["direct_title"],
                  "content": "<p>" + a["page_text"] + "</p>"} for u in urls]
        return {"data": items, "retrieved_at": "2026-11-22T00:00:00+00:00"}, {}, items

    def fake_api(endpoint, body, job):
        return {"data": {"results": a["search_results"]}, "retrieved_at": "2026-11-22T00:00:00+00:00"}
    try:
        run._fetch, run.api = fake_fetch, fake_api
        run._LOOKUPS["used"] = 0
        out = run.new_out()
        pages = run._read_pages([goto], "unit-test", out, {goto: a["google_title"]})
        it = (pages.get(goto) or (None,))[0] or {}
        return it.get("resolved_url")
    finally:
        run._fetch, run.api = saved[0], saved[1]
        run._LOOKUPS.update(saved[2])


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
    if fn == "deadline_gives_up":
        # A stalled endpoint must give up before the run's deadline instead of retrying past it.
        import requests, tempfile, time as _t
        work = tempfile.mkdtemp(prefix="gatekeeper_deadline_")
        shutil.copy("config.json", work)
        old_cwd, old_post, old_deadline = os.getcwd(), requests.post, cc.DEADLINE
        old_db = store.DB_PATH
        try:
            os.chdir(work)
            store.DB_PATH = os.path.join(work, "t.db")
            def slow(*_a, **k):
                _t.sleep(min(k.get("timeout", 30), a["stall_seconds"]))
                raise requests.Timeout("simulated stall")
            requests.post = slow
            cc.DEADLINE = _t.time() + a["deadline_seconds"]
            t0 = _t.time()
            rec = cc.call("/web/search/live", {"query": f"deadline {t0}"}, "deadline-test")
            took = _t.time() - t0
        finally:
            requests.post, cc.DEADLINE, store.DB_PATH = old_post, old_deadline, old_db
            os.chdir(old_cwd)
            shutil.rmtree(work, ignore_errors=True)
        ok = rec["data"] is None and took <= a["deadline_seconds"]
        return ok, f"gave up after {took:.1f}s (deadline {a['deadline_seconds']}s): {rec.get('unavailable_reason')}"
    if fn == "classify_doh":
        got = checks.classify_doh(a["answer"])
        return got == exp["value"], got
    if fn == "role_rank":
        got = run._role_rank(a["title"])
        return got == exp["value"], str(got)
    if fn == "is_lookalike":
        got = checks.is_lookalike(a["domain"], a["real_domains"])
        return got[0] == exp["value"], f"{got[0]} (closest {got[1]}, similarity {got[2]})"
    if fn == "headline_core":
        got = checks.headline_core(a["title"])
        return got == exp["value"], repr(got)
    if fn == "same_headline":
        got = checks.same_headline(a["a"], a["b"])
        return got == exp["value"], str(got)
    if fn == "website_cert":
        o = _website_cert_case(a)
        fl = [f["flag"] for f in o["red_flags"]]
        ok = (all(f in fl for f in exp.get("red_flags_include", []))
              and len(o["unavailable"]) == exp["unavailable"]
              and all(t in " ".join(u["reason"] for u in o["unavailable"]) for t in exp.get("reason_contains", [])))
        return ok, f"flags={fl} unavailable={[u['reason'] for u in o['unavailable']]}"
    if fn == "google_link_resolved":
        got = _google_link_case(a)
        return got == exp["value"], repr(got)
    if fn == "describe_unavailable":
        got = run.describe_unavailable(a["u"])
        ok = all(t in got for t in exp.get("contains", [])) and not any(t in got for t in exp.get("not_contains", []))
        return ok, got
    if fn == "company_social_page":
        saved = run.api, run._read_pages, run.gdelt.search
        goto = "https://www.google.com/goto?url=CAESsocial&gsessionid=x"
        try:
            run.gdelt.search = lambda q, job: {"data": {"articles": []}, "retrieved_at": "2026-11-22T00:00:00+00:00"}
            run.api = lambda ep, body, job: {"data": {"results": [
                {"position": 1, "url": goto, "title": a["title"], "snippet": "fraud cases involving Shadowfax"}]},
                "retrieved_at": "2026-11-22T00:00:00+00:00"}
            run._read_pages = lambda urls, job, out, titles=None: {u: ({
                "success": True, "url": u, "title": a["page_title"],
                "content": "<p>" + a["page_text"] + "</p>"}, "2026-11-22T00:00:00+00:00") for u in urls}
            o = run.step_company_adverse_media("unit-test", a["company_names"], "shadowfax.in")
        finally:
            run.api, run._read_pages, run.gdelt.search = saved
        vs = [h["verdict"] for h in o["data"]["hits"]]
        fl = [f["flag"] for f in o["red_flags"]]
        ok = (exp["verdict"] in vs and all(f in fl for f in exp["flags_include"])
              and not any(f in fl for f in exp["flags_exclude"]))
        return ok, f"verdicts={vs} flags={fl}"
    if fn == "decide_missing":
        d = _decide_case({**a, "return_missing": True})
        text = " | ".join(d["missing"])
        ok = d["verdict"] == exp["verdict"] and all(t in text for t in exp.get("missing_contains", []))
        return ok, f"{d['verdict']}: {text}"
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
    info = (f"{r['verdict']}, {r['runtime_seconds']}s, {r.get('run_cost_credits', '?')} credits this run "
            f"({r['cost_credits']} job total)")
    if r["runtime_seconds"] > TIME_LIMIT:
        return "FAIL", f"{info}; took longer than the {TIME_LIMIT}s limit"
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

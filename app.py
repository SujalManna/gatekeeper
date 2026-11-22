"""Streamlit front end for the vendor check.

    streamlit run app.py

It changes no pipeline logic: Run executes `run.py` exactly as on the command line and reads
`output/<job_id>.json`; Approve executes `approve.py <job_id>` (the same human gate).
"""
import json
import os
import re
import subprocess
import sys

import pandas as pd
import streamlit as st

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)

import checks  # noqa: E402  (pure helpers: URL normalisation)
import run  # noqa: E402     (only job_id_for, to find the output file)

st.set_page_config(page_title="Gatekeeper · vendor check", page_icon="🛡️", layout="wide")

BANNER = {
    "REAL_AND_TRADING": ("#1b7f3b", "REAL AND TRADING", "Every check verified. A person still has to approve it."),
    "LOOKS_OFF": ("#b42318", "LOOKS OFF", "Concrete negative evidence was found (see red flags)."),
    "CANNOT_VERIFY": ("#b26a00", "CANNOT VERIFY", "Not enough verified evidence either way (see what is missing)."),
}
URL_RE = re.compile(r"https?://[^\s)\"']+")


def load_result(job_id):
    path = os.path.join(HERE, "output", f"{job_id}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def py(*args, timeout=600):
    """Run one of the project's scripts with this interpreter, the way the CLI does."""
    p = subprocess.run([sys.executable, "-u", *args], cwd=HERE, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def first_url(text):
    m = URL_RE.search(str(text or ""))
    return m.group().rstrip(".,;") if m else None


# ---------- sidebar: pricing used only for display ----------
with st.sidebar:
    st.header("Cost display")
    usd_per_credit = st.number_input("USD per credit", value=0.30, step=0.01, format="%.2f",
                                     help="From 15,000 credits = $4,500")
    inr_per_usd = st.number_input("INR per USD", value=92.66, step=0.01, format="%.2f",
                                  help="x-rates.com, 22 Nov 2026 08:53 UTC")
    st.caption("Change these to reprice; they don't affect the check.")

# ---------- inputs ----------
st.title("🛡️ Gatekeeper · vendor check")
st.caption("Is a new vendor real, trading and run by its director? Every fact below links to its source.")

with st.form("check"):
    c1, c2 = st.columns(2)
    name = c1.text_input("Vendor name", placeholder="Shadowfax Technologies")
    website = c2.text_input("Website", placeholder="https://www.shadowfax.in")
    recheck = st.checkbox("Re-check from cache (re-runs every step; no re-spend on cached calls)")
    go = st.form_submit_button("Run check", type="primary")

if go:
    if not name.strip() or not website.strip():
        st.error("Enter both a vendor name and a website.")
    else:
        job_id = run.job_id_for(checks.norm_space(name), checks.normalize_url(website))
        args = ["run.py", "--name", name.strip(), "--website", website.strip()] + (["--recheck"] if recheck else [])
        with st.spinner(f"Checking {name.strip()} … (job {job_id}; usually under 2 minutes)"):
            try:
                code, log = py(*args)
            except subprocess.TimeoutExpired:
                code, log = -1, "run.py did not finish within 10 minutes"
        st.session_state.update({"job_id": job_id, "run_code": code, "run_log": log, "approve_msg": None})

job_id = st.session_state.get("job_id")
if not job_id:
    st.info("Enter a vendor and press **Run check**.")
    st.stop()

r = load_result(job_id)
if r is None:
    st.error(f"No output was written for job {job_id} (run.py exit code {st.session_state.get('run_code')}).")
    with st.expander("run.py log"):
        st.code(st.session_state.get("run_log", "")[-6000:])
    st.stop()

# ---------- verdict banner ----------
colour, label, blurb = BANNER.get(r["verdict"], ("#555", r["verdict"], ""))
st.markdown(
    f"""<div style="background:{colour};color:#fff;padding:22px 28px;border-radius:12px;margin:8px 0 18px">
    <div style="font-size:2.3rem;font-weight:800;letter-spacing:.02em">{label}</div>
    <div style="font-size:1.05rem;opacity:.95">{r['input']['name']} · {r['input']['website']}</div>
    <div style="font-size:.95rem;opacity:.9;margin-top:6px">{blurb} &nbsp;Basis: {r.get('verdict_basis', '')}</div>
    </div>""",
    unsafe_allow_html=True,
)
if r.get("stopped"):
    st.warning(f"The run stopped early: {r['stopped']}. Run the same check again to resume.")

# ---------- headline numbers ----------
s = r["summary"]
cost_run = r.get("run_cost_credits", 0) or 0
cost_job = r.get("cost_credits", 0) or 0
m1, m2, m3, m4 = st.columns(4)
age = s.get("age") or "unknown"
age_value, _, age_note = age.partition(" (")  # "9 years (founded 2017)" -> "9 years", "founded 2017"
m1.metric("Age", age_value, age_note.rstrip(")") or None, delta_color="off")
m2.metric("Runtime", f"{r['runtime_seconds']} s")
m3.metric("Cost this run", f"{cost_run:g} credits", f"₹{cost_run * usd_per_credit * inr_per_usd:,.0f}",
          delta_color="off")
m4.metric("Cost for this vendor (all runs)", f"{cost_job:g} credits",
          f"₹{cost_job * usd_per_credit * inr_per_usd:,.0f}", delta_color="off")

left, right = st.columns([3, 2])
with left:
    st.subheader("Who runs it")
    st.write(s.get("who_runs_it") or "not established")
    st.markdown(
        f"**Company:** {s.get('company') or '—'} &nbsp;·&nbsp; **Status:** {s.get('status') or '—'} &nbsp;·&nbsp; "
        f"**HQ:** {s.get('hq') or '—'} &nbsp;·&nbsp; **Size:** {s.get('employee_count_range') or '—'}")
    st.markdown(f"**Entity link:** {s.get('entity_link') or '—'}  \n**Operating signals:** {s.get('operating_signals') or '—'}")
with right:
    st.subheader("Anything off")
    st.write(s.get("anything_off") or "nothing found")
    if r.get("missing"):
        st.markdown("**Missing for a clean pass:**")
        for m in r["missing"]:
            st.markdown(f"- {m}")

# ---------- approval ----------
st.subheader("Approval")
appr = r.get("approval") or {}
if r["verdict"] == "REAL_AND_TRADING":
    status = appr.get("status")
    if status == "pending":
        st.write("A person must approve before this vendor is marked cleared.")
        if st.button("✅ Approve: mark vendor cleared", type="primary"):
            code, out = py("approve.py", job_id, timeout=120)
            st.session_state["approve_msg"] = (code, out)
            st.rerun()
    else:
        st.success(f"Approval status: **{status}**")
else:
    st.write(f"No approval: only REAL_AND_TRADING vendors can be cleared (status: {appr.get('status') or 'none'}).")
if st.session_state.get("approve_msg"):
    code, out = st.session_state["approve_msg"]
    (st.success if code == 0 else st.error)(out.strip().splitlines()[-1] if out.strip() else f"exit {code}")

# ---------- red flags ----------
st.subheader(f"Red flags ({len(r['red_flags'])})")
if r["red_flags"]:
    order = {"concrete": 0, "warning": 1, "note": 2}
    flags = sorted(r["red_flags"], key=lambda f: order.get(f["severity"], 3))
    st.dataframe(pd.DataFrame(flags)[["severity", "flag", "detail"]], hide_index=True, use_container_width=True)
else:
    st.write("None.")

# ---------- adverse media ----------
hits = ([{"about": "director: " + (h.get("person") or ""), **h} for h in r.get("adverse_media", [])]
        + [{"about": "company", **h} for h in r.get("company_adverse_media", [])])
relevant = [h for h in hits if h.get("verdict") != "not_relevant"]
st.subheader(f"Adverse media ({len(relevant)} relevant of {len(hits)} hits)")
show_all = st.toggle("Show hits cleared at triage (not_relevant)", value=False)
rows = hits if show_all else relevant
if rows:
    df = pd.DataFrame([{
        "about": h["about"], "verdict": h.get("verdict"), "link": h.get("url"),
        "title": h.get("page_title") or h.get("title"), "reason": h.get("reason"),
        "basis": h.get("basis"), "record date": h.get("record_date"),
    } for h in rows])
    st.dataframe(df, hide_index=True, use_container_width=True,
                 column_config={"link": st.column_config.LinkColumn("source", display_text="open")})
else:
    st.write("No relevant hits.")

# ---------- evidence ----------
st.subheader(f"Evidence ({len(r['evidence'])} facts)")
ev = pd.DataFrame([{
    "link": first_url(e["source"]),
    "source": e["source"],
    "field / passage": e["field_or_passage"],
    "value": e["value"] if isinstance(e["value"], str) else json.dumps(e["value"], ensure_ascii=False),
    "record date": e.get("record_date"),
    "retrieved_at": e.get("retrieved_at"),
} for e in r["evidence"]])
if not ev.empty:
    st.caption("Web sources have an *open* link; API facts show the exact endpoint and request in *source*.")
    q = st.text_input("Filter evidence", placeholder="e.g. year_founded, shadowfax.in, passage")
    if q:
        ev = ev[ev.apply(lambda row: q.lower() in " ".join(map(str, row.values)).lower(), axis=1)]
    ev["link"] = ev["link"].fillna("")
    st.dataframe(ev, hide_index=True, use_container_width=True,
                 column_config={"link": st.column_config.LinkColumn("link", display_text="open"),
                                "value": st.column_config.TextColumn(width="large")})

# ---------- timing and details ----------
with st.expander("Runtime by step"):
    steps = r.get("step_seconds") or {}
    if steps:
        st.bar_chart(pd.Series(steps, name="seconds"), horizontal=True)
    st.caption(f"Total {r['runtime_seconds']} s (steps marked parallel overlap). Limit: 180 s.")
with st.expander("Checks"):
    st.dataframe(pd.DataFrame(r["checks"]), hide_index=True, use_container_width=True)
with st.expander("Sources degraded and retries"):
    st.write(r.get("sources_degraded") or "none")
    st.write(r.get("retries") or "none")
with st.expander("run.py log"):
    st.code(st.session_state.get("run_log", "(loaded from a previous run)")[-6000:])
with st.expander("Raw output JSON"):
    st.json(r, expanded=False)

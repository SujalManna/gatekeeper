"""Gatekeeper HTTP API (FastAPI). Served on Vercel as the `api` service; locally:

    uvicorn server:app --reload --port 8000

Every endpoint except /api/health needs `Authorization: Bearer <APP_ACCESS_TOKEN>`: the API spends
Crustdata credits and records approvals, so it is never open to the internet.

A check runs `run.py` in its own process, exactly as the CLI does. The pipeline keeps per-run
state in module globals (deadline, credit budget, human_mode cap), so concurrent checks must not
share a process. Progress streams back as NDJSON while it runs; the result is read from the store.
No pipeline logic lives here.
"""
import asyncio
import hmac
import json
import os
import re
import sys
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import approve  # noqa: E402  (the human approval gate, shared with the CLI)
import checks  # noqa: E402  (pure helpers: URL and name normalisation)
import store  # noqa: E402

with open(os.path.join(HERE, "config.json"), encoding="utf-8") as _f:
    CFG = json.load(_f)

app = FastAPI(title="Gatekeeper API", version="1.0.0",
              docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")

JOB_ID_RE = re.compile(r"^v_[0-9a-f]{12}$")
STEP_RE = re.compile(r"^\s*\[(run |skip)\]\s+(\w+)")
MAX_CONCURRENT_RUNS = 2  # per server instance; each run is its own process
_slots = asyncio.Semaphore(MAX_CONCURRENT_RUNS)
_running: set[str] = set()


# ---------- auth ----------
def require_token(authorization: str = Header(default="")):
    token = os.environ.get("APP_ACCESS_TOKEN", "")
    if not token:
        raise HTTPException(503, "The server has no APP_ACCESS_TOKEN set, so the API is closed.")
    scheme, _, supplied = authorization.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(supplied.encode(), token.encode()):
        raise HTTPException(401, "Missing or wrong access token.", headers={"WWW-Authenticate": "Bearer"})


def job_id_for(name: str, website: str) -> str:
    # Same formula as run.job_id_for, without importing the pipeline into the API process.
    import hashlib
    key = checks.norm_space(name).lower() + "|" + checks.domain_of(checks.normalize_url(website))
    return "v_" + hashlib.sha256(key.encode()).hexdigest()[:12]


def valid_job(job_id: str) -> str:
    if not JOB_ID_RE.match(job_id):
        raise HTTPException(404, "No such check.")
    return job_id


# ---------- models ----------
class CheckRequest(BaseModel):
    name: str = Field(min_length=2, max_length=200, description="Vendor name, e.g. 'Shadowfax Technologies'")
    website: str = Field(min_length=4, max_length=300, description="Vendor website, e.g. https://www.shadowfax.in")
    recheck: bool = Field(False, description="Re-run every step from cached responses (no re-spend)")


class ApprovalRequest(BaseModel):
    decision: Literal["approve", "reject"]
    approver: str = Field(min_length=2, max_length=120, description="Who is deciding (recorded)")


# ---------- endpoints ----------
@app.get("/api/health")
def health():
    """Unauthenticated liveness and configuration check (no secrets, no data)."""
    return {"ok": True, "storage": "postgres" if store.USE_POSTGRES else "sqlite",
            "crustdata_key_set": bool(os.environ.get("CRUSTDATA_API_KEY")),
            "access_token_set": bool(os.environ.get("APP_ACCESS_TOKEN"))}


@app.get("/api/config", dependencies=[Depends(require_token)])
def config():
    p = CFG.get("pricing", {})
    return {"usd_per_credit": p.get("usd_per_credit", 0.30), "usd_per_credit_note": p.get("usd_per_credit_note"),
            "inr_per_usd": p.get("inr_per_usd"), "inr_per_usd_note": p.get("inr_per_usd_note"),
            "time_limit_seconds": CFG.get("time_limit_seconds_per_run"),
            "budget_credits_per_run": CFG.get("cost_limit_credits_per_run")}


@app.get("/api/checks", dependencies=[Depends(require_token)])
def list_checks(limit: int = Query(30, ge=1, le=200)):
    return {"checks": store.list_results(limit)}


@app.get("/api/checks/{job_id}", dependencies=[Depends(require_token)])
def get_check(job_id: str):
    res = store.get_result(valid_job(job_id))
    if res is None:
        raise HTTPException(404, "No result for this check yet.")
    return res


@app.post("/api/checks/{job_id}/approval", dependencies=[Depends(require_token)])
def decide_approval(job_id: str, body: ApprovalRequest):
    ok, message, status, result = approve.decide(valid_job(job_id), approve=body.decision == "approve",
                                                 decided_by=body.approver.strip())
    if not ok:
        raise HTTPException(409, message)
    return {"ok": True, "message": message, "status": status, "result": result}


@app.post("/api/checks", dependencies=[Depends(require_token)])
async def run_check(body: CheckRequest):
    """Run a check and stream progress as NDJSON lines:
    {"type":"started"}, {"type":"step"} per step, {"type":"log"} for retries, {"type":"ping"} every
    10 s while waiting (keeps the connection alive), and finally {"type":"done", "result": {...}}."""
    name, website = checks.norm_space(body.name), body.website.strip()
    domain = checks.domain_of(checks.normalize_url(website))
    if "." not in domain or " " in domain:
        raise HTTPException(422, f"'{website}' is not a website address.")
    job = job_id_for(name, website)
    if job in _running:
        raise HTTPException(409, "This vendor is already being checked; wait for that run to finish.")

    async def stream():
        def line(obj):
            return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")

        _running.add(job)
        proc = None
        try:
            yield line({"type": "started", "job_id": job, "name": name, "website": website})
            async with _slots:
                args = [sys.executable, "-u", os.path.join(HERE, "run.py"), "--name", name, "--website", website]
                if body.recheck:
                    args.append("--recheck")
                proc = await asyncio.create_subprocess_exec(
                    *args, cwd=HERE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                    env={**os.environ, "PYTHONIOENCODING": "utf-8"})
                while True:
                    try:
                        raw = await asyncio.wait_for(proc.stdout.readline(), timeout=10)
                    except asyncio.TimeoutError:
                        yield line({"type": "ping"})
                        continue
                    if not raw:
                        break
                    text = raw.decode("utf-8", "replace").rstrip()
                    m = STEP_RE.match(text)
                    if m:
                        yield line({"type": "step", "state": m.group(1).strip(), "step": m.group(2)})
                    elif re.search(r"attempt \d+/\d+|giving up|no time left|interrupted|Traceback|Error:", text):
                        yield line({"type": "log", "line": text.strip()[:300]})
                code = await proc.wait()
            result = await asyncio.to_thread(store.get_result, job)
            yield line({"type": "done", "job_id": job, "exit_code": code, "result": result})
        finally:
            _running.discard(job)
            if proc is not None and proc.returncode is None:
                # The client went away mid-run: stop it. Finished steps are saved; re-running resumes.
                proc.terminate()

    return StreamingResponse(stream(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

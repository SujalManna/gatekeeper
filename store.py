"""SQLite store: jobs, steps (restart-safe), cost ledger, evidence, approvals.

Kill the process at any moment and restart: finished steps are skipped,
and actions marked done are never repeated.
"""
import json
import sqlite3
import time
from datetime import datetime, timezone

DB_PATH = "gatekeeper.db"


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY, request TEXT, status TEXT,
            result TEXT, created_at TEXT, updated_at TEXT);
        CREATE TABLE IF NOT EXISTS steps (
            job_id TEXT, step TEXT, status TEXT, output TEXT, finished_at TEXT,
            PRIMARY KEY (job_id, step));
        CREATE TABLE IF NOT EXISTS ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, endpoint TEXT,
            credits REAL, cached INTEGER, status_code INTEGER, at TEXT);
        CREATE TABLE IF NOT EXISTS evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, claim TEXT,
            source TEXT, field_or_passage TEXT, value TEXT,
            record_date TEXT, retrieved_at TEXT);
        CREATE TABLE IF NOT EXISTS approvals (
            job_id TEXT, action TEXT, status TEXT, requested_at TEXT,
            decided_at TEXT, PRIMARY KEY (job_id, action));
        CREATE TABLE IF NOT EXISTS retries (
            id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, endpoint TEXT,
            attempt INTEGER, reason TEXT, wait_seconds REAL, gave_up INTEGER, at TEXT);
        """
    )
    return c


# ---------- jobs ----------
def create_job(job_id, request):
    with conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO jobs VALUES (?,?,?,?,?,?)",
            (job_id, json.dumps(request), "running", None, now_iso(), now_iso()),
        )


def finish_job(job_id, result, status="done"):
    with conn() as c:
        c.execute(
            "UPDATE jobs SET status=?, result=?, updated_at=? WHERE job_id=?",
            (status, json.dumps(result), now_iso(), job_id),
        )


# ---------- steps (idempotent) ----------
def run_step(job_id, step, fn):
    """Run fn() once per (job, step). On restart, return the saved output."""
    with conn() as c:
        row = c.execute(
            "SELECT output FROM steps WHERE job_id=? AND step=? AND status='done'",
            (job_id, step),
        ).fetchone()
    if row:
        print(f"  [skip] {step} already done (resumed)")
        return json.loads(row["output"])
    print(f"  [run ] {step}")
    out = fn()
    with conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO steps VALUES (?,?,?,?,?)",
            (job_id, step, "done", json.dumps(out), now_iso()),
        )
    return out


def reset_job_steps(job_id):
    """Forget every finished step (the API cache is kept, so re-checking re-spends nothing)."""
    with conn() as c:
        c.execute("DELETE FROM steps WHERE job_id=?", (job_id,))


def reset_step(job_id, step):
    """Forget a finished step so the next run re-runs it (used when a source was unavailable)."""
    with conn() as c:
        c.execute("DELETE FROM steps WHERE job_id=? AND step=?", (job_id, step))


# ---------- retries (every one is logged) ----------
def log_retry(job_id, endpoint, attempt, reason, wait_seconds, gave_up=False):
    with conn() as c:
        c.execute(
            "INSERT INTO retries (job_id, endpoint, attempt, reason, wait_seconds, gave_up, at) "
            "VALUES (?,?,?,?,?,?,?)",
            (job_id, endpoint, attempt, reason, wait_seconds, int(gave_up), now_iso()),
        )


def get_retries(job_id):
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM retries WHERE job_id=? ORDER BY id", (job_id,))]


# ---------- cost ledger ----------
def log_cost(job_id, endpoint, credits, cached, status_code):
    with conn() as c:
        c.execute(
            "INSERT INTO ledger (job_id, endpoint, credits, cached, status_code, at) "
            "VALUES (?,?,?,?,?,?)",
            (job_id, endpoint, credits, int(cached), status_code, now_iso()),
        )


def job_cost(job_id):
    with conn() as c:
        r = c.execute(
            "SELECT COALESCE(SUM(credits),0) AS s FROM ledger WHERE job_id=?", (job_id,)
        ).fetchone()
    return r["s"]


# ---------- evidence ----------
def add_evidence(job_id, claim, source, field_or_passage, value, record_date, retrieved_at):
    with conn() as c:
        c.execute(
            "INSERT INTO evidence (job_id, claim, source, field_or_passage, value, "
            "record_date, retrieved_at) VALUES (?,?,?,?,?,?,?)",
            (job_id, claim, source, field_or_passage, str(value), record_date, retrieved_at),
        )


def clear_evidence(job_id):
    """Evidence is rebuilt from saved step outputs on every run, so a resumed job never duplicates rows."""
    with conn() as c:
        c.execute("DELETE FROM evidence WHERE job_id=?", (job_id,))


def get_evidence(job_id):
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM evidence WHERE job_id=?", (job_id,))]


# ---------- approvals (irreversible actions) ----------
def request_approval(job_id, action):
    with conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO approvals VALUES (?,?,?,?,?)",
            (job_id, action, "pending", now_iso(), None),
        )
        # A request withdrawn by an earlier verdict becomes pending again; a human decision stands.
        c.execute(
            "UPDATE approvals SET status='pending', requested_at=?, decided_at=NULL "
            "WHERE job_id=? AND action=? AND status='withdrawn'",
            (now_iso(), job_id, action),
        )


def withdraw_approval(job_id, action):
    """The verdict is no longer REAL_AND_TRADING: a still-pending request must not be approvable."""
    with conn() as c:
        c.execute(
            "UPDATE approvals SET status='withdrawn', decided_at=? "
            "WHERE job_id=? AND action=? AND status='pending'",
            (now_iso(), job_id, action),
        )


def approval_status(job_id, action):
    with conn() as c:
        r = c.execute(
            "SELECT status FROM approvals WHERE job_id=? AND action=?", (job_id, action)
        ).fetchone()
    return r["status"] if r else None


def decide_approval(job_id, action, approved: bool):
    with conn() as c:
        c.execute(
            "UPDATE approvals SET status=?, decided_at=? WHERE job_id=? AND action=?",
            ("approved" if approved else "rejected", now_iso(), job_id, action),
        )

"""Job store: jobs, steps (restart-safe), cost ledger, evidence, approvals, retries, results, API cache.

Kill the process at any moment and restart: finished steps are skipped,
and actions marked done are never repeated.

Backend is chosen from the environment, with the same SQL on both:
  DATABASE_URL / POSTGRES_URL set  -> Postgres (production on Vercel: functions keep no files)
  otherwise                        -> SQLite file in DATA_DIR (local CLI and tests)
DATA_DIR holds the SQLite file, the file cache and output/: "." locally, /tmp/gatekeeper on Vercel
(the only writable place there), or GATEKEEPER_DATA_DIR if set.
"""
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

DATABASE_URL = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or ""
ON_VERCEL = bool(os.environ.get("VERCEL"))
DATA_DIR = os.environ.get("GATEKEEPER_DATA_DIR") or ("/tmp/gatekeeper" if ON_VERCEL else ".")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "gatekeeper.db")
USE_POSTGRES = bool(DATABASE_URL)

_SCHEMA_SQLITE = """
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
CREATE TABLE IF NOT EXISTS results (
    job_id TEXT PRIMARY KEY, name TEXT, website TEXT, verdict TEXT,
    result TEXT, generated_at TEXT);
CREATE TABLE IF NOT EXISTS api_cache (
    key TEXT PRIMARY KEY, value TEXT, created_at TEXT);
"""
_SCHEMA_POSTGRES = (_SCHEMA_SQLITE
                    .replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
                    .replace(" REAL", " DOUBLE PRECISION"))
# Columns added after the first release, applied to existing databases too.
_MIGRATIONS = ["ALTER TABLE approvals ADD COLUMN decided_by TEXT"]

_ready = set()
_lock = threading.Lock()


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_tls = threading.local()


class _Postgres:
    """Gives a psycopg connection the sqlite3-style surface this module uses:
    `with conn() as c: c.execute(sql_with_?_placeholders, params)`, dict rows, commit on exit.
    One connection per thread is kept open and reused: a run makes hundreds of store calls,
    and opening a network connection for each would add seconds."""

    def __init__(self):
        import psycopg
        from psycopg.rows import dict_row
        c = getattr(_tls, "pg", None)
        if c is None or c.closed or c.broken:
            c = psycopg.connect(DATABASE_URL, row_factory=dict_row, connect_timeout=15)
            _tls.pg = c
        self.c = c

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *_):
        try:
            (self.c.commit if exc_type is None else self.c.rollback)()
        except Exception:
            self.c.close()  # a broken connection is replaced on the next call
            raise

    def execute(self, sql, params=()):
        return self.c.execute(sql.replace("?", "%s"), params)


def _migrate(c):
    for stmt in _MIGRATIONS:
        try:
            if USE_POSTGRES:
                c.execute(stmt.replace("ADD COLUMN", "ADD COLUMN IF NOT EXISTS"))
            else:
                c.execute(stmt)
        except sqlite3.OperationalError:
            pass  # column already there


def conn():
    key = DATABASE_URL if USE_POSTGRES else os.path.abspath(DB_PATH)
    if USE_POSTGRES:
        c = _Postgres()
        if key not in _ready:
            with _lock:
                if key not in _ready:
                    for stmt in filter(str.strip, _SCHEMA_POSTGRES.split(";")):
                        c.execute(stmt)
                    _migrate(c)
                    c.c.commit()
                    _ready.add(key)
        return c
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    if key not in _ready:
        c.executescript(_SCHEMA_SQLITE)
        _migrate(c)
        c.commit()
        _ready.add(key)
    return c


# ---------- jobs ----------
def create_job(job_id, request):
    with conn() as c:
        c.execute(
            "INSERT INTO jobs (job_id, request, status, result, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT DO NOTHING",
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
        print(f"  [skip] {step} already done (resumed)", flush=True)
        return json.loads(row["output"])
    print(f"  [run ] {step}", flush=True)
    out = fn()
    with conn() as c:
        c.execute(
            "INSERT INTO steps (job_id, step, status, output, finished_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT (job_id, step) DO UPDATE SET status=excluded.status, output=excluded.output, "
            "finished_at=excluded.finished_at",
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


def finished_steps(job_id):
    with conn() as c:
        return [r["step"] for r in c.execute(
            "SELECT step FROM steps WHERE job_id=? AND status='done' ORDER BY finished_at", (job_id,))]


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
    return float(r["s"])


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
            "INSERT INTO approvals (job_id, action, status, requested_at, decided_at) "
            "VALUES (?,?,?,?,?) ON CONFLICT DO NOTHING",
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


def approval_record(job_id, action):
    with conn() as c:
        r = c.execute("SELECT * FROM approvals WHERE job_id=? AND action=?", (job_id, action)).fetchone()
    return dict(r) if r else None


def decide_approval(job_id, action, approved: bool, decided_by=None):
    with conn() as c:
        c.execute(
            "UPDATE approvals SET status=?, decided_at=?, decided_by=? WHERE job_id=? AND action=?",
            ("approved" if approved else "rejected", now_iso(), decided_by, job_id, action),
        )


# ---------- results (the output JSON, so any server instance can read it) ----------
def save_result(job_id, result):
    with conn() as c:
        c.execute(
            "INSERT INTO results (job_id, name, website, verdict, result, generated_at) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT (job_id) DO UPDATE SET name=excluded.name, website=excluded.website, "
            "verdict=excluded.verdict, result=excluded.result, generated_at=excluded.generated_at",
            (job_id, result["input"]["name"], result["input"]["website"], result["verdict"],
             json.dumps(result, ensure_ascii=False), result.get("generated_at") or now_iso()),
        )


def get_result(job_id):
    with conn() as c:
        r = c.execute("SELECT result FROM results WHERE job_id=?", (job_id,)).fetchone()
    return json.loads(r["result"]) if r else None


def list_results(limit=50):
    with conn() as c:
        rows = c.execute(
            "SELECT r.job_id, r.name, r.website, r.verdict, r.generated_at, a.status AS approval_status "
            "FROM results r LEFT JOIN approvals a ON a.job_id = r.job_id AND a.action = 'mark_vendor_cleared' "
            "ORDER BY r.generated_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


# ---------- API response cache in the database (used when files don't persist) ----------
def cache_get(key):
    with conn() as c:
        r = c.execute("SELECT value FROM api_cache WHERE key=?", (key,)).fetchone()
    return json.loads(r["value"]) if r else None


def cache_put(key, value):
    with conn() as c:
        c.execute(
            "INSERT INTO api_cache (key, value, created_at) VALUES (?,?,?) "
            "ON CONFLICT (key) DO UPDATE SET value=excluded.value, created_at=excluded.created_at",
            (key, json.dumps(value, ensure_ascii=False), now_iso()),
        )


def cache_has(key):
    with conn() as c:
        return c.execute("SELECT 1 FROM api_cache WHERE key=?", (key,)).fetchone() is not None


def cache_delete(key):
    with conn() as c:
        c.execute("DELETE FROM api_cache WHERE key=?", (key,))

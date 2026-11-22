import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { api, ApiError, clearToken, getToken, runCheck } from "./api";
import { VERDICT, when } from "./format";
import type { AppConfig, CheckResult, CheckSummary, StreamEvent } from "./types";
import ResultView from "./components/ResultView";
import RunProgress, { type StepState } from "./components/RunProgress";
import TokenGate from "./components/TokenGate";

interface Run {
  startedAt: number;
  steps: Record<string, StepState>;
  logs: string[];
  finished: boolean;
}

export default function App() {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [checking, setChecking] = useState(Boolean(getToken()));
  const [recent, setRecent] = useState<CheckSummary[]>([]);
  const [result, setResult] = useState<CheckResult | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [website, setWebsite] = useState("");
  const [recheck, setRecheck] = useState(false);
  const abort = useRef<AbortController | null>(null);

  const signOut = useCallback(() => {
    clearToken();
    setConfig(null);
    setResult(null);
    setRun(null);
  }, []);

  const handleError = useCallback(
    (err: unknown) => {
      if (err instanceof ApiError && err.status === 401) signOut();
      else setError((err as Error).message);
    },
    [signOut],
  );

  const loadRecent = useCallback(() => {
    api.list().then((r) => setRecent(r.checks)).catch(handleError);
  }, [handleError]);

  // A token saved earlier in this tab: verify it once on load.
  useEffect(() => {
    if (!getToken()) return;
    api.config().then(setConfig).catch(() => clearToken()).finally(() => setChecking(false));
  }, []);

  useEffect(() => {
    if (config) loadRecent();
  }, [config, loadRecent]);

  async function start(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setResult(null);
    const state: Run = { startedAt: Date.now(), steps: {}, logs: [], finished: false };
    setRun(state);
    abort.current = new AbortController();
    const onEvent = (ev: StreamEvent) => {
      if (ev.type === "step") {
        setRun((r) => r && { ...r, steps: { ...r.steps, [ev.step]: ev.state === "skip" ? "resumed" : "running" } });
      } else if (ev.type === "log") {
        setRun((r) => r && { ...r, logs: [...r.logs, ev.line] });
      } else if (ev.type === "done") {
        setRun((r) => {
          if (!r) return r;
          const steps = { ...r.steps };
          for (const k of Object.keys(steps)) if (steps[k] === "running") steps[k] = "done";
          return { ...r, steps, finished: true };
        });
        if (ev.result) setResult(ev.result);
        else setError(`The check ended without a result (exit code ${ev.exit_code}). Run it again to resume.`);
        loadRecent();
      }
    };
    try {
      await runCheck({ name: name.trim(), website: website.trim(), recheck }, onEvent, abort.current.signal);
    } catch (err) {
      if ((err as Error).name !== "AbortError") handleError(err);
      setRun((r) => r && { ...r, finished: true });
    }
  }

  async function open(jobId: string) {
    setError(null);
    setRun(null);
    try {
      setResult(await api.get(jobId));
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (err) {
      handleError(err);
    }
  }

  if (checking) return <div className="gate"><p className="muted">Loading…</p></div>;
  if (!config) return <TokenGate onReady={setConfig} />;

  const running = run !== null && !run.finished;
  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">
          <span className="logo" aria-hidden>◆</span> Gatekeeper
        </div>
        <h3>Recent checks</h3>
        {recent.length === 0 && <p className="muted small">None yet.</p>}
        <ul className="recent">
          {recent.map((c) => (
            <li key={c.job_id}>
              <button className={result?.job_id === c.job_id ? "active" : ""} onClick={() => open(c.job_id)} disabled={running}>
                <span className={`dot ${VERDICT[c.verdict]?.tone ?? ""}`} aria-hidden />
                <span className="recent-name">{c.name}</span>
                <span className="recent-meta">
                  {VERDICT[c.verdict]?.label ?? c.verdict}
                  {c.approval_status && c.approval_status !== "withdrawn" ? ` · ${c.approval_status}` : ""} · {when(c.generated_at)}
                </span>
              </button>
            </li>
          ))}
        </ul>
        <button className="btn link" onClick={signOut}>Sign out</button>
      </aside>

      <main className="main">
        <header>
          <h1>Vendor check</h1>
          <p className="muted">Is a new vendor real, trading and run by its director? Every fact links to its source.</p>
        </header>

        <form className="card check-form" onSubmit={start}>
          <label>
            Vendor name
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Shadowfax Technologies" required minLength={2} maxLength={200} disabled={running} />
          </label>
          <label>
            Website
            <input value={website} onChange={(e) => setWebsite(e.target.value)} placeholder="https://www.shadowfax.in" required minLength={4} maxLength={300} disabled={running} />
          </label>
          <label className="toggle">
            <input type="checkbox" checked={recheck} onChange={(e) => setRecheck(e.target.checked)} disabled={running} />
            Re-check from saved responses (no re-spend)
          </label>
          <div className="row">
            <button className="btn primary" type="submit" disabled={running}>
              {running ? "Checking…" : "Run check"}
            </button>
            {running && (
              <button className="btn" type="button" onClick={() => abort.current?.abort()}>
                Stop
              </button>
            )}
          </div>
          <p className="muted small">
            Usually 1–2 minutes (limit {config.time_limit_seconds}s, budget {config.budget_credits_per_run} credits). Stopping keeps finished steps; running again resumes.
          </p>
        </form>

        {error && <p className="card error">{error}</p>}
        {run && <RunProgress steps={run.steps} logs={run.logs} startedAt={run.startedAt} finished={run.finished} />}
        {result && <ResultView result={result} config={config} onChange={(r) => { setResult(r); loadRecent(); }} />}
      </main>
    </div>
  );
}

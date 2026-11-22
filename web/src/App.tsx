import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { api, ApiError, clearToken, getToken, runCheck } from "./api";
import { STEP_TITLE, STEPS, TRAIL_PREVIEW, VERDICT, when } from "./format";
import type { AppConfig, CheckResult, CheckSummary, StreamEvent } from "./types";
import { Inbox, Search, Shield, VerdictIcon } from "./components/Icons";
import ResultView from "./components/ResultView";
import RunProgress, { type LogLine, type StepState } from "./components/RunProgress";
import TokenGate from "./components/TokenGate";

interface Run {
  name: string;
  website: string;
  startedAt: number;
  steps: Record<string, StepState>;
  log: LogLine[];
  finished: boolean;
}

const EXAMPLES = [
  { label: "Retool · retool.com", name: "Retool", website: "retool.com" },
  { label: "Shadowfax · shadowfax.in", name: "Shadowfax Technologies", website: "shadowfax.in" },
  { label: "A lookalike · retooI.com", name: "Retool", website: "retooI.com" },
];
const PHASE: Record<string, number> = Object.fromEntries(STEPS.map((s) => [s.id, s.phase]));

export default function App() {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [checking, setChecking] = useState(Boolean(getToken()));
  const [recent, setRecent] = useState<CheckSummary[]>([]);
  const [onlyPending, setOnlyPending] = useState(false);
  const [result, setResult] = useState<CheckResult | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [website, setWebsite] = useState("");
  const [recheck, setRecheck] = useState(false);
  const abort = useRef<AbortController | null>(null);

  const signOut = useCallback(() => {
    abort.current?.abort();
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

  function goHome() {
    if (run && !run.finished) return;
    setResult(null);
    setRun(null);
    setError(null);
    setOnlyPending(false);
  }

  async function start(e?: FormEvent) {
    e?.preventDefault();
    if (!name.trim() || !website.trim()) {
      setError("Enter both a vendor name and a website.");
      return;
    }
    setError(null);
    setResult(null);
    const t0 = Date.now();
    const at = () => (Date.now() - t0) / 1000;
    setRun({ name: name.trim(), website: website.trim(), startedAt: t0, steps: {}, log: [], finished: false });
    abort.current = new AbortController();

    const onEvent = (ev: StreamEvent) => {
      if (ev.type === "started") {
        setRun((r) => r && { ...r, log: [...r.log, { at: at(), text: `started · job ${ev.job_id}` }] });
      } else if (ev.type === "step") {
        setRun((r) => {
          if (!r) return r;
          const steps = { ...r.steps };
          // Parallel steps report only when they start: a step that is running when a step of a
          // later phase starts has finished.
          for (const k of Object.keys(steps)) {
            if (steps[k] === "running" && (PHASE[k] ?? 0) < (PHASE[ev.step] ?? 0)) steps[k] = "done";
          }
          steps[ev.step] = ev.state === "skip" ? "resumed" : "running";
          const title = STEP_TITLE[ev.step] ?? ev.step;
          const text = ev.state === "skip" ? `${title} · saved result reused` : `${title} · started`;
          return { ...r, steps, log: [...r.log, { at: at(), text }] };
        });
      } else if (ev.type === "log") {
        setRun((r) => r && { ...r, log: [...r.log, { at: at(), text: ev.line, warn: true }] });
      } else if (ev.type === "done") {
        setRun((r) => {
          if (!r) return r;
          const steps = { ...r.steps };
          for (const k of Object.keys(steps)) if (steps[k] === "running") steps[k] = "done";
          return { ...r, steps, finished: true, log: [...r.log, { at: at(), text: `finished · ${ev.result ? VERDICT[ev.result.verdict].label.toLowerCase() : "no result"}` }] };
        });
        if (ev.result) {
          // Let the finished trail land for a beat, then show the verdict.
          const res = ev.result;
          setTimeout(() => setResult(res), 700);
        } else setError(`The check ended without a result (exit code ${ev.exit_code}). Run it again to resume.`);
        loadRecent();
      }
    };
    try {
      await runCheck({ name: name.trim(), website: website.trim(), recheck }, onEvent, abort.current.signal);
    } catch (err) {
      if ((err as Error).name === "AbortError") setError("Stopped. Finished steps are saved; run the same check again to resume.");
      else handleError(err);
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

  if (checking) {
    return (
      <div className="gate">
        <md-circular-progress indeterminate aria-label="Loading"></md-circular-progress>
      </div>
    );
  }
  if (!config) return <TokenGate onReady={setConfig} />;

  const running = run !== null && !run.finished;
  const pendingCount = recent.filter((c) => c.approval_status === "pending").length;
  const shown = onlyPending ? recent.filter((c) => c.approval_status === "pending") : recent;
  const view: "home" | "run" | "result" = result ? "result" : run ? "run" : "home";

  return (
    <div className="layout">
      <aside className="rail">
        <button className="brand" type="button" onClick={goHome} disabled={running}>
          <span style={{ color: "var(--ok-glow)" }}><Shield size={28} /></span> Gatekeeper
        </button>
        <nav className="nav" aria-label="Main">
          <button type="button" className={`nav-item ${view === "home" && !onlyPending ? "active" : ""}`} onClick={goHome} disabled={running}>
            <Search /> New check
          </button>
          <button type="button" className={`nav-item ${onlyPending ? "active" : ""}`} onClick={() => setOnlyPending((v) => !v)} aria-pressed={onlyPending}>
            <Inbox /> Approvals {pendingCount > 0 && <span className="badge">{pendingCount}</span>}
          </button>
        </nav>
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <div className="rail-label">{onlyPending ? "Awaiting your approval" : "Recent checks"}</div>
          {shown.length === 0 && <p className="small" style={{ color: "var(--rail-muted)", margin: "0 12px" }}>{onlyPending ? "Nothing waiting." : "None yet."}</p>}
          <ul className="recent">
            {shown.map((c) => {
              const v = VERDICT[c.verdict];
              const approval = c.approval_status === "pending" ? " · awaiting approval" : c.approval_status === "approved" ? " · cleared" : c.approval_status === "rejected" ? " · rejected" : "";
              return (
                <li key={c.job_id}>
                  <button type="button" className={`recent-item ${result?.job_id === c.job_id ? "active" : ""}`} onClick={() => open(c.job_id)} disabled={running}>
                    <span className={`dot ${v?.tone ?? ""}`} aria-hidden="true" />
                    <span className="recent-name">{c.name}</span>
                    <span className="recent-meta">{(v?.label ?? c.verdict) + approval} · {when(c.generated_at)}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
        <div className="rail-foot">
          <span>Signed in</span>
          <md-text-button onClick={signOut}>Sign out</md-text-button>
        </div>
      </aside>

      <main className="main">
        {error && <p className="error-box container" role="alert" style={{ marginBottom: 20 }}>{error}</p>}

        {view === "home" && (
          <div className="container">
            <header className="rise" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              <span className="eyebrow">Vendor verification</span>
              <h1 className="hero-title">Is this vendor real, trading, and run by who they say?</h1>
              <p className="lede">Enter a name and website. Gatekeeper walks a trail of seven inspections and shows every fact with its source.</p>
            </header>

            <form className="card check-form rise-2" onSubmit={start}>
              <div className="form-grid">
                <md-outlined-text-field
                  label="Vendor name"
                  placeholder="Shadowfax Technologies"
                  value={name}
                  required
                  onInput={(e: FormEvent<HTMLElement>) => setName((e.target as HTMLInputElement).value)}
                >
                  <md-icon slot="leading-icon">storefront</md-icon>
                </md-outlined-text-field>
                <md-outlined-text-field
                  label="Website"
                  placeholder="shadowfax.in"
                  value={website}
                  required
                  onInput={(e: FormEvent<HTMLElement>) => setWebsite((e.target as HTMLInputElement).value)}
                >
                  <md-icon slot="leading-icon">language</md-icon>
                </md-outlined-text-field>
              </div>
              <div className="examples">
                <span>Try</span>
                <md-chip-set>
                  {EXAMPLES.map((x) => (
                    <md-assist-chip key={x.label} label={x.label} onClick={() => { setName(x.name); setWebsite(x.website); }}></md-assist-chip>
                  ))}
                </md-chip-set>
              </div>
              <div className="form-foot">
                <label className="toggle">
                  <md-switch selected={recheck} onInput={(e: FormEvent<HTMLElement>) => setRecheck(Boolean((e.target as HTMLElement & { selected: boolean }).selected))}></md-switch>
                  Re-check from saved responses (no new spend)
                </label>
                <div style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
                  <span className="muted small">
                    ≈ 30–120 s · capped at {config.budget_credits_per_run} credits
                  </span>
                  <md-filled-button className="big" type="button" onClick={() => start()}>
                    Run check
                    <md-icon slot="icon">arrow_forward</md-icon>
                  </md-filled-button>
                </div>
              </div>
            </form>

            <section aria-label="The inspection trail" className="rise-3" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              <h2 style={{ fontSize: 18 }}>The trail every check walks</h2>
              <ol className="trail-preview">
                {TRAIL_PREVIEW.map((t) => (
                  <li key={t.n} className="card">
                    <span className="n">{t.n}</span>
                    <span className="t">{t.title}</span>
                    <span className="b">{t.body}</span>
                  </li>
                ))}
              </ol>
            </section>

            <section aria-label="What the verdicts mean" className="verdict-keys">
              {(Object.keys(VERDICT) as (keyof typeof VERDICT)[]).map((k) => (
                <div key={k} className="card verdict-key">
                  <span className={`vk-icon ${VERDICT[k].tone}`}><VerdictIcon tone={VERDICT[k].tone} /></span>
                  <div>
                    <div style={{ fontWeight: 700 }}>{VERDICT[k].label}</div>
                    <div className="muted" style={{ fontSize: 14 }}>{VERDICT[k].meaning}</div>
                  </div>
                </div>
              ))}
            </section>
          </div>
        )}

        {view === "run" && run && (
          <RunProgress
            name={run.name}
            website={run.website}
            steps={run.steps}
            log={run.log}
            startedAt={run.startedAt}
            finished={run.finished}
            onStop={() => abort.current?.abort()}
          />
        )}

        {view === "result" && result && (
          <ResultView
            result={result}
            config={config}
            onBack={goHome}
            onChange={(r) => { setResult(r); loadRecent(); }}
          />
        )}
      </main>
    </div>
  );
}

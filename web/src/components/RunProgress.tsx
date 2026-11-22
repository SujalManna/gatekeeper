import { useEffect, useState } from "react";
import { clock, STEPS } from "../format";
import { Check } from "./Icons";

export type StepState = "waiting" | "running" | "resumed" | "done";
export interface LogLine {
  at: number; // seconds since start
  text: string;
  warn?: boolean;
}

/** The live inspection trail. Shows only what the server streams: which steps start or resume,
 *  and retry/source messages. Findings and cost arrive with the result, not before. */
export default function RunProgress({
  name,
  website,
  steps,
  log,
  startedAt,
  finished,
  onStop,
}: {
  name: string;
  website: string;
  steps: Record<string, StepState>;
  log: LogLine[];
  startedAt: number;
  finished: boolean;
  onStop: () => void;
}) {
  const [now, setNow] = useState(Date.now());
  const [endedAt, setEndedAt] = useState<number | null>(null);
  useEffect(() => {
    if (finished) {
      setEndedAt((e) => e ?? Date.now());
      return;
    }
    const t = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(t);
  }, [finished]);

  const elapsed = ((endedAt ?? now) - startedAt) / 1000;
  const doneCount = STEPS.filter((s) => steps[s.id] === "done" || steps[s.id] === "resumed").length;
  const pct = finished ? 100 : Math.round((doneCount / STEPS.length) * 100);
  const current = STEPS.find((s) => steps[s.id] === "running");

  return (
    <section className="container rise" aria-live="polite" aria-busy={!finished}>
      <div className="run-head">
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <span className={`live ${finished ? "done" : ""}`}>{finished ? "Check finished" : "Live · checking now"}</span>
          <h1 style={{ fontSize: 36 }}>{name}</h1>
          <span className="mono muted" style={{ fontSize: 15 }}>{website}</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span className="clock" aria-label={`Elapsed ${Math.round(elapsed)} seconds`}>{clock(elapsed)}</span>
          {!finished && (
            <md-outlined-button onClick={onStop}>
              Stop
              <md-icon slot="icon">stop_circle</md-icon>
            </md-outlined-button>
          )}
        </div>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <div className="progress-meta">
          <span>{finished ? "All inspections done" : current ? `Now: ${current.title}` : "Starting…"}</span>
          <span>{pct}%</span>
        </div>
        <md-linear-progress value={pct / 100} aria-label="Check progress"></md-linear-progress>
      </div>

      <div className="run-grid">
        <ol className="card trail" aria-label="Inspection trail">
          {STEPS.map((s, i) => {
            const st = steps[s.id] ?? "waiting";
            const isDone = st === "done" || st === "resumed";
            return (
              <li key={s.id} className={`trail-step ${st}`}>
                <div className="trail-rail">
                  <span className={`node ${st}`}>{isDone ? <Check size={16} /> : String(i + 1).padStart(2, "0")}</span>
                  {i < STEPS.length - 1 && <span className={`connector ${isDone ? "done" : ""}`} />}
                </div>
                <div className="step-body">
                  <span className="step-title">{s.title}</span>
                  <span className="step-detail">{st === "waiting" ? "Waiting" : s.checking}</span>
                  {st === "running" && <span className="shim" />}
                </div>
                <span className="step-state">
                  {st === "running" ? "running" : st === "resumed" ? "saved" : st === "done" ? "done" : ""}
                </span>
              </li>
            );
          })}
        </ol>

        <section className="log" aria-label="What's happening">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <h2>What's happening</h2>
            <span className="mono" style={{ fontSize: 12, color: "#8fa39a" }}>{log.length} events</span>
          </div>
          <div className="log-lines">
            {[...log].reverse().map((l, i) => (
              <div key={`${l.at}-${i}`} className={`log-line ${l.warn ? "warn" : ""}`}>
                <span className="t">{Math.round(l.at)}s</span>
                <span>{l.text}</span>
              </div>
            ))}
          </div>
          <p className="run-note">
            Stopping or closing the tab keeps finished steps; running the same check again resumes. Cost and findings appear with the result.
          </p>
        </section>
      </div>
    </section>
  );
}

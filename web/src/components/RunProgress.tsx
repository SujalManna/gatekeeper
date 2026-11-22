import { useEffect, useState } from "react";
import { STEP_LABELS, STEP_ORDER } from "../format";

export type StepState = "waiting" | "running" | "resumed" | "done";

/** Live view of a running check: each pipeline step and any retries, with elapsed time. */
export default function RunProgress({
  steps,
  logs,
  startedAt,
  finished,
}: {
  steps: Record<string, StepState>;
  logs: string[];
  startedAt: number;
  finished: boolean;
}) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (finished) return;
    const t = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(t);
  }, [finished]);
  const elapsed = Math.max(0, Math.round(((finished ? now : Date.now()) - startedAt) / 1000));

  return (
    <section className="card progress" aria-live="polite">
      <div className="progress-head">
        <h2>{finished ? "Check finished" : "Checking…"}</h2>
        <span className="muted">{elapsed}s</span>
      </div>
      <ol className="steps">
        {STEP_ORDER.map((s) => {
          const st = steps[s] ?? "waiting";
          return (
            <li key={s} className={`step ${st}`}>
              <span className="dot" aria-hidden />
              <span>{STEP_LABELS[s]}</span>
              <span className="step-state">
                {st === "running" ? "running" : st === "resumed" ? "resumed (saved)" : st === "done" ? "done" : ""}
              </span>
            </li>
          );
        })}
      </ol>
      {logs.length > 0 && (
        <details className="logs">
          <summary>{logs.length} retry / source message(s)</summary>
          <pre>{logs.join("\n")}</pre>
        </details>
      )}
    </section>
  );
}

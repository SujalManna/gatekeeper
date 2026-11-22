import { useMemo, useState } from "react";
import { credits, firstUrl, hostOf, inr, show, STEP_LABELS, VERDICT, when } from "../format";
import type { AppConfig, CheckResult, MediaHit } from "../types";
import ApprovalPanel from "./ApprovalPanel";

const SEVERITY_ORDER = { concrete: 0, warning: 1, note: 2 } as const;
const HIT_TONE: Record<string, string> = {
  adverse: "bad",
  possible_adverse: "warn",
  unreviewed: "warn",
  not_adverse: "ok",
  mentioned_not_adverse: "ok",
  namesake_cleared: "ok",
  no_adverse_content: "ok",
  not_about_company: "ok",
  not_relevant: "muted",
};

export default function ResultView({
  result,
  config,
  onChange,
}: {
  result: CheckResult;
  config: AppConfig;
  onChange: (r: CheckResult) => void;
}) {
  const v = VERDICT[result.verdict];
  const s = result.summary;
  const rupees = (cr: number | undefined) => inr((cr ?? 0) * config.usd_per_credit * config.inr_per_usd);
  const [ageValue, ageNote] = (s.age ?? "unknown").split(" (");

  return (
    <div className="result">
      <section className={`banner ${v.tone}`}>
        <div className="banner-label">{v.label}</div>
        <div className="banner-vendor">
          {result.input.name} · <a href={result.input.website} target="_blank" rel="noreferrer">{result.input.website}</a>
        </div>
        <div className="banner-blurb">
          {v.blurb} {result.verdict_basis && <span className="basis">Basis: {result.verdict_basis}.</span>}
        </div>
      </section>
      {result.stopped && <p className="card error">The run stopped early: {result.stopped}. Run the same check again to resume.</p>}

      <section className="metrics">
        <Metric label="Age" value={ageValue} note={ageNote?.replace(/\)$/, "")} />
        <Metric label="Runtime" value={`${result.runtime_seconds}s`} note={`limit ${config.time_limit_seconds}s`} />
        <Metric label="Cost this run" value={credits(result.run_cost_credits)} note={rupees(result.run_cost_credits)} />
        <Metric label="Cost for this vendor (all runs)" value={credits(result.cost_credits)} note={rupees(result.cost_credits)} />
      </section>

      <div className="grid-2">
        <section className="card">
          <h2>Who runs it</h2>
          <p className="lead">{s.who_runs_it || "not established"}</p>
          <dl className="facts">
            <dt>Company</dt><dd>{s.company || "—"}</dd>
            <dt>Status</dt><dd>{s.status || "—"}</dd>
            <dt>HQ</dt><dd>{s.hq || "—"}</dd>
            <dt>Size</dt><dd>{s.employee_count_range || "—"}</dd>
            <dt>Entity link</dt><dd>{s.entity_link || "—"}</dd>
            <dt>Operating signals</dt><dd>{s.operating_signals || "—"}</dd>
          </dl>
        </section>
        <section className="card">
          <h2>Anything off</h2>
          <p>{s.anything_off || "nothing found"}</p>
          {result.missing && result.missing.length > 0 && (
            <>
              <h3>Missing for a clean pass</h3>
              <ul className="tight">{result.missing.map((m) => <li key={m}>{m}</li>)}</ul>
            </>
          )}
        </section>
      </div>

      <ApprovalPanel result={result} onChange={onChange} />
      <RedFlags result={result} />
      <AdverseMedia result={result} />
      <Evidence result={result} />
      <Timing result={result} />

      <p className="muted small">
        Job {result.job_id} · generated {when(result.generated_at)} · cost at ${config.usd_per_credit}/credit
        {config.usd_per_credit_note ? ` (${config.usd_per_credit_note})` : ""} and ₹{config.inr_per_usd}/$
        {config.inr_per_usd_note ? ` (${config.inr_per_usd_note})` : ""}
      </p>
    </div>
  );
}

function Metric({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="card metric">
      <div className="metric-label">{label}</div>
      <div className="metric-value">{value}</div>
      {note && <div className="metric-note">{note}</div>}
    </div>
  );
}

function RedFlags({ result }: { result: CheckResult }) {
  const flags = [...result.red_flags].sort((a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity]);
  return (
    <section className="card">
      <h2>Red flags <span className="count">{flags.length}</span></h2>
      {flags.length === 0 ? (
        <p className="muted">None.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Severity</th><th>Flag</th><th>Detail</th></tr></thead>
            <tbody>
              {flags.map((f, i) => (
                <tr key={i}>
                  <td><span className={`pill ${f.severity === "concrete" ? "bad" : f.severity === "warning" ? "warn" : "muted"}`}>{f.severity}</span></td>
                  <td className="mono">{f.flag}</td>
                  <td>{f.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function AdverseMedia({ result }: { result: CheckResult }) {
  const [showAll, setShowAll] = useState(false);
  const hits: (MediaHit & { about: string })[] = [
    ...result.adverse_media.map((h) => ({ ...h, about: `Director: ${h.person ?? ""}` })),
    ...(result.company_adverse_media ?? []).map((h) => ({ ...h, about: "Company" })),
  ];
  const relevant = hits.filter((h) => h.verdict !== "not_relevant");
  const rows = showAll ? hits : relevant;
  return (
    <section className="card">
      <div className="section-head">
        <h2>Adverse media <span className="count">{relevant.length} relevant of {hits.length}</span></h2>
        <label className="toggle">
          <input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} /> Show hits cleared at triage
        </label>
      </div>
      {rows.length === 0 ? (
        <p className="muted">No relevant hits.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>About</th><th>Verdict</th><th>Article</th><th>Why</th></tr></thead>
            <tbody>
              {rows.map((h, i) => (
                <tr key={i}>
                  <td>{h.about}</td>
                  <td><span className={`pill ${HIT_TONE[h.verdict] ?? "muted"}`}>{h.verdict.replaceAll("_", " ")}</span></td>
                  <td>
                    <a href={h.url} target="_blank" rel="noreferrer">{h.page_title || h.title || hostOf(h.url)}</a>
                    <div className="muted small">{hostOf(h.url)}{h.record_date ? ` · ${when(h.record_date)}` : ""}</div>
                  </td>
                  <td>{h.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Evidence({ result }: { result: CheckResult }) {
  const [q, setQ] = useState("");
  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return result.evidence.filter(
      (e) => !needle || `${e.source} ${e.field_or_passage} ${show(e.value)}`.toLowerCase().includes(needle),
    );
  }, [q, result.evidence]);
  return (
    <section className="card">
      <div className="section-head">
        <h2>Evidence <span className="count">{rows.length} of {result.evidence.length} facts</span></h2>
        <input className="filter" placeholder="Filter: year_founded, shadowfax.in, passage…" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      <p className="muted small">Web sources link to the page; API facts show the exact endpoint and request.</p>
      <div className="table-wrap tall">
        <table className="evidence">
          <thead><tr><th>Source</th><th>Field / passage</th><th>Value</th><th>Record date</th><th>Retrieved</th></tr></thead>
          <tbody>
            {rows.map((e, i) => {
              const url = firstUrl(e.source);
              return (
                <tr key={i}>
                  <td className="src">
                    {url ? <a href={url} target="_blank" rel="noreferrer">{hostOf(url)}</a> : null}
                    <div className="mono small">{e.source}</div>
                  </td>
                  <td className="mono small">{e.field_or_passage}</td>
                  <td className="value">{show(e.value)}</td>
                  <td className="small">{e.record_date ? when(e.record_date) : ""}</td>
                  <td className="small">{when(e.retrieved_at)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Timing({ result }: { result: CheckResult }) {
  const steps = Object.entries(result.step_seconds ?? {});
  const max = Math.max(1, ...steps.map(([, v]) => v));
  if (steps.length === 0) return null;
  return (
    <details className="card">
      <summary><h2 className="inline">Runtime by step</h2></summary>
      <div className="bars">
        {steps.map(([k, v]) => (
          <div className="bar-row" key={k}>
            <span>{STEP_LABELS[k] ?? k}</span>
            <div className="bar"><div style={{ width: `${(v / max) * 100}%` }} /></div>
            <span className="small">{v}s</span>
          </div>
        ))}
      </div>
      <p className="muted small">Some steps run in parallel, so these add up to more than the total runtime.</p>
      {result.sources_degraded && result.sources_degraded.length > 0 && (
        <>
          <h3>Sources slow or down</h3>
          <ul className="tight small">
            {result.sources_degraded.map((d, i) => <li key={i}><strong>{d.source}</strong>: {d.reason}</li>)}
          </ul>
        </>
      )}
    </details>
  );
}

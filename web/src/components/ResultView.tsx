import { useMemo, useState, type FormEvent } from "react";
import {
  cr, firstUrl, headline, hostOf, HIT_TONE, humanize, inr, leadersOf, matchOf, operatingOf,
  severityTone, show, sourceLabel, STEP_TITLE, VERDICT, when,
} from "../format";
import type { AppConfig, CheckResult, MediaHit, MissingItem } from "../types";
import ApprovalPanel from "./ApprovalPanel";
import { VerdictIcon } from "./Icons";

type Tab = "evidence" | "news" | "flags" | "timing";
type Hit = MediaHit & { about: string };

export default function ResultView({
  result: r,
  config,
  onChange,
  onBack,
}: {
  result: CheckResult;
  config: AppConfig;
  onChange: (r: CheckResult) => void;
  onBack: () => void;
}) {
  const v = VERDICT[r.verdict];
  const rupees = (n: number | undefined) => inr((n ?? 0) * config.usd_per_credit * config.inr_per_usd);
  const leaders = leadersOf(r);
  const ops = operatingOf(r);
  const match = matchOf(r);
  const [ageValue, ageNote] = (r.summary.age ?? "unknown").split(" (");
  const hits: Hit[] = [
    ...r.adverse_media.map((h) => ({ ...h, about: h.person ?? "Director" })),
    ...(r.company_adverse_media ?? []).map((h) => ({ ...h, about: "Company" })),
  ];
  const relevant = hits.filter((h) => h.verdict !== "not_relevant");
  const accusing = hits.filter((h) => h.verdict === "adverse" && h.basis === "page");
  const [tab, setTab] = useState<Tab>(r.verdict === "REAL_AND_TRADING" ? "evidence" : "flags");

  return (
    <div className="container">
      <nav className="crumbs" aria-label="Breadcrumb">
        <button type="button" onClick={onBack}>Checks</button>
        <span aria-hidden="true">/</span>
        <span style={{ color: "var(--ink)" }}>{r.input.name}</span>
      </nav>

      <section className="hero-row">
        <div className={`card verdict-card rise ${v.tone}`}>
          <div className={`seal ${v.tone}`}>
            <svg className="ring" width="112" height="112" viewBox="0 0 112 112" aria-hidden="true">
              <circle cx="56" cy="56" r="52" fill="none" stroke="currentColor" strokeWidth="1.5" strokeDasharray="3 5"
                style={{ color: v.tone === "ok" ? "var(--ok)" : v.tone === "bad" ? "var(--bad)" : "#b26a00" }} />
            </svg>
            <div className="core"><VerdictIcon tone={v.tone} size={40} /></div>
          </div>
          <div className="verdict-text">
            <span className={`pill ${v.tone}`} style={{ alignSelf: "flex-start", letterSpacing: ".1em", textTransform: "uppercase" }}>{v.label}</span>
            <h1>{headline(r)}</h1>
            <div className="verdict-meta">
              <a href={r.input.website} target="_blank" rel="noreferrer" className="mono">{hostOf(r.input.website)}</a>
              <span>Checked {when(r.generated_at)}</span>
              <span>{r.runtime_seconds}s</span>
              <span>{cr(r.run_cost_credits)} this run · {cr(r.cost_credits)} total ({rupees(r.cost_credits)})</span>
            </div>
          </div>
        </div>
        <ApprovalPanel result={r} onChange={onChange} />
      </section>

      {r.stopped && <p className="error-box">The run stopped early: {r.stopped}. Run the same check again to resume.</p>}

      {r.verdict === "LOOKS_OFF" && accusing.length > 0 && <WhyLooksOff hits={accusing} />}

      {r.verdict === "CANNOT_VERIFY" && r.missing && r.missing.length > 0 && <Missing r={r} />}

      <section className="facts rise-3" aria-label="Key facts">
        <Fact k="Age" v={ageValue} s={ageNote ? ageNote.replace(/\)$/, "").replace(/^founded/, "Founded") : undefined} />
        <Fact k="People" v={ops.employees ?? "—"} s={`Current employees${r.summary.employee_count_range ? ` · ${r.summary.employee_count_range} band` : ""}`} />
        <Fact k="Still hiring" v={ops.jobs ?? "—"} s="Job postings in 180 days" />
        <Fact k="Identity match" v={match ?? "—"} s={r.summary.hq ? `HQ ${r.summary.hq.split(",")[0]}` : "Signals agreeing"} />
      </section>

      {leaders.length > 0 && (
        <section aria-label="Who runs it" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <h2 style={{ fontSize: 20 }}>Who runs it</h2>
          <div className="leaders">
            {leaders.map((l) => (
              <div key={l.name} className="card leader">
                <span className={`avatar ${l.confirmed && l.screened ? "" : "warn"}`} aria-hidden="true">{l.initials}</span>
                <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
                  <strong style={{ fontSize: 16 }}>{l.name}</strong>
                  <span className="muted" style={{ fontSize: 14 }}>{l.role}</span>
                  <div className="chips">
                    {l.confirmed === true && <span className="pill ok">Current role confirmed</span>}
                    {l.confirmed === false && <span className="pill warn">Current role not confirmed</span>}
                    {l.screened ? <span className="pill ok">News screened</span> : <span className="pill warn">News not screened: surname hidden</span>}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      <section className="card tabs-card" aria-label="Details">
        <md-tabs aria-label="Result details">
          <TabButton id="evidence" tab={tab} setTab={setTab} label="Evidence" count={String(r.evidence.length)} />
          <TabButton id="news" tab={tab} setTab={setTab} label="News" count={`${relevant.length} of ${hits.length}`} />
          <TabButton id="flags" tab={tab} setTab={setTab} label="Flags" count={String(r.red_flags.length)} />
          <TabButton id="timing" tab={tab} setTab={setTab} label="Timing" count={`${r.runtime_seconds}s`} />
        </md-tabs>
        {tab === "evidence" && <EvidenceTab r={r} />}
        {tab === "news" && <NewsTab hits={hits} relevant={relevant} />}
        {tab === "flags" && <FlagsTab r={r} />}
        {tab === "timing" && <TimingTab r={r} />}
      </section>

      <p className="footnote">
        Job {r.job_id} · cost at ${config.usd_per_credit}/credit{config.usd_per_credit_note ? ` (${config.usd_per_credit_note})` : ""} and ₹{config.inr_per_usd}/$
        {config.inr_per_usd_note ? ` (${config.inr_per_usd_note})` : ""}
      </p>
    </div>
  );
}

const GOOGLE_LINK = /https?:\/\/(?:www\.)?google\.[^\s]*goto\?\S+/g;

/** Older results only have plain strings: keep them, but never show a raw Google redirect link. */
function itemsOf(r: CheckResult): MissingItem[] {
  if (r.missing_items && r.missing_items.length) return r.missing_items;
  return (r.missing ?? []).map((m) => ({ group: "check" as const, text: m.replace(GOOGLE_LINK, "a Google link") }));
}

/** "What's missing": sources and checks first, then unread news grouped into one item. */
function Missing({ r }: { r: CheckResult }) {
  const items = itemsOf(r);
  const news = items.filter((i) => i.group === "news");
  const rest = items.filter((i) => i.group !== "news");
  return (
    <section className="card missing rise-3" aria-label="What's missing">
      <h2 style={{ fontSize: 18 }}>What's missing for a clean answer</h2>
      <ul>
        {rest.map((m, i) => <li key={i}>{m.text}</li>)}
        {news.length > 0 && (
          <li>
            {news.length === 1 ? "1 news article" : `${news.length} news articles`} that may matter couldn't be read, so
            they can't be cleared:
            <ul className="missing-news">
              {news.map((n, i) => (
                <li key={i}>
                  <span className="headline">{n.title ? `“${n.title}”` : "Untitled result"}</span>{" "}
                  {n.url ? (
                    <a href={n.url} target="_blank" rel="noreferrer noopener">{n.site ?? "Google link"}</a>
                  ) : (
                    <span className="muted">{n.site ?? "unknown site"}</span>
                  )}
                  <span className="muted small">
                    {" "}· {n.about ? `about ${n.about} · ` : ""}{n.why}
                  </span>
                </li>
              ))}
            </ul>
          </li>
        )}
      </ul>
    </section>
  );
}

function Fact({ k, v, s }: { k: string; v: string; s?: string }) {
  return (
    <div className="card fact">
      <div className="k">{k}</div>
      <div className="v">{v}</div>
      {s && <div className="s">{s}</div>}
    </div>
  );
}

function TabButton({ id, tab, setTab, label, count }: { id: Tab; tab: Tab; setTab: (t: Tab) => void; label: string; count: string }) {
  return (
    <md-primary-tab active={tab === id} onClick={() => setTab(id)}>
      {label} <span className="count mono" style={{ fontSize: 12, opacity: 0.75 }}>{count}</span>
    </md-primary-tab>
  );
}

function WhyLooksOff({ hits }: { hits: Hit[] }) {
  const [first, ...rest] = hits;
  return (
    <section aria-label="Why it looks off" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <h2 style={{ fontSize: 20 }}>Why it looks off</h2>
      <article className="card why">
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
          <span className="pill bad">Concrete · accuses {first.about === "Company" ? "the company" : first.about}</span>
          <span className="tag">{hostOf(first.url)}</span>
          {first.record_date && <span className="muted small">Published {when(first.record_date)}</span>}
        </div>
        <a className="title" href={first.url} target="_blank" rel="noreferrer">{first.page_title || first.title || hostOf(first.url)}</a>
        {first.passage && <blockquote>{first.passage}</blockquote>}
        <p className="muted" style={{ margin: 0, fontSize: 14 }}>{first.reason}</p>
        {rest.length > 0 && (
          <md-chip-set>
            {rest.slice(0, 3).map((h) => (
              <md-assist-chip key={h.url} label={hostOf(h.url)} href={h.url} target="_blank">
                <md-icon slot="icon">open_in_new</md-icon>
              </md-assist-chip>
            ))}
            {rest.length > 3 && <md-assist-chip label={`+ ${rest.length - 3} more in News`}></md-assist-chip>}
          </md-chip-set>
        )}
      </article>
    </section>
  );
}

function EvidenceTab({ r }: { r: CheckResult }) {
  const [q, setQ] = useState("");
  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return r.evidence.filter((e) => !needle || `${e.source} ${e.field_or_passage} ${show(e.value)}`.toLowerCase().includes(needle));
  }, [q, r.evidence]);
  return (
    <div className="tabpanel" role="tabpanel">
      <div className="panel-head">
        <p className="muted small" style={{ margin: 0 }}>Every fact, with where it came from and when it was read.</p>
        <div style={{ width: "min(320px, 100%)" }}>
          <md-outlined-text-field
            label="Filter evidence"
            placeholder="founded, privacy, jobs…"
            value={q}
            onInput={(e: FormEvent<HTMLElement>) => setQ((e.target as HTMLInputElement).value)}
          >
            <md-icon slot="leading-icon">search</md-icon>
          </md-outlined-text-field>
        </div>
      </div>
      <div className="table-wrap tall">
        <table style={{ minWidth: 760 }}>
          <thead><tr><th>Value</th><th>Source</th><th>Record date</th><th>Read</th></tr></thead>
          <tbody>
            {rows.map((e, i) => {
              const url = firstUrl(e.source);
              const label = sourceLabel(e.source);
              return (
                <tr key={i}>
                  <td className="value">{show(e.value)}</td>
                  <td className="src">
                    {url ? <a className="tag" href={url} target="_blank" rel="noreferrer">{label}</a> : <span className="tag">{label}</span>}
                    <div className="field-path">{e.field_or_passage}</div>
                  </td>
                  <td className="small">{e.record_date ? when(e.record_date) : ""}</td>
                  <td className="small muted" style={{ whiteSpace: "nowrap" }}>{when(e.retrieved_at)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {rows.length === 0 && <p className="muted small">Nothing matches that filter.</p>}
    </div>
  );
}

function NewsTab({ hits, relevant }: { hits: Hit[]; relevant: Hit[] }) {
  const [all, setAll] = useState(false);
  const rows = all ? hits : relevant;
  return (
    <div className="tabpanel" role="tabpanel">
      <div className="panel-head">
        <p className="muted small" style={{ margin: 0 }}>
          {hits.length} hits found · {hits.length - relevant.length} cleared from the headline alone · {relevant.length} looked at closely.
        </p>
        <label className="toggle" style={{ fontSize: 13 }}>
          <md-switch
            selected={all}
            onInput={(e: FormEvent<HTMLElement>) => setAll(Boolean((e.target as HTMLElement & { selected: boolean }).selected))}
          ></md-switch>
          Show all hits
        </label>
      </div>
      {rows.length === 0 && <p className="muted">No relevant hits.</p>}
      {rows.map((h, i) => (
        <div key={i} className="hit">
          <span className={`pill ${HIT_TONE[h.verdict] ?? "muted"}`} style={{ alignSelf: "flex-start" }}>{humanize(h.verdict)}</span>
          <div className="hit-body">
            <a href={h.url} target="_blank" rel="noreferrer">{h.page_title || h.title || hostOf(h.url)}</a>
            <span className="muted small">{h.reason}</span>
          </div>
          <span className="muted small">{h.about} · {hostOf(h.url)}</span>
        </div>
      ))}
    </div>
  );
}

function FlagsTab({ r }: { r: CheckResult }) {
  const order = { concrete: 0, warning: 1, note: 2 } as const;
  const flags = [...r.red_flags].sort((a, b) => order[a.severity] - order[b.severity]);
  return (
    <div className="tabpanel" role="tabpanel">
      {flags.length === 0 && <p className="muted">No flags.</p>}
      <div>
        {flags.map((f, i) => (
          <div key={i} className="flag-row">
            <span className={`pill ${severityTone(f)}`} style={{ justifySelf: "start" }}>{f.severity}</span>
            <div style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 0 }}>
              <strong>{humanize(f.flag)}</strong>
              <span className="muted" style={{ fontSize: 14, wordBreak: "break-word" }}>{f.detail}</span>
            </div>
          </div>
        ))}
      </div>
      {r.sources_degraded && r.sources_degraded.length > 0 && (
        <details>
          <summary className="small muted">{r.sources_degraded.length} source(s) were slow or down</summary>
          <ul className="small">{r.sources_degraded.map((d, i) => <li key={i}><strong>{d.source}</strong>: {d.reason}</li>)}</ul>
        </details>
      )}
    </div>
  );
}

function TimingTab({ r }: { r: CheckResult }) {
  const steps = Object.entries(r.step_seconds ?? {});
  const max = Math.max(1, ...steps.map(([, s]) => s));
  return (
    <div className="tabpanel" role="tabpanel">
      <div className="bars">
        {steps.map(([k, s]) => (
          <div key={k} className="bar-row">
            <span>{STEP_TITLE[k] ?? k}</span>
            <div className="bar"><div style={{ width: `${Math.max(2, (s / max) * 100)}%` }} /></div>
            <span className="mono small muted" style={{ textAlign: "right" }}>{s}s</span>
          </div>
        ))}
      </div>
      <p className="footnote" style={{ margin: 0 }}>Some steps run in parallel, so these add up to more than the {r.runtime_seconds}s total.</p>
    </div>
  );
}

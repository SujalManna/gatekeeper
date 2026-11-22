import type { CheckResult, RedFlag, Verdict } from "./types";

export type Tone = "ok" | "warn" | "bad";

export const VERDICT: Record<Verdict, { label: string; tone: Tone; meaning: string }> = {
  REAL_AND_TRADING: { label: "Real and trading", tone: "ok", meaning: "Every check verified. A person still approves." },
  LOOKS_OFF: { label: "Looks off", tone: "bad", meaning: "Concrete negative evidence, with the source." },
  CANNOT_VERIFY: { label: "Cannot verify", tone: "warn", meaning: "Not enough proof either way. It says what's missing." },
};

/** Pipeline steps in run order, what each inspects, and the parallel phase it runs in. */
export const STEPS: { id: string; title: string; checking: string; phase: number }[] = [
  { id: "identify", title: "Identify the company", checking: "Do the website and the name point to one company?", phase: 0 },
  { id: "company_profile", title: "Company profile", checking: "Status, age, size and headquarters", phase: 1 },
  { id: "who_runs_it", title: "Who runs it", checking: "Founders and directors, current role confirmed", phase: 2 },
  { id: "website_check", title: "Website", checking: "Does it exist, name the company and list contact details?", phase: 2 },
  { id: "operating_signals", title: "Operating signals", checking: "Current employees, recent hiring, real operations", phase: 3 },
  { id: "adverse_media", title: "News about the directors", checking: "Screening each leader, clearing namesakes", phase: 3 },
  { id: "company_adverse_media", title: "News about the company", checking: "Is the company itself accused of anything?", phase: 3 },
  { id: "entity_resolution", title: "Entity match", checking: "Do all signals agree on one company?", phase: 4 },
  { id: "decide", title: "Decision", checking: "Weighing the evidence", phase: 5 },
  { id: "approval", title: "Approval gate", checking: "Filing a request for a person to approve", phase: 6 },
];
export const STEP_TITLE: Record<string, string> = Object.fromEntries(STEPS.map((s) => [s.id, s.title]));

export const TRAIL_PREVIEW = [
  { n: "01", title: "Identity", body: "Website and name point to one company" },
  { n: "02", title: "Profile", body: "Status, age, size, headquarters" },
  { n: "03", title: "Who runs it", body: "Founders and directors, current role confirmed" },
  { n: "04", title: "Website", body: "Exists, names the company, lists contact" },
  { n: "05", title: "Operating", body: "Employees, hiring, real operations" },
  { n: "06", title: "News", body: "Directors and company, namesakes cleared" },
  { n: "07", title: "Decision", body: "A verdict, then a person approves" },
];

const INR = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
export const inr = (n: number) => INR.format(n);
export const cr = (n: number | undefined) => `${(n ?? 0).toLocaleString("en-IN", { maximumFractionDigits: 2 })} cr`;

export function when(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
}

export function clock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

const URL_RE = /https?:\/\/[^\s)"']+/;
export function firstUrl(text: string): string | null {
  const m = URL_RE.exec(text);
  return m ? m[0].replace(/[.,;]+$/, "") : null;
}

export function show(value: unknown): string {
  if (value === null || value === undefined) return "";
  return typeof value === "string" ? value : JSON.stringify(value);
}

export function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

/** Short, readable name for an evidence source ("company/enrich", "docs.retool.com", "dns.google"). */
export function sourceLabel(source: string): string {
  const crust = /crustdata\s+\/([\w/]+)/.exec(source);
  if (crust) return crust[1];
  if (source.startsWith("DNS-over-HTTPS")) return hostOf(firstUrl(source) ?? "dns");
  if (source.startsWith("gdelt")) return "GDELT news";
  const url = firstUrl(source);
  return url ? hostOf(url) : source.slice(0, 40);
}

export interface Leader {
  name: string;
  role: string;
  confirmed: boolean | null;
  screened: boolean;
  initials: string;
}

/** "David Hsu (Founder, CEO, current role confirmed); Anthony G. (...)" -> structured leaders. */
export function leadersOf(r: CheckResult): Leader[] {
  const raw = r.summary.who_runs_it;
  if (!raw || raw === "not established") return [];
  const unscreened = new Set(
    r.red_flags.filter((f) => f.flag === "leader_not_screenable").map((f) => f.detail.split(":")[0].trim()),
  );
  return raw.split("; ").map((part) => {
    const m = /^(.*?) \((.*)\)$/.exec(part.trim());
    const name = m ? m[1] : part.trim();
    let role = m ? m[2] : "";
    let confirmed: boolean | null = null;
    if (/current role confirmed$/.test(role)) confirmed = true;
    else if (/current role NOT confirmed$/.test(role)) confirmed = false;
    role = role.replace(/, current role (NOT )?confirmed$/, "");
    const initials = name.split(/\s+/).map((w) => w[0] ?? "").join("").slice(0, 2).toUpperCase();
    return { name, role, confirmed, screened: !unscreened.has(name), initials };
  });
}

/** Numbers out of "3/3 — 383 current employees, 66 recent job postings, site operations True". */
export function operatingOf(r: CheckResult): { employees: string | null; jobs: string | null } {
  const s = r.summary.operating_signals ?? "";
  const e = /(\d[\d,]*) current employees/.exec(s);
  const j = /(\d[\d,]*) recent job postings/.exec(s);
  return { employees: e ? Number(e[1].replace(/,/g, "")).toLocaleString("en-IN") : null, jobs: j ? j[1] : null };
}

export function matchOf(r: CheckResult): string | null {
  const m = /match_confidence ([\d.]+)/.exec(r.summary.entity_link ?? "");
  return m ? `${Math.round(Number(m[1]) * 100)}%` : null;
}

const CONCRETE_SENTENCE: Record<string, string> = {
  company_adverse_media: "News reports accuse this company itself.",
  director_adverse_media: "News reports accuse one of its directors.",
  lookalike_domain: "The website imitates another company's domain.",
  parked_domain: "The website is parked or for sale.",
  default_server_page: "The website is an empty server page.",
};

/** One plain sentence for the verdict headline. */
export function headline(r: CheckResult): string {
  const company = r.summary.company || r.input.name;
  if (r.verdict === "REAL_AND_TRADING") {
    const leaders = leadersOf(r);
    const by = leaders.some((l) => /founder/i.test(l.role)) ? "its founders" : leaders.length ? "its leadership" : "its stated leaders";
    return `${company} is real, active, and run by ${by}.`;
  }
  if (r.verdict === "LOOKS_OFF") {
    const concrete = r.red_flags.find((f) => f.severity === "concrete");
    return (concrete && CONCRETE_SENTENCE[concrete.flag]) || "Concrete negative evidence was found.";
  }
  return `We couldn't verify ${company} either way.`;
}

export function severityTone(f: RedFlag): Tone | "muted" {
  return f.severity === "concrete" ? "bad" : f.severity === "warning" ? "warn" : "muted";
}

export const HIT_TONE: Record<string, Tone | "muted"> = {
  adverse: "bad",
  possible_adverse: "warn",
  unreviewed: "warn",
  not_adverse: "ok",
  mentioned_not_adverse: "ok",
  namesake_cleared: "ok",
  namesake_unconfirmed: "muted",
  no_adverse_content: "muted",
  not_about_company: "muted",
  not_read_no_accusation_in_snippet: "muted",
  social_post_not_evidence: "muted",
  not_relevant: "muted",
};

export const humanize = (s: string) => s.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());

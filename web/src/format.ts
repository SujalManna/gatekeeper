import type { Verdict } from "./types";

export const VERDICT: Record<Verdict, { label: string; tone: string; blurb: string }> = {
  REAL_AND_TRADING: {
    label: "Real and trading",
    tone: "ok",
    blurb: "Every check verified. A person still has to approve before the vendor is cleared.",
  },
  LOOKS_OFF: {
    label: "Looks off",
    tone: "bad",
    blurb: "Concrete negative evidence was found. See the red flags.",
  },
  CANNOT_VERIFY: {
    label: "Cannot verify",
    tone: "warn",
    blurb: "Not enough verified evidence either way. See what is missing.",
  },
};

export const STEP_LABELS: Record<string, string> = {
  identify: "Identify company",
  company_profile: "Company profile",
  who_runs_it: "Who runs it",
  website_check: "Website",
  operating_signals: "Operating signals",
  adverse_media: "Adverse media: directors",
  company_adverse_media: "Adverse media: company",
  entity_resolution: "Entity resolution",
  decide: "Decide",
  approval: "Approval gate",
};
export const STEP_ORDER = Object.keys(STEP_LABELS);

const INR = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
export const inr = (n: number) => INR.format(n);
export const credits = (n: number | undefined) => `${(n ?? 0).toLocaleString("en-IN", { maximumFractionDigits: 2 })} cr`;

export function when(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
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

// Shapes of the API's JSON. They mirror run.py's output file and server.py's responses.

export type Verdict = "REAL_AND_TRADING" | "LOOKS_OFF" | "CANNOT_VERIFY";
export type Severity = "concrete" | "warning" | "note";

export interface RedFlag {
  flag: string;
  severity: Severity;
  detail: string;
}

export interface CheckRow {
  check: string;
  status: "verified" | "failed" | "unclear" | "not_checked" | string;
  detail: string;
}

export interface EvidenceRow {
  source: string;
  field_or_passage: string;
  value: unknown;
  record_date: string | null;
  retrieved_at: string | null;
}

export interface MediaHit {
  person?: string;
  source?: string;
  url: string;
  title?: string | null;
  page_title?: string | null;
  record_date?: string | null;
  verdict: string;
  reason: string;
  basis?: string;
  passage?: string | null;
}

export interface Approval {
  action: string;
  status: string | null;
  how?: string | null;
  decided_at?: string | null;
  decided_by?: string | null;
  warning?: string;
}

export interface DegradedSource {
  source: string;
  status?: string;
  reason?: string | null;
}

export interface CheckResult {
  job_id: string;
  input: { name: string; website: string };
  verdict: Verdict;
  verdict_basis?: string;
  summary: Record<string, string | null | undefined>;
  missing?: string[];
  checks: CheckRow[];
  red_flags: RedFlag[];
  adverse_media: MediaHit[];
  company_adverse_media?: MediaHit[];
  evidence: EvidenceRow[];
  approval: Approval;
  stopped?: string | null;
  sources_degraded?: DegradedSource[];
  cost_credits: number;
  run_cost_credits?: number;
  runtime_seconds: number;
  step_seconds?: Record<string, number>;
  generated_at: string;
}

export interface CheckSummary {
  job_id: string;
  name: string;
  website: string;
  verdict: Verdict;
  generated_at: string;
  approval_status: string | null;
}

export interface AppConfig {
  usd_per_credit: number;
  usd_per_credit_note?: string;
  inr_per_usd: number;
  inr_per_usd_note?: string;
  time_limit_seconds: number;
  budget_credits_per_run: number;
}

export type StreamEvent =
  | { type: "started"; job_id: string; name: string; website: string }
  | { type: "step"; state: "run" | "skip"; step: string }
  | { type: "log"; line: string }
  | { type: "ping" }
  | { type: "done"; job_id: string; exit_code: number; result: CheckResult | null };

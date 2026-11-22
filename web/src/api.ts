import type { AppConfig, CheckResult, CheckSummary, StreamEvent } from "./types";

// The access token lives only for this browser tab (sessionStorage), never in the bundle.
const TOKEN_KEY = "gatekeeper.token";
export const getToken = () => sessionStorage.getItem(TOKEN_KEY) ?? "";
export const setToken = (t: string) => sessionStorage.setItem(TOKEN_KEY, t);
export const clearToken = () => sessionStorage.removeItem(TOKEN_KEY);

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

function headers(): Record<string, string> {
  return { "Content-Type": "application/json", Authorization: `Bearer ${getToken()}` };
}

async function errorFrom(res: Response): Promise<ApiError> {
  let message = res.statusText || `HTTP ${res.status}`;
  try {
    const body = await res.json();
    if (typeof body?.detail === "string") message = body.detail;
    else if (body?.detail) message = JSON.stringify(body.detail);
  } catch {
    /* not JSON */
  }
  return new ApiError(res.status, message);
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(path, { ...init, headers: headers() });
  if (!res.ok) throw await errorFrom(res);
  return (await res.json()) as T;
}

export const api = {
  config: () => request<AppConfig>("/api/config"),
  list: () => request<{ checks: CheckSummary[] }>("/api/checks?limit=50"),
  get: (jobId: string) => request<CheckResult>(`/api/checks/${encodeURIComponent(jobId)}`),
  decide: (jobId: string, decision: "approve" | "reject", approver: string) =>
    request<{ ok: boolean; message: string; status: string; result: CheckResult | null }>(
      `/api/checks/${encodeURIComponent(jobId)}/approval`,
      { method: "POST", body: JSON.stringify({ decision, approver }) },
    ),
};

/** Start a check and call onEvent for every NDJSON line the server streams back. */
export async function runCheck(
  body: { name: string; website: string; recheck: boolean },
  onEvent: (e: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch("/api/checks", {
    method: "POST",
    headers: headers(),
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) throw await errorFrom(res);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let nl: number;
    while ((nl = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, nl).trim();
      buffer = buffer.slice(nl + 1);
      if (line) onEvent(JSON.parse(line) as StreamEvent);
    }
  }
  if (buffer.trim()) onEvent(JSON.parse(buffer) as StreamEvent);
}

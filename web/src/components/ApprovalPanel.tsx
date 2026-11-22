import { useEffect, useRef, useState, type FormEvent } from "react";
import { api } from "../api";
import { when } from "../format";
import type { CheckResult } from "../types";

const APPROVER_KEY = "gatekeeper.approver";

/** "Your decision": the human gate. Only a REAL_AND_TRADING check with a pending request can be
 *  approved. Each decision is confirmed in an M3 dialog and records who made it. */
export default function ApprovalPanel({ result, onChange }: { result: CheckResult; onChange: (r: CheckResult) => void }) {
  const [approver, setApprover] = useState(() => localStorage.getItem(APPROVER_KEY) ?? "");
  const [pending, setPending] = useState<"approve" | "reject" | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dialog = useRef<HTMLElement>(null);
  const a = result.approval;
  const status = a.status ?? "none";
  const nameOk = approver.trim().length >= 2;

  // The dialog can also be dismissed with Esc or the scrim: treat that as Cancel.
  useEffect(() => {
    const el = dialog.current;
    if (!el) return;
    const onClosed = () => setPending(null);
    el.addEventListener("closed", onClosed);
    return () => el.removeEventListener("closed", onClosed);
  }, []);

  async function confirm() {
    if (!pending) return;
    setBusy(true);
    setError(null);
    try {
      localStorage.setItem(APPROVER_KEY, approver.trim());
      const res = await api.decide(result.job_id, pending, approver.trim());
      if (res.result) onChange(res.result);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
      setPending(null);
    }
  }

  const tone = result.verdict === "LOOKS_OFF" ? "bad" : result.verdict === "CANNOT_VERIFY" ? "warn" : "";
  const chip = status === "pending" ? "Awaiting approval" : status === "approved" ? "Cleared" : status === "rejected" ? "Rejected" : "Not clearable";

  return (
    <aside className={`decision rise-2 ${tone}`} aria-label="Your decision">
      <md-elevation></md-elevation>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
        <h2>{result.verdict === "REAL_AND_TRADING" ? "Your decision" : "Can't be cleared"}</h2>
        <span className={`status-chip ${status}`}>{chip}</span>
      </div>

      {result.verdict !== "REAL_AND_TRADING" ? (
        <>
          <p>Only vendors that are real and trading can be approved.</p>
          {result.verdict === "LOOKS_OFF" ? (
            <ul>
              <li>Don't onboard or pay this vendor.</li>
              <li>Share the evidence below with compliance.</li>
            </ul>
          ) : (
            <ul>
              <li>See what's missing below.</li>
              <li>Run the check again: it resumes and retries what couldn't be read.</li>
            </ul>
          )}
          {a.warning && <p className="error-box">{a.warning}</p>}
        </>
      ) : status === "pending" ? (
        <>
          <p>Clearing a vendor is the only irreversible step. Your name is recorded with it.</p>
          <md-outlined-text-field
            label="Your name"
            value={approver}
            supporting-text="Recorded with the decision"
            onInput={(e: FormEvent<HTMLElement>) => setApprover((e.target as HTMLInputElement).value)}
          ></md-outlined-text-field>
          <div className="actions">
            <md-filled-button disabled={!nameOk} onClick={() => setPending("approve")}>
              Approve and clear
              <md-icon slot="icon">verified</md-icon>
            </md-filled-button>
            <md-outlined-button disabled={!nameOk} onClick={() => setPending("reject")}>
              Reject
            </md-outlined-button>
          </div>
        </>
      ) : (
        <p style={{ color: "var(--ink)" }}>
          {status === "approved" ? "Cleared" : status === "rejected" ? "Rejected" : `Status: ${status}`}
          {a.decided_by ? ` by ${a.decided_by}` : ""}
          {a.decided_at ? `, ${when(a.decided_at)}` : ""}.
        </p>
      )}
      {error && <p role="alert" className="error-box">{error}</p>}

      <md-dialog ref={dialog} open={pending !== null}>
        <div slot="headline">{pending === "reject" ? "Reject this vendor?" : "Clear this vendor?"}</div>
        <div slot="content" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <span>
            <strong>{result.input.name}</strong> will be marked <strong>{pending === "reject" ? "rejected" : "cleared"}</strong> by{" "}
            <strong>{approver.trim()}</strong>.
          </span>
          <span className="muted small">This is recorded and can't be undone from the app.</span>
        </div>
        <div slot="actions">
          <md-text-button disabled={busy} onClick={() => setPending(null)}>Cancel</md-text-button>
          <md-filled-button disabled={busy} onClick={confirm}>
            {busy ? "Saving…" : pending === "reject" ? "Yes, reject" : "Yes, clear"}
          </md-filled-button>
        </div>
      </md-dialog>
    </aside>
  );
}

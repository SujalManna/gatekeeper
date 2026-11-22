import { useState } from "react";
import { api } from "../api";
import { when } from "../format";
import type { CheckResult } from "../types";

const APPROVER_KEY = "gatekeeper.approver";

/** The human gate. Only a REAL_AND_TRADING check with a pending request can be approved,
 *  and every decision needs a second, explicit confirmation and records who made it. */
export default function ApprovalPanel({ result, onChange }: { result: CheckResult; onChange: (r: CheckResult) => void }) {
  const [approver, setApprover] = useState(() => localStorage.getItem(APPROVER_KEY) ?? "");
  const [pending, setPending] = useState<"approve" | "reject" | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const a = result.approval;

  async function confirm() {
    if (!pending) return;
    setBusy(true);
    setMessage(null);
    try {
      localStorage.setItem(APPROVER_KEY, approver.trim());
      const res = await api.decide(result.job_id, pending, approver.trim());
      setMessage({ ok: true, text: res.message });
      if (res.result) onChange(res.result);
    } catch (err) {
      setMessage({ ok: false, text: (err as Error).message });
    } finally {
      setBusy(false);
      setPending(null);
    }
  }

  if (result.verdict !== "REAL_AND_TRADING") {
    return (
      <section className="card">
        <h2>Approval</h2>
        <p className="muted">
          Only vendors that are <strong>real and trading</strong> can be cleared. Status: {a.status ?? "no request"}.
        </p>
        {a.warning && <p className="error">{a.warning}</p>}
      </section>
    );
  }

  return (
    <section className="card approval">
      <h2>Approval</h2>
      {a.status === "pending" ? (
        <>
          <p>A person must approve before this vendor is marked cleared.</p>
          <div className="row">
            <label className="grow">
              Your name (recorded with the decision)
              <input value={approver} onChange={(e) => setApprover(e.target.value)} maxLength={120} />
            </label>
          </div>
          {pending ? (
            <div className="confirm">
              <span>
                {pending === "approve" ? "Mark this vendor cleared" : "Reject this vendor"} as{" "}
                <strong>{approver.trim()}</strong>?
              </span>
              <button className={`btn ${pending === "approve" ? "primary" : "danger"}`} onClick={confirm} disabled={busy}>
                {busy ? "Saving…" : `Yes, ${pending}`}
              </button>
              <button className="btn" onClick={() => setPending(null)} disabled={busy}>
                Cancel
              </button>
            </div>
          ) : (
            <div className="row">
              <button className="btn primary" disabled={approver.trim().length < 2} onClick={() => setPending("approve")}>
                Approve: mark vendor cleared
              </button>
              <button className="btn danger-outline" disabled={approver.trim().length < 2} onClick={() => setPending("reject")}>
                Reject
              </button>
            </div>
          )}
        </>
      ) : (
        <p className={a.status === "approved" ? "ok-text" : "muted"}>
          Status: <strong>{a.status}</strong>
          {a.decided_by ? ` by ${a.decided_by}` : ""}
          {a.decided_at ? `, ${when(a.decided_at)}` : ""}
        </p>
      )}
      {message && <p className={message.ok ? "ok-text" : "error"}>{message.text}</p>}
    </section>
  );
}

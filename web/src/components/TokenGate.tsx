import { useState, type FormEvent, type KeyboardEvent } from "react";
import { api, ApiError, clearToken, setToken } from "../api";
import type { AppConfig } from "../types";
import { Shield } from "./Icons";

/** Asks for the access token and checks it against the API before letting anyone in. */
export default function TokenGate({ onReady }: { onReady: (cfg: AppConfig) => void }) {
  const [token, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!token.trim() || busy) return;
    setBusy(true);
    setError(null);
    setToken(token.trim());
    try {
      onReady(await api.config());
    } catch (err) {
      clearToken();
      setError(err instanceof ApiError && err.status === 401 ? "That access token isn't valid." : (err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="gate">
      <form className="card gate-card rise" onSubmit={(e) => e.preventDefault()}>
        <div className="gate-brand">
          <span style={{ color: "var(--ok)" }}><Shield size={28} /></span> Gatekeeper
        </div>
        <p className="muted" style={{ margin: 0 }}>
          Vendor verification for your team. Enter the access token your administrator gave you.
        </p>
        <md-outlined-text-field
          label="Access token"
          type="password"
          autocomplete="current-password"
          value={token}
          error={Boolean(error)}
          error-text={error ?? ""}
          onInput={(e: FormEvent<HTMLElement>) => setValue((e.target as HTMLInputElement).value)}
          onKeyDown={(e: KeyboardEvent<HTMLElement>) => { if (e.key === "Enter") submit(); }}
        >
          <md-icon slot="leading-icon">key</md-icon>
        </md-outlined-text-field>
        <md-filled-button className="big" type="button" disabled={busy || !token.trim()} onClick={() => submit()}>
          {busy ? "Checking…" : "Continue"}
          <md-icon slot="icon">arrow_forward</md-icon>
        </md-filled-button>
        <p className="muted small" style={{ margin: 0 }}>The token stays in this browser tab only.</p>
      </form>
    </div>
  );
}

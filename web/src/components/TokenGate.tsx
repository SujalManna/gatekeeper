import { useState, type FormEvent } from "react";
import { api, ApiError, clearToken, setToken } from "../api";
import type { AppConfig } from "../types";

/** Asks for the access token and checks it against the API before letting anyone in. */
export default function TokenGate({ onReady }: { onReady: (cfg: AppConfig) => void }) {
  const [token, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setToken(token.trim());
    try {
      onReady(await api.config());
    } catch (err) {
      clearToken();
      setError(err instanceof ApiError && err.status === 401 ? "That access token is not valid." : String((err as Error).message));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="gate">
      <form className="card gate-card" onSubmit={submit}>
        <h1>Gatekeeper</h1>
        <p className="muted">Vendor verification. Enter the access token your administrator gave you.</p>
        <label htmlFor="token">Access token</label>
        <input
          id="token"
          type="password"
          autoComplete="current-password"
          value={token}
          onChange={(e) => setValue(e.target.value)}
          required
        />
        {error && <p className="error">{error}</p>}
        <button className="btn primary" type="submit" disabled={busy || !token.trim()}>
          {busy ? "Checking…" : "Continue"}
        </button>
      </form>
    </div>
  );
}

import { useState, type FormEvent } from "react";
import { api, ApiError } from "../api";
import { useLive } from "../live";
import { Icon, Logo, Spinner } from "../ui";

export default function Login() {
  const { setSession, reloadSession } = useLive();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (e: FormEvent) => {
    e.preventDefault(); setBusy(true); setError("");
    try { setSession(await api.signIn(username, password)); }
    catch (err) {
      setError(err instanceof ApiError ? err.message : "The server did not answer.");
      if (err instanceof ApiError && err.status === 403) await reloadSession();   // a fresh CSRF token
    } finally { setBusy(false); }
  };

  return (
    <div className="login-wrap">
      <form className="login card" onSubmit={submit}>
        <div className="login-brand"><Logo size={52} />
          <div><h1>Fabric Factory</h1><p className="mute">Network self-service: request changes to the fabric — operators approve them, the automation builds them while you watch — and look at any device's configuration and state.</p></div></div>
        <label>User name<input autoFocus autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required /></label>
        <label>Password<input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></label>
        {error && <div className="note bad"><Icon name="alert" />{error}</div>}
        <button className="btn primary" disabled={busy}>{busy ? <Spinner /> : null}Sign in</button>
        <p className="mute small">Accounts are managed by an operator in Django admin.</p>
      </form>
    </div>
  );
}

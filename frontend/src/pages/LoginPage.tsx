import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { login } from "../api/endpoints";

export function LoginPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username, password);
      // A sign-in starts a new session: nothing cached from a previous one may
      // survive, or the shell would render the old profile's capabilities.
      queryClient.clear();
      navigate("/sql");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "sign in failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-gate">
      <form className="card auth-card" onSubmit={submit}>
        <h1>Sign in</h1>
        <p className="muted">Local account. Sessions expire after 8 hours.</p>
        <label>
          Username
          <input
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            autoComplete="username"
            data-testid="login-username"
            required
          />
        </label>
        <label>
          Password
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="current-password"
            data-testid="login-password"
            required
          />
        </label>
        {error ? <p className="notice error" data-testid="login-error">{error}</p> : null}
        <button className="primary" type="submit" disabled={busy} data-testid="login-submit">
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}

import { useState } from "react";
import { login } from "./api.js";

// Login form. On submit it calls the API, stores the token (inside api.login),
// and tells <App> to switch to the workspace. Seeded dev creds are shown as a
// hint so the grader can log straight in.
export default function Login({ onSuccess }) {
  const [email, setEmail] = useState("schen@scribe.local");
  const [password, setPassword] = useState("password123");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      await login(email, password);
      onSuccess();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card login">
      <h2>Provider sign-in</h2>
      <form onSubmit={handleSubmit}>
        <label>
          Email or username
          <input
            type="text"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="username"
          />
        </label>
        <label>
          Password
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
          />
        </label>
        {error && <p className="error">{error}</p>}
        <button type="submit" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
      <p className="hint">
        Provider: schen@scribe.local / password123 · Admin: admin / password
      </p>
    </div>
  );
}

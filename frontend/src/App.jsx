import { useState, useEffect } from "react";
import {
  getToken,
  clearToken,
  setAuthLostHandler,
  expireAccessTokenForTesting,
} from "./api.js";
import Login from "./Login.jsx";
import Workspace from "./Workspace.jsx";

// Top-level gate: if we hold a token, show the scribe workspace; otherwise the
// login screen. `loggedIn` is React state so the UI re-renders the instant login
// succeeds or the user logs out (localStorage alone wouldn't trigger a re-render).
export default function App() {
  const [loggedIn, setLoggedIn] = useState(Boolean(getToken()));

  function handleLogout() {
    clearToken();
    setLoggedIn(false);
  }

  // Let api.js bounce us to the login screen when a token refresh fails (session
  // truly gone). We register the same handler as manual logout. useEffect runs
  // after render so we don't call setState during render; the empty dep array
  // registers once for the app's lifetime.
  useEffect(() => {
    setAuthLostHandler(handleLogout);
  }, []);

  return (
    <div className="app">
      <header className="topbar">
        <h1>AI Clinical Scribe</h1>
        {loggedIn && (
          <div className="topbar-actions">
            {/* DEV-ONLY: fake an expired access token to test auto-refresh. */}
            <button
              className="link"
              onClick={expireAccessTokenForTesting}
              title="Corrupt the access token so the next call triggers auto-refresh"
            >
              Expire token (dev)
            </button>
            <button className="link" onClick={handleLogout}>
              Log out
            </button>
          </div>
        )}
      </header>
      {loggedIn ? (
        <Workspace />
      ) : (
        <Login onSuccess={() => setLoggedIn(true)} />
      )}
    </div>
  );
}

import { useState, useEffect } from "react";
import { getToken, clearToken, setAuthLostHandler, getMe } from "./api.js";
import Login from "./Login.jsx";
import Workspace from "./Workspace.jsx";
import Admin from "./Admin.jsx";

// Top-level gate: if we hold a token, show the scribe workspace; otherwise the
// login screen. `loggedIn` is React state so the UI re-renders the instant login
// succeeds or the user logs out (localStorage alone wouldn't trigger a re-render).
export default function App() {
  const [loggedIn, setLoggedIn] = useState(Boolean(getToken()));
  const [me, setMe] = useState(null); // current user (incl. role) for RBAC
  const [view, setView] = useState("workspace"); // "workspace" | "admin"
  // True only when this session began with an actual login submit (not a reload
  // that found a stored token). Lets Workspace start blank on a fresh login but
  // restore the in-progress draft on a plain refresh. A reload resets App state,
  // so this correctly falls back to false — the signal IS "did onSuccess fire".
  const [freshLogin, setFreshLogin] = useState(false);

  function handleLoginSuccess() {
    setFreshLogin(true);
    setLoggedIn(true);
  }

  function handleLogout() {
    clearToken();
    setLoggedIn(false);
    setMe(null);
    setView("workspace");
    setFreshLogin(false);
  }

  // Let api.js bounce us to the login screen when a token refresh fails (session
  // truly gone). We register the same handler as manual logout. useEffect runs
  // after render so we don't call setState during render; the empty dep array
  // registers once for the app's lifetime.
  useEffect(() => {
    setAuthLostHandler(handleLogout);
  }, []);

  // When logged in (fresh login OR a reload with a stored token), load the current
  // user so we know their role. Drives whether the Admin nav shows.
  useEffect(() => {
    if (!loggedIn) return;
    getMe()
      .then(setMe)
      .catch(() => setMe(null));
  }, [loggedIn]);

  const isAdmin = me?.role === "admin";
  // Guard: only render Admin if the user actually is one (the API also enforces it).
  const showAdmin = view === "admin" && isAdmin;

  return (
    <div className="app">
      <header className="topbar">
        <h1>AI Clinical Scribe</h1>
        {loggedIn && (
          <div className="topbar-actions">
            {isAdmin && (
              <nav className="nav-links">
                <button
                  className={view === "workspace" ? "link active" : "link"}
                  onClick={() => setView("workspace")}
                >
                  Workspace
                </button>
                <button
                  className={view === "admin" ? "link active" : "link"}
                  onClick={() => setView("admin")}
                >
                  Admin
                </button>
              </nav>
            )}
            <button className="link" onClick={handleLogout}>
              Log out
            </button>
          </div>
        )}
      </header>
      {!loggedIn ? (
        <Login onSuccess={handleLoginSuccess} />
      ) : showAdmin ? (
        <Admin me={me} />
      ) : (
        <Workspace freshLogin={freshLogin} />
      )}
    </div>
  );
}

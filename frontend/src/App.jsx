import { useState } from "react";
import { getToken, clearToken } from "./api.js";
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

  return (
    <div className="app">
      <header className="topbar">
        <h1>AI Clinical Scribe</h1>
        {loggedIn && (
          <button className="link" onClick={handleLogout}>
            Log out
          </button>
        )}
      </header>
      {loggedIn ? (
        <Workspace onAuthLost={handleLogout} />
      ) : (
        <Login onSuccess={() => setLoggedIn(true)} />
      )}
    </div>
  );
}

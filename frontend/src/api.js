// All backend calls live here so components don't sprinkle fetch() everywhere.
// The access token is kept in memory + localStorage and attached as a Bearer
// header on every authed call.

const BASE = "http://127.0.0.1:8000";

export function getToken() {
  return localStorage.getItem("access_token");
}
function setToken(t) {
  localStorage.setItem("access_token", t);
}
function getRefreshToken() {
  return localStorage.getItem("refresh_token");
}
function setRefreshToken(t) {
  localStorage.setItem("refresh_token", t);
}
export function clearToken() {
  localStorage.removeItem("access_token");
  localStorage.removeItem("refresh_token");
}

// The app registers a callback so api.js can bounce the user to the login screen
// when the refresh itself fails (the session is truly gone). This keeps api.js
// UI-agnostic — it doesn't import React or know how navigation works.
let onAuthLost = () => {};
export function setAuthLostHandler(fn) {
  onAuthLost = fn;
}

function authHeaders() {
  const t = getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

// DEV-ONLY: corrupt the stored ACCESS token (leaving the refresh token intact) so
// the next authed call gets a 401 — lets us test the auto-refresh path without
// waiting ~20 min for the real token to expire. Not wired into any real flow.
export function expireAccessTokenForTesting() {
  setToken("expired.invalid.token"); // syntactically-invalid JWT → backend 401s
}

// Exchange the stored refresh token for a fresh access token. Returns true if we
// got one (and stored it), false if there's no refresh token or the endpoint
// rejects it (expired/revoked → the session is really over).
async function tryRefresh() {
  const refresh_token = getRefreshToken();
  if (!refresh_token) return false;
  const res = await fetch(`${BASE}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token }),
  });
  if (!res.ok) return false;
  const data = await res.json();
  setToken(data.access_token);
  return true;
}

// Every authed call goes through here. It injects the Bearer header, and on a
// 401 (access token expired) it transparently mints a fresh token and REPLAYS
// the request once. Only if the refresh fails do we give up: clear tokens and
// notify the app. The original request body/URL are untouched, so no draft is
// lost — the retry is a byte-for-byte replay with a new token.
async function authedFetch(url, options = {}) {
  const withAuth = () => ({
    ...options,
    headers: { ...(options.headers || {}), ...authHeaders() },
  });

  const res = await fetch(url, withAuth());
  if (res.status !== 401) return res;

  const refreshed = await tryRefresh();
  if (!refreshed) {
    clearToken();
    onAuthLost();
    return res; // hand back the original 401 so the caller still fails cleanly
  }
  return fetch(url, withAuth()); // retry once, now with the new access token
}

export async function login(email, password) {
  const res = await fetch(`${BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) throw new Error("Login failed — check your email and password.");
  const data = await res.json();
  setToken(data.access_token);
  setRefreshToken(data.refresh_token); // keep it — this is what powers auto-refresh
  return data;
}

export async function createEncounter(body) {
  const res = await authedFetch(`${BASE}/encounters`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`Could not start encounter (${res.status}).`);
  return res.json();
}

export async function saveVersion(encounterId, soap, icdCodes = []) {
  const res = await authedFetch(`${BASE}/encounters/${encounterId}/versions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    // Send the approved SOAP fields plus the selected ICD codes as one record.
    body: JSON.stringify({ ...soap, icd_codes: icdCodes }),
  });
  if (!res.ok) throw new Error(`Save failed (${res.status}).`);
  return res.json();
}

// Filter AI-suggested codes down to ones our catalog recognizes. Returns
// [{code, description}] with canonical descriptions (never a hallucinated code).
export async function validateIcdCodes(codes) {
  const res = await authedFetch(`${BASE}/icd/validate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ codes }),
  });
  if (!res.ok) throw new Error("ICD validation failed.");
  const data = await res.json();
  return data.results;
}

// Autocomplete over patients THIS provider has treated (backend scopes by
// provider_id). Returns [{id, first_name, last_name, dob}]. Used by the patient
// picker to avoid re-typing identity fields and minting duplicate patient rows.
export async function searchPatients(query) {
  const res = await authedFetch(
    `${BASE}/patients/search?q=${encodeURIComponent(query)}`
  );
  if (!res.ok) throw new Error("Patient search failed.");
  return res.json();
}

// This provider's prior encounters for one patient (newest first), each with a
// version count. Read-only history.
export async function listPatientEncounters(patientId) {
  const res = await authedFetch(`${BASE}/patients/${patientId}/encounters`);
  if (!res.ok) throw new Error("Could not load patient history.");
  return res.json();
}

// The saved note versions for one encounter (newest first), each with its ICD
// codes. Read-only.
export async function listVersions(encounterId) {
  const res = await authedFetch(`${BASE}/encounters/${encounterId}/versions`);
  if (!res.ok) throw new Error("Could not load note versions.");
  return res.json();
}

export async function searchIcd(query) {
  const res = await authedFetch(
    `${BASE}/icd/search?q=${encodeURIComponent(query)}&limit=6`
  );
  if (!res.ok) throw new Error("ICD search failed.");
  const data = await res.json();
  return data.results;
}

// Stream a SOAP note. The backend sends Server-Sent Events; we consume them with
// fetch() + ReadableStream (NOT EventSource, which can't send an auth header).
// Each SSE frame is `data: {json}\n\n`. We buffer bytes, split on the blank-line
// delimiter, JSON-parse each frame, and call the callbacks: onText for each text
// chunk, onDone on clean completion, onError on a mid-stream error event.
export async function generateNote(
  encounterId,
  { onText, onReset, onDone, onError }
) {
  const res = await authedFetch(`${BASE}/encounters/${encounterId}/generate`, {
    method: "POST",
  });
  // Up-front failures (404 not owned, 422 empty transcript) arrive as normal
  // HTTP errors BEFORE the stream opens — surface them and stop.
  if (!res.ok) {
    let detail = `Generation failed (${res.status}).`;
    try {
      detail = (await res.json()).detail || detail;
    } catch {
      /* non-JSON body; keep the generic message */
    }
    onError(detail);
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // A complete SSE event ends in a blank line. Process every complete frame
    // in the buffer and keep the trailing partial for the next read.
    let sep;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const line = frame.trim();
      if (!line.startsWith("data:")) continue;
      const payload = JSON.parse(line.slice(line.indexOf(":") + 1).trim());
      if (payload.text !== undefined) onText(payload.text);
      else if (payload.reset) onReset && onReset();
      else if (payload.done) onDone();
      else if (payload.error) onError(payload.error);
    }
  }
}

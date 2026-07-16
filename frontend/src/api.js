// All backend calls live here so components don't sprinkle fetch() everywhere.
// The access token is kept in memory + localStorage and attached as a Bearer
// header on every authed call.

// Base URL for the API. In local dev it defaults to the uvicorn dev server; in
// prod the app is served behind nginx on the same origin, so VITE_API_BASE is
// set to "" (empty) and every path becomes same-origin relative (e.g. /auth/login).
const BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";

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

// The authenticated user (id, email, name, role). Used for RBAC — the app shows
// the Admin nav only when role === "admin". The API is the real gate (403s).
export async function getMe() {
  const res = await authedFetch(`${BASE}/auth/me`);
  if (!res.ok) throw new Error("Could not load current user.");
  return res.json();
}

// --- admin (require_admin on the server) ---
export async function adminStats() {
  const res = await authedFetch(`${BASE}/admin/stats`);
  if (!res.ok) throw new Error("Could not load stats.");
  return res.json();
}
export async function adminAudit(limit = 50) {
  const res = await authedFetch(`${BASE}/admin/audit?limit=${limit}`);
  if (!res.ok) throw new Error("Could not load audit log.");
  return res.json();
}
export async function adminUsers() {
  const res = await authedFetch(`${BASE}/admin/users`);
  if (!res.ok) throw new Error("Could not load users.");
  return res.json();
}
export async function adminSetUserRole(userId, role) {
  const res = await authedFetch(`${BASE}/admin/users/${userId}/role`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role }),
  });
  if (!res.ok) {
    let detail = "Could not change role.";
    try {
      detail = (await res.json()).detail || detail;
    } catch {
      /* keep generic */
    }
    throw new Error(detail);
  }
  return res.json();
}

// Pull an error `detail` out of a failed JSON response, falling back to a generic
// message. Used by the admin mutations so the UI can surface server-side reasons
// (e.g. "email already registered", "cannot deactivate yourself").
async function detailOr(res, fallback) {
  let detail = fallback;
  try {
    detail = (await res.json()).detail || detail;
  } catch {
    /* non-JSON body; keep the generic message */
  }
  return detail;
}

// --- admin: templates management ---
export async function adminTemplates() {
  const res = await authedFetch(`${BASE}/admin/templates`);
  if (!res.ok) throw new Error("Could not load templates.");
  return res.json();
}
export async function adminCreateTemplate(body) {
  // body: {name, prompt_body, is_active}
  const res = await authedFetch(`${BASE}/admin/templates`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await detailOr(res, "Could not create template."));
  return res.json();
}
export async function adminUpdateTemplate(templateId, body) {
  // body: any subset of {name, prompt_body, is_active}
  const res = await authedFetch(`${BASE}/admin/templates/${templateId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await detailOr(res, "Could not update template."));
  return res.json();
}
export async function adminDeleteTemplate(templateId) {
  const res = await authedFetch(`${BASE}/admin/templates/${templateId}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(await detailOr(res, "Could not delete template."));
  return true;
}
export async function adminGenerateTemplate(description) {
  // Ask Claude to draft a template from a short description. Returns
  // {name, prompt_body} — a DRAFT the admin reviews/edits before saving.
  const res = await authedFetch(`${BASE}/admin/templates/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ description }),
  });
  if (!res.ok) throw new Error(await detailOr(res, "Could not generate a template."));
  return res.json();
}

// --- admin: provider management ---
export async function adminCreateUser(body) {
  // body: {email, full_name, password, role}
  const res = await authedFetch(`${BASE}/admin/users`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await detailOr(res, "Could not create user."));
  return res.json();
}
export async function adminSetUserActive(userId, isActive) {
  const res = await authedFetch(`${BASE}/admin/users/${userId}/active`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ is_active: isActive }),
  });
  if (!res.ok) throw new Error(await detailOr(res, "Could not change status."));
  // Tolerate a 204/empty body: fall back to the value we just set so the caller
  // can still update its row.
  try {
    return await res.json();
  } catch {
    return { id: userId, is_active: isActive };
  }
}

// --- admin: encounter oversight ---
// filters: {provider_id, start, end} — all optional. Returns every encounter in
// the system (across providers) for the given filter window.
export async function adminEncounters(filters = {}) {
  const params = new URLSearchParams();
  if (filters.provider_id) params.set("provider_id", filters.provider_id);
  if (filters.start) params.set("start", filters.start);
  if (filters.end) params.set("end", filters.end);
  const qs = params.toString();
  const res = await authedFetch(`${BASE}/admin/encounters${qs ? `?${qs}` : ""}`);
  if (!res.ok) throw new Error("Could not load encounters.");
  return res.json();
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

// The provider's most recent OPEN draft encounter, so a returning provider (even
// on a different browser/device) picks up where they left off. Returns the draft
// object {id, patient_first_name, patient_last_name, patient_dob, transcript_text,
// template_id, ...} or null when there's nothing in progress.
export async function getDraft() {
  const res = await authedFetch(`${BASE}/encounters/draft`);
  if (!res.ok) throw new Error("Could not load draft.");
  return res.json(); // may be JSON null
}

// Persist an in-progress transcript to the server so the draft survives a reload
// or a switch to another device. Only the transcript is autosaved here.
export async function patchEncounter(encounterId, body) {
  const res = await authedFetch(`${BASE}/encounters/${encounterId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`Could not save draft (${res.status}).`);
  return res.json();
}

// Active note templates any authed provider can pick from before generating.
// Returns [{id, name}]. The backend re-reads the chosen template at generation
// time, so admin edits take effect on the next note without a page reload.
export async function listTemplates() {
  const res = await authedFetch(`${BASE}/templates`);
  if (!res.ok) throw new Error("Could not load templates.");
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

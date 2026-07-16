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
export function clearToken() {
  localStorage.removeItem("access_token");
}

function authHeaders() {
  const t = getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
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
  return data;
}

export async function createEncounter(body) {
  const res = await fetch(`${BASE}/encounters`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`Could not start encounter (${res.status}).`);
  return res.json();
}

export async function saveVersion(encounterId, soap) {
  const res = await fetch(`${BASE}/encounters/${encounterId}/versions`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(soap),
  });
  if (!res.ok) throw new Error(`Save failed (${res.status}).`);
  return res.json();
}

export async function searchIcd(query) {
  const res = await fetch(
    `${BASE}/icd/search?q=${encodeURIComponent(query)}&limit=6`,
    { headers: authHeaders() }
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
export async function generateNote(encounterId, { onText, onDone, onError }) {
  const res = await fetch(`${BASE}/encounters/${encounterId}/generate`, {
    method: "POST",
    headers: authHeaders(),
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
      else if (payload.done) onDone();
      else if (payload.error) onError(payload.error);
    }
  }
}

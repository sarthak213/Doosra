// Unset: the local API. Set to "" (the container build): the API is on the same origin as the page.
export const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

// Engine errors come back as HTTP 400 with {error, candidates?}; surface them
// as an Error carrying that payload so views can show "did you mean...".
export class ApiError extends Error {
  constructor(payload) {
    super(payload?.error || payload?.detail || "Request failed");
    this.payload = payload;
  }
}

// Fired when the server says the session is gone, so the app can show the login page.
export const UNAUTHORIZED = "doosra:unauthorized";

async function handle(res) {
  const body = await res.json().catch(() => ({}));
  if (res.status === 401) window.dispatchEvent(new Event(UNAUTHORIZED));
  if (!res.ok || body?.error) throw new ApiError(body);
  return body;
}

function query(params) {
  const q = new URLSearchParams();
  Object.entries(params || {}).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") q.set(k, Array.isArray(v) ? v.join(",") : v);
  });
  const s = q.toString();
  return s ? `?${s}` : "";
}

export function apiGet(path, params, signal) {
  return fetch(`${API_BASE}${path}${query(params)}`, { signal, credentials: "include" }).then(handle);
}

export function apiSend(path, body, method = "POST", signal) {
  return fetch(`${API_BASE}${path}`, {
    method,
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  }).then(handle);
}

export const playerPath = (name) => `/api/players/${encodeURIComponent(name)}`;

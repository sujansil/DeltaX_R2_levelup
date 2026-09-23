function auth() {
  const raw = localStorage.getItem("levelup_auth");
  return raw ? JSON.parse(raw) : null;
}
function saveAuth(data) {
  localStorage.setItem("levelup_auth", JSON.stringify(data));
}
function clearAuth() {
  localStorage.removeItem("levelup_auth");
}
async function api(path, opts = {}) {
  const a = auth();
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  if (a && a.access_token && !path.endsWith("/login")) {
    headers.Authorization = "Bearer " + a.access_token;
  }
  const res = await fetch(path, { ...opts, headers });
  const text = await res.text();
  let body = null;
  try { body = text ? JSON.parse(text) : null; } catch { body = { message: text }; }
  if (!res.ok) {
    const msg = (body && (body.message || body.detail?.message || body.detail)) || res.statusText;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return body;
}
function connectWs(onMsg) {
  const a = auth();
  if (!a) return;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(a.access_token)}`);
  ws.onmessage = (ev) => {
    try { onMsg(JSON.parse(ev.data)); } catch {}
  };
  return ws;
}

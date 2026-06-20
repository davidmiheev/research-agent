// Thin browser-side client for the local invoke proxy (see server/ignite-api.js).
async function post(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  return r.json();
}

export const connect = (saId, saSecret) => post("/api/connect", { saId, saSecret });
export const disconnect = () => post("/api/disconnect", {});
export const status = () => fetch("/api/status").then((r) => r.json());
export const invoke = (appId, payload, url) =>
  post("/api/invoke", { appId, payload, url });

# 🧭 Atlas — UI

A chat UI for the [`ignite-research-agent`](../ignite-research-agent) app.
Sign in with a Dodil service account and talk to Atlas, your personal research
agent — watch it store long-term memories in K3 (tool calls show up inline).

## Run it locally

```bash
npm install
npm run dev
# open http://localhost:5174
```

Single command. (Requires Node 18+.) Runs on **port 5174**.

## How it works

```
browser  ──►  /api/* (Vite dev-server proxy, server/ignite-api.js)  ──►  https://<app>.ignite.dodil.cloud/
            (React chat)      token mint + authenticated POST
```

- **Auth page** collects a **Service Account ID + Secret**, posted once to the
  local proxy which mints + refreshes a short-lived token
  (OIDC `client_credentials`). The secret never lives in the browser.
- **Invocation uses the HTTP path**: the proxy POSTs `{message, session_id}` to
  the app's public URL with `Authorization: Bearer <token>`. Done server-side
  because the Ignite gateway requires the token and emits no CORS headers.
- Each reply renders the agent's `tools_used` (e.g. `save_memory → k3://…`) so
  the K3 writes are visible during the demo.

## Prerequisites
- [`ignite-research-agent`](../ignite-research-agent) deployed to Ignite **with
  its model + K3 env configured** (`MODEL_API_BASE/KEY/NAME`, `DODIL_SA_*`).
- A service account in the same org allowed to invoke it.

## Config (optional env)
| Var | Default |
|-----|---------|
| `DODIL_OIDC_URL` | `https://id.dev.dodil.io/realms/dodil/protocol/openid-connect/token` |
| `IGNITE_BASE_DOMAIN` | `ignite.dodil.cloud` |

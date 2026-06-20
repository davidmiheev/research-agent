// Tiny server-side proxy for the Dodil Ignite HTTP invoke path, exposed as a
// Vite dev-server middleware so the whole UI runs from a single `npm run dev`.
//
// Why a server-side proxy at all?
//   * The Ignite gateway requires a Bearer token on every invoke (401 without).
//   * It returns no CORS headers, so a browser cannot call the public app URL
//     directly. Doing the call from Node (here) sidesteps CORS entirely.
//   * The service-account secret stays on this side — the browser only posts it
//     once to /api/connect and never has to hold a token itself.
//
// Endpoints added to the dev server:
//   POST /api/connect    { saId, saSecret }        -> { connected, org }
//   GET  /api/status                                -> { connected, org }
//   POST /api/invoke     { appId, payload, url? }   -> { ok, httpStatus, json|raw }
//   POST /api/disconnect                            -> { connected:false }

const OIDC_URL =
  process.env.DODIL_OIDC_URL ||
  "https://id.dev.dodil.io/realms/dodil/protocol/openid-connect/token";
const BASE_DOMAIN = process.env.IGNITE_BASE_DOMAIN || "ignite.dodil.cloud";

// Single in-memory session — this is a local, single-user dev tool.
let session = null; // { saId, saSecret, token, expEpoch, org }

function decodeJwt(token) {
  try {
    const payload = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    return JSON.parse(Buffer.from(payload, "base64").toString("utf8"));
  } catch {
    return {};
  }
}

function orgFromClaims(claims) {
  const orgs = claims.organization || {};
  const name = Object.keys(orgs)[0];
  return { id: name ? orgs[name]?.id : claims.org_id, name: name || claims.org_name };
}

async function mintToken(saId, saSecret) {
  const body = new URLSearchParams({
    client_id: saId,
    client_secret: saSecret,
    grant_type: "client_credentials",
  });
  const r = await fetch(OIDC_URL, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  if (!r.ok) {
    const text = await r.text();
    throw new Error(`auth failed (HTTP ${r.status}): ${text.slice(0, 200)}`);
  }
  return (await r.json()).access_token;
}

async function freshToken() {
  if (!session) throw new Error("not connected — POST /api/connect first");
  const now = Date.now() / 1000;
  if (!session.token || now > session.expEpoch - 30) {
    session.token = await mintToken(session.saId, session.saSecret);
    const claims = decodeJwt(session.token);
    session.expEpoch = claims.exp || now + 300;
    session.org = orgFromClaims(claims);
  }
  return session.token;
}

// `cardinalai:fx-converter` (or `cardinalai/fx-converter`) ->
// https://fx-converter-cardinalai.ignite.dodil.cloud/
function appIdToUrl(appId) {
  const parts = String(appId).split(/[:/]/);
  const org = parts[0];
  const fn = parts.slice(1).join("-");
  if (!org || !fn) throw new Error(`invalid app id: ${appId} (expected org:app)`);
  return `https://${fn}-${org}.${BASE_DOMAIN}/`;
}

function readBody(req) {
  return new Promise((resolve) => {
    let data = "";
    req.on("data", (c) => (data += c));
    req.on("end", () => {
      try {
        resolve(JSON.parse(data || "{}"));
      } catch {
        resolve({});
      }
    });
  });
}

function send(res, status, obj) {
  res.statusCode = status;
  res.setHeader("Content-Type", "application/json");
  res.end(JSON.stringify(obj));
}

export function ignitePlugin() {
  return {
    name: "ignite-invoke-proxy",
    configureServer(server) {
      server.middlewares.use("/api/connect", async (req, res, next) => {
        if (req.method !== "POST") return next();
        try {
          const { saId, saSecret } = await readBody(req);
          if (!saId || !saSecret)
            return send(res, 400, { error: "saId and saSecret are required" });
          session = { saId, saSecret, token: null, expEpoch: 0, org: null };
          await freshToken(); // validates the creds immediately
          send(res, 200, { connected: true, org: session.org });
        } catch (e) {
          session = null;
          send(res, 401, { connected: false, error: String(e.message || e) });
        }
      });

      server.middlewares.use("/api/status", (req, res) => {
        send(res, 200, { connected: !!session, org: session?.org || null });
      });

      server.middlewares.use("/api/disconnect", (req, res) => {
        session = null;
        send(res, 200, { connected: false });
      });

      server.middlewares.use("/api/invoke", async (req, res, next) => {
        if (req.method !== "POST") return next();
        try {
          const { appId, payload, url } = await readBody(req);
          const token = await freshToken();
          const target = url || appIdToUrl(appId);
          const started = Date.now();
          const upstream = await fetch(target, {
            method: "POST",
            headers: {
              Authorization: `Bearer ${token}`,
              "Content-Type": "application/json",
            },
            body: JSON.stringify(payload ?? {}),
          });
          const text = await upstream.text();
          let json;
          try {
            json = JSON.parse(text);
          } catch {
            json = undefined;
          }
          send(res, 200, {
            ok: upstream.ok,
            httpStatus: upstream.status,
            target,
            ms: Date.now() - started,
            json,
            raw: json === undefined ? text : undefined,
          });
        } catch (e) {
          send(res, 500, { ok: false, error: String(e.message || e) });
        }
      });
    },
  };
}

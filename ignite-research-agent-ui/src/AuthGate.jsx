// Shared auth shell used by all the Ignite demo UIs.
//
// Renders a service-account sign-in card. Once connected, it exposes the
// connection (org + a bound `invoke`) to children via context. The service
// account id + secret are only ever posted to the local proxy — they are never
// stored in the browser.
import React, { createContext, useContext, useEffect, useState } from "react";
import * as api from "./api.js";

const IgniteContext = createContext(null);
export const useIgnite = () => useContext(IgniteContext);

export default function AuthGate({ title, subtitle, defaultAppId, children }) {
  const [connected, setConnected] = useState(false);
  const [org, setOrg] = useState(null);
  const [appId, setAppId] = useState(defaultAppId);
  const [overrideUrl, setOverrideUrl] = useState("");
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    api.status().then((s) => {
      setConnected(!!s.connected);
      setOrg(s.org || null);
      setBusy(false);
    });
  }, []);

  async function onConnect(saId, saSecret) {
    setBusy(true);
    setError("");
    const r = await api.connect(saId, saSecret);
    setBusy(false);
    if (r.connected) {
      setConnected(true);
      setOrg(r.org || null);
    } else {
      setError(r.error || "connection failed");
    }
  }

  async function onDisconnect() {
    await api.disconnect();
    setConnected(false);
    setOrg(null);
  }

  const ctx = {
    org,
    appId,
    setAppId,
    invoke: (payload) => api.invoke(appId, payload, overrideUrl || undefined),
  };

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <span className="logo">⚡</span>
          <div>
            <h1>{title}</h1>
            <p>{subtitle}</p>
          </div>
        </div>
        {connected && (
          <div className="conn">
            <span className="chip ok">● {org?.name || "connected"}</span>
            <button className="ghost" onClick={onDisconnect}>
              Sign out
            </button>
          </div>
        )}
      </header>

      <main className="content">
        {connected ? (
          <IgniteContext.Provider value={ctx}>
            <div className="targetbar">
              <label>
                App
                <input
                  value={appId}
                  onChange={(e) => setAppId(e.target.value)}
                  spellCheck={false}
                />
              </label>
              <label className="grow">
                Override URL (optional)
                <input
                  value={overrideUrl}
                  placeholder="auto-derived from app id"
                  onChange={(e) => setOverrideUrl(e.target.value)}
                  spellCheck={false}
                />
              </label>
            </div>
            {children}
          </IgniteContext.Provider>
        ) : (
          <ConnectForm busy={busy} error={error} appId={appId} setAppId={setAppId} onConnect={onConnect} />
        )}
      </main>
    </div>
  );
}

function ConnectForm({ busy, error, appId, setAppId, onConnect }) {
  const [saId, setSaId] = useState("");
  const [saSecret, setSaSecret] = useState("");

  function submit(e) {
    e.preventDefault();
    onConnect(saId.trim(), saSecret.trim());
  }

  return (
    <form className="card auth" onSubmit={submit}>
      <h2>Connect a service account</h2>
      <p className="muted">
        Invocations are authenticated with a Dodil service account. Credentials
        are sent only to the local proxy and used to mint a short-lived token.
      </p>
      <label>
        Service Account ID
        <input
          value={saId}
          onChange={(e) => setSaId(e.target.value)}
          placeholder="my-svc-acct@svcacc.dodil.io"
          autoFocus
          spellCheck={false}
        />
      </label>
      <label>
        Service Account Secret
        <input
          type="password"
          value={saSecret}
          onChange={(e) => setSaSecret(e.target.value)}
          placeholder="••••••••••••"
        />
      </label>
      <details className="advanced">
        <summary>Advanced</summary>
        <label>
          Target app id
          <input value={appId} onChange={(e) => setAppId(e.target.value)} spellCheck={false} />
        </label>
      </details>
      {error && <div className="error">{error}</div>}
      <button className="primary" disabled={busy || !saId || !saSecret}>
        {busy ? "Connecting…" : "Connect"}
      </button>
    </form>
  );
}

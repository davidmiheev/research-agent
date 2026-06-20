import React, { useEffect, useRef, useState } from "react";
import AuthGate, { useIgnite } from "./AuthGate.jsx";

export default function App() {
  return (
    <AuthGate
      title="Atlas"
      subtitle="Personal research agent with long-term memory in K3 · Dodil Ignite"
      defaultAppId="cardinalai:research-agent"
    >
      <Chat />
    </AuthGate>
  );
}

const SESSION_ID = "ui-" + Math.random().toString(36).slice(2);

function Chat() {
  const { invoke } = useIgnite();
  const [messages, setMessages] = useState([]); // {role, text} or {tool, result}
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const logRef = useRef(null);

  useEffect(() => {
    logRef.current?.scrollTo(0, logRef.current.scrollHeight);
  }, [messages, busy]);

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", text }]);
    setBusy(true);
    const r = await invoke({ message: text, session_id: SESSION_ID });
    setBusy(false);

    if (r.error) return push({ role: "bot", text: `⚠ ${r.error}` });
    if (r.ok === false) return push({ role: "bot", text: `⚠ HTTP ${r.httpStatus}: ${r.raw || ""}` });
    const data = r.json ?? {};
    (data.tools_used || []).forEach((t) =>
      push({ tool: t.tool, result: t.result })
    );
    push({ role: "bot", text: data.reply || data.error || JSON.stringify(data) });
  }

  function push(item) {
    setMessages((m) => [...m, item]);
  }

  const suggestions = [
    "Remember that my main project is a RAG agent benchmark, due July 10.",
    "What are you remembering for me so far?",
    "Summarize what I should focus on next.",
  ];

  return (
    <div className="panel">
      <div className="card">
        <div className="chatlog" ref={logRef}>
          {messages.length === 0 && (
            <div className="muted" style={{ padding: "8px 2px" }}>
              Ask Atlas something, or tell it a fact to remember — it stores
              memories in K3 via the unified CLI.
            </div>
          )}
          {messages.map((m, i) =>
            m.tool ? (
              <div className="toolnote" key={i}>
                🛠 {m.tool} → {m.result}
              </div>
            ) : (
              <div className={`bubble ${m.role}`} key={i}>
                {m.text}
              </div>
            )
          )}
          {busy && <div className="bubble bot">…</div>}
        </div>

        <div className="composer">
          <input
            value={input}
            placeholder="Message Atlas…"
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && send()}
            autoFocus
          />
          <button className="primary" onClick={send} disabled={busy}>
            Send
          </button>
        </div>
      </div>

      <div className="card">
        <div className="muted" style={{ marginBottom: 10 }}>Try:</div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
          {suggestions.map((s, i) => (
            <button key={i} className="ghost" onClick={() => setInput(s)}>
              {s}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

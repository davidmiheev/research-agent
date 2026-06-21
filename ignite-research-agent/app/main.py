"""FastAPI surface for the research agent, shaped for the Ignite BYOI contract.

Ignite routes traffic to this container on $PORT (default 80) and uses GET
/healthz for readiness/liveness. `ignite invoke <app>` performs a POST / with a
JSON body, so POST / is the invocation entrypoint. GET / serves a tiny browser
chat UI so the agent can also be demoed live by opening its public URL.
"""

from __future__ import annotations

from collections import defaultdict

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from . import agent, k3, llm

app = FastAPI(title="Ignite Research Agent", docs_url="/docs")

# Full per-session conversation, kept in-process (fine for a single-replica
# demo). The whole thing is what `save_chat` persists; only the most recent
# CONTEXT_WINDOW messages are fed to the model each turn for continuity.
_SESSIONS: dict[str, list[dict]] = defaultdict(list)
CONTEXT_WINDOW = 40


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


def _chat(message: str, session_id: str) -> dict:
    convo = _SESSIONS[session_id]
    result = agent.run_agent(message, convo[-CONTEXT_WINDOW:], full_history=convo)
    convo.append({"role": "user", "content": message})
    convo.append({"role": "assistant", "content": result["reply"]})
    return result


@app.post("/")
async def invoke(request: Request):
    """Invocation entrypoint (used by `ignite invoke` and the browser UI)."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if isinstance(body, str):
        body = {"message": body}

    message = (body or {}).get("message") or (body or {}).get("prompt") or ""
    session_id = (body or {}).get("session_id", "default")
    if not message:
        return JSONResponse(
            {"error": "send {\"message\": \"...\"}", "model_configured": llm.is_configured()},
            status_code=400,
        )
    return _chat(message, session_id)


@app.post("/memories/list")
def memories_list():
    return {"memories": k3.list_memories()}


@app.post("/memories/search")
async def memories_search(request: Request):
    body = await request.json()
    return {"results": k3.search_memories(body.get("query", ""), int(body.get("top_k", 5)))}


@app.post("/chat/save")
async def chat_save(request: Request):
    """Persist a session's conversation to memory (used by the UI's save button)."""
    body = await request.json()
    session_id = (body or {}).get("session_id", "default")
    return {"result": k3.save_chat(_SESSIONS.get(session_id, []), (body or {}).get("title"))}


@app.get("/", response_class=HTMLResponse)
def home():
    return HTMLResponse(_INDEX_HTML)


# No startup warm-up on purpose: K3 provisioning (`k3.ensure_ingest`) runs lazily
# on the first save/search instead. Doing that network burst at boot starves the
# single-CPU pod's event loop during the platform's tight liveness probe
# (initial_delay 2s, timeout 1s) and can crashloop the container before it ever
# goes Ready. The pod must answer GET /healthz the instant uvicorn binds.


_INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Atlas — Research Agent</title>
<style>
  :root { color-scheme: dark; }
  body { margin:0; font-family: ui-sans-serif, system-ui, sans-serif; background:#0b0e14; color:#e6e6e6; }
  header { padding:18px 24px; border-bottom:1px solid #1c2230; }
  header h1 { margin:0; font-size:18px; } header p { margin:4px 0 0; color:#8a93a6; font-size:13px; }
  #log { max-width:760px; margin:0 auto; padding:24px 16px 120px; }
  .msg { padding:12px 16px; border-radius:12px; margin:10px 0; white-space:pre-wrap; line-height:1.45; }
  .user { background:#1b2a4a; margin-left:48px; }
  .bot  { background:#15192340; border:1px solid #1c2230; margin-right:48px; }
  .tool { font-size:12px; color:#7fd1b9; background:#0f1a17; border:1px solid #1d3b33; border-radius:8px; padding:8px 12px; margin:6px 48px 6px 0; }
  form { position:fixed; bottom:0; left:0; right:0; background:#0b0e14; border-top:1px solid #1c2230; padding:14px; }
  .row { max-width:760px; margin:0 auto; display:flex; gap:10px; }
  input { flex:1; padding:12px 14px; border-radius:10px; border:1px solid #2a3346; background:#11151f; color:#e6e6e6; font-size:15px; }
  button { padding:12px 20px; border:0; border-radius:10px; background:#3b82f6; color:#fff; font-weight:600; cursor:pointer; }
</style>
</head>
<body>
<header>
  <h1>🧭 Atlas — personal research agent</h1>
  <p>On Dodil Ignite · semantic long-term memory in K3 · arXiv paper search</p>
</header>
<div id="log"></div>
<form id="f"><div class="row">
  <input id="m" autocomplete="off" placeholder="Ask Atlas, or tell it something to remember…" autofocus/>
  <button>Send</button>
</div></form>
<script>
const log = document.getElementById('log');
const sid = 'web-' + Math.random().toString(36).slice(2);
function add(cls, text){ const d=document.createElement('div'); d.className='msg '+cls; d.textContent=text; log.appendChild(d); window.scrollTo(0,document.body.scrollHeight); }
function addTool(t){ const d=document.createElement('div'); d.className='tool'; d.textContent='🛠 '+t.tool+' → '+t.result; log.appendChild(d); }
document.getElementById('f').addEventListener('submit', async (e)=>{
  e.preventDefault();
  const inp=document.getElementById('m'); const msg=inp.value.trim(); if(!msg) return;
  inp.value=''; add('user', msg); add('bot','…');
  const thinking=log.lastChild;
  try{
    const r=await fetch('/',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:msg,session_id:sid})});
    const j=await r.json();
    (j.tools_used||[]).forEach(addTool);
    thinking.textContent = j.reply || j.error || JSON.stringify(j);
  }catch(err){ thinking.textContent='Error: '+err; }
});
</script>
</body>
</html>"""

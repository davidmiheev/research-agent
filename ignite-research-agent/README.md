# 🧭 Atlas — a personal research agent on Dodil Ignite

> *"I'm an AI researcher. I want a personal assistant that remembers things for
> me, can dig up papers, and runs on Ignite platform."*

Atlas is a small Python agent that:

- serves a **chat API + browser UI** over HTTP,
- thinks with **Ignite's own OpenAI-compatible model endpoint**,
- keeps **long-term memory in Dodil K3 with semantic vector search**, and
- searches **arXiv** for relevant papers.

It deploys to **Dodil Ignite straight from this repo's Dockerfile** — and it
authenticates to everything (model, K3, all of it) with a single **service
account**, so the image is just Python + `httpx`.

---

## How it works

```
browser / `ignite invoke`
        │  POST /  {"message": "..."}
        ▼
   FastAPI (app/main.py)
        │
        ▼
   agent loop (app/agent.py) ──► Ignite chat completions (app/llm.py)
        │                          POST https://api.dev.dodil.io/v1/chat/completions
        │
        ├──► save / search memory (app/k3.py) ──► K3 REST (https://k3.dev.dodil.io)
        │         bucket + auto vector engine + `object_embedding_index` pipeline
        │
        └──► search_arxiv (app/arxiv.py) ──► export.arxiv.org
```

The agent is **endpoint-agnostic**: it prompts the model to emit a small JSON
object to call a tool, so it works against any chat-completions gateway.

### Tools
| Tool | What it does |
|------|--------------|
| `save_memory` | store a durable fact as a K3 object (auto-embedded for search) |
| `search_memory` | **semantic vector search** over memories (`POST /:bucket/vector/search`) |
| `list_memories` | list stored memory objects |
| `search_arxiv` | find relevant arXiv papers |
| `load_arxiv_paper` | download a paper's **PDF** and upload it to K3 — K3 extracts + embeds it, so the full text becomes searchable |
| `save_chat` | persist the current conversation to memory so a future chat can recall it |

### Conversation context
The agent keeps the **full conversation per session** in process; the last
`CONTEXT_WINDOW` (40) messages are fed to the model each turn for continuity, and
the whole conversation is what `save_chat` (or `POST /chat/save`) persists. A
loaded arXiv PDF is processed by K3's ingestion pipeline (extract → chunk →
embed) — e.g. *Attention Is All You Need* lands as ~86 searchable chunks.

### Memory + vector search in K3
A saved memory goes through K3's real ingestion chain, which the agent sets up
idempotently on first use:

```
bucket → auto vector engine → text-embedding pipeline → source → ingest rule
       → discovery + ingestion (K3 embeds the text) → vector search
```

`save_memory` PUTs the memory as a text object and triggers ingestion, so K3
embeds it (template `text_embedding_index`, managed engine — the agent never
embeds anything itself). `search_memory` is a plain text query against
`POST /:bucket/vector/search`; K3 embeds the query and returns the nearest
memories with scores. If an operator has already provisioned the engine +
pipelines on the bucket, the agent discovers them instead of duplicating.

---

## Configuration (all via environment variables)

| Variable | Default | Purpose |
|----------|---------|---------|
| `DODIL_SA_ID` | — | service account id (mints the bearer token for model + K3) |
| `DODIL_SA_SECRET` | — | service account secret |
| `MODEL_API_BASE` | `https://api.dev.dodil.io/v1` | OpenAI-style chat base |
| `MODEL_NAME` | `kimi-k2.6` | chat model id |
| `MODEL_API_KEY` | — | static bearer instead of the SA token (for a non-Ignite gateway) |
| `MODEL_TEMPERATURE` | — | omitted by default (kimi only accepts its fixed default) |
| `K3_API_BASE` | `https://k3.dev.dodil.io` | K3 REST base |
| `K3_BUCKET` | `agent-memories` | memory bucket |
| `K3_COLLECTION` | `memories` | vector collection name |
| `K3_TEMPLATE_ID` | `text_embedding_index` | K3 embedding pipeline template |

> Secrets are **never** committed — supply them at deploy time with `--env`.

---

## Deploy to Ignite from this repo


One command — `app deploy` does create (or update) → save → compile/build →
publish:

```bash
dodil ignite app deploy your-org:research-agent \
  --git-url https://github.com/davidmiheev/ignite-research-agent.git --git-ref main \
  --git-sub-path ignite-research-agent
  --dockerfile-path Dockerfile \
  --tier medium --description "Personal research agent with K3 memory + arXiv" \
  --env DODIL_SA_ID=<svcacc-id> --env DODIL_SA_SECRET=<svcacc-secret> \
  --follow
  # MODEL_*/K3_* already default to the dev cluster; override with more --env if needed
```

---

## Demo it live

```bash
# Ask it something — it answers with Ignite's model
dodil ignite invoke your-org:research-agent \
  -p '{"message":"In one sentence, what is MoE?"}'

# Tell it a fact — it saves a memory to K3
dodil ignite invoke your-org:research-agent \
  -p '{"message":"Remember my project is a RAG benchmark called RagBench, due July 10."}'

# Ask for papers — it searches arXiv
dodil ignite invoke your-org:research-agent \
  -p '{"message":"Find recent arXiv papers on distillation."}'

# Later: semantic recall (vector search over K3 memories)
dodil ignite invoke your-org:research-agent \
  -p '{"message":"What do you remember about my project deadlines?"}'
```

Or open the public URL and chat in the browser (tool calls show inline). The
companion **`ignite-research-agent-ui`** repo is a polished front end for this.

> **Note:** the first time a bucket's `auto` engine is created, K3 provisions a
> vbase instance asynchronously (a minute or two) — `save_memory`/`list` work
> immediately; `search_memory` lights up once the engine is ready. After that,
> a newly saved memory takes ~30s to discover + embed before it's searchable.

---

## Run locally

```bash
pip install -r requirements.txt
export DODIL_SA_ID=... DODIL_SA_SECRET=...
PORT=8080 uvicorn app.main:app --host 0.0.0.0 --port 8080
# open http://localhost:8080/
```

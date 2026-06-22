"""Long-term memory backed by Dodil K3 with native vector search.

Everything goes through the K3 HTTP REST API (the gRPC CLI can't be used from a
service). The full, working flow K3 actually requires:

  bucket → vector engine (auto) → embedding pipeline (template) →
  source → ingest rule → discovery + ingestion → vector search

So a saved memory becomes searchable like this:
  1. PUT the memory text as an object            PUT  /:bucket/:key
  2. an ingest rule routes it to the text pipeline, which embeds it
  3. semantic recall                             POST /:bucket/vector/search {text}

`ensure_ingest()` provisions the engine/pipeline/rule idempotently, so the agent
works against a freshly-created bucket *and* against one an operator already set
up (it discovers the existing text pipeline instead of duplicating it). After
each save we trigger discovery+ingestion so a new memory indexes promptly
instead of waiting for the source's periodic sync.
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import os
import re
import time
import xml.etree.ElementTree as ET

import httpx

from . import auth

BASE = os.getenv("K3_API_BASE", "https://k3.dev.dodil.io").rstrip("/")
BUCKET = os.getenv("K3_BUCKET", "agent-memories")
COLLECTION = os.getenv("K3_COLLECTION", "memories")
# The built-in text embedding pipeline template (K3 embeds objects for us).
TEMPLATE_ID = os.getenv("K3_TEMPLATE_ID", "text_embedding_index")

_state = {"bucket": False, "ingest": False, "source_id": None, "pipeline_id": None}


def _headers(content_type: str = "application/json") -> dict:
    h = {
        "Authorization": f"Bearer {auth.get_token()}",
        "x-organization-id": auth.org_id(),
        "x-organization-name": auth.org_name(),
    }
    if content_type:
        h["Content-Type"] = content_type
    return h


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s or "memory")[:48]


def _get(client: httpx.Client, path: str) -> dict:
    try:
        r = client.get(path, headers=_headers(content_type=""))
        return r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    except Exception:
        return {}


def _post(client: httpx.Client, path: str, body: dict) -> dict:
    try:
        r = client.post(path, headers=_headers(), json=body)
        try:
            return r.json()
        except Exception:
            return {}
    except Exception:
        return {}


def ensure_bucket() -> None:
    if _state["bucket"]:
        return
    with httpx.Client(base_url=BASE, timeout=30) as c:
        _post(c, "/admin/buckets", {"name": BUCKET, "description": "research agent memory"})
    _state["bucket"] = True


def ensure_ingest() -> None:
    """Idempotently ensure: engine + text pipeline + internal source + rule."""
    if _state["ingest"]:
        return
    ensure_bucket()
    with httpx.Client(base_url=BASE, timeout=60) as c:
        _post(c, f"/{BUCKET}/vector", {"bucket": BUCKET, "mode": "auto"})

        # Find an existing text-embedding collection, else create one.
        cols = _get(c, f"/{BUCKET}/vector/collections").get("collections", [])
        text_col = next(
            (x for x in cols if x.get("embedPipelineName") == TEMPLATE_ID), None
        ) or next((x for x in cols if "text" in (x.get("name") or "")), None)
        if not text_col:
            text_col = _post(
                c,
                f"/{BUCKET}/vector/pipelines",
                {"bucket": BUCKET, "name": COLLECTION, "template_id": TEMPLATE_ID},
            )
        _state["pipeline_id"] = (text_col or {}).get("embedPipelineId")

        srcs = _get(c, f"/{BUCKET}/sources").get("sources", [])
        _state["source_id"] = srcs[0]["sourceId"] if srcs else None

        rules = _get(c, f"/{BUCKET}/rules").get("rules", [])
        if _state["source_id"] and _state["pipeline_id"] and not rules:
            _post(
                c,
                f"/{BUCKET}/rules",
                {
                    "bucket": BUCKET,
                    "source_id": _state["source_id"],
                    "name": "agent-memories",
                    "include_patterns": ["**"],
                    "pipeline_id": _state["pipeline_id"],
                    "enabled": True,
                },
            )
    _state["ingest"] = True


def _trigger_ingest() -> None:
    sid = _state.get("source_id")
    if not sid:
        return
    with httpx.Client(base_url=BASE, timeout=30) as c:
        _post(c, f"/{BUCKET}/sources/{sid}/discover", {"bucket": BUCKET, "source_id": sid, "full_sync": True})
        _post(c, f"/{BUCKET}/sources/{sid}/ingest", {"bucket": BUCKET, "source_id": sid})


def save_memory(title: str, content: str, tags: list[str] | None = None) -> str:
    try:
        ensure_bucket()
        ensure_ingest()
    except auth.NotConfigured as e:
        return f"Could not save memory: {e}"
    except Exception:
        pass  # provisioning is best-effort; the write below is what matters

    key = f"{_slug(title)}-{int(time.time())}.txt"
    body = f"{title}\n\n{content}"
    if tags:
        body += f"\n\ntags: {', '.join(tags)}"

    try:
        with httpx.Client(base_url=BASE, timeout=30) as c:
            r = c.put(f"/{BUCKET}/{key}", headers=_headers("text/plain"), content=body.encode())
    except Exception as e:
        return f"Could not save memory: {e}"
    if r.status_code >= 300:
        return f"Could not save memory: HTTP {r.status_code} {r.text[:160]}"

    try:
        _trigger_ingest()  # index it now rather than waiting for the periodic sync
    except Exception:
        pass
    return f"Saved memory '{title}' to k3://{BUCKET}/{key} — K3 is embedding it for semantic recall."


def put_object(key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
    """Upload a raw object (e.g. a PDF) and let K3's pipeline process it."""
    try:
        ensure_bucket()
        ensure_ingest()
    except auth.NotConfigured as e:
        return f"Could not upload: {e}"
    except Exception:
        pass  # provisioning is best-effort; the write below is what matters

    try:
        with httpx.Client(base_url=BASE, timeout=120) as c:
            r = c.put(f"/{BUCKET}/{key}", headers=_headers(content_type), content=data)
    except Exception as e:
        return f"Could not upload: {e}"
    if r.status_code >= 300:
        return f"Could not upload {key}: HTTP {r.status_code} {r.text[:160]}"

    try:
        _trigger_ingest()
    except Exception:
        pass
    return (
        f"Stored k3://{BUCKET}/{key} — K3 is indexing it now; it becomes searchable "
        f"in ~30s (not yet), so don't search for its contents immediately."
    )


# ── Loaded-paper index ────────────────────────────────────────────────────────
# A small catalog object so the agent remembers which arXiv papers it has already
# uploaded (and doesn't re-download/re-embed them).
INDEX_KEY = os.getenv("K3_PAPERS_INDEX", "papers-index.json")


def _get_index() -> dict:
    try:
        with httpx.Client(base_url=BASE, timeout=15) as c:
            r = c.get(f"/{BUCKET}/{INDEX_KEY}", headers=_headers(content_type=""))
        if r.status_code == 200:
            data = r.json()
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def paper_loaded(arxiv_id: str) -> bool:
    # Authoritative check: does the PDF object exist? (Covers papers uploaded
    # before the index existed.) Fall back to the index if the HEAD fails.
    key = f"arxiv-{arxiv_id.replace('/', '-')}.pdf"
    try:
        with httpx.Client(base_url=BASE, timeout=15) as c:
            r = c.head(f"/{BUCKET}/{key}", headers=_headers(content_type=""))
        if r.status_code == 200:
            return True
        if r.status_code == 404:
            return False
    except Exception:
        pass
    return arxiv_id in _get_index()


def _put_index(idx: dict) -> None:
    try:
        ensure_bucket()
        with httpx.Client(base_url=BASE, timeout=15) as c:
            c.put(f"/{BUCKET}/{INDEX_KEY}", headers=_headers("application/json"),
                  content=json.dumps(idx).encode())
    except Exception:
        pass  # the index is a convenience; never fail an upload over it


def record_paper(arxiv_id: str, title: str, url: str = "") -> None:
    idx = _get_index()
    idx[arxiv_id] = {
        "title": title,
        "url": url,
        "loaded_at": time.strftime("%Y-%m-%d", time.gmtime()),
    }
    _put_index(idx)


def list_papers() -> str:
    # Authoritative list = the uploaded PDF objects; titles come from the index.
    idx = _get_index()
    ids: list[str] = []
    try:
        with httpx.Client(base_url=BASE, timeout=15) as c:
            r = c.get(f"/{BUCKET}?list-type=2&prefix=arxiv-", headers=_headers(content_type=""))
        if r.status_code == 200:
            root = ET.fromstring(r.text)
            ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
            for k in root.findall(".//s3:Contents/s3:Key", ns):
                m = re.match(r"arxiv-(.+)\.pdf$", k.text or "")
                if m:
                    ids.append(m.group(1))
    except Exception:
        ids = list(idx.keys())

    all_ids = sorted(set(ids) | set(idx.keys()))
    if not all_ids:
        return "No arXiv papers have been loaded into memory yet."

    # Fill in any titles we don't know yet (one batched arXiv call) and remember
    # them, so papers loaded before the index get titles too.
    missing = [a for a in all_ids if not (isinstance(idx.get(a), dict) and idx[a].get("title"))]
    if missing:
        from . import arxiv  # lazy import to avoid a cycle

        fetched = arxiv.titles_for(missing)
        if fetched:
            for a, t in fetched.items():
                idx[a] = {**(idx.get(a) if isinstance(idx.get(a), dict) else {}), "title": t}
            _put_index(idx)

    lines = []
    for aid in all_ids:
        meta = idx.get(aid)
        title = meta.get("title", "") if isinstance(meta, dict) else ""
        lines.append(f"- arXiv:{aid}" + (f" — {title}" if title else ""))
    return "Papers currently in memory:\n" + "\n".join(lines)


def save_chat(messages: list[dict], title: str | None = None) -> str:
    """Persist a conversation to memory so a future chat can recall it."""
    lines = [
        f"{m.get('role', '?')}: {m.get('content', '')}"
        for m in (messages or [])
        if m.get("content")
    ]
    if not lines:
        return "Nothing to save — the conversation is empty."
    stamp = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    return save_memory(title or f"Conversation {stamp}", "\n\n".join(lines), tags=["chat"])


_STOP = {
    "the", "a", "an", "of", "to", "in", "on", "and", "or", "for", "is", "are",
    "was", "were", "what", "do", "does", "did", "i", "my", "me", "about", "that",
    "this", "with", "how", "when", "where", "which", "you", "your", "please",
    "can", "could", "would", "tell", "give", "show",
}


def _variant_query(q: str) -> str:
    """A lighter, keyword-only rephrasing — relevant but different enough to
    widen recall — used as a hedge when the first search is slow."""
    words = [w for w in re.findall(r"[A-Za-z0-9]+", q.lower()) if len(w) > 2 and w not in _STOP]
    return " ".join(words[:8])


def _search_raw(query: str, top_k: int) -> list[dict]:
    with httpx.Client(base_url=BASE, timeout=15) as c:
        r = c.post(
            f"/{BUCKET}/vector/search",
            headers=_headers(),
            json={"bucket": BUCKET, "text": query, "top_k": top_k, "include_content": True},
        )
    if r.status_code >= 300:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:160]}")
    return r.json().get("results") or []


def search_memories(query: str, top_k: int = 5) -> str:
    try:
        ensure_ingest()
    except auth.NotConfigured as e:
        return f"Could not search memories: {e}"
    except Exception:
        pass  # provisioning is best-effort; search may still succeed

    results: list[dict] | None = None
    error: str | None = None
    ex = cf.ThreadPoolExecutor(max_workers=2)
    try:
        primary = ex.submit(_search_raw, query, top_k)
        done, _ = cf.wait([primary], timeout=2.0)
        futs = [primary]
        if not done:
            # Primary is slow (>2s): race a second async search with a variant,
            # still-relevant query and use whichever returns first.
            vq = _variant_query(query)
            if vq and vq != query.strip().lower():
                futs.append(ex.submit(_search_raw, vq, top_k))
        try:
            for fut in cf.as_completed(futs, timeout=16):
                try:
                    results = fut.result()
                    break  # first response wins — don't wait for the other
                except Exception as e:
                    error = str(e)  # this one failed; keep waiting for the other
        except cf.TimeoutError:
            pass
    finally:
        ex.shutdown(wait=False, cancel_futures=True)

    if results is None:
        return f"Memory search unavailable: {error[:160]}" if error else "Memory search timed out."
    if not results:
        return "No matching memories yet (a just-saved memory takes ~30s to index)."

    lines = []
    for i, m in enumerate(results[:top_k], 1):
        text = (m.get("content") or m.get("text") or m.get("key") or "").strip()
        score = m.get("score")
        suffix = f"  (score {round(float(score), 3)})" if score is not None else ""
        lines.append(f"{i}. {text[:300]}{suffix}")
    return "\n".join(lines)


def list_memories() -> str:
    try:
        ensure_bucket()
        with httpx.Client(base_url=BASE, timeout=30) as c:
            r = c.get(f"/{BUCKET}?list-type=2", headers=_headers(content_type=""))
    except auth.NotConfigured as e:
        return f"Could not list memories: {e}"
    except Exception as e:
        return f"Could not list memories: {e}"
    if r.status_code >= 300:
        return f"Could not list memories: HTTP {r.status_code}"
    try:
        root = ET.fromstring(r.text)
        ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
        keys = [k.text for k in root.findall(".//s3:Contents/s3:Key", ns)]
        return "\n".join(keys) if keys else "(no memories yet)"
    except Exception:
        return r.text[:400]

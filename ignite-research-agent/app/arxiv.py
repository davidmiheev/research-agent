"""arXiv paper search + loading papers into memory.

Uses the public arXiv Atom API (no key required). `search_text` returns compact
records for the agent to summarise. `load_to_memory` resolves a paper (by id or
query), downloads its PDF and uploads it to K3 — K3's ingestion pipeline does
the PDF processing (extract → chunk → embed), so it becomes semantically
searchable like any other memory.
"""

from __future__ import annotations

import re
import urllib.parse
import xml.etree.ElementTree as ET

import httpx

API = "https://export.arxiv.org/api/query"
ATOM = "{http://www.w3.org/2005/Atom}"

# 2406.12345 / 2406.12345v2  or old style  cs.AI/0301001
_ID_RE = re.compile(r"^(\d{4}\.\d{4,5}(v\d+)?|[a-z\-]+(\.[A-Z]{2})?/\d{7}(v\d+)?)$")


def _parse_entries(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    papers = []
    for e in root.findall(f"{ATOM}entry"):
        authors = [a.findtext(f"{ATOM}name") for a in e.findall(f"{ATOM}author")]
        url = e.findtext(f"{ATOM}id") or ""
        m = re.search(r"arxiv\.org/abs/(.+)$", url)
        papers.append(
            {
                "id": m.group(1) if m else None,
                "title": " ".join((e.findtext(f"{ATOM}title") or "").split()),
                "authors": [a for a in authors if a][:8],
                "summary": " ".join((e.findtext(f"{ATOM}summary") or "").split()),
                "url": url,
                "published": (e.findtext(f"{ATOM}published") or "")[:10],
            }
        )
    return papers


def search(query: str, max_results: int = 5) -> list[dict]:
    params = {
        "search_query": f"all:{query}",
        "start": 0,
        "max_results": max_results,
        "sortBy": "relevance",
        "sortOrder": "descending",
    }
    r = httpx.get(f"{API}?{urllib.parse.urlencode(params)}", timeout=30, follow_redirects=True)
    r.raise_for_status()
    return _parse_entries(r.text)


def get_by_id(arxiv_id: str) -> dict | None:
    r = httpx.get(f"{API}?id_list={urllib.parse.quote(arxiv_id)}", timeout=30, follow_redirects=True)
    r.raise_for_status()
    papers = _parse_entries(r.text)
    return papers[0] if papers else None


def search_text(query: str, max_results: int = 5) -> str:
    """Human-readable rendering for the agent loop."""
    try:
        papers = search(query, max_results)
    except Exception as e:
        return f"arXiv search failed: {e}"
    if not papers:
        return "No arXiv papers found for that query."
    out = []
    for i, p in enumerate(papers, 1):
        authors = ", ".join(p["authors"][:3]) + (" et al." if len(p["authors"]) > 3 else "")
        out.append(
            f"{i}. {p['title']} ({p['published']}) — arXiv:{p['id']}\n"
            f"   {authors}\n"
            f"   {p['summary'][:280]}…\n"
            f"   {p['url']}"
        )
    return "\n".join(out)


def load_to_memory(id_or_query: str) -> str:
    """Resolve a paper (by id or query), download its PDF, upload it to K3.

    K3 handles the PDF processing (extraction + embedding) on ingest.
    """
    from . import k3

    id_or_query = (id_or_query or "").strip()
    if not id_or_query:
        return "Provide an arXiv id or a search query to load a paper."

    try:
        if _ID_RE.match(id_or_query):
            arxiv_id = id_or_query
            paper = get_by_id(arxiv_id) or {"id": arxiv_id, "title": arxiv_id}
        else:
            hits = search(id_or_query, max_results=1)
            if not hits:
                return f"No arXiv paper found for '{id_or_query}'."
            paper = hits[0]
            arxiv_id = paper.get("id")
        if not arxiv_id:
            return f"Could not resolve an arXiv id for '{id_or_query}'."
    except Exception as e:
        return f"arXiv lookup failed: {e}"

    title = paper.get("title") or arxiv_id
    # Already loaded? Don't re-download/re-embed.
    if k3.paper_loaded(arxiv_id):
        return f"arXiv:{arxiv_id} '{title}' is already in your memory — no need to re-upload."

    try:
        r = httpx.get(f"https://arxiv.org/pdf/{arxiv_id}", timeout=120, follow_redirects=True)
        r.raise_for_status()
        pdf = r.content
    except Exception as e:
        return f"Found arXiv:{arxiv_id} but could not download its PDF: {e}"

    key = f"arxiv-{arxiv_id.replace('/', '-')}.pdf"
    status = k3.put_object(key, pdf, "application/pdf")
    k3.record_paper(arxiv_id, title, paper.get("url") or f"https://arxiv.org/abs/{arxiv_id}")
    return f"Uploaded arXiv:{arxiv_id} '{title}' ({len(pdf)} bytes) to memory. {status}"

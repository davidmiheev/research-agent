"""arXiv paper search — a research assistant has to read papers.

Uses the public arXiv Atom API (no key required). Returns compact records the
agent can summarise or save as a memory.
"""

from __future__ import annotations

import urllib.parse
import xml.etree.ElementTree as ET

import httpx

API = "https://export.arxiv.org/api/query"
ATOM = "{http://www.w3.org/2005/Atom}"


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
    root = ET.fromstring(r.text)

    papers = []
    for e in root.findall(f"{ATOM}entry"):
        authors = [a.findtext(f"{ATOM}name") for a in e.findall(f"{ATOM}author")]
        papers.append(
            {
                "title": " ".join((e.findtext(f"{ATOM}title") or "").split()),
                "authors": [a for a in authors if a][:5],
                "summary": " ".join((e.findtext(f"{ATOM}summary") or "").split()),
                "url": e.findtext(f"{ATOM}id"),
                "published": (e.findtext(f"{ATOM}published") or "")[:10],
            }
        )
    return papers


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
            f"{i}. {p['title']} ({p['published']})\n"
            f"   {authors}\n"
            f"   {p['summary'][:280]}…\n"
            f"   {p['url']}"
        )
    return "\n".join(out)

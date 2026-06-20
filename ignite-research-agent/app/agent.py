"""The agent loop: a small, endpoint-agnostic ReAct-style controller.

The model isn't asked to use any provider-specific "tools" API. Instead it is
told to emit a single JSON object to call a tool, and plain text when it is done.
This keeps the agent working against any chat-completions endpoint.

Tools:
  - save_memory(title, content, tags?)  -> store a memory in K3 (auto-embedded)
  - search_memory(query)                -> semantic vector search over memories
  - list_memories()                     -> list stored memory objects
  - search_arxiv(query, max_results?)   -> find relevant arXiv papers
"""

from __future__ import annotations

import json
import re

from . import arxiv, k3, llm

MAX_STEPS = 6

SYSTEM_PROMPT = """\
You are Atlas, the personal research assistant for an AI researcher. You are
helpful, concise and technically fluent. You have a long-term memory backed by
Dodil K3 (with semantic vector search) that persists across conversations, and
you can search arXiv for papers.

You can call tools. To call a tool, reply with ONLY a single JSON object on its
own, and nothing else:

  {"tool": "save_memory", "args": {"title": "...", "content": "...", "tags": ["..."]}}
  {"tool": "search_memory", "args": {"query": "..."}}
  {"tool": "list_memories", "args": {}}
  {"tool": "search_arxiv", "args": {"query": "...", "max_results": 5}}

Rules:
- Save a memory whenever the user shares a durable fact, preference, decision,
  or asks you to remember something. Keep the title short; put detail in content.
- When the user asks what you know/remember, or asks a question that earlier
  context might answer, use search_memory first.
- Use search_arxiv when the user wants papers, prior work, or literature.
- After a tool returns, continue. When you are done, reply with a normal
  natural-language message (no JSON). Never wrap tool JSON in markdown fences.
"""

_TOOLS = {
    "save_memory": lambda a: k3.save_memory(
        a.get("title", "untitled"), a.get("content", ""), a.get("tags")
    ),
    "search_memory": lambda a: k3.search_memories(a.get("query", ""), int(a.get("top_k", 5))),
    "list_memories": lambda a: k3.list_memories(),
    "search_arxiv": lambda a: arxiv.search_text(a.get("query", ""), int(a.get("max_results", 5))),
}


def _extract_tool_call(text: str) -> dict | None:
    candidate = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", candidate, re.DOTALL)
    if fence:
        candidate = fence.group(1)
    if not candidate.startswith("{"):
        brace = re.search(r"\{.*\}", candidate, re.DOTALL)
        if not brace:
            return None
        candidate = brace.group(0)
    try:
        obj = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if isinstance(obj, dict) and obj.get("tool") in _TOOLS:
        obj.setdefault("args", {})
        return obj
    return None


def run_agent(user_message: str, history: list[dict] | None = None) -> dict:
    """Run one turn. Returns {'reply': str, 'tools_used': [...]}."""
    history = history or []
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    tools_used: list[dict] = []

    for _ in range(MAX_STEPS):
        try:
            reply = llm.chat(messages)
        except llm.LLMNotConfigured as e:
            return {"reply": str(e), "tools_used": tools_used}
        except Exception as e:
            return {"reply": f"Model call failed: {e}", "tools_used": tools_used}

        call = _extract_tool_call(reply)
        if not call:
            return {"reply": reply.strip(), "tools_used": tools_used}

        result = _TOOLS[call["tool"]](call["args"])
        tools_used.append({"tool": call["tool"], "args": call["args"], "result": result})

        messages.append({"role": "assistant", "content": reply})
        messages.append(
            {"role": "user", "content": f"[tool:{call['tool']}] result:\n{result}"}
        )

    summary = "; ".join(t["result"] for t in tools_used) or "I wasn't able to finish that."
    return {"reply": summary, "tools_used": tools_used}

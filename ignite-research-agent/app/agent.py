"""The agent loop: a small, endpoint-agnostic ReAct-style controller.

The model isn't asked to use any provider-specific "tools" API. Instead it is
told to emit a single JSON object to call a tool, and plain text when it is done.
This keeps the agent working against any chat-completions endpoint.

Tools:
  - save_memory(title, content, tags?)  -> store a memory in K3 (auto-embedded)
  - search_memory(query)                -> semantic vector search over memories
  - list_memories()                     -> list stored memory objects
  - search_arxiv(query, max_results?)   -> find relevant arXiv papers
  - load_arxiv_paper(id|query)          -> upload a paper's PDF to memory (idempotent)
  - list_papers()                       -> list arXiv papers already loaded into memory
  - save_chat(title?)                   -> persist this conversation to memory
"""

from __future__ import annotations

import json
import re

from . import arxiv, k3, llm

MAX_STEPS = 6

SYSTEM_PROMPT = """\
You are Atlas, the personal research assistant for an AI researcher. You are
helpful, concise and technically fluent. You have a long-term memory backed by
Dodil K3 (with semantic vector search) that persists across conversations, you
can search and ingest arXiv papers, and you keep the context of the current
conversation in mind.

You can call tools. To call a tool, reply with ONLY a single JSON object on its
own, and nothing else:

  {"tool": "save_memory", "args": {"title": "...", "content": "...", "tags": ["..."]}}
  {"tool": "search_memory", "args": {"query": "..."}}
  {"tool": "list_memories", "args": {}}
  {"tool": "search_arxiv", "args": {"query": "...", "max_results": 5}}
  {"tool": "load_arxiv_paper", "args": {"query": "..."}}      // or {"id": "2406.12345"}
  {"tool": "list_papers", "args": {}}
  {"tool": "save_chat", "args": {"title": "..."}}             // title optional

Rules:
- Save a memory whenever the user shares a durable fact, preference, decision,
  or asks you to remember something. Keep the title short; put detail in content.
- When the user asks what you know/remember, or asks a question earlier context
  might answer, use search_memory first. Don't over-search: after one or two
  searches, ANSWER from what you found (plus the conversation). Re-running
  reworded queries on noisy results rarely helps and wastes the user's time.
- Use search_arxiv to find papers. Use load_arxiv_paper when the user wants a
  paper *ingested* into memory. NOTE: a freshly loaded paper is NOT searchable
  for ~30s while K3 indexes it — do NOT immediately search for its contents;
  answer from the abstract (search_arxiv) or tell the user it's being added.
- You remember which papers you've loaded: use list_papers to see them, and
  prefer it before loading (load_arxiv_paper is idempotent and won't duplicate,
  but checking avoids needless work). If asked "which papers do you have",
  use list_papers.
- Use save_chat when the user asks to save/remember this conversation.
- After a tool returns, continue. When you are done, reply with a normal
  natural-language message (no JSON). Never wrap tool JSON in markdown fences.

Formatting your answer to the user:
- Write in Markdown (headings, lists, **bold**, `code`, tables when useful).
- Write ALL mathematics as LaTeX so it renders with KaTeX: inline as $ ... $ and
  display equations as $$ ... $$. For example: the attention scores are
  $\\mathrm{softmax}\\!\\left(\\tfrac{QK^\\top}{\\sqrt{d_k}}\\right)V$. Do not write
  math as plain text or inside code fences.
- Your FINAL message to the user MUST be natural language — never a JSON object,
  a tool call, or a raw search/query payload.
"""


def _build_tools(conversation: list[dict]):
    """Tool handlers. Built per turn so save_chat can close over the conversation."""
    return {
        "save_memory": lambda a: k3.save_memory(
            a.get("title", "untitled"), a.get("content", ""), a.get("tags")
        ),
        "search_memory": lambda a: k3.search_memories(a.get("query", ""), int(a.get("top_k", 5))),
        "list_memories": lambda a: k3.list_memories(),
        "search_arxiv": lambda a: arxiv.search_text(a.get("query", ""), int(a.get("max_results", 5))),
        "load_arxiv_paper": lambda a: arxiv.load_to_memory(
            a.get("id") or a.get("arxiv_id") or a.get("query", "")
        ),
        "list_papers": lambda a: k3.list_papers(),
        "save_chat": lambda a: k3.save_chat(conversation, a.get("title")),
    }


def _extract_tool_call(text: str, tool_names) -> dict | None:
    stripped = text.strip()
    candidate = stripped
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", candidate, re.DOTALL)
    if fence:
        candidate = fence.group(1).strip()
    elif not candidate.startswith("{"):
        brace = re.search(r"\{.*\}", candidate, re.DOTALL)
        if not brace:
            return None
        candidate = brace.group(0)
    try:
        obj = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    if obj.get("tool") in tool_names:
        obj.setdefault("args", {})
        return obj
    # Lenient: the model sometimes emits a bare tool *payload* (no "tool"
    # wrapper) as its whole reply — e.g. {"query": "..."} for a search. Treat
    # that as the intended call instead of leaking raw JSON to the user.
    if stripped.startswith("{") and stripped.endswith("}") and "tool" not in obj:
        args = obj.get("args") if isinstance(obj.get("args"), dict) else obj
        if "query" in args:
            return {"tool": "search_memory", "args": args}
        if "title" in args and "content" in args:
            return {"tool": "save_memory", "args": args}
    return None


def _looks_like_payload(text: str) -> bool:
    """True if the model's 'final' reply is actually a raw tool/search payload."""
    s = text.strip()
    if not s.startswith("{"):
        return False
    try:
        obj = json.loads(s)
        return isinstance(obj, dict) and any(
            k in obj for k in ("tool", "args", "query", "top_k", "include_content")
        )
    except Exception:
        # Malformed JSON, but clearly a leaked tool/search payload blob.
        return bool(re.match(r'\{\s*"(tool|query|args|top_k)"\s*:', s))


def _final_synthesis(messages: list[dict]) -> str:
    """Out of tool budget: ask for one final natural-language answer (no tools)."""
    msgs = messages + [{
        "role": "user",
        "content": "Stop using tools now. Using everything above, answer my original "
        "question in clear natural language (Markdown; math in $…$ / $$…$$). If the "
        "information is incomplete, summarize what you found and what is still missing "
        "— do NOT call any tool or output JSON.",
    }]
    try:
        out = llm.chat(msgs).strip()
    except Exception:
        out = ""
    if not out or _looks_like_payload(out):
        return (
            "I gathered several relevant excerpts but couldn't compose a clean answer "
            "in time — try narrowing the question (e.g. a specific benchmark or table)."
        )
    return out


def run_agent(
    user_message: str,
    history: list[dict] | None = None,
    full_history: list[dict] | None = None,
) -> dict:
    """Run one turn. Returns {'reply': str, 'tools_used': [...]}.

    `history` is the recent window passed to the model (session continuity);
    `full_history` (if given) is the entire conversation, used by save_chat.
    """
    history = history or []
    conversation = list(full_history if full_history is not None else history)
    conversation.append({"role": "user", "content": user_message})

    tools = _build_tools(conversation)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    tools_used: list[dict] = []
    searches = 0
    nudged = False

    for _ in range(MAX_STEPS):
        try:
            reply = llm.chat(messages)
        except llm.LLMNotConfigured as e:
            return {"reply": str(e), "tools_used": tools_used}
        except Exception as e:
            return {"reply": f"Model call failed: {e}", "tools_used": tools_used}

        call = _extract_tool_call(reply, tools)
        if not call:
            final = reply.strip()
            if _looks_like_payload(final):
                # Model dumped a raw payload instead of answering — ask once more
                # for a plain reply rather than showing JSON in the chat.
                messages.append({"role": "assistant", "content": reply})
                messages.append({
                    "role": "user",
                    "content": "That looked like a raw tool payload. Reply in plain "
                    "natural language (Markdown, math in $…$ / $$…$$), no JSON.",
                })
                continue
            return {"reply": final, "tools_used": tools_used}

        result = tools[call["tool"]](call["args"])
        tools_used.append({"tool": call["tool"], "args": call["args"], "result": result})

        messages.append({"role": "assistant", "content": reply})
        messages.append(
            {"role": "user", "content": f"[tool:{call['tool']}] result:\n{result}"}
        )

        if call["tool"] == "search_memory":
            searches += 1
        if searches >= 3 and not nudged:
            # Stop the search-loop the model can fall into on noisy results.
            nudged = True
            messages.append({
                "role": "user",
                "content": "You've searched several times. Do NOT search again — answer "
                "my original question now in natural language from what you found "
                "(Markdown; math in $…$ / $$…$$). If something isn't available yet, say so.",
            })

    # Tool budget exhausted — synthesise one natural-language answer instead of
    # dumping raw tool output to the chat.
    return {"reply": _final_synthesis(messages), "tools_used": tools_used}

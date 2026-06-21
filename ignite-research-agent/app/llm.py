"""Chat client for Ignite's OpenAI-compatible model endpoint.

Ignite serves `POST /v1/chat/completions` in the OpenAI shape, wrapped in a
`{"data": <openai response>, "status": "success"}` envelope. Auth is the same
service-account bearer token used everywhere else (or a static MODEL_API_KEY if
you'd rather point this at a plain OpenAI-compatible gateway).

Defaults target the dev cluster + a current chat model; override via env:
    MODEL_API_BASE   default https://api.dev.dodil.io/v1
    MODEL_NAME       default kimi-k2.6
    MODEL_API_KEY    optional static bearer (else a token is minted from the SA)
"""

from __future__ import annotations

import os
import httpx

from . import auth

DEFAULT_BASE = "https://api.dev.dodil.io/v1"
DEFAULT_MODEL = "kimi-k2.6"


class LLMNotConfigured(RuntimeError):
    pass


def _base() -> str:
    return os.getenv("MODEL_API_BASE", DEFAULT_BASE).rstrip("/")


def _url() -> str:
    base = _base()
    return base if base.endswith("/chat/completions") else f"{base}/chat/completions"


def _bearer() -> str:
    key = os.getenv("MODEL_API_KEY")
    if key:
        return key
    return auth.get_token()  # raises auth.NotConfigured if SA creds are missing


def is_configured() -> bool:
    return auth.is_configured()


def chat(messages: list[dict]) -> str:
    """Send a chat completion and return the assistant's text."""
    try:
        bearer = _bearer()
    except auth.NotConfigured as e:
        raise LLMNotConfigured(str(e))

    payload = {
        "model": os.getenv("MODEL_NAME", DEFAULT_MODEL),
        "messages": messages,
        "max_tokens": int(os.getenv("MODEL_MAX_TOKENS", "15000")),
    }
    # Some models (e.g. kimi) only accept their fixed default temperature, so we
    # omit it unless explicitly configured.
    if os.getenv("MODEL_TEMPERATURE"):
        payload["temperature"] = float(os.getenv("MODEL_TEMPERATURE"))
    headers = {"Authorization": f"Bearer {bearer}", "Content-Type": "application/json"}

    with httpx.Client(timeout=float(os.getenv("MODEL_TIMEOUT_SECS", "90"))) as client:
        resp = client.post(_url(), json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    # Unwrap Ignite's {"data": ..., "status": ...} envelope if present.
    if isinstance(data, dict) and "choices" not in data and "data" in data:
        if data.get("status") == "error":
            err = data["data"].get("error") if isinstance(data["data"], dict) else data["data"]
            raise RuntimeError(f"model error: {err}")
        data = data["data"]

    try:
        msg = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return data.get("content") or str(data)
    # Reasoning models may put the answer in content and chain-of-thought in
    # reasoningContent; prefer the visible content.
    return (msg.get("content") or msg.get("reasoningContent") or "").strip()

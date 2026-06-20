"""Service-account → bearer token, shared by the model and K3 clients.

The agent authenticates everything (Ignite model endpoint + K3) with a single
Dodil service account. We mint a short-lived access token via OIDC
`client_credentials` and cache it until just before it expires, decoding the
org id/name straight out of the JWT so callers never have to pass them.
"""

from __future__ import annotations

import base64
import json
import os
import threading
import time

import httpx

OIDC_URL = os.getenv(
    "DODIL_OIDC_URL",
    "https://id.dev.dodil.io/realms/dodil/protocol/openid-connect/token",
)

_lock = threading.Lock()
_state = {"token": None, "exp": 0.0, "org_id": None, "org_name": None}


def _decode_claims(token: str) -> dict:
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part))
    except Exception:
        return {}


def _org_from_claims(claims: dict) -> tuple[str | None, str | None]:
    orgs = claims.get("organization") or {}
    name = next(iter(orgs), None)
    if name:
        return orgs.get(name, {}).get("id"), name
    return claims.get("org_id"), claims.get("org_name")


class NotConfigured(RuntimeError):
    pass


def get_token() -> str:
    with _lock:
        now = time.time()
        if _state["token"] and now < _state["exp"] - 30:
            return _state["token"]

        sa_id = os.getenv("DODIL_SA_ID", "")
        sa_secret = os.getenv("DODIL_SA_SECRET", "")
        if not sa_id or not sa_secret:
            raise NotConfigured(
                "DODIL_SA_ID / DODIL_SA_SECRET are not set — the agent needs a "
                "service account to call the model and K3."
            )

        resp = httpx.post(
            OIDC_URL,
            data={
                "client_id": sa_id,
                "client_secret": sa_secret,
                "grant_type": "client_credentials",
            },
            timeout=20,
        )
        resp.raise_for_status()
        token = resp.json()["access_token"]
        claims = _decode_claims(token)
        _state["token"] = token
        _state["exp"] = float(claims.get("exp", now + 300))
        _state["org_id"], _state["org_name"] = _org_from_claims(claims)
        return token


def org_id() -> str:
    get_token()
    return _state["org_id"] or ""


def org_name() -> str:
    get_token()
    return _state["org_name"] or ""


def is_configured() -> bool:
    return bool(os.getenv("DODIL_SA_ID") and os.getenv("DODIL_SA_SECRET")) or bool(
        os.getenv("MODEL_API_KEY")
    )

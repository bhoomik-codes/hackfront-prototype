import hashlib
import hmac
import json
import secrets
import time
from typing import Any
from urllib.parse import quote, unquote

SESSION_COOKIE = "why_workspace"
SESSION_MAX_AGE = 60 * 60 * 24 * 14
OAUTH_STATE_COOKIE = "why_oauth_state"
GOOGLE_OAUTH_STATE_COOKIE = "why_google_oauth_state"


def _signature(workspace_id: str, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"), workspace_id.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def create_session(
    secret: str,
    user_id: str | None = None,
    login: str | None = None,
    workspace_id: str | None = None,
    auth_provider: str | None = None,
) -> tuple[str, str]:
    workspace_id = workspace_id or secrets.token_urlsafe(24)
    claims = {
        "workspace_id": workspace_id,
        "user_id": user_id,
        "login": login,
        "auth_provider": auth_provider,
        "expires_at": int(time.time()) + SESSION_MAX_AGE,
    }
    payload = quote(json.dumps(claims, separators=(",", ":")), safe="~")
    token = f"{payload}.{_signature(payload, secret)}"
    return workspace_id, token


def create_oauth_state(secret: str) -> tuple[str, str]:
    state = f"{int(time.time())}.{secrets.token_urlsafe(32)}"
    return state, f"{state}.{_signature(state, secret)}"


def verify_oauth_state(token: str | None, state: str | None, secret: str) -> bool:
    if (
        not token
        or not state
        or len(token) > 256
        or len(state) > 256
        or "." not in token
    ):
        return False
    supplied_state, supplied_signature = token.rsplit(".", 1)
    expected_signature = _signature(supplied_state, secret)
    try:
        created_at = int(supplied_state.split(".", 1)[0])
    except (ValueError, IndexError):
        return False
    return (
        0 <= time.time() - created_at <= 600
        and hmac.compare_digest(supplied_state, state)
        and hmac.compare_digest(supplied_signature, expected_signature)
    )


def session_claims(token: str | None, secret: str) -> dict[str, Any] | None:
    if not token or len(token) > 2048 or "." not in token:
        return None
    payload, supplied_signature = token.rsplit(".", 1)
    expected_signature = _signature(payload, secret)
    if not hmac.compare_digest(supplied_signature, expected_signature):
        return None
    try:
        claims = json.loads(unquote(payload))
    except (ValueError, TypeError):
        return None
    if (
        not isinstance(claims, dict)
        or not isinstance(claims.get("workspace_id"), str)
        or not isinstance(claims.get("expires_at"), int)
        or claims["expires_at"] <= int(time.time())
    ):
        return None
    return claims


def verify_session(token: str | None, secret: str) -> str | None:
    claims = session_claims(token, secret)
    return str(claims["workspace_id"]) if claims else None

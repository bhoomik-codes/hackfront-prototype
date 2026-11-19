import hashlib
import hmac
import secrets

SESSION_COOKIE = "why_workspace"
SESSION_MAX_AGE = 60 * 60 * 24 * 14


def _signature(workspace_id: str, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"), workspace_id.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def create_session(secret: str) -> tuple[str, str]:
    workspace_id = secrets.token_urlsafe(24)
    return workspace_id, f"{workspace_id}.{_signature(workspace_id, secret)}"


def verify_session(token: str | None, secret: str) -> str | None:
    if not token or len(token) > 256 or "." not in token:
        return None
    workspace_id, supplied_signature = token.rsplit(".", 1)
    if not workspace_id or not supplied_signature:
        return None
    expected_signature = _signature(workspace_id, secret)
    if not hmac.compare_digest(supplied_signature, expected_signature):
        return None
    return workspace_id

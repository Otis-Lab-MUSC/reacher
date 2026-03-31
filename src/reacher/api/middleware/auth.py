"""API key authentication for REACHER.

If REACHER_API_KEY is set, uses that value.  Otherwise generates a random
32-character hex key, writes it to ~/.reacher/api_key, and logs once.

All /api/* routes require ``Authorization: Bearer <key>``.
WebSocket connections use ``?token=<key>`` query parameter.
The /health endpoint is exempt.
"""

import logging
import os
import secrets

from fastapi import Depends, HTTPException, Request, WebSocket
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)

_KEY_DIR = os.path.expanduser("~/.reacher")
_KEY_FILE = os.path.join(_KEY_DIR, "api_key")

_bearer = HTTPBearer(auto_error=False)


def _resolve_api_key() -> str:
    """Return the API key, generating one if necessary."""
    env_key = os.getenv("REACHER_API_KEY")
    if env_key:
        return env_key

    if os.path.isfile(_KEY_FILE):
        with open(_KEY_FILE) as f:
            stored = f.read().strip()
        if stored:
            return stored

    # Generate a new key
    key = secrets.token_hex(16)
    os.makedirs(_KEY_DIR, exist_ok=True)
    with open(_KEY_FILE, "w") as f:
        f.write(key)
    os.chmod(_KEY_FILE, 0o600)
    logger.info("Generated API key and wrote to %s", _KEY_FILE)
    return key


# Resolved once at import time
API_KEY: str = _resolve_api_key()


async def require_api_key(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    """FastAPI dependency that enforces Bearer token auth on /api/* routes."""
    if credentials is not None and credentials.credentials == API_KEY:
        # Record activity so the pairing module knows the controller is alive.
        from ... import pairing
        pairing.touch()
        return
    raise HTTPException(status_code=401, detail="Invalid or missing API key")


def verify_ws_token(websocket: WebSocket) -> bool:
    """Check the ?token= query param on WebSocket upgrade requests.

    Fix: PY-005 — The token is passed via URL query parameter because the
    WebSocket API does not support custom headers on the upgrade request.
    This is acceptable for localhost-only usage; the token may appear in
    server access logs but REACHER is not exposed to the network.
    """
    token = websocket.query_params.get("token")
    return token == API_KEY

"""API key authentication for protected endpoints."""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Callable

from fastapi import HTTPException, Request

from .config import settings
from .log import get_logger
from .models import Token

log = get_logger()

_api_key: str = ""


def generate_api_key() -> str:
    """Generate a new 64-character hex API key."""
    return secrets.token_hex(32)


def write_secure_file(path: Path, content: str, mode: int = 0o600) -> None:
    """Write *content* to *path* with restricted permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(mode)


def load_or_create_key() -> str:
    """Load API key from env, file, or generate a new one.

    Priority:
    1. CL_API_KEY environment variable
    2. Key file on disk
    3. Generate new key and write to disk
    """
    global _api_key

    # 1. Environment variable takes precedence
    env_key = os.environ.get("CL_API_KEY", "").strip()
    if env_key:
        _api_key = env_key
        log.info("auth.key_loaded", metadata={"source": "environment"})
        return _api_key

    # 2. Try reading from file
    key_file = settings.api_key_file
    if key_file.exists():
        _api_key = key_file.read_text().strip()
        if _api_key:
            log.info("auth.key_loaded", metadata={"source": "file", "path": str(key_file)})
            return _api_key

    # 3. Generate new key
    _api_key = generate_api_key()
    write_secure_file(key_file, _api_key)
    log.info("auth.key_created", metadata={"path": str(key_file)})
    return _api_key


def get_api_key() -> str:
    """Return the current API key (must call load_or_create_key first)."""
    return _api_key


def require_api_key(request: Request) -> None:
    """FastAPI dependency that validates the X-API-Key header.

    Raises 401 if the key is missing or invalid.
    """
    provided = request.headers.get("x-api-key", "")
    if not provided or not _api_key:
        raise HTTPException(status_code=401, detail="Missing or invalid API key")
    if not secrets.compare_digest(provided, _api_key):
        raise HTTPException(status_code=401, detail="Missing or invalid API key")


def get_bearer_token(request: Request) -> Token | None:
    """Extract and validate a Bearer token from the Authorization header."""
    from .registry import validate_token

    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token_id = auth[7:].strip()
    return validate_token(token_id)


def _is_api_key_valid(request: Request) -> bool:
    """Return True if a valid X-API-Key header is present."""
    provided = request.headers.get("x-api-key", "")
    return bool(provided and _api_key and secrets.compare_digest(provided, _api_key))


def require_capability(capability: str) -> Callable:
    """Dependency factory: accepts API key (full trust) OR session token with the named capability.

    Returns the session Token if authenticated via bearer token, or None if authenticated
    via API key. Raises 401/403 otherwise.
    """
    def _dep(request: Request) -> Token | None:
        # API key → full trust, no capability check needed
        if _is_api_key_valid(request):
            return None
        # Bearer token path
        token = get_bearer_token(request)
        if not token:
            raise HTTPException(
                status_code=401,
                detail="Missing or invalid API key or Bearer token",
            )
        if capability not in token.capabilities:
            raise HTTPException(
                status_code=403,
                detail=f"Token lacks required capability: {capability!r}",
            )
        return token
    return _dep

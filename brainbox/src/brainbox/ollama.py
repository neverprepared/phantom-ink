"""Ollama API client for chat completions, model management, and health checks.

Each function takes optional ``base_url``, ``headers``, and ``verify``
parameters so callers can talk to either the local Ollama (no auth, http)
or a runner's HTTPS proxy (X-API-Key auth, self-signed cert).
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from .config import settings
from .log import get_logger

_log = get_logger()

# ---------------------------------------------------------------------------
# Cached HTTPx clients — one per (base_url, verify) pair for connection pooling
# ---------------------------------------------------------------------------

_httpx_clients: dict[tuple[str, bool], httpx.Client] = {}


class OllamaError(RuntimeError):
    def __init__(self, operation: str, reason: str):
        self.operation = operation
        self.reason = reason
        super().__init__(f"ollama {operation} failed: {reason}")


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


@dataclass(frozen=True)
class ChatResult:
    model: str
    message: ChatMessage
    total_duration: int = 0
    eval_count: int = 0


@dataclass(frozen=True)
class ModelInfo:
    name: str
    size: int
    modified_at: str
    digest: str


def _client(base_url: str | None = None, *, verify: bool = True) -> httpx.Client:
    """Cached httpx client per (base_url, verify) pair."""
    url = base_url or settings.ollama.host
    key = (url, verify)
    if key not in _httpx_clients:
        _httpx_clients[key] = httpx.Client(base_url=url, timeout=300.0, verify=verify)
    return _httpx_clients[key]


def health_check(
    base_url: str | None = None,
    *,
    headers: dict[str, str] | None = None,
    verify: bool = True,
) -> bool:
    """Check if Ollama is reachable."""
    try:
        resp = _client(base_url, verify=verify).get("/", headers=headers)
        return resp.status_code == 200
    except Exception as exc:
        _log.debug("ollama.health_check_failed", metadata={"reason": str(exc)})
        return False


def chat(
    messages: list[dict],
    model: str | None = None,
    base_url: str | None = None,
    *,
    headers: dict[str, str] | None = None,
    verify: bool = True,
) -> ChatResult:
    """Send a chat completion request to Ollama."""
    resolved_model = model or settings.ollama.model
    url = base_url or settings.ollama.host
    payload = {
        "model": resolved_model,
        "messages": messages,
        "stream": False,
    }
    try:
        resp = _client(base_url, verify=verify).post("/api/chat", json=payload, headers=headers)
        resp.raise_for_status()
        body = resp.json()
    except httpx.ConnectError as exc:
        raise OllamaError("chat", f"could not connect to Ollama at {url}: {exc}")
    except httpx.HTTPStatusError as exc:
        raise OllamaError("chat", f"HTTP {exc.response.status_code}: {exc.response.text}")
    except httpx.HTTPError as exc:
        raise OllamaError("chat", str(exc))

    try:
        msg = body["message"]
        return ChatResult(
            model=body.get("model", resolved_model),
            message=ChatMessage(role=msg["role"], content=msg["content"]),
            total_duration=body.get("total_duration", 0),
            eval_count=body.get("eval_count", 0),
        )
    except (KeyError, TypeError) as exc:
        raise OllamaError("chat", f"unexpected response structure: {exc}")


def list_models(
    base_url: str | None = None,
    *,
    headers: dict[str, str] | None = None,
    verify: bool = True,
) -> list[ModelInfo]:
    """List models available on the Ollama server."""
    url = base_url or settings.ollama.host
    try:
        resp = _client(base_url, verify=verify).get("/api/tags", headers=headers)
        resp.raise_for_status()
        data = resp.json()
    except httpx.ConnectError as exc:
        raise OllamaError("list_models", f"could not connect to Ollama at {url}: {exc}")
    except httpx.HTTPError as exc:
        raise OllamaError("list_models", str(exc))

    return [
        ModelInfo(
            name=m.get("name", ""),
            size=m.get("size", 0),
            modified_at=m.get("modified_at", ""),
            digest=m.get("digest", ""),
        )
        for m in data.get("models", [])
    ]


def pull_model(
    name: str,
    base_url: str | None = None,
    *,
    headers: dict[str, str] | None = None,
    verify: bool = True,
) -> str:
    """Pull a model from the Ollama registry."""
    url = base_url or settings.ollama.host
    try:
        resp = _client(base_url, verify=verify).post(
            "/api/pull", json={"name": name, "stream": False}, headers=headers
        )
        resp.raise_for_status()
        return resp.json().get("status", "success")
    except httpx.ConnectError as exc:
        raise OllamaError("pull_model", f"could not connect to Ollama at {url}: {exc}")
    except httpx.HTTPError as exc:
        raise OllamaError("pull_model", str(exc))


def delete_model(
    name: str,
    base_url: str | None = None,
    *,
    headers: dict[str, str] | None = None,
    verify: bool = True,
) -> str:
    """Delete a model from the Ollama server."""
    url = base_url or settings.ollama.host
    try:
        resp = _client(base_url, verify=verify).request(
            "DELETE", "/api/delete", json={"name": name}, headers=headers
        )
        resp.raise_for_status()
        return "deleted"
    except httpx.ConnectError as exc:
        raise OllamaError("delete_model", f"could not connect to Ollama at {url}: {exc}")
    except httpx.HTTPError as exc:
        raise OllamaError("delete_model", str(exc))

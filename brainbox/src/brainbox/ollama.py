"""Ollama API client for chat completions, model management, and health checks."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from .config import settings
from .log import get_logger

_log = get_logger()

# ---------------------------------------------------------------------------
# Cached HTTPx client for connection pooling
# ---------------------------------------------------------------------------

_httpx_client: httpx.Client | None = None


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


def _client() -> httpx.Client:
    """Get cached httpx client with connection pooling."""
    global _httpx_client
    if _httpx_client is None:
        _httpx_client = httpx.Client(
            base_url=settings.ollama.host,
            timeout=300.0,
        )
    return _httpx_client


def health_check() -> bool:
    """Check if Ollama is reachable."""
    try:
        c = _client()
        resp = c.get("/")
        return resp.status_code == 200
    except Exception as exc:
        _log.debug("ollama.health_check_failed", metadata={"reason": str(exc)})
        return False


def chat(messages: list[dict], model: str | None = None) -> ChatResult:
    """Send a chat completion request to Ollama.

    Args:
        messages: List of message dicts with 'role' and 'content' keys.
        model: Model name override (default: settings.ollama.model).

    Returns:
        ChatResult with model name, response message, and timing info.
    """
    resolved_model = model or settings.ollama.model
    payload = {
        "model": resolved_model,
        "messages": messages,
        "stream": False,
    }
    try:
        c = _client()
        resp = c.post("/api/chat", json=payload)
        resp.raise_for_status()
        body = resp.json()
    except httpx.ConnectError as exc:
        raise OllamaError("chat", f"could not connect to Ollama at {settings.ollama.host}: {exc}")
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


def list_models() -> list[ModelInfo]:
    """List models available on the Ollama server."""
    try:
        c = _client()
        resp = c.get("/api/tags")
        resp.raise_for_status()
        data = resp.json()
    except httpx.ConnectError as exc:
        raise OllamaError(
            "list_models", f"could not connect to Ollama at {settings.ollama.host}: {exc}"
        )
    except httpx.HTTPError as exc:
        raise OllamaError("list_models", str(exc))

    results = []
    for m in data.get("models", []):
        results.append(
            ModelInfo(
                name=m.get("name", ""),
                size=m.get("size", 0),
                modified_at=m.get("modified_at", ""),
                digest=m.get("digest", ""),
            )
        )
    return results


def pull_model(name: str) -> str:
    """Pull a model from the Ollama registry.

    Args:
        name: Model name to pull (e.g. 'llama3.2', 'qwen3:8b').

    Returns:
        Final status string from Ollama.
    """
    try:
        c = _client()
        resp = c.post("/api/pull", json={"name": name, "stream": False})
        resp.raise_for_status()
        body = resp.json()
        return body.get("status", "success")
    except httpx.ConnectError as exc:
        raise OllamaError(
            "pull_model", f"could not connect to Ollama at {settings.ollama.host}: {exc}"
        )
    except httpx.HTTPError as exc:
        raise OllamaError("pull_model", str(exc))

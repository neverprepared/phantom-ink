"""Ollama API client.

Implementation note (do not "fix" this back to httpx without reading):
====================================================================
On macOS + Python 3.14 + httpx, outbound connections from the running
brainbox daemon to LAN destinations (e.g. a runner's Ollama HTTPS proxy
at 192.168.x.y) fail with ``OSError(65, 'No route to host')`` even
though:

  - curl from the same daemon process succeeds
  - a plain stdlib ``socket.connect()`` from the same daemon succeeds
  - httpx from a standalone python script on the same host succeeds

The failure reproduces with both ``httpx.AsyncClient`` and
``httpx.Client`` (in a thread). The httpcore / anyio socket creation
path inside the long-running daemon process picks up some state — likely
related to dual-stack/happy-eyeballs interaction with macOS's routing
table — that the bare-socket and curl paths avoid.

Workaround: shell out to ``curl`` for every Ollama call. Per-call
overhead is one process spawn (~5ms); inconsequential next to the
seconds-long completion times of Ollama operations.

Re-evaluate when (a) Python ships a fix in 3.14.x, or (b) we move the
daemon off Python 3.14. See app/CLAUDE.md "Known issues" for the
broader gotcha listing.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from dataclasses import dataclass

from .config import settings
from .log import get_logger

_log = get_logger()


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


# ---------------------------------------------------------------------------
# Curl-based transport (see module docstring)
# ---------------------------------------------------------------------------


def _build_curl_args(
    method: str,
    base_url: str | None,
    path: str,
    *,
    body: dict | None,
    headers: dict[str, str] | None,
    verify: bool,
    timeout: float,
) -> list[str]:
    url = (base_url or settings.ollama.host).rstrip("/") + path
    args: list[str] = [
        "curl", "-s",
        "-X", method,
        "-m", str(int(timeout)),
        "-w", "\n%{http_code}",
        url,
    ]
    if not verify:
        args.append("-k")
    for k, v in (headers or {}).items():
        args += ["-H", f"{k}: {v}"]
    if body is not None:
        args += ["-H", "Content-Type: application/json",
                 "--data-binary", json.dumps(body)]
    return args


def _parse_curl_output(text: str) -> tuple[int, str]:
    body_text, _, code = text.rpartition("\n")
    try:
        status = int(code.strip() or "0")
    except ValueError:
        status = 0
    return status, body_text


async def acurl_request(
    method: str,
    base_url: str | None,
    path: str,
    *,
    body: dict | None = None,
    headers: dict[str, str] | None = None,
    verify: bool = True,
    timeout: float = 300.0,
) -> tuple[int, str]:
    """Execute an HTTP request via curl, fully async (no threadpool).

    Uses ``asyncio.create_subprocess_exec`` so the event loop polls the
    curl pipes directly. Many concurrent callers do not consume threadpool
    workers and do not serialize behind each other.
    """
    args = _build_curl_args(
        method, base_url, path,
        body=body, headers=headers, verify=verify, timeout=timeout,
    )
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout + 5
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise OSError(f"curl timed out after {timeout + 5}s")
    if proc.returncode != 0:
        raise OSError(
            f"curl failed (rc={proc.returncode}): "
            f"{stderr.decode(errors='replace').strip()[:200]}"
        )
    return _parse_curl_output(stdout.decode("utf-8", errors="replace"))


def _curl_request(
    method: str,
    base_url: str | None,
    path: str,
    *,
    body: dict | None = None,
    headers: dict[str, str] | None = None,
    verify: bool = True,
    timeout: float = 300.0,
) -> tuple[int, str]:
    """Sync variant — kept for non-async callers (scripts, MCP server).

    Async callers should prefer :func:`acurl_request` to avoid occupying
    a threadpool worker for the duration of the curl call.
    """
    args = _build_curl_args(
        method, base_url, path,
        body=body, headers=headers, verify=verify, timeout=timeout,
    )
    proc = subprocess.run(args, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise OSError(f"curl failed (rc={proc.returncode}): {proc.stderr.strip()[:200]}")
    return _parse_curl_output(proc.stdout)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def health_check(
    base_url: str | None = None,
    *,
    headers: dict[str, str] | None = None,
    verify: bool = True,
) -> bool:
    """Check if Ollama is reachable."""
    try:
        status, _ = _curl_request("GET", base_url, "/", headers=headers, verify=verify, timeout=5)
        return status == 200
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
    url_display = base_url or settings.ollama.host
    payload = {
        "model": resolved_model,
        "messages": messages,
        "stream": False,
    }
    try:
        status, text = _curl_request(
            "POST", base_url, "/api/chat",
            body=payload, headers=headers, verify=verify, timeout=300,
        )
    except OSError as exc:
        raise OllamaError("chat", f"could not connect to Ollama at {url_display}: {exc}")
    if status != 200:
        raise OllamaError("chat", f"HTTP {status}: {text[:200]}")
    try:
        body = json.loads(text)
        msg = body["message"]
        return ChatResult(
            model=body.get("model", resolved_model),
            message=ChatMessage(role=msg["role"], content=msg["content"]),
            total_duration=body.get("total_duration", 0),
            eval_count=body.get("eval_count", 0),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise OllamaError("chat", f"unexpected response structure: {exc}")


def list_models(
    base_url: str | None = None,
    *,
    headers: dict[str, str] | None = None,
    verify: bool = True,
) -> list[ModelInfo]:
    """List models available on the Ollama server."""
    url_display = base_url or settings.ollama.host
    try:
        status, text = _curl_request(
            "GET", base_url, "/api/tags",
            headers=headers, verify=verify, timeout=10,
        )
    except OSError as exc:
        raise OllamaError("list_models", f"could not connect to Ollama at {url_display}: {exc}")
    if status != 200:
        raise OllamaError("list_models", f"HTTP {status}: {text[:200]}")
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise OllamaError("list_models", f"invalid JSON: {exc}")
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
    url_display = base_url or settings.ollama.host
    try:
        status, text = _curl_request(
            "POST", base_url, "/api/pull",
            body={"name": name, "stream": False},
            headers=headers, verify=verify, timeout=1800,
        )
    except OSError as exc:
        raise OllamaError("pull_model", f"could not connect to Ollama at {url_display}: {exc}")
    if status != 200:
        raise OllamaError("pull_model", f"HTTP {status}: {text[:200]}")
    try:
        return json.loads(text).get("status", "success")
    except ValueError as exc:
        raise OllamaError("pull_model", f"invalid JSON: {exc}")


def delete_model(
    name: str,
    base_url: str | None = None,
    *,
    headers: dict[str, str] | None = None,
    verify: bool = True,
) -> str:
    """Delete a model from the Ollama server."""
    url_display = base_url or settings.ollama.host
    try:
        status, text = _curl_request(
            "DELETE", base_url, "/api/delete",
            body={"name": name}, headers=headers, verify=verify, timeout=30,
        )
    except OSError as exc:
        raise OllamaError("delete_model", f"could not connect to Ollama at {url_display}: {exc}")
    if status != 200:
        raise OllamaError("delete_model", f"HTTP {status}: {text[:200]}")
    return "deleted"


# ---------------------------------------------------------------------------
# Async variants of the public API — preferred from FastAPI handlers so
# concurrent requests don't pile up in the default thread pool.
# ---------------------------------------------------------------------------


async def achat(
    messages: list[dict],
    model: str | None = None,
    base_url: str | None = None,
    *,
    headers: dict[str, str] | None = None,
    verify: bool = True,
) -> ChatResult:
    resolved_model = model or settings.ollama.model
    url_display = base_url or settings.ollama.host
    payload = {"model": resolved_model, "messages": messages, "stream": False}
    try:
        status, text = await acurl_request(
            "POST", base_url, "/api/chat",
            body=payload, headers=headers, verify=verify, timeout=300,
        )
    except OSError as exc:
        raise OllamaError("chat", f"could not connect to Ollama at {url_display}: {exc}")
    if status != 200:
        raise OllamaError("chat", f"HTTP {status}: {text[:200]}")
    try:
        body = json.loads(text)
        msg = body["message"]
        return ChatResult(
            model=body.get("model", resolved_model),
            message=ChatMessage(role=msg["role"], content=msg["content"]),
            total_duration=body.get("total_duration", 0),
            eval_count=body.get("eval_count", 0),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise OllamaError("chat", f"unexpected response structure: {exc}")


async def alist_models(
    base_url: str | None = None,
    *,
    headers: dict[str, str] | None = None,
    verify: bool = True,
) -> list[ModelInfo]:
    url_display = base_url or settings.ollama.host
    try:
        status, text = await acurl_request(
            "GET", base_url, "/api/tags",
            headers=headers, verify=verify, timeout=10,
        )
    except OSError as exc:
        raise OllamaError("list_models", f"could not connect to Ollama at {url_display}: {exc}")
    if status != 200:
        raise OllamaError("list_models", f"HTTP {status}: {text[:200]}")
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise OllamaError("list_models", f"invalid JSON: {exc}")
    return [
        ModelInfo(
            name=m.get("name", ""),
            size=m.get("size", 0),
            modified_at=m.get("modified_at", ""),
            digest=m.get("digest", ""),
        )
        for m in data.get("models", [])
    ]


async def apull_model(
    name: str,
    base_url: str | None = None,
    *,
    headers: dict[str, str] | None = None,
    verify: bool = True,
) -> str:
    url_display = base_url or settings.ollama.host
    try:
        status, text = await acurl_request(
            "POST", base_url, "/api/pull",
            body={"name": name, "stream": False},
            headers=headers, verify=verify, timeout=1800,
        )
    except OSError as exc:
        raise OllamaError("pull_model", f"could not connect to Ollama at {url_display}: {exc}")
    if status != 200:
        raise OllamaError("pull_model", f"HTTP {status}: {text[:200]}")
    try:
        return json.loads(text).get("status", "success")
    except ValueError as exc:
        raise OllamaError("pull_model", f"invalid JSON: {exc}")


async def adelete_model(
    name: str,
    base_url: str | None = None,
    *,
    headers: dict[str, str] | None = None,
    verify: bool = True,
) -> str:
    url_display = base_url or settings.ollama.host
    try:
        status, text = await acurl_request(
            "DELETE", base_url, "/api/delete",
            body={"name": name}, headers=headers, verify=verify, timeout=30,
        )
    except OSError as exc:
        raise OllamaError("delete_model", f"could not connect to Ollama at {url_display}: {exc}")
    if status != 200:
        raise OllamaError("delete_model", f"HTTP {status}: {text[:200]}")
    return "deleted"


async def ahealth_check(
    base_url: str | None = None,
    *,
    headers: dict[str, str] | None = None,
    verify: bool = True,
) -> bool:
    try:
        status, _ = await acurl_request(
            "GET", base_url, "/", headers=headers, verify=verify, timeout=5,
        )
        return status == 200
    except Exception:
        return False

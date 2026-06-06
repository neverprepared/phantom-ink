"""FastAPI application: hub API, session management, dashboard, and SSE."""

from __future__ import annotations

import asyncio
import collections as _collections
import json
import os
import secrets
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import docker
import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sse_starlette.sse import EventSourceResponse
from starlette.middleware.cors import CORSMiddleware

from datetime import datetime, timezone

from .auth import _is_api_key_valid, get_api_key, get_bearer_token, load_or_create_key, require_api_key, require_capability
from .config import settings
from .rate_limit import limiter, rate_limit_exceeded_handler
from .hub import init as hub_init, shutdown as hub_shutdown
from .backends.docker import _calc_cpu, _docker, _human_bytes
from .lifecycle import (
    provision,
    configure,
    recycle,
    run_pipeline,
    start as lifecycle_start,
    monitor as lifecycle_monitor,
)
from .validation import (
    validate_session_name,
    ValidationError,
)
from .log import get_logger, setup_logging
from .models import TaskCreate, Token
from .models_api import (
    CompleteChannelRequest,
    CreateAgentRequest,
    CreateChannelRequest,
    CreateSessionRequest,
    DeleteSessionRequest,
    ExecSessionRequest,
    PostChannelMessageRequest,
    QuerySessionRequest,
    StartSessionRequest,
    StopSessionRequest,
    UpdateAgentRequest,
)
from .registry import (
    create_agent,
    delete_agent,
    get_agent,
    list_agents,
    list_tokens,
    update_agent,
    validate_token,
)
from .router import (
    cancel_task,
    complete_task,
    get_task,
    list_tasks,
    on_event,
    submit_task,
)
from .langfuse_client import (
    LangfuseError,
    health_check as langfuse_health_check,
    get_session_traces_summary,
    get_trace as langfuse_get_trace,
    list_traces as langfuse_list_traces,
)
from .messages import get_message_log, get_messages, route as route_message
from .channels import (
    add_participant as channel_add_participant,
    complete_channel,
    create_channel,
    delete_channel,
    get_channel,
    get_messages as channel_get_messages,
    list_channels,
    on_event as channel_on_event,
    post_message as channel_post_message,
    remove_participant as channel_remove_participant,
)
from .playbooks import (
    cancel_playbook,
    create_playbook,
    delete_playbook,
    get_playbook,
    list_playbooks,
    on_event as playbook_on_event,
    run_playbook,
    update_playbook,
)
from .models import ChannelParticipant
from .models_api import OllamaChatRequest, OllamaPullRequest, CreatePlaybookRequest, UpdatePlaybookRequest
from .ollama import (
    OllamaError,
    chat as ollama_chat,
    delete_model as ollama_delete_model,
    health_check as ollama_health_check,
    list_models as ollama_list_models,
    pull_model as ollama_pull_model,
)
from .ollama_pool import get_pool

log = get_logger()


# ---------------------------------------------------------------------------
# Audit logging helper
# ---------------------------------------------------------------------------


def validated_session_name(name: str) -> str:
    try:
        return validate_session_name(name)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _audit_log(
    request: Request,
    operation: str,
    session_name: str | None = None,
    success: bool = True,
    error: str | None = None,
) -> None:
    """Log destructive operations with client metadata and request ID."""
    client_ip = get_remote_address(request) if hasattr(request, "client") else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())

    log.info(
        "audit.operation",
        metadata={
            "request_id": request_id,
            "operation": operation,
            "session_name": session_name or "N/A",
            "client_ip": client_ip,
            "user_agent": user_agent,
            "success": success,
            "error": error,
        },
    )

    try:
        from .store import insert_audit
        detail: dict = {"client_ip": client_ip, "user_agent": user_agent, "request_id": request_id}
        if error:
            detail["error"] = error
        insert_audit(
            operation,
            session_name=session_name,
            actor=client_ip,
            success=success,
            detail=detail,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# SSE client management
# ---------------------------------------------------------------------------

_sse_queues: set[asyncio.Queue] = set()
_sse_drops: int = 0
_channel_queues: dict[str, set[asyncio.Queue]] = {}


def _broadcast_sse(data: str) -> None:
    if not _sse_queues:
        return
    global _sse_drops
    for q in list(_sse_queues):
        try:
            q.put_nowait(data)
        except asyncio.QueueFull:
            _sse_drops += 1
            if _sse_drops % 50 == 1:
                log.warning(
                    "sse.queue_full",
                    metadata={"total_drops": _sse_drops, "connected_clients": len(_sse_queues)},
                )


def _broadcast_to_channel(channel_id: str, data: str) -> None:
    for q in list(_channel_queues.get(channel_id, set())):
        try:
            q.put_nowait(data)
        except asyncio.QueueFull:
            pass
    _broadcast_sse(json.dumps({"action": "channel.message", "channel_id": channel_id}))


# ---------------------------------------------------------------------------
# Docker events watcher
# ---------------------------------------------------------------------------

_docker_events_task: asyncio.Task | None = None


async def _watch_docker_events() -> None:
    """Watch Docker events and broadcast to SSE clients."""
    loop = asyncio.get_running_loop()
    retry = 0

    def _blocking_watch() -> bool:
        """Run in thread — blocks on Docker event stream.

        Returns True if at least one event was processed successfully.
        """
        try:
            client = _docker()
            for event in client.events(filters={"label": "brainbox.managed=true"}, decode=True):
                action = event.get("Action", "")
                if action in ("create", "start", "stop", "die", "destroy"):
                    loop.call_soon_threadsafe(
                        _broadcast_sse,
                        json.dumps({"action": action, "source": "docker"}),
                    )
            return True
        except Exception as e:
            log.warning(
                "docker.events.watcher_error",
                metadata={"reason": str(e)},
            )
            return False

    while True:
        ok = False
        try:
            ok = await loop.run_in_executor(None, _blocking_watch)
        except Exception as e:
            log.warning(
                "docker.events.watcher_error",
                metadata={"reason": str(e)},
            )

        if ok:
            retry = 0
        else:
            retry += 1

        # Exponential backoff before restarting the stream
        await asyncio.sleep(min(2**retry, 60))


# ---------------------------------------------------------------------------
# SPA static files
# ---------------------------------------------------------------------------

_dashboard_dist = Path(__file__).resolve().parent.parent.parent / "dashboard" / "dist"


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------


def _extract_token(request: Request) -> Token | None:
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token_id = auth[7:].strip()
    return validate_token(token_id)


def require_token(request: Request) -> Token:
    token = _extract_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Missing or invalid Bearer token")
    return token


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    from brainbox.config import migrate_config_dir
    migrate_config_dir()
    setup_logging()
    await hub_init()
    load_or_create_key()

    # Forward hub events to SSE
    on_event(
        lambda event, data: _broadcast_sse(
            json.dumps(
                {
                    "hub": True,
                    "event": event,
                    "data": data.model_dump() if hasattr(data, "model_dump") else data,
                }
            )
        )
    )

    # Forward channel events to per-channel SSE queues
    def _on_channel_event(event: str, data: object) -> None:
        if event == "channel.message":
            cid = data.get("channel_id") if isinstance(data, dict) else None  # type: ignore[union-attr]
            msg = data.get("message") if isinstance(data, dict) else None  # type: ignore[union-attr]
            if cid:
                payload = json.dumps({
                    "event": event,
                    "channel_id": cid,
                    "message": msg.model_dump() if hasattr(msg, "model_dump") else msg,
                })
                _broadcast_to_channel(cid, payload)
        elif event in ("channel.created", "channel.completed"):
            _broadcast_sse(json.dumps({
                "action": event,
                "data": data.model_dump() if hasattr(data, "model_dump") else data,
            }))

    channel_on_event(_on_channel_event)

    # Forward playbook events to global SSE
    playbook_on_event(
        lambda event, data: _broadcast_sse(
            json.dumps({
                "action": event,
                "data": data.model_dump() if hasattr(data, "model_dump") else data,
            })
        )
    )

    # Start Docker events watcher
    global _docker_events_task, _metrics_sample_task
    _docker_events_task = asyncio.create_task(_watch_docker_events())
    _metrics_sample_task = asyncio.create_task(_metrics_sample_loop())

    # Start Ollama instance pool
    get_pool().start()

    log.info("api.started", metadata={"port": settings.api_port})
    yield

    get_pool().stop()

    if _docker_events_task:
        _docker_events_task.cancel()
    if _metrics_sample_task:
        _metrics_sample_task.cancel()
    await asyncio.gather(
        *[t for t in (_docker_events_task, _metrics_sample_task) if t],
        return_exceptions=True,
    )

    await hub_shutdown()


app = FastAPI(title="Brainbox", version="0.2.0", lifespan=lifespan)

# CORS — restrict to localhost by default; override via CL_CORS_ORIGINS
_cors_origins = settings.cors_origins or [
    "http://localhost:9999",
    "http://127.0.0.1:9999",
    "http://localhost:9998",  # brainbox-ui nginx container
    "http://localhost:5173",  # Vite dev server
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["GET", "POST", "DELETE", "PATCH"],
    allow_headers=["Authorization", "Content-Type", "X-Request-Id", "X-API-Key"],
)

# Add rate limiter state and exception handler
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)


# ---------------------------------------------------------------------------
# Terminal proxy — /t/{session_name}/
#
# When CL_SESSIONS_URL is set, brainbox proxies ttyd HTTP and WebSocket
# traffic so a single reverse-proxy entry (e.g. sessions.neverprepared.com
# → 127.0.0.1:9999) serves all session terminals at /t/SESSION_NAME/.
# ttyd is started with --base-path /t/SESSION_NAME so its assets resolve.
# ---------------------------------------------------------------------------


def _session_endpoint(session_name: str) -> tuple[str, int, bool] | None:
    """Return (host, port, has_base_path) for a running session's ttyd, or None.

    has_base_path is True when ttyd was started with --base-path /t/{session_name}
    (Python lifecycle path), False when started without it (Swift runner path, serves at /).

    Checks the in-memory session store first, then falls back to inspecting
    Docker directly for sessions not tracked in memory.
    """
    from .lifecycle import get_session
    from .runners import get_registry
    ctx = get_session(session_name)
    if ctx and ctx.port:
        runner_host = ctx.runner_host
        if ctx.runner_name:
            try:
                info = get_registry()._runners.get(ctx.runner_name)
                if info and info.host:
                    runner_host = info.host
                    ctx.runner_host = info.host
            except Exception:
                pass
        host = runner_host or "127.0.0.1"
        has_base_path = True
        log.info(
            "terminal.endpoint_resolved",
            metadata={"session": session_name, "host": host, "port": ctx.port, "has_base_path": has_base_path},
        )
        return (host, ctx.port, has_base_path)
    if ctx:
        log.warning(
            "terminal.port_missing",
            metadata={"session": session_name, "container": ctx.container_name},
        )

    # Fall back: scan Docker for a container whose session_name label or name
    # matches. Recovered sessions have no runner_name so they use base-path.
    try:
        import docker as docker_sdk
        client = docker_sdk.from_env()
        for container in client.containers.list():
            labels = container.labels or {}
            cname = container.name or ""
            if labels.get("brainbox.session_name") == session_name or \
               cname == session_name or \
               cname.endswith(f"-{session_name}"):
                ports = container.ports.get("7681/tcp") or []
                if ports:
                    port = int(ports[0]["HostPort"])
                    log.info(
                        "terminal.docker_port_fallback",
                        metadata={"session": session_name, "container": cname, "port": port},
                    )
                    return ("127.0.0.1", port, True)
                log.warning(
                    "terminal.container_no_port",
                    metadata={"session": session_name, "container": cname, "ports": str(container.ports)},
                )
    except Exception as exc:
        log.warning("terminal.docker_scan_failed", metadata={"session": session_name, "error": str(exc)})

    log.warning("terminal.session_not_found", metadata={"session": session_name})
    return None


@app.get("/t/{session_name}", include_in_schema=False)
async def terminal_proxy_redirect(session_name: str, request: Request):
    """Redirect bare /t/{name} to /t/{name}/ preserving the correct scheme."""
    from fastapi.responses import RedirectResponse
    base = settings.session_base_url
    return RedirectResponse(url=f"{base}/t/{session_name}/", status_code=301)


@app.api_route(
    "/t/{session_name}/{path:path}",
    methods=["GET", "HEAD", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    include_in_schema=False,
)
async def terminal_proxy_http(session_name: str, path: str, request: Request):
    """Reverse-proxy HTTP requests (ttyd assets) to the session's container port."""
    import httpx

    log.info("terminal.proxy_request", metadata={"session": session_name, "path": path, "method": request.method})
    endpoint = _session_endpoint(session_name)
    if endpoint is None:
        raise HTTPException(404, f"Session '{session_name}' not found or not running")
    host, port, has_base_path = endpoint

    if has_base_path:
        target_url = f"http://{host}:{port}/t/{session_name}/{path}"
    else:
        target_url = f"http://{host}:{port}/{path}"
    if request.url.query:
        target_url += f"?{request.url.query}"

    # Drop hop-by-hop headers before forwarding; force identity encoding so
    # httpx doesn't decompress the body while we forward the original headers.
    skip = {"host", "connection", "te", "trailers", "transfer-encoding", "upgrade"}
    fwd_headers = {k: v for k, v in request.headers.items() if k.lower() not in skip}
    fwd_headers["accept-encoding"] = "identity"

    import asyncio
    # On the initial page load (empty path) retry for up to ~10 s so that
    # runner-hosted sessions have time for ttyd to bind inside the container.
    max_attempts = 15 if not path else 1
    for attempt in range(max_attempts):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                rp = await client.request(
                    method=request.method,
                    url=target_url,
                    headers=fwd_headers,
                    content=await request.body(),
                )
            skip_resp = {"transfer-encoding", "connection", "content-encoding", "content-length"}
            return Response(
                content=rp.content,
                status_code=rp.status_code,
                headers={k: v for k, v in rp.headers.items() if k.lower() not in skip_resp},
            )
        except httpx.ConnectError:
            if attempt + 1 < max_attempts:
                await asyncio.sleep(2)
                continue
            raise HTTPException(502, "Terminal not reachable — container may still be starting")


@app.websocket("/t/{session_name}/ws")
async def terminal_proxy_ws(session_name: str, websocket: WebSocket):
    """Bidirectional WebSocket relay between client and session's ttyd.

    Forwards the 'tty' subprotocol and handles both text and binary frames.
    Tries the base-path URL first (sessions started after --base-path was added),
    then falls back to the root /ws path (legacy sessions).
    """
    import websockets as ws_lib
    from fastapi.websockets import WebSocketState

    async def _reject(code: int = 1011) -> None:
        """Accept then immediately close — avoids sending an HTTP error response."""
        try:
            await websocket.accept()
            await websocket.close(code)
        except Exception:
            pass

    endpoint = _session_endpoint(session_name)
    if endpoint is None:
        await _reject(1011)
        return
    host, port, has_base_path = endpoint

    # Forward the subprotocol the browser requested (ttyd uses "tty")
    raw_protocols = websocket.headers.get("sec-websocket-protocol", "")
    subprotocols = [p.strip() for p in raw_protocols.split(",") if p.strip()] or ["tty"]

    # Use base-path URL for Python-lifecycle sessions; root /ws for Swift runner sessions.
    # Always try both so old and new sessions work without reconfiguration.
    if has_base_path:
        candidate_urls = [
            f"ws://{host}:{port}/t/{session_name}/ws",
            f"ws://{host}:{port}/ws",
        ]
    else:
        candidate_urls = [
            f"ws://{host}:{port}/ws",
            f"ws://{host}:{port}/t/{session_name}/ws",
        ]

    backend = None
    for url in candidate_urls:
        try:
            backend = await ws_lib.connect(url, subprotocols=subprotocols, open_timeout=3)
            break
        except Exception:
            continue

    if backend is None:
        await _reject(1011)
        return

    try:
        negotiated = getattr(backend, "subprotocol", None) or subprotocols[0]
        await websocket.accept(subprotocol=negotiated)

        async def to_backend():
            try:
                while True:
                    msg = await websocket.receive()
                    if msg.get("type") == "websocket.disconnect":
                        break
                    if msg.get("bytes"):
                        await backend.send(msg["bytes"])
                    elif msg.get("text"):
                        await backend.send(msg["text"])
            except Exception:
                pass

        async def to_client():
            try:
                async for msg in backend:
                    if isinstance(msg, bytes):
                        await websocket.send_bytes(msg)
                    else:
                        await websocket.send_text(msg)
            except Exception:
                pass

        await asyncio.gather(to_backend(), to_client())
    except Exception:
        pass
    finally:
        try:
            await backend.close()
        except Exception:
            pass
        if websocket.client_state != WebSocketState.DISCONNECTED:
            try:
                await websocket.close(1011)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# API key endpoint (loopback only)
# ---------------------------------------------------------------------------


@app.get("/api/auth/key")
async def api_get_key(request: Request):
    """Return the API key — only accessible from loopback or private Docker networks.

    The API binds to 127.0.0.1 on the host, so only localhost processes and
    containers on the same Docker network (172.x / 10.x / 192.168.x) can reach
    this endpoint. Private-network requests come from trusted internal containers
    (e.g. the brainbox-ui nginx proxy) and are treated as equivalent to localhost.
    """
    import ipaddress

    client_ip = request.client.host if request.client else ""
    try:
        addr = ipaddress.ip_address(client_ip)
        trusted = addr.is_loopback or addr.is_private
    except ValueError:
        trusted = False
    if not trusted:
        raise HTTPException(status_code=403, detail="Only accessible from localhost")
    return {"key": get_api_key()}


# ---------------------------------------------------------------------------
# Dashboard (session info helper used by API)
# ---------------------------------------------------------------------------


_ROLE_PREFIXES = (
    "assistant-", "developer-", "researcher-", "performer-",
    "supervisor-", "worker-", "merge-queue-", "pr-shepherd-", "reviewer-",
    "bash-", "golang-", "java-", "linter-", "python-", "qa-", "typescript-",
)


def _extract_session_name(container_name: str) -> str:
    """Strip any known role prefix from a container name."""
    for prefix in _ROLE_PREFIXES:
        if container_name.startswith(prefix):
            return container_name[len(prefix) :]
    return container_name


def _find_container_name(client: Any, name: str) -> str:
    """Resolve a session name to a container name across all role prefixes.

    Tries the default prefix first for backward compatibility, then falls back
    to all known role prefixes so that supervisor/worker task containers are
    found when callers pass just the session name (e.g. 'task-abc123').

    Returns the matching container name, or raises HTTPException(404).
    """
    # If already a full container name (starts with a known prefix), use as-is
    for prefix in _ROLE_PREFIXES:
        if name.startswith(prefix):
            return name

    candidates = [f"{settings.resolved_prefix}{name}"]
    for prefix in _ROLE_PREFIXES:
        candidate = f"{prefix}{name}"
        if candidate not in candidates:
            candidates.append(candidate)

    for candidate in candidates:
        try:
            client.containers.get(candidate)
            return candidate
        except docker.errors.NotFound:
            continue

    raise HTTPException(status_code=404, detail=f"Container '{name}' not found")


def _extract_role(container: Any) -> str:
    """Get the role label from a container, defaulting to 'developer'."""
    labels = container.labels or {}
    return labels.get("brainbox.role", "developer")


def _get_sessions_info() -> list[dict[str, Any]]:
    """Get session info from all backends (Docker + UTM)."""
    from .backends import create_backend

    sessions = []

    # Get Docker sessions
    try:
        docker_backend = create_backend("docker")
        docker_sessions = docker_backend.get_sessions_info()
        for sess in docker_sessions:
            # Prefer session_name from Docker label; fall back to prefix stripping
            if not sess.get("session_name"):
                sess["session_name"] = _extract_session_name(sess["name"])
            sess["role"] = sess.get("role", "developer")
        sessions.extend(docker_sessions)
    except Exception as exc:
        log.warning("docker.list_sessions_failed", metadata={"reason": str(exc)})

    # Get UTM sessions
    try:
        utm_backend = create_backend("utm")
        utm_sessions = utm_backend.get_sessions_info()
        sessions.extend(utm_sessions)
    except Exception as exc:
        log.warning("utm.list_sessions_failed", metadata={"reason": str(exc)})

    # Merge in-memory runner sessions not visible in local Docker/UTM.
    # Runner sessions on a remote host (e.g. BrainboxRunner on Mac) never
    # appear in the server's Docker list; add them from _sessions so they
    # show up in the UI with a usable terminal URL.
    try:
        from .lifecycle import list_sessions
        from .models import SessionState
        known_names = {s.get("session_name") for s in sessions}
        for ctx in list_sessions():
            if ctx.runner_name and ctx.session_name not in known_names:
                port = ctx.port or 0
                url = f"{settings.session_base_url}/t/{ctx.session_name}"
                sessions.append({
                    "backend": ctx.backend or "docker",
                    "name": ctx.container_name,
                    "session_name": ctx.session_name,
                    "port": port,
                    "url": url,
                    "active": ctx.state == SessionState.RUNNING,
                    "role": ctx.role or "developer",
                    "llm_provider": ctx.llm_provider or "claude",
                    "llm_model": ctx.llm_model or "",
                    "workspace_profile": ctx.workspace_profile or "",
                    "state": ctx.state.value if ctx.state else None,
                    "runner_name": ctx.runner_name,
                })
    except Exception as exc:
        log.warning("sessions.runner_merge_failed", metadata={"reason": str(exc)})

    return sessions



# ---------------------------------------------------------------------------
# Webhooks — inbound trigger endpoint (no API key; URL key is the secret)
# ---------------------------------------------------------------------------


@app.post("/api/webhooks/{key}")
async def webhook_trigger(key: str, request: Request):
    """Receive an inbound webhook and broadcast it to the SSE stream.

    The key in the URL path is the shared secret — anyone who knows it can
    fire this webhook.  Broadcasts action=webhook.trigger so the desktop app
    can route it to the automation engine.
    """
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    _broadcast_sse(json.dumps({"action": "webhook.trigger", "key": key, "payload": payload}))
    return {"status": "received", "key": key}


# ---------------------------------------------------------------------------
# SSE endpoint
# ---------------------------------------------------------------------------


@app.get("/api/events")
async def sse_events(
    session: str | None = Query(None, description="Filter events by session name"),
):
    queue: asyncio.Queue = asyncio.Queue(maxsize=50)
    _sse_queues.add(queue)

    async def event_generator():
        try:
            yield {"data": "connected"}
            while True:
                data = await queue.get()
                # If a session filter is active, only forward matching events
                if session:
                    try:
                        parsed = json.loads(data)
                        event_session = parsed.get("data", {}).get("session_name") or parsed.get(
                            "session_name"
                        )
                        if event_session and event_session != session:
                            continue
                    except (json.JSONDecodeError, TypeError, AttributeError):
                        pass  # Non-JSON events (docker actions) pass through
                yield {"data": data}
        except asyncio.CancelledError:
            pass
        finally:
            _sse_queues.discard(queue)

    return EventSourceResponse(event_generator())


@app.post("/api/sessions/preview")
async def sessions_preview(request: Request, _key=Depends(require_api_key)):
    """Dispatch preview: where will a session with these parameters land?

    Body fields (all optional):
        backend: "docker" (default) or "utm"
        runner: explicit runner name. "" or null means in-process.
        tags: list[str] — preferred runner tags

    Returns:
        selected_runner: name of the runner that will execute, or null
        in_process: True if no runner was chosen and the API will run locally
        reason: human-readable explanation
        candidates: list of registered runners matching the backend capability
    """
    from .runners import get_registry

    body = await request.json() if (await request.body()) else {}
    backend = (body.get("backend") or "docker").lower()
    requested = body.get("runner") or None
    preferred_tags = [t for t in (body.get("tags") or []) if isinstance(t, str)]

    reg = get_registry()
    all_runners = await reg.list_runners()
    now_ms = int(time.time() * 1000)

    def runner_to_dict(r, score: int | None = None) -> dict[str, Any]:
        online = (now_ms - r.last_seen) < 90_000
        headroom = max(0, r.max_concurrent - r.in_flight)
        return {
            "name": r.name,
            "version": r.version,
            "tags": r.tags,
            "online": online,
            "supports_backend": bool(r.capabilities.get(backend)),
            "tag_score": score,
            "queue_depth": r.queue_depth,
            "in_flight": r.in_flight,
            "max_concurrent": r.max_concurrent,
            "headroom": headroom,
        }

    eligible = [r for r in all_runners if r.capabilities.get(backend)]

    def score(r) -> int:
        if not preferred_tags:
            return 0
        return sum(1 for t in preferred_tags if t in r.tags)

    # Sort: online first, then most headroom, then shortest queue, then tag score, then name
    candidates = sorted(
        (runner_to_dict(r, score=score(r)) for r in eligible),
        key=lambda d: (
            0 if d["online"] else 1,
            -(d["headroom"]),
            d["queue_depth"],
            -(d["tag_score"] or 0),
            d["name"],
        ),
    )

    if requested and requested != "local":
        match = next((r for r in all_runners if r.name == requested), None)
        if match is None:
            return {
                "selected_runner": None,
                "in_process": False,
                "reason": f"runner {requested!r} is not registered",
                "candidates": candidates,
                "error": "not_registered",
            }
        if not match.capabilities.get(backend):
            return {
                "selected_runner": None,
                "in_process": False,
                "reason": f"runner {requested!r} does not advertise capability {backend!r}",
                "candidates": candidates,
                "error": "missing_capability",
            }
        online = (now_ms - match.last_seen) < 90_000
        if not online:
            return {
                "selected_runner": requested,
                "in_process": False,
                "reason": f"runner {requested!r} matches but is stale (last_seen {(now_ms - match.last_seen) // 1000}s ago)",
                "candidates": candidates,
                "error": "stale",
            }
        return {
            "selected_runner": requested,
            "in_process": False,
            "reason": f"explicit runner {requested!r} ({backend} capable, online)",
            "candidates": candidates,
        }

    # No explicit runner requested — the API will execute in-process.
    if not candidates:
        return {
            "selected_runner": None,
            "in_process": True,
            "reason": f"no runner requested; no registered runner advertises {backend!r} either",
            "candidates": [],
        }
    return {
        "selected_runner": None,
        "in_process": True,
        "reason": f"no runner requested; falls back to in-process {backend} backend. {len(candidates)} eligible runner(s) available if you want remote dispatch",
        "candidates": candidates,
    }


# ---------------------------------------------------------------------------
# Runner registry — remote agents register here and long-poll for work.
# ---------------------------------------------------------------------------


@app.post("/api/runners/register")
async def runners_register(request: Request, _key=Depends(require_api_key)):
    from .runners import get_registry

    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    caps = body.get("capabilities") or {}
    if not isinstance(caps, dict):
        raise HTTPException(status_code=400, detail="capabilities must be an object")
    reg = get_registry()
    in_flight = int(body.get("in_flight") or 0)
    max_concurrent = int(body.get("max_concurrent") or 4)
    host = (body.get("host") or "").strip() or None
    machine_id = (body.get("machine_id") or "").strip() or None
    ollama_port = int(body["ollama_port"]) if body.get("ollama_port") else None
    # If a stable machine_id is provided and a runner with that ID already
    # exists under a different name, remove the old entry so the rename is
    # seamless rather than creating a duplicate.
    if machine_id:
        await reg.deregister_by_machine_id(machine_id, except_name=name)
    info = await reg.register(
        name=name,
        capabilities={k: bool(v) for k, v in caps.items()},
        tags=body.get("tags") or [],
        version=body.get("version") or "",
        host=host,
        in_flight=in_flight,
        max_concurrent=max_concurrent,
        machine_id=machine_id,
        ollama_port=ollama_port,
    )
    # If runner advertises Ollama and we know its host, add it to the pool.
    if caps.get("ollama") and host:
        await get_pool().add_runner(name, host, ollama_port or 11434)
    return {
        "ok": True,
        "runner": {
            "name": info.name,
            "capabilities": info.capabilities,
            "tags": info.tags,
        },
        "poll_interval": 30,
    }


@app.get("/api/runners")
async def runners_list(_key=Depends(require_api_key)):
    from .runners import get_registry

    reg = get_registry()
    runners = await reg.list_runners()
    return [
        {
            "name": r.name,
            "capabilities": r.capabilities,
            "tags": r.tags,
            "version": r.version,
            "registered_at": r.registered_at,
            "last_seen": r.last_seen,
            "queue_depth": r.queue_depth,
            "in_flight": r.in_flight,
            "max_concurrent": r.max_concurrent,
            "host": r.host,
        }
        for r in runners
    ]


@app.post("/api/runners/{name}/heartbeat")
async def runners_heartbeat(name: str, request: Request, _key=Depends(require_api_key)):
    """Runner heartbeat — updates liveness and optionally reports load metrics.

    Body fields (all optional):
        in_flight: int — sessions currently provisioning
        max_concurrent: int — runner's concurrency limit
    """
    from .runners import get_registry

    reg = get_registry()
    body = await request.json() if await request.body() else {}
    in_flight = body.get("in_flight")
    max_concurrent = body.get("max_concurrent")
    found = await reg.update_load(
        name,
        in_flight=int(in_flight) if in_flight is not None else None,
        max_concurrent=int(max_concurrent) if max_concurrent is not None else None,
    )
    if not found:
        raise HTTPException(status_code=404, detail="runner not registered")
    return {"ok": True}


@app.get("/api/runners/{name}/pending")
async def runners_pending(name: str, _key=Depends(require_api_key)):
    from .runners import get_registry

    reg = get_registry()
    info = await reg.get(name)
    if info is None:
        raise HTTPException(status_code=404, detail="runner not registered")
    item = await reg.next_pending(name, timeout=30.0)
    if item is None:
        return Response(status_code=204)
    return {
        "id": item.id,
        "kind": item.kind,
        "payload": item.payload,
    }


@app.post("/api/runners/{name}/result/{work_id}")
async def runners_result(
    name: str, work_id: str, request: Request, _key=Depends(require_api_key)
):
    from .runners import get_registry
    from .lifecycle import register_runner_session
    from .models import SessionContext
    from .store import async_upsert_session

    body = await request.json()
    reg = get_registry()
    if await reg.get(name) is None:
        raise HTTPException(status_code=404, detail="runner not registered")

    fulfilled = await reg.fulfill(work_id, body)
    if not fulfilled:
        # Late result — the original future timed out while the API was
        # unreachable, but the runner completed the work and is delivering
        # it now. Register the session so it's not lost.
        if body.get("ok") and isinstance(body.get("data"), dict):
            try:
                ctx = SessionContext(**body["data"])
                register_runner_session(ctx)
                await async_upsert_session(ctx)
                log.info(
                    "runners.late_result_accepted",
                    metadata={"runner": name, "work_id": work_id, "session": ctx.session_name},
                )
            except Exception as exc:
                log.warning(
                    "runners.late_result_parse_failed",
                    metadata={"runner": name, "work_id": work_id, "reason": str(exc)},
                )
    return {"ok": True}


@app.post("/api/runners/{name}/event")
async def runners_event(name: str, request: Request, _key=Depends(require_api_key)):
    """Runner posts a status event (e.g. image pull progress) to be broadcast to SSE clients."""
    body = await request.json()
    message = (body.get("message") or "").strip()
    session = (body.get("session") or "").strip() or None
    if not message:
        raise HTTPException(status_code=422, detail="message required")
    _broadcast_sse(json.dumps({
        "action": "runner.status",
        "runner": name,
        "session": session,
        "message": message,
    }))
    return {"ok": True}


@app.delete("/api/runners/{name}")
async def runners_delete(name: str, _key=Depends(require_api_key)):
    """Deregister a runner. Pending work for it is cancelled so callers
    stop waiting. Sessions already running on the runner are untouched —
    cleaning those up is a separate operation (sessions own their own
    container/VM teardown)."""
    from .runners import get_registry

    reg = get_registry()
    removed = await reg.deregister(name)
    if not removed:
        raise HTTPException(status_code=404, detail="runner not registered")
    await get_pool().remove_runner(name)
    return {"ok": True}


@app.get("/api/runner/latest")
async def runner_latest(_key=Depends(require_api_key)):
    """Return the latest BrainboxRunner release from GitHub.

    Reads CL_GITHUB__TOKEN and CL_GITHUB__RUNNER_TAG_PREFIX from config.
    Returns version, asset_id, and asset_name for the DMG asset so the
    runner can fetch it via /api/runner/asset/{asset_id}.
    """
    gh = settings.github
    headers: dict[str, str] = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if gh.token:
        headers["Authorization"] = f"Bearer {gh.token}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"https://api.github.com/repos/{gh.repo}/releases",
                headers=headers,
            )
            resp.raise_for_status()
            releases = resp.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"GitHub API error: {exc}")

    for release in releases:
        tag = release.get("tag_name", "")
        if not tag.startswith(gh.runner_tag_prefix):
            continue
        version = tag[len(gh.runner_tag_prefix):]
        assets = release.get("assets", [])
        dmg = next((a for a in assets if a["name"].endswith(".dmg")), None)
        return {
            "version": version,
            "tag": tag,
            "asset_id": dmg["id"] if dmg else None,
            "asset_name": dmg["name"] if dmg else None,
            "published_at": release.get("published_at"),
            "notes": release.get("body", ""),
        }

    return {"version": None, "tag": None, "asset_id": None}


@app.get("/api/runner/asset/{asset_id}")
async def runner_asset(asset_id: int, _key=Depends(require_api_key)):
    """Proxy-download a GitHub release asset by ID.

    Requires CL_GITHUB__TOKEN to be set — private repo assets are not
    publicly accessible. Streams the binary back to the caller so the
    runner can save the DMG without exposing the token.
    """
    from fastapi.responses import StreamingResponse

    gh = settings.github
    if not gh.token:
        raise HTTPException(status_code=503, detail="CL_GITHUB__TOKEN not configured on this server")

    headers = {
        "Authorization": f"Bearer {gh.token}",
        "Accept": "application/octet-stream",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
            resp = await client.get(
                f"https://api.github.com/repos/{gh.repo}/releases/assets/{asset_id}",
                headers=headers,
            )
            resp.raise_for_status()
            content = resp.content
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"GitHub asset download failed: {exc}")

    asset_name = f"BrainboxRunner-{asset_id}.dmg"
    from fastapi.responses import Response as FastResponse
    return FastResponse(
        content=content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{asset_name}"'},
    )


@app.post("/api/runners/pair/start")
async def runners_pair_start(request: Request, _key=Depends(require_api_key)):
    """Issue a one-time pairing ticket. Caller must already be authenticated —
    they're delegating their api_url + api_key to a new runner. Body fields:
    api_url (required), api_key (optional; defaults to caller's auth), ttl,
    runner_name_suggestion."""
    from .auth import get_api_key
    from .runners import get_pairing_store

    body = await request.json() if await request.body() else {}
    api_url = (body.get("api_url") or "").strip()
    if not api_url:
        raise HTTPException(status_code=400, detail="api_url required")
    api_key = body.get("api_key") or get_api_key()
    if not api_key:
        raise HTTPException(status_code=500, detail="server has no api key to share")
    ttl = float(body.get("ttl") or 300)
    if ttl <= 0 or ttl > 1800:
        raise HTTPException(status_code=400, detail="ttl must be 0 < ttl <= 1800")
    store = get_pairing_store()
    ticket = await store.issue(
        api_url=api_url,
        api_key=api_key,
        runner_name_suggestion=body.get("runner_name_suggestion") or "",
        ttl_seconds=ttl,
    )
    return {
        "token": ticket.token,
        "expires_at": ticket.expires_at,
        "api_url": ticket.api_url,
    }


@app.post("/api/runners/pair/claim")
async def runners_pair_claim(request: Request):
    """Exchange a pairing token for the api_url + api_key. No auth: the token
    itself is the proof. Single-use; rate-limited by token scarcity + TTL."""
    from .runners import get_pairing_store

    body = await request.json() if await request.body() else {}
    token = (body.get("token") or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="token required")
    store = get_pairing_store()
    ticket = await store.claim(token)
    if ticket is None:
        raise HTTPException(status_code=404, detail="token not found, expired, or already used")
    return {
        "api_url": ticket.api_url,
        "api_key": ticket.api_key,
        "runner_name_suggestion": ticket.runner_name_suggestion,
    }


# ---------------------------------------------------------------------------
# Session management routes (from dashboard/server.js)
# ---------------------------------------------------------------------------


@app.get("/api/sessions")
async def api_list_sessions(workspace_profile: str | None = None):
    loop = asyncio.get_running_loop()
    sessions = await loop.run_in_executor(None, _get_sessions_info)
    if workspace_profile is not None:
        sessions = [s for s in sessions if s.get("workspace_profile") == workspace_profile]
    return sessions


@app.get("/api/sessions/{name}")
async def api_get_session(name: str = Depends(validated_session_name)):
    """Get info for a single session by name."""
    loop = asyncio.get_running_loop()
    sessions = await loop.run_in_executor(None, _get_sessions_info)
    for session in sessions:
        if session.get("session_name") == name:
            return session
    raise HTTPException(status_code=404, detail=f"Session '{name}' not found")


@app.post("/api/stop")
@limiter.limit("10/minute")
async def api_stop_session(
    request: Request, body: StopSessionRequest, _key=Depends(require_api_key)
):
    from .lifecycle import get_session, _dispatch_runner_op, _sessions
    name = body.name
    session_name = _extract_session_name(name)

    # Route to runner for remote sessions — but fall through to local handling
    # if the runner is unreachable or deregistered so the container still stops.
    ctx = get_session(session_name)
    if ctx and ctx.runner_name:
        try:
            await _dispatch_runner_op(session_name, "session.stop", timeout=10.0)
            _sessions.pop(session_name, None)
            _audit_log(request, "session.stop", session_name=session_name, success=True)
            _broadcast_sse(json.dumps({"action": "session.stop", "session": session_name}))
            return {"success": True}
        except Exception as exc:
            log.warning(
                "session.runner_stop_failed",
                metadata={"session": session_name, "runner": ctx.runner_name, "error": str(exc), "fallback": "local"},
            )

    try:
        await recycle(session_name, reason="dashboard_stop")
        _audit_log(request, "session.stop", session_name=session_name, success=True)
        _broadcast_sse(json.dumps({"action": "session.stop", "session": session_name}))
        return {"success": True}
    except Exception as exc:
        # Fallback to direct Docker stop
        log.warning(
            "session.recycle_failed",
            metadata={"session": session_name, "error": str(exc), "fallback": "direct_docker_stop"},
        )
        try:
            client = _docker()
            for candidate in [name, f"{settings.resolved_prefix}{session_name}"]:
                try:
                    container = client.containers.get(candidate)
                    container.stop(timeout=1)
                    container.remove(force=True)
                    _audit_log(request, "session.stop", session_name=session_name, success=True)
                    _broadcast_sse(json.dumps({"action": "session.stop", "session": session_name}))
                    return {"success": True}
                except docker.errors.NotFound:
                    continue
            _audit_log(
                request, "session.stop", session_name=session_name, success=False, error="not_found"
            )
            log.error("session.stop_failed.not_found", metadata={"container": name})
            raise HTTPException(status_code=404, detail=f"Container not found: {name}")
        except docker.errors.DockerException as docker_exc:
            _audit_log(
                request,
                "session.stop",
                session_name=session_name,
                success=False,
                error=str(docker_exc),
            )
            log.error(
                "session.stop_failed.docker_error",
                metadata={"container": name, "error": str(docker_exc)},
            )
            raise HTTPException(status_code=500, detail=f"Docker error: {docker_exc}")
        except Exception as fallback_exc:
            _audit_log(
                request,
                "session.stop",
                session_name=session_name,
                success=False,
                error=str(fallback_exc),
            )
            log.exception("session.stop_failed.unexpected")
            raise HTTPException(status_code=500, detail=f"Failed to stop session: {fallback_exc}")


@app.post("/api/delete")
@limiter.limit("10/minute")
async def api_delete_session(
    request: Request, body: DeleteSessionRequest, _key=Depends(require_api_key)
):
    from .lifecycle import get_session, _dispatch_runner_op, _sessions
    name = body.name
    session_name = _extract_session_name(name)

    # Route to runner for remote sessions — fall through to local handling if unreachable.
    ctx = get_session(session_name)
    if ctx and ctx.runner_name:
        try:
            await _dispatch_runner_op(session_name, "session.delete", timeout=10.0)
            _sessions.pop(session_name, None)
            _audit_log(request, "session.delete", session_name=session_name, success=True)
            _broadcast_sse(json.dumps({"action": "session.delete", "session": session_name}))
            return {"success": True}
        except Exception as exc:
            log.warning(
                "session.runner_delete_failed",
                metadata={"session": session_name, "runner": ctx.runner_name, "error": str(exc), "fallback": "local"},
            )

    try:
        await recycle(session_name, reason="dashboard_delete")
        _audit_log(request, "session.delete", session_name=session_name, success=True)
        _broadcast_sse(json.dumps({"action": "session.delete", "session": session_name}))
        return {"success": True}
    except Exception as exc:
        log.warning(
            "session.recycle_failed",
            metadata={
                "session": session_name,
                "error": str(exc),
                "fallback": "direct_docker_remove",
            },
        )
        try:
            client = _docker()
            # Try the original name first (full container name), then the prefix+session pattern
            for candidate in [name, f"{settings.resolved_prefix}{session_name}"]:
                try:
                    container = client.containers.get(candidate)
                    container.remove(force=True)
                    _audit_log(request, "session.delete", session_name=session_name, success=True)
                    _broadcast_sse(json.dumps({"action": "session.delete", "session": session_name}))
                    return {"success": True}
                except docker.errors.NotFound:
                    continue
            # Neither name found — container already gone
            _audit_log(request, "session.delete", session_name=session_name, success=True)
            _broadcast_sse(json.dumps({"action": "session.delete", "session": session_name}))
            return {"success": True}
        except docker.errors.DockerException as docker_exc:
            _audit_log(
                request,
                "session.delete",
                session_name=session_name,
                success=False,
                error=str(docker_exc),
            )
            log.error(
                "session.delete_failed.docker_error",
                metadata={"container": name, "error": str(docker_exc)},
            )
            raise HTTPException(status_code=500, detail=f"Docker error: {docker_exc}")
        except Exception as fallback_exc:
            _audit_log(
                request,
                "session.delete",
                session_name=session_name,
                success=False,
                error=str(fallback_exc),
            )
            log.exception("session.delete_failed.unexpected")
            raise HTTPException(status_code=500, detail=f"Failed to delete session: {fallback_exc}")


@app.post("/api/start")
@limiter.limit("10/minute")
async def api_start_session(
    request: Request, body: StartSessionRequest, _key=Depends(require_api_key)
):
    name = body.name
    session_name = _extract_session_name(name)
    try:
        ctx = await provision(session_name=session_name, hardened=False)
        await configure(ctx)
        await lifecycle_start(ctx)
        await lifecycle_monitor(ctx)
        _audit_log(request, "session.start", session_name=session_name, success=True)
        _broadcast_sse(json.dumps({"action": "session.start", "session": session_name}))
        return {"success": True, "url": f"{settings.session_base_url}/t/{session_name}"}
    except Exception as exc:
        log.error(
            "session.start_failed.lifecycle", metadata={"session": session_name, "error": str(exc)}
        )
        # Fallback to direct Docker start
        try:
            client = _docker()
            container = client.containers.get(name)
            container.start()
            _audit_log(request, "session.start", session_name=session_name, success=True)
            _broadcast_sse(json.dumps({"action": "session.start", "session": session_name}))
            return {"success": True, "url": f"{settings.session_base_url}/t/{session_name}"}
        except docker.errors.NotFound:
            _audit_log(
                request,
                "session.start",
                session_name=session_name,
                success=False,
                error="not_found",
            )
            log.error("session.start_failed.not_found", metadata={"container": name})
            raise HTTPException(status_code=404, detail=f"Container not found: {name}")
        except docker.errors.DockerException as docker_exc:
            _audit_log(
                request,
                "session.start",
                session_name=session_name,
                success=False,
                error=str(docker_exc),
            )
            log.error(
                "session.start_failed.docker_error",
                metadata={"container": name, "error": str(docker_exc)},
            )
            raise HTTPException(status_code=500, detail=f"Docker error: {docker_exc}")
        except Exception as fallback_exc:
            _audit_log(
                request,
                "session.start",
                session_name=session_name,
                success=False,
                error=str(fallback_exc),
            )
            log.exception("session.start_failed.unexpected")
            raise HTTPException(status_code=500, detail=f"Failed to start session: {fallback_exc}")


@app.post("/api/create")
@limiter.limit("10/minute")
async def api_create_session(
    request: Request, body: CreateSessionRequest, _key=Depends(require_api_key)
):
    """Create and start a new container session.

    Provisions a Docker container (or UTM virtual machine) running Claude Code,
    injects secrets and configuration, then starts the session and begins
    lifecycle monitoring.

    Request body fields (``CreateSessionRequest``):
        name (str, optional):
            Session name; the container is named ``{role}-{name}``.
            Defaults to ``"default"``.
        role (str, optional):
            Agent role injected as the system prompt.  Supported values:
            ``developer`` (default), ``supervisor``, ``worker``, ``reviewer``,
            ``merge-queue``, ``pr-shepherd``.
        volumes (list[str], optional):
            Host-to-container volume mounts, each formatted as
            ``/host/path:/container/path[:ro]``.
        volume (str, optional):
            Legacy single-volume shorthand; normalised to ``volumes``.
        llm_provider (str):
            LLM backend — ``"claude"`` (default), ``"ollama"``, or
            ``"codex"``.
        llm_model (str, optional):
            Model identifier for the chosen provider
            (e.g. ``"claude-opus-4-7"``).
        llm_effort (str, optional):
            Claude extended-thinking budget: ``"low"``, ``"medium"``, or
            ``"high"``.
        ollama_host (str, optional):
            Ollama API base URL; overrides ``OLLAMA_HOST`` env var.
        codex_api_key (str, optional):
            OpenAI API key for Codex provider.
        workspace_profile (str, optional):
            Workspace profile name.  Selects the matching ``.env`` file and
            credentials bundle from the profiles directory.
        workspace_home (str, optional):
            Override for the host workspace home directory mounted into the
            container.
        backend (str):
            Compute backend — ``"docker"`` (default) or ``"utm"``.
        vm_template (str, optional):
            UTM template VM name (UTM backend only).
        guest_os (str):
            Guest OS for UTM sessions: ``"linux"`` (default), ``"macos"``,
            or ``"windows"``.
        task (str, optional):
            Initial task description sent to Claude on first launch.  The
            session is also registered in the hub so it appears in the
            dashboard.
        ports (dict[str, int], optional):
            Extra port mappings as ``{container_port: host_port}``.
        docker_host (str, optional):
            Docker daemon URL (e.g. ``tcp://remote:2376``).  ``None`` means
            the local socket.
        repo (RepoConfig, optional):
            Repository access configuration.  Supports ``worktree-mount``
            (host path mounted read-write), ``clone`` (shallow clone),
            ``clone-worktree`` (clone into a worktree), and ``ci-ratchet``
            (full multiclaude ratchet workflow — clones repo, runs task,
            opens PR).

    Returns:
        Docker backend: ``{"success": true, "backend": "docker",
        "url": "http://localhost:{port}"}``

        UTM backend: ``{"success": true, "backend": "utm",
        "ssh_port": <int>, "url": null}``

    Raises:
        400 if request validation fails.
        500 if provisioning or Docker/UTM interaction fails.

    Example::

        POST /api/create
        {
          "name": "my-task",
          "role": "worker",
          "volumes": ["/home/user/code:/workspace"],
          "task": "Fix the failing tests in tests/",
          "repo": {"url": "https://github.com/org/repo",
                   "mode": "ci-ratchet",
                   "task": "Fix the failing tests in tests/"}
        }
    """
    try:
        # Register as a hub task when a task description is provided
        hub_token = None
        task_id = None
        if body.task:
            # Regular session with a task — register in hub so it shows in dashboard
            from .router import _tasks
            from .utils import now_ms as _now_ms
            from .models import Task as HubTask, TaskStatus
            from .registry import issue_token
            tid = str(uuid.uuid4())
            role = body.role or "developer"
            token = issue_token(role, tid, ttl=settings.hub.token_ttl)
            hub_token = token
            task_id = tid
            _tasks[tid] = HubTask(
                id=tid,
                description=body.task,
                agent_name=role,
                status=TaskStatus.RUNNING,
                created_at=_now_ms(),
                updated_at=_now_ms(),
                token_id=token.token_id,
                session_name=body.name,
                repo_url=None,
            )
            _broadcast_sse(json.dumps({"action": "task.submit", "agent": role, "task_id": tid}))

        ctx = await run_pipeline(
            session_name=body.name,
            role=body.role,
            hardened=False,
            volume_mounts=body.volumes,
            llm_provider=body.llm_provider,
            llm_model=body.llm_model,
            llm_effort=body.llm_effort,
            ollama_host=body.ollama_host,
            codex_api_key=body.codex_api_key,
            workspace_profile=body.workspace_profile,
            workspace_home=body.workspace_home,
            backend=body.backend,
            vm_template=body.vm_template,
            guest_os=body.guest_os,
            ports=body.ports,
            docker_host=body.docker_host,
            token=hub_token,
            task_description=body.task,
            task_id=task_id,
            delivery=body.delivery,
            runner=body.runner,
            extra_env=body.env or {},
        )
        _audit_log(request, "session.create", session_name=body.name, success=True)
        _broadcast_sse(json.dumps({"action": "session.create", "session": body.name, "profile": body.workspace_profile or ""}))

        # Response format depends on backend
        if ctx.backend == "utm":
            return {
                "success": True,
                "backend": "utm",
                "ssh_port": ctx.ssh_port,
                "url": None,
            }
        else:
            # Always return the proxy URL — clients route through the API,
            # never directly to the runner host.
            return {
                "success": True,
                "backend": "docker",
                "url": f"{settings.session_base_url}/t/{ctx.session_name}",
            }
    except RuntimeError as exc:
        _audit_log(request, "session.create", session_name=body.name, success=False, error=str(exc))
        msg = str(exc)
        if "saturated" in msg:
            log.warning("session.create.saturated", metadata={"runner": body.runner, "error": msg})
            raise HTTPException(
                status_code=429,
                detail={"error": "runner_saturated", "message": msg},
                headers={"Retry-After": "10"},
            )
        log.error("session.create.failed", metadata={"error": msg})
        raise HTTPException(status_code=500, detail=msg)
    except Exception as exc:
        _audit_log(request, "session.create", session_name=body.name, success=False, error=str(exc))
        log.error("session.create.failed", metadata={"error": str(exc)})
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/sessions/{name}/exec")
@limiter.limit("10/minute")
async def api_exec_session(
    request: Request,
    body: ExecSessionRequest,
    name: str = Depends(validated_session_name),
    _key=Depends(require_api_key),
):
    """Execute a command inside a running container."""
    from .lifecycle import get_session, _dispatch_runner_op

    # Sanitize command input
    if not body.command or not body.command.strip():
        raise HTTPException(status_code=400, detail="Command cannot be empty")
    if "\x00" in body.command:
        raise HTTPException(status_code=400, detail="Command cannot contain null bytes")
    if len(body.command) > 10_000:
        raise HTTPException(status_code=400, detail="Command too long (max 10000 chars)")

    # Route to runner for remote sessions.
    ctx = get_session(name)
    if ctx and ctx.runner_name:
        try:
            data = await _dispatch_runner_op(name, "session.exec", {"command": body.command})
            _audit_log(request, "session.exec", session_name=name, success=data.get("success", True))
            return data
        except Exception as exc:
            _audit_log(request, "session.exec", session_name=name, success=False, error=str(exc))
            raise HTTPException(status_code=500, detail=f"Runner exec failed: {exc}")

    client = _docker()
    try:
        container_name = _find_container_name(client, name)
        container = client.containers.get(container_name)
    except HTTPException:
        _audit_log(request, "session.exec", session_name=name, success=False, error="not_found")
        raise

    loop = asyncio.get_running_loop()
    exit_code, output = await loop.run_in_executor(
        None, lambda: container.exec_run(["sh", "-c", body.command])
    )
    _audit_log(
        request,
        "session.exec",
        session_name=name,
        success=exit_code == 0,
        error=None if exit_code == 0 else f"exit_code={exit_code}",
    )
    return {
        "success": exit_code == 0,
        "exit_code": exit_code,
        "output": output.decode(errors="replace"),
    }


@app.post("/api/sessions/{name}/refresh-secrets")
@limiter.limit("5/minute")
async def api_refresh_secrets(
    request: Request,
    name: str = Depends(validated_session_name),
    _key=Depends(require_api_key),
):
    """Re-resolve and re-inject secrets into a running session."""
    from .secrets import resolve_secrets
    from .lifecycle import get_session

    ctx = get_session(name)
    if not ctx:
        _audit_log(
            request, "session.refresh_secrets", session_name=name, success=False, error="not_found"
        )
        raise HTTPException(status_code=404, detail=f"Session '{name}' not found")

    try:
        secrets = resolve_secrets()
        ctx.secrets.update(secrets)
        await configure(ctx)
        _audit_log(request, "session.refresh_secrets", session_name=name, success=True)
        return {"success": True, "secrets_count": len(secrets)}
    except Exception as exc:
        _audit_log(
            request, "session.refresh_secrets", session_name=name, success=False, error=str(exc)
        )
        raise HTTPException(status_code=500, detail=f"Secret refresh failed: {exc}")


@app.post("/api/sessions/{name}/query")
@limiter.limit("5/minute")
async def api_query_session(
    request: Request,
    body: QuerySessionRequest,
    name: str = Depends(validated_session_name),
    _key=Depends(require_api_key),
):
    """Send a prompt to Claude Code running in the container via tmux."""
    from .lifecycle import get_session, _dispatch_runner_op

    ctx = get_session(name)
    if ctx and ctx.runner_name:
        try:
            data = await _dispatch_runner_op(
                name,
                "session.query",
                {"prompt": body.prompt, "timeout": body.timeout, "working_dir": body.working_dir},
                timeout=float(body.timeout) + 10,
            )
            _audit_log(request, "session.query", session_name=name, success=True)
            return data
        except Exception as exc:
            _audit_log(request, "session.query", session_name=name, success=False, error=str(exc))
            raise HTTPException(status_code=500, detail=f"Runner query failed: {exc}")

    return await _query_via_tmux(request, name, body)


def _parse_claude_output(raw_output: str) -> str:
    """Parse Claude CLI output to extract just the assistant's response.

    Removes box drawing, ANSI codes, prompts, and extracts clean content.
    """
    import re

    # First, strip out the welcome box (everything before the first ❯)
    if "❯" in raw_output:
        # Find the first occurrence of ❯ and remove everything before it
        first_prompt_idx = raw_output.find("❯")
        raw_output = raw_output[first_prompt_idx:]

    # Split by the prompt marker (❯) to get command/response sections
    sections = raw_output.split("❯")

    # Find the LAST section that contains Claude's response (marked with ●)
    response_text = ""
    for section in reversed(sections):
        # Skip empty sections
        if not section.strip():
            continue

        # Skip sections that are just navigation commands (cd /)
        lines = section.strip().splitlines()
        if lines and lines[0].strip().startswith("cd /"):
            continue

        # Look for Claude's response marker (●)
        if "●" in section:
            # Split on ● and take everything after the LAST ●
            parts = section.split("●")
            # Get the last non-empty part
            for part in reversed(parts):
                if part.strip():
                    response_text = part.strip()
                    break
            if response_text:
                break

    if not response_text:
        # Fallback: return original if we can't parse
        return raw_output.strip()

    # Clean up artifacts
    # Remove "Web Search(...)" lines
    response_text = re.sub(r"Web Search\([^)]+\)\n", "", response_text)
    # Remove search timing indicators like "⎿  Did 1 search in 7s"
    response_text = re.sub(r"⎿\s*Did \d+ search.*?\n", "", response_text)
    # Remove completion timing like "✻ Churned for 37s"
    response_text = re.sub(r"✻\s*(?:Brewed|Churned|Percolated|Simmered).*?\n", "", response_text)
    # Remove separator lines
    response_text = re.sub(r"─{10,}", "", response_text)
    # Remove permission UI indicators
    response_text = re.sub(r"⏵.*?bypass permissions.*?\n", "", response_text, flags=re.IGNORECASE)
    # Normalize whitespace
    response_text = re.sub(r"\n{3,}", "\n\n", response_text)

    return response_text.strip()


def _tmux_verify_container(client, container_name: str):
    """Validate that a container exists and is running.

    Raises HTTPException(404) if the container is not found,
    HTTPException(400) if the container exists but is not running.
    """
    try:
        container = client.containers.get(container_name)
    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail=f"Container '{container_name}' not found")
    if container.status != "running":
        raise HTTPException(
            status_code=400,
            detail=f"Container '{container_name}' is not running (status: {container.status})",
        )
    return container


async def _tmux_send_and_wait(
    container_name: str,
    prompt: str,
    timeout: float,
    working_dir: str | None = None,
    docker_host: str | None = None,
) -> str:
    """Send a prompt to the container's tmux session and wait for completion.

    Handles send-keys, marker injection, the polling loop, and raw output
    capture.  Raises TimeoutError if the response is not ready within
    *timeout* seconds.
    """
    loop = asyncio.get_running_loop()
    container = _docker(docker_host).containers.get(container_name)

    # Clear any existing input
    await loop.run_in_executor(
        None, lambda: container.exec_run(["tmux", "send-keys", "-t", "main", "C-c"])
    )
    await asyncio.sleep(0.5)

    # Change to working directory if specified
    if working_dir:
        # Validate working_dir to prevent path traversal / shell injection.
        # Reject null bytes, ".." sequences, and use shlex.quote so the path is
        # safe when embedded in the shell command sent through tmux send-keys.
        import shlex as _shlex
        if "\x00" in working_dir or ".." in working_dir:
            raise HTTPException(
                status_code=400,
                detail="working_dir must not contain null bytes or '..' path components",
            )
        cd_cmd = f"cd {_shlex.quote(working_dir)}"
        await loop.run_in_executor(
            None,
            lambda: container.exec_run(["tmux", "send-keys", "-t", "main", cd_cmd, "Enter"]),
        )
        await asyncio.sleep(0.5)

    # Capture pane before sending prompt
    exit_code, before_output = await loop.run_in_executor(
        None, lambda: container.exec_run(["tmux", "capture-pane", "-t", "main", "-p"])
    )

    # Send prompt to tmux session
    await loop.run_in_executor(
        None,
        lambda: container.exec_run(["tmux", "send-keys", "-t", "main", prompt, "Enter"]),
    )

    # Wait a moment for Claude to show the permission prompt
    await asyncio.sleep(2)

    # Auto-approve permissions by pressing Enter (bypass is already on)
    await loop.run_in_executor(
        None, lambda: container.exec_run(["tmux", "send-keys", "-t", "main", "Enter"])
    )

    # Wait for Claude to complete - detect completion markers
    max_wait = timeout
    waited = 0
    poll_interval = 0.5
    last_output = ""
    stable_count = 0
    completion_markers = [
        "● Done",  # Claude's done marker
        "● Complete",  # Alternative completion
        "● Error",  # Error completion
        "● Failed",  # Failure completion
    ]

    while waited < max_wait:
        await asyncio.sleep(poll_interval)
        waited += poll_interval

        # Capture current pane content
        exit_code, current_output = await loop.run_in_executor(
            None, lambda: container.exec_run(["tmux", "capture-pane", "-t", "main", "-p"])
        )
        output_text = current_output.decode("utf-8", errors="replace")

        # Also check if prompt is back (lines with ❯ that aren't in the permission UI)
        lines = output_text.splitlines()
        prompt_back = False
        for i, line in enumerate(lines):
            # Look for prompt line that's not followed by permission UI
            if line.strip().startswith("❯") and len(line.strip()) == 1:
                # Check next few lines don't have permission UI
                if i + 1 < len(lines):
                    next_line = lines[i + 1] if i + 1 < len(lines) else ""
                    if "bypass permissions" not in next_line and "⏵" not in next_line:
                        prompt_back = True
                        break

        # Check if any completion marker is present
        has_completion_marker = any(marker in output_text for marker in completion_markers)

        # If output hasn't changed for 2 polls and we see completion, we're done
        if output_text == last_output:
            if has_completion_marker or prompt_back:
                stable_count += 1
                if stable_count >= 2:  # Stable for 1 second with completion
                    break
        else:
            stable_count = 0

        last_output = output_text

    if waited >= max_wait:
        raise TimeoutError(f"Query execution timed out after {timeout}s")

    # Capture final output
    exit_code, final_output = await loop.run_in_executor(
        None,
        lambda: container.exec_run(["tmux", "capture-pane", "-t", "main", "-p", "-S", "-100"]),
    )
    return final_output.decode("utf-8", errors="replace")


def _tmux_parse_output(raw_output: str, start_marker: str, end_marker: str) -> str:
    """Extract and clean Claude's response from raw tmux pane output.

    Finds the response by locating *start_marker* in the prompt line, then
    applies the regex cleanup chain via _parse_claude_output.
    """
    lines = raw_output.splitlines()
    response_lines = []
    found_prompt = False

    for line in lines:
        if start_marker in line and "❯" in line:
            found_prompt = True
            continue
        if found_prompt:
            response_lines.append(line)

    cleaned_output = "\n".join(response_lines).strip()
    raw = cleaned_output or raw_output
    return _parse_claude_output(raw) if raw else ""


async def _query_via_tmux(request: Request, name: str, body: QuerySessionRequest):
    """Query container via tmux (legacy fallback)."""
    from .lifecycle import get_session

    start_time = time.time()

    ctx = get_session(name)
    session_docker_host = ctx.docker_host if ctx else None
    client = _docker(session_docker_host)
    try:
        container_name = _find_container_name(client, name)
    except HTTPException:
        _audit_log(request, "session.query", session_name=name, success=False, error="not_found")
        raise

    # Verify container exists and is running
    try:
        container = _tmux_verify_container(client, container_name)
    except HTTPException as exc:
        if exc.status_code == 404:
            _audit_log(
                request, "session.query", session_name=name, success=False, error="not_found"
            )
        raise

    # Check if tmux session exists
    loop = asyncio.get_running_loop()
    exit_code, _ = await loop.run_in_executor(
        None, lambda: container.exec_run(["tmux", "has-session", "-t", "main"])
    )

    if exit_code != 0:
        _audit_log(
            request, "session.query", session_name=name, success=False, error="no_tmux_session"
        )
        raise HTTPException(
            status_code=503,
            detail="Claude tmux session not found in container. Is Claude running?",
        )

    try:
        raw_output = await _tmux_send_and_wait(
            container_name,
            body.prompt,
            body.timeout,
            working_dir=body.working_dir,
            docker_host=session_docker_host,
        )

        # Calculate duration
        duration = time.time() - start_time

        # Parse the Claude CLI output for clean presentation
        parsed_response = _tmux_parse_output(raw_output, body.prompt, "")

        _audit_log(request, "session.query", session_name=name, success=True)

        return {
            "success": True,
            "conversation_id": f"{name}-{int(time.time())}",
            "response": parsed_response,  # Clean, parsed assistant response
            "output": raw_output,  # Keep raw output for debugging
            "error": None,
            "exit_code": 0,
            "duration_seconds": duration,
        }

    except TimeoutError:
        _audit_log(request, "session.query", session_name=name, success=False, error="timeout")
        raise HTTPException(
            status_code=408,
            detail=f"Query execution timed out after {body.timeout}s",
        )
    except Exception as e:
        _audit_log(request, "session.query", session_name=name, success=False, error=str(e))
        raise HTTPException(
            status_code=500,
            detail=f"Query execution failed: {e}",
        )


# ---------------------------------------------------------------------------
# Container metrics (with LangFuse trace count cache)
# ---------------------------------------------------------------------------

_trace_cache: dict[str, dict[str, Any]] = {}  # session_name -> {data, ts}
_trace_cache_lock = threading.Lock()
_TRACE_CACHE_TTL = 60  # seconds - increased from 10s to reduce load


def _get_trace_counts(session_name: str, timeout: float = 2.0) -> dict[str, int]:
    """Get trace/error counts for a session, cached for 60s with timeout."""
    now = time.monotonic()
    with _trace_cache_lock:
        cached = _trace_cache.get(session_name)
    if cached and (now - cached["ts"]) < _TRACE_CACHE_TTL:
        return cached["data"]

    if settings.langfuse.mode == "off":
        return {"trace_count": 0, "error_count": 0}

    try:
        # Use a thread pool with timeout to prevent blocking
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(get_session_traces_summary, session_name)
            summary = future.result(timeout=timeout)
            data = {"trace_count": summary.total_traces, "error_count": summary.error_count}
    except (Exception, concurrent.futures.TimeoutError):
        # Return zeros on timeout or error, don't cache failures
        return {"trace_count": 0, "error_count": 0}

    with _trace_cache_lock:
        _trace_cache[session_name] = {"data": data, "ts": now}
    return data


def _new_docker_client() -> "docker.DockerClient":
    """Create a fresh Docker client (not the shared lifecycle client)."""
    from pathlib import Path
    macos_sock = Path.home() / ".docker" / "run" / "docker.sock"
    if macos_sock.is_socket():
        return docker.DockerClient(base_url=f"unix://{macos_sock}", timeout=5)
    return docker.from_env(timeout=5)


def _get_container_metrics() -> list[dict[str, Any]]:
    """Collect per-container CPU %, memory usage, and uptime (blocking)."""
    import concurrent.futures

    results = []
    try:
        client = _docker()
        containers = client.containers.list(filters={"label": "brainbox.managed=true"})

        if not containers:
            return results

        def get_container_metrics(c):
            """Get metrics for a single container using a dedicated client."""
            stats_client = None
            try:
                # Use a dedicated client to avoid contention with the lifecycle
                # monitor's shared client when both call c.stats() concurrently.
                stats_client = _new_docker_client()
                container = stats_client.containers.get(c.id)
                stats = container.stats(stream=False)
                cpu_pct = _calc_cpu(stats)
                mem = stats.get("memory_stats", {})
                mem_usage = mem.get("usage", 0)
                mem_limit = mem.get("limit", 1)

                # Uptime from State.StartedAt
                started_at = c.attrs.get("State", {}).get("StartedAt", "")
                uptime_seconds = 0
                if started_at:
                    try:
                        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                        uptime_seconds = (datetime.now(timezone.utc) - started).total_seconds()
                    except (ValueError, TypeError):
                        pass

                labels = c.labels or {}
                session_name = _extract_session_name(c.name)
                # Use shorter timeout for trace counts to prevent blocking
                trace_counts = _get_trace_counts(session_name, timeout=1.0)

                return {
                    "name": c.name,
                    "session_name": session_name,
                    "role": _extract_role(c),
                    "llm_provider": labels.get("brainbox.llm_provider", "claude"),
                    "workspace_profile": labels.get("brainbox.workspace_profile", ""),
                    "cpu_percent": round(cpu_pct, 2),
                    "mem_usage": mem_usage,
                    "mem_usage_human": _human_bytes(mem_usage),
                    "mem_limit": mem_limit,
                    "mem_limit_human": _human_bytes(mem_limit),
                    "uptime_seconds": round(uptime_seconds),
                    "trace_count": trace_counts["trace_count"],
                    "error_count": trace_counts["error_count"],
                }
            except Exception as exc:
                log.debug("metrics.container_stat_failed", metadata={"container": c.name, "reason": str(exc)})
                return None
            finally:
                if stats_client is not None:
                    try:
                        stats_client.close()
                    except Exception as exc:
                        log.debug("container_metrics.failed", metadata={"reason": str(exc)})

        # Process containers in parallel with a timeout per container
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(containers), 8)) as executor:
            future_to_container = {executor.submit(get_container_metrics, c): c for c in containers}
            try:
                for future in concurrent.futures.as_completed(future_to_container, timeout=15):
                    try:
                        result = future.result(timeout=2)
                        if result:
                            results.append(result)
                    except Exception as exc:
                        log.debug("metrics.container_future_failed", metadata={"reason": str(exc)})
            except concurrent.futures.TimeoutError as exc:
                log.warning("metrics.containers_timeout", metadata={"reason": str(exc)})

    except Exception as exc:
        log.warning("metrics.containers_failed", metadata={"reason": str(exc)})

    results.sort(key=lambda r: r["name"])
    return results


@app.get("/api/metrics/containers")
async def api_container_metrics():
    """Per-container resource metrics for all running brainbox-managed containers.

    Each element in the returned list describes one container:

    - ``name`` (str): Full container name (e.g. ``worker-my-task``).
    - ``session_name`` (str): Session name with role prefix stripped.
    - ``role`` (str): Agent role (e.g. ``developer``, ``worker``).
    - ``llm_provider`` (str): LLM backend the session was started with.
    - ``workspace_profile`` (str): Active workspace profile, or empty string.
    - ``cpu_percent`` (float): CPU usage as a percentage of one core (e.g.
      ``45.2`` means 45.2 % of one CPU).
    - ``mem_usage`` (int): Memory used by the container in **bytes**.
    - ``mem_usage_human`` (str): Human-readable memory usage (e.g. ``"1.2 GB"``).
    - ``mem_limit`` (int): Container memory limit in **bytes**.
    - ``mem_limit_human`` (str): Human-readable memory limit.
    - ``uptime_seconds`` (int): Seconds since the container was started.
    - ``trace_count`` (int): Number of LangFuse traces recorded for this
      session (cached for 60 s; 0 when LangFuse is disabled or unreachable).
    - ``error_count`` (int): Number of error-level LangFuse traces for this
      session (cached alongside ``trace_count``).
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _get_container_metrics)


# ---------------------------------------------------------------------------
# Metrics history — in-memory ring buffer
# ---------------------------------------------------------------------------

_metrics_history: _collections.deque[dict[str, Any]] = _collections.deque(maxlen=360)
_session_metrics_history: dict[str, _collections.deque[dict[str, Any]]] = {}
_metrics_sample_task: asyncio.Task[None] | None = None


async def _metrics_sample_loop() -> None:
    """Sample aggregate and per-session metrics every 10 s."""
    loop = asyncio.get_running_loop()
    while True:
        await asyncio.sleep(10)
        try:
            container_metrics = await loop.run_in_executor(None, _get_container_metrics)
            ts = time.time()
            _metrics_history.append({
                "ts": ts,
                "agent_count": len(container_metrics),
                "total_cpu": round(sum(m.get("cpu_percent", 0) for m in container_metrics), 2),
                "total_mem": sum(m.get("mem_usage", 0) for m in container_metrics),
            })
            for m in container_metrics:
                sname = m.get("session_name") or m.get("name", "")
                if not sname:
                    continue
                if sname not in _session_metrics_history:
                    _session_metrics_history[sname] = _collections.deque(maxlen=360)
                _session_metrics_history[sname].append({
                    "ts": ts,
                    "mem_usage": m.get("mem_usage", 0),
                    "cpu_percent": m.get("cpu_percent", 0),
                })
        except Exception:
            pass


@app.get("/api/metrics/history")
async def api_metrics_history():
    """Aggregate metrics ring buffer — last hour at 10 s resolution."""
    return list(_metrics_history)


@app.get("/api/metrics/sessions/history")
async def api_session_metrics_history():
    """Per-session metrics ring buffers — last hour at 10 s resolution."""
    return {k: list(v) for k, v in _session_metrics_history.items()}


# ---------------------------------------------------------------------------
# Hub API routes (from hub-api.js)
# ---------------------------------------------------------------------------

# --- Agents ---


@app.get("/api/hub/agents")
async def hub_list_agents():
    return [a.model_dump() for a in list_agents()]


@app.get("/api/hub/agents/{name}")
async def hub_get_agent(name: str):
    agent = get_agent(name)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    from .registry import get_role_prompt
    data = agent.model_dump()
    data["role_prompt_content"] = get_role_prompt(name) or ""
    return data


@app.post("/api/hub/agents", status_code=201)
async def hub_create_agent(body: CreateAgentRequest, _key=Depends(require_api_key)):
    try:
        agent = create_agent(
            name=body.name,
            image=body.image,
            description=body.description,
            capabilities=body.capabilities,
            hardened=body.hardened,
            persistent=body.persistent,
            role_prompt_content=body.role_prompt_content,
            claude_model=body.claude_model,
            claude_effort=body.claude_effort,
            codex_model=body.codex_model,
            ollama_model=body.ollama_model,
            category=body.category,
            spawn_mode=body.spawn_mode,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _broadcast_sse(json.dumps({"action": "agent.created", "name": body.name}))
    return agent.model_dump()


@app.patch("/api/hub/agents/{name}")
async def hub_update_agent(name: str, body: UpdateAgentRequest, _key=Depends(require_api_key)):
    try:
        agent = update_agent(
            name=name,
            image=body.image,
            description=body.description,
            capabilities=body.capabilities,
            hardened=body.hardened,
            persistent=body.persistent,
            role_prompt_content=body.role_prompt_content,
            claude_model=body.claude_model,
            claude_effort=body.claude_effort,
            codex_model=body.codex_model,
            ollama_model=body.ollama_model,
            category=body.category,
            spawn_mode=body.spawn_mode,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _broadcast_sse(json.dumps({"action": "agent.updated", "name": name}))
    from .registry import get_role_prompt
    data = agent.model_dump()
    data["role_prompt_content"] = get_role_prompt(name) or ""
    return data


@app.delete("/api/hub/agents/{name}", status_code=204)
async def hub_delete_agent(name: str, _key=Depends(require_api_key)):
    try:
        delete_agent(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _broadcast_sse(json.dumps({"action": "agent.deleted", "name": name}))
    return Response(status_code=204)


# --- Tasks ---


@app.post("/api/hub/tasks", status_code=201)
async def hub_submit_task(
    body: TaskCreate,
    token: Token | None = Depends(require_capability("task_submit")),
):
    try:
        task = await submit_task(
            body.description,
            body.agent_name,
            repo_url=body.repo_url,
            workspace_profile=body.workspace_profile,
            workspace_home=body.workspace_home,
            job_id=body.job_id,
            runner=body.runner,
            runner_tags=body.runner_tags,
            backend=body.backend,
            priority=body.priority,
            max_attempts=body.max_attempts,
            deadline_ms=body.deadline_ms,
        )
        _broadcast_sse(json.dumps({"action": "task.submit", "agent": body.agent_name, "repo": body.repo_url or ""}))
        return task.model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/hub/tasks")
async def hub_list_tasks(
    status: str | None = None,
    limit: int = 50,
    job_id: str | None = None,
    workspace_profile: str | None = None,
    _key=Depends(require_api_key),
):
    tasks = list_tasks(status=status, limit=limit, job_id=job_id, workspace_profile=workspace_profile)
    return [t.model_dump() for t in tasks]


@app.get("/api/hub/tasks/{task_id}")
async def hub_get_task(task_id: str, _key=Depends(require_api_key)):
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
    return task.model_dump()


@app.delete("/api/hub/tasks/{task_id}")
async def hub_cancel_task(task_id: str, request: Request):
    """Cancel a task. Accepts API key (full trust) or the owning agent's bearer token."""
    if not _is_api_key_valid(request):
        token = get_bearer_token(request)
        if not token:
            raise HTTPException(status_code=401, detail="Missing or invalid API key or Bearer token")
        if token.task_id != task_id:
            raise HTTPException(status_code=403, detail="Token is not the owner of this task")
    try:
        task = await cancel_task(task_id)
        _broadcast_sse(json.dumps({"action": "task.cancel", "task_id": task_id}))
        return task.model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# --- Messages ---


def _require_token_or_api_key(request: Request) -> Token:
    """Accept either Bearer token or X-API-Key for message routing."""
    token = _extract_token(request)
    if token:
        return token
    # Fall back to API key — create a synthetic hub token
    api_key = request.headers.get("x-api-key", "")
    if api_key and secrets.compare_digest(api_key, get_api_key()):
        now = int(time.time() * 1000)
        return Token(
            token_id="api-key-fallback",
            agent_name="hub",
            task_id="",
            capabilities=[],
            issued=now,
            expiry=now + 3600000,
        )
    raise HTTPException(status_code=401, detail="Missing or invalid Bearer token or API key")


@app.post("/api/hub/messages")
async def hub_route_message(request: Request, token: Token = Depends(_require_token_or_api_key)):
    body = await request.json()
    payload = body.get("payload", {})

    # API key fallback path — skip route_message validation, handle completion directly
    if token.token_id == "api-key-fallback":
        if isinstance(payload, dict) and payload.get("event") == "task.completed":
            completion_result = payload.get("result", "done")
            task_id = payload.get("task_id", "")
            if task_id:
                try:
                    await complete_task(task_id, completion_result)
                    return {"delivered": True, "message_id": "api-key-completion", "task_id": task_id}
                except Exception as exc:
                    log.warning("hub.task_completion_error", metadata={"task_id": task_id, "reason": str(exc)})
            return {"delivered": True, "message_id": "api-key-no-task-id"}
        return {"delivered": True, "message_id": "api-key-passthrough"}

    try:
        result = route_message(
            {
                "sender_token_id": token.token_id,
                "recipient": body.get("recipient", "hub"),
                "type": body.get("type"),
                "payload": body.get("payload"),
            }
        )
    except ValueError as exc:
        status = 401 if "token" in str(exc).lower() else 400
        raise HTTPException(status_code=status, detail=str(exc))

    # Handle task completion side effect
    if isinstance(payload, dict) and payload.get("event") == "task.completed":
        task_id = token.task_id
        completion_result = payload.get("result")
        if task_id:
            try:
                await complete_task(task_id, completion_result)
            except Exception as exc:
                log.warning(
                    "hub.task_completion_error",
                    metadata={"task_id": task_id, "reason": str(exc)},
                )

    return result


@app.get("/api/hub/messages")
async def hub_get_messages(token: Token = Depends(require_token)):
    return get_messages(token.token_id)


# --- Tokens ---


@app.get("/api/hub/tokens")
async def hub_list_tokens(_key=Depends(require_api_key)):
    return [t.model_dump() for t in list_tokens()]


# --- State ---


@app.get("/api/hub/state")
async def hub_state(_key=Depends(require_api_key)):
    return {
        "agents": [a.model_dump() for a in list_agents()],
        "tasks": [t.model_dump() for t in list_tasks()],
        "tokens": [t.model_dump() for t in list_tokens()],
        "messages": get_message_log(),
    }


@app.get("/api/hub/message-log")
async def hub_message_log(_key=Depends(require_api_key)):
    """Return the hub message audit log (admin read-only, no agent token required)."""
    return get_message_log()


# ---------------------------------------------------------------------------
# Group chat channels
# ---------------------------------------------------------------------------


async def _bootstrap_session_in_channel(
    participant: ChannelParticipant,
    channel: Any,
    *,
    note: str = "",
) -> None:
    """Drop a CHANNEL.md into the session's container and nudge Claude (via
    tmux) to start participating. Called both when a channel is first created
    AND when a session is added to an already-live channel — without this
    bootstrap, the session has no idea it's been included and won't respond.

    `note` is appended to the tmux prompt so e.g. late joins can say
    "you've been added to an in-progress conversation, catch up first."
    Errors are logged but don't bubble — bootstrap failure shouldn't block
    the channel mutation that triggered it.
    """
    if participant.type != "session" or not participant.session_name:
        return

    api_port = settings.api_port
    api_key_val = get_api_key()
    bootstrap = (
        f"# Group Channel: {channel.name}\n\n"
        f"You are **{participant.name}** in a group discussion.\n\n"
        f"**Channel ID:** `{channel.id}`\n"
        f"**API URL:** `http://host.docker.internal:{api_port}`\n"
        f"**Your API key:** `{api_key_val}`\n\n"
        "## How to participate\n\n"
        "Use these MCP tools (already available in your session):\n"
        f'- `channel_read(channel_id="{channel.id}", since_id=<last_id>)` — get new messages\n'
        f'- `channel_send(channel_id="{channel.id}", content=<msg>, summary=<brief>)` — post a message\n'
        f'- `channel_complete(channel_id="{channel.id}", reason=<why>)` — signal discussion is done\n\n'
        "## Rules\n"
        "1. Poll `channel_read` every few seconds to check for new messages\n"
        "2. Respond to broadcast messages and messages addressed to @" + participant.name + "\n"
        "3. When sending, always include `summary=` with a 1-2 sentence brief of your key point\n"
        "4. Use `addressed_to=` to direct a response at a specific participant\n"
        "5. Call `channel_complete` when you believe the discussion has concluded\n"
    )
    if participant.system_prompt:
        bootstrap += f"6. Your role: {participant.system_prompt}\n"

    try:
        client = _docker()
        container_name = _find_container_name(client, participant.session_name)
        container = client.containers.get(container_name)
        loop = asyncio.get_running_loop()
        import io
        import tarfile

        # Pre-flight: confirm tmux session 'main' is actually live in the
        # container. If we skip this check and the session isn't there,
        # `tmux send-keys` exits non-zero but `exec_run` reports it as
        # a normal completion — leading to a false-positive bootstrap_sent
        # log while the agent never actually receives anything.
        check_res = await loop.run_in_executor(
            None,
            lambda c=container: c.exec_run(
                ["tmux", "has-session", "-t", "main"],
                user="developer",
            ),
        )
        if check_res.exit_code != 0:
            log.warning(
                "channel.bootstrap_exec_failed",
                metadata={
                    "session": participant.session_name,
                    "channel_id": channel.id,
                    "reason": (
                        "no tmux session 'main' in container — the agent "
                        "isn't running yet. Open the session's terminal "
                        "once to spawn it, or wait for the container to "
                        "finish booting, then retry."
                    ),
                    "tmux_has_session_exit": check_res.exit_code,
                },
            )
            return

        content_bytes = bootstrap.encode("utf-8")
        tarstream = io.BytesIO()
        with tarfile.open(fileobj=tarstream, mode="w") as tar:
            info = tarfile.TarInfo(name="CHANNEL.md")
            info.size = len(content_bytes)
            tar.addfile(info, io.BytesIO(content_bytes))
        tarstream.seek(0)
        await loop.run_in_executor(
            None,
            lambda c=container, ts=tarstream: c.put_archive("/home/developer", ts),
        )

        tmux_prompt = (
            f"Read /home/developer/CHANNEL.md carefully. "
            f"You are now a participant in group channel '{channel.name}' (ID: {channel.id}). "
            f"Begin participating autonomously: use channel_read to poll for messages, "
            f"respond using channel_send (always include a summary=), and call channel_complete "
            f"when the discussion has concluded. "
            f"{note}"
            f"Start now by reading the channel and introducing yourself."
        )
        # Type the prompt as literal text, then press Enter as a separate
        # tmux call with a brief pause between. Claude Code v2.x's TUI
        # treats a single send-keys batch like a bracketed paste and
        # consumes the trailing Enter as part of the paste rather than as
        # a submit keystroke. Splitting the send into two operations gives
        # the TUI a chance to leave paste mode before the Enter arrives.
        type_res = await loop.run_in_executor(
            None,
            lambda c=container, prompt=tmux_prompt: c.exec_run(
                ["tmux", "send-keys", "-t", "main", "-l", prompt],
                user="developer",
            ),
        )
        if type_res.exit_code != 0:
            stderr = (type_res.output or b"").decode("utf-8", errors="replace").strip()
            log.warning(
                "channel.bootstrap_exec_failed",
                metadata={
                    "session": participant.session_name,
                    "channel_id": channel.id,
                    "reason": f"tmux send-keys (text) failed (exit {type_res.exit_code}): {stderr or 'unknown'}",
                },
            )
            return

        await asyncio.sleep(0.3)

        send_res = await loop.run_in_executor(
            None,
            lambda c=container: c.exec_run(
                ["tmux", "send-keys", "-t", "main", "Enter"],
                user="developer",
            ),
        )
        if send_res.exit_code != 0:
            stderr = (send_res.output or b"").decode("utf-8", errors="replace").strip()
            log.warning(
                "channel.bootstrap_exec_failed",
                metadata={
                    "session": participant.session_name,
                    "channel_id": channel.id,
                    "reason": f"tmux send-keys (Enter) failed (exit {send_res.exit_code}): {stderr or 'unknown'}",
                },
            )
            return

        log.info(
            "channel.bootstrap_sent",
            metadata={"session": participant.session_name, "channel_id": channel.id},
        )
    except Exception as exc:
        log.warning(
            "channel.bootstrap_exec_failed",
            metadata={"session": participant.session_name, "reason": str(exc)},
        )


@app.post("/api/hub/channels")
async def hub_create_channel(body: CreateChannelRequest, request: Request, _key=Depends(require_api_key)):
    """Create a group chat channel and bootstrap session participants."""
    participants = [
        ChannelParticipant(
            name=p.name,
            type=p.type,
            session_name=p.session_name,
            ollama_model=p.ollama_model,
            system_prompt=p.system_prompt,
        )
        for p in body.participants
    ]
    channel = create_channel(
        body.name,
        participants,
        parent_task_id=body.parent_task_id,
        workspace_profile=body.workspace_profile,
    )

    if body.parent_task_id:
        from .router import _add_channel_to_task
        _add_channel_to_task(body.parent_task_id, channel.id)

    for p in participants:
        await _bootstrap_session_in_channel(p, channel)

    return channel.model_dump()


@app.get("/api/hub/channels")
async def hub_list_channels(
    workspace_profile: str | None = None,
    _key=Depends(require_api_key),
):
    return [c.model_dump() for c in list_channels(workspace_profile=workspace_profile)]


@app.get("/api/hub/channels/{channel_id}")
async def hub_get_channel(channel_id: str, _key=Depends(require_api_key)):
    channel = get_channel(channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail=f"Channel '{channel_id}' not found")
    return channel.model_dump()


@app.delete("/api/hub/channels/{channel_id}")
async def hub_delete_channel(channel_id: str, _key=Depends(require_api_key)):
    try:
        delete_channel(channel_id)
        _broadcast_sse(json.dumps({"action": "channel.deleted", "channel_id": channel_id}))
        return {"ok": True}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/hub/channels/{channel_id}/participants")
async def hub_add_channel_participant(
    channel_id: str,
    body: ChannelParticipant,
    _key=Depends(require_api_key),
):
    """Add a session as a participant in an already-created channel.

    Returns the updated channel. 404 if the channel doesn't exist; 400 if
    the participant is already in the channel or the channel is completed.
    """
    try:
        channel = channel_add_participant(channel_id, body)
        _broadcast_sse(json.dumps({"action": "channel.participant_added", "channel_id": channel_id}))
        # Critical: bootstrap the session so it actually starts participating.
        # Without this, the participant row exists but the container never
        # learns about the channel and never polls / responds.
        await _bootstrap_session_in_channel(
            body,
            channel,
            note=(
                "Note: you've been added to an in-progress conversation. "
                "Use channel_read to catch up on prior messages before responding. "
            ),
        )
        return channel.model_dump()
    except ValueError as exc:
        msg = str(exc)
        status = 404 if "not found" in msg else 400
        raise HTTPException(status_code=status, detail=msg)


@app.post("/api/hub/channels/{channel_id}/join")
async def hub_join_channel(
    channel_id: str,
    token: Token = Depends(require_token),
):
    """Session self-join: register as a channel participant using bearer token identity.

    Idempotent — safe to call if already a member. The session identity is
    derived from the bearer token; no body is needed. Requires the
    'hub_messaging' capability.
    """
    from .channels import join_channel
    from .router import get_task as _get_task

    if "hub_messaging" not in token.capabilities:
        raise HTTPException(
            status_code=403,
            detail="Token lacks required capability: 'hub_messaging'",
        )

    task = _get_task(token.task_id) if token.task_id else None
    session_name = (task.session_name if task else None) or token.agent_name

    try:
        channel = join_channel(channel_id, session_name=session_name)
        if task and channel.id not in task.channel_ids:
            task.channel_ids.append(channel.id)
            if channel.parent_task_id is None:
                channel.parent_task_id = task.id
        _broadcast_sse(
            json.dumps({"action": "channel.participant_joined", "channel_id": channel_id, "session": session_name})
        )
        return channel.model_dump()
    except ValueError as exc:
        msg = str(exc)
        status = 404 if "not found" in msg else 400
        raise HTTPException(status_code=status, detail=msg)


@app.delete("/api/hub/channels/{channel_id}/participants/{name}")
async def hub_remove_channel_participant(
    channel_id: str,
    name: str,
    _key=Depends(require_api_key),
):
    """Remove a participant from a channel by name. The participant stops
    receiving messages but historical messages they posted stay in the log.
    """
    try:
        channel = channel_remove_participant(channel_id, name)
        _broadcast_sse(json.dumps({"action": "channel.participant_removed", "channel_id": channel_id, "name": name}))
        return channel.model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/api/hub/channels/{channel_id}/messages")
async def hub_get_channel_messages(
    channel_id: str,
    since_id: str | None = Query(default=None),
    _key=Depends(require_api_key),
):
    channel = get_channel(channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail=f"Channel '{channel_id}' not found")
    msgs = channel_get_messages(channel_id, since_id=since_id)
    return [m.model_dump() for m in msgs]


@app.get("/api/hub/channels/{channel_id}/wait")
async def hub_wait_channel_activity(
    channel_id: str,
    since_id: str | None = Query(default=None),
    timeout: float = Query(default=30.0, ge=1.0, le=120.0),
    _key=Depends(require_api_key),
):
    """Long-poll: block until a new message arrives or channel completes.

    Returns immediately if there are already messages after since_id or the
    channel is already completed. Otherwise waits up to `timeout` seconds.
    """
    channel = get_channel(channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail=f"Channel '{channel_id}' not found")

    msgs = channel_get_messages(channel_id, since_id=since_id)
    if msgs or channel.status == "completed":
        return {
            "messages": [m.model_dump() for m in msgs],
            "completed": channel.status == "completed",
            "completion_reason": channel.completed_by,
        }

    wakeup = asyncio.Event()

    def _listener(event: str, data: object) -> None:
        if event in ("channel.message", "channel.completed"):
            payload = data if isinstance(data, dict) else {}
            cid = payload.get("channel_id") or (data.id if hasattr(data, "id") else None)
            if cid == channel_id:
                wakeup.set()

    import brainbox.channels as _ch_module
    _ch_module._listeners.append(_listener)
    try:
        await asyncio.wait_for(wakeup.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        pass
    finally:
        try:
            _ch_module._listeners.remove(_listener)
        except ValueError:
            pass

    msgs = channel_get_messages(channel_id, since_id=since_id)
    channel = get_channel(channel_id)
    return {
        "messages": [m.model_dump() for m in msgs],
        "completed": channel.status == "completed" if channel else True,
        "completion_reason": channel.completed_by if channel else None,
    }


@app.post("/api/hub/channels/{channel_id}/messages")
async def hub_post_channel_message(
    channel_id: str,
    body: PostChannelMessageRequest,
    token: Token | None = Depends(require_capability("hub_messaging")),
):
    channel = get_channel(channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail=f"Channel '{channel_id}' not found")

    # Session-token path: override from_participant with token identity and enforce membership.
    from_participant = body.from_participant
    if token is not None:
        # Derive participant name from session_name (the token's task maps to a session)
        from .router import get_task as _get_task
        task = _get_task(token.task_id) if token.task_id else None
        session_name = task.session_name if task else None
        member_names = {p.name for p in channel.participants}
        member_sessions = {p.session_name for p in channel.participants if p.session_name}
        # Accept if the token's agent name or session name matches a participant
        identity = session_name or token.agent_name
        if identity not in member_names and (not session_name or session_name not in member_sessions):
            raise HTTPException(
                status_code=403,
                detail=f"Session '{identity}' is not a member of channel '{channel_id}'",
            )
        # Override the claimed from_participant with the token's verified identity
        from_participant = identity

    try:
        msg = channel_post_message(
            channel_id,
            from_participant=from_participant,
            content=body.content,
            summary=body.summary,
            addressed_to=body.addressed_to,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return msg.model_dump()


@app.post("/api/hub/channels/{channel_id}/complete")
async def hub_complete_channel(
    channel_id: str,
    body: CompleteChannelRequest,
    _key=Depends(require_api_key),
):
    channel = get_channel(channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail=f"Channel '{channel_id}' not found")
    try:
        updated = complete_channel(channel_id, by=body.by, reason=body.reason)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return updated.model_dump()


@app.get("/api/hub/channels/{channel_id}/stream")
async def hub_channel_stream(channel_id: str, request: Request, _key=Depends(require_api_key)):
    """SSE stream for a single channel — delivers new messages in real-time."""
    channel = get_channel(channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail=f"Channel '{channel_id}' not found")

    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    _channel_queues.setdefault(channel_id, set()).add(q)

    async def event_generator():
        try:
            yield {"data": "connected"}
            while True:
                data = await q.get()
                yield {"data": data}
        except asyncio.CancelledError:
            pass
        finally:
            _channel_queues.get(channel_id, set()).discard(q)

    return EventSourceResponse(event_generator())


# ---------------------------------------------------------------------------
# Playbooks
# ---------------------------------------------------------------------------

_playbook_queues: dict[str, set[asyncio.Queue]] = {}


def _broadcast_to_playbook(playbook_id: str, data: str) -> None:
    for q in list(_playbook_queues.get(playbook_id, set())):
        try:
            q.put_nowait(data)
        except asyncio.QueueFull:
            pass


@app.post("/api/hub/playbooks")
async def hub_create_playbook(body: CreatePlaybookRequest, _key=Depends(require_api_key)):
    pb = create_playbook(name=body.name, markdown=body.markdown, workspace_profile=body.workspace_profile, runner=body.runner)
    return pb.model_dump()


@app.get("/api/hub/playbooks")
async def hub_list_playbooks(profile: str | None = None, _key=Depends(require_api_key)):
    return [pb.model_dump() for pb in list_playbooks(profile=profile)]


@app.get("/api/hub/playbooks/{playbook_id}")
async def hub_get_playbook(playbook_id: str, _key=Depends(require_api_key)):
    pb = get_playbook(playbook_id)
    if not pb:
        raise HTTPException(status_code=404, detail=f"Playbook '{playbook_id}' not found")
    return pb.model_dump()


@app.patch("/api/hub/playbooks/{playbook_id}")
async def hub_update_playbook(playbook_id: str, body: UpdatePlaybookRequest, _key=Depends(require_api_key)):
    try:
        from .playbooks import _UNSET as _PB_UNSET
        runner_arg = body.runner if body.model_fields_set and "runner" in body.model_fields_set else _PB_UNSET
        pb = update_playbook(playbook_id, name=body.name, markdown=body.markdown, runner=runner_arg)
        return pb.model_dump()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/hub/playbooks/{playbook_id}")
async def hub_delete_playbook(playbook_id: str, _key=Depends(require_api_key)):
    try:
        delete_playbook(playbook_id)
        _broadcast_sse(json.dumps({"action": "playbook.deleted", "playbook_id": playbook_id}))
        return {"ok": True}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/hub/playbooks/{playbook_id}/run")
async def hub_run_playbook(playbook_id: str, request: Request, _key=Depends(require_api_key)):
    try:
        profile = None
        runner = None
        try:
            body = await request.json()
            if isinstance(body, dict):
                profile = body.get("workspace_profile")
                runner = body.get("runner")
        except Exception:
            pass
        pb = await run_playbook(playbook_id, workspace_profile=profile, runner=runner)
        return pb.model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/hub/playbooks/{playbook_id}/cancel")
async def hub_cancel_playbook(playbook_id: str, _key=Depends(require_api_key)):
    pb = get_playbook(playbook_id)
    if not pb:
        raise HTTPException(status_code=404, detail=f"Playbook '{playbook_id}' not found")
    cancel_playbook(playbook_id)
    return {"ok": True}


@app.get("/api/hub/playbooks/{playbook_id}/stream")
async def hub_playbook_stream(playbook_id: str, request: Request, _key=Depends(require_api_key)):
    """SSE stream for a single playbook — delivers task progress events in real-time."""
    pb = get_playbook(playbook_id)
    if not pb:
        raise HTTPException(status_code=404, detail=f"Playbook '{playbook_id}' not found")

    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    _playbook_queues.setdefault(playbook_id, set()).add(q)

    # Wire this playbook's events to the per-playbook queue
    def _on_playbook_event(event: str, data: object) -> None:
        pid = None
        if isinstance(data, dict):
            pid = data.get("playbook_id")
        elif hasattr(data, "id"):
            pid = data.id  # type: ignore[union-attr]
        if pid == playbook_id:
            payload = json.dumps({
                "event": event,
                "data": data.model_dump() if hasattr(data, "model_dump") else data,
            })
            _broadcast_to_playbook(playbook_id, payload)

    playbook_on_event(_on_playbook_event)

    async def event_generator():
        try:
            yield {"data": "connected"}
            while True:
                data = await q.get()
                yield {"data": data}
        except asyncio.CancelledError:
            pass
        finally:
            _playbook_queues.get(playbook_id, set()).discard(q)

    return EventSourceResponse(event_generator())


# ---------------------------------------------------------------------------
# LangFuse observability proxy
# ---------------------------------------------------------------------------


async def _langfuse_op(operation_fn, *args, **kwargs):
    """Run a LangFuse operation respecting the configured mode."""
    mode = settings.langfuse.mode
    if mode == "off":
        raise HTTPException(status_code=503, detail="LangFuse integration is disabled")
    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: operation_fn(*args, **kwargs))
    except LangfuseError as exc:
        if mode == "enforce":
            raise HTTPException(status_code=502, detail=str(exc))
        log.warning("langfuse.operation_failed", metadata={"error": str(exc)})
        return None
    except Exception as exc:
        if mode == "enforce":
            raise HTTPException(status_code=502, detail=str(exc))
        log.warning("langfuse.operation_failed", metadata={"error": str(exc)})
        return None


@app.get("/api/langfuse/health")
async def api_langfuse_health():
    """Check LangFuse connectivity."""
    mode = settings.langfuse.mode
    if mode == "off":
        return {"healthy": False, "mode": "off", "url": None, "detail": "LangFuse integration is disabled"}
    loop = asyncio.get_running_loop()
    healthy = await loop.run_in_executor(None, langfuse_health_check)
    return {"healthy": healthy, "mode": mode, "url": settings.langfuse.base_url, "detail": None}


@app.get("/api/qdrant/health")
async def api_qdrant_health():
    """Check Qdrant connectivity."""
    if not settings.qdrant.enabled:
        return {"healthy": False, "mode": None, "url": None, "detail": "Qdrant integration is disabled"}

    try:
        import httpx

        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{settings.qdrant.url}/")
            healthy = response.status_code == 200
            return {"healthy": healthy, "mode": None, "url": settings.qdrant.url, "detail": None}
    except Exception as e:
        return {"healthy": False, "mode": None, "url": settings.qdrant.url, "detail": str(e)}


@app.get("/api/langfuse/sessions/{session_name}/traces")
async def api_langfuse_session_traces(
    session_name: str, limit: int = Query(default=50), _key=Depends(require_api_key)
):
    """List traces for a container session."""
    result = await _langfuse_op(langfuse_list_traces, session_name, limit)
    if result is None:
        return []
    return [
        {
            "id": t.id,
            "name": t.name,
            "session_id": t.session_id,
            "timestamp": t.timestamp,
            "status": t.status,
            "input": t.input,
            "output": t.output,
        }
        for t in result
    ]


@app.get("/api/langfuse/sessions/{session_name}/summary")
async def api_langfuse_session_summary(session_name: str, _key=Depends(require_api_key)):
    """Trace count, error count, and tool breakdown for a session."""
    result = await _langfuse_op(get_session_traces_summary, session_name)
    if result is None:
        return {
            "session_id": session_name,
            "total_traces": 0,
            "total_observations": 0,
            "error_count": 0,
            "tool_counts": {},
        }
    return {
        "session_id": result.session_id,
        "total_traces": result.total_traces,
        "total_observations": result.total_observations,
        "error_count": result.error_count,
        "tool_counts": result.tool_counts,
    }


@app.get("/api/langfuse/traces/{trace_id}")
async def api_langfuse_trace_detail(trace_id: str, _key=Depends(require_api_key)):
    """Single trace detail with observations."""
    result = await _langfuse_op(langfuse_get_trace, trace_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Trace not available")
    trace, observations = result
    return {
        "trace": {
            "id": trace.id,
            "name": trace.name,
            "session_id": trace.session_id,
            "timestamp": trace.timestamp,
            "status": trace.status,
            "input": trace.input,
            "output": trace.output,
        },
        "observations": [
            {
                "id": o.id,
                "trace_id": o.trace_id,
                "name": o.name,
                "type": o.type,
                "start_time": o.start_time,
                "end_time": o.end_time,
                "status": o.status,
                "level": o.level,
            }
            for o in observations
        ],
    }


# ---------------------------------------------------------------------------
# Ollama LLM proxy
# ---------------------------------------------------------------------------


@app.get("/api/ollama/health")
async def api_ollama_health():
    """Check Ollama connectivity — picks the best healthy pool instance."""
    pool = get_pool()
    inst = pool.pick()
    if inst is None:
        return {"healthy": False, "host": settings.ollama.host}
    loop = asyncio.get_running_loop()
    healthy = await loop.run_in_executor(None, lambda: ollama_health_check(inst.url))
    return {"healthy": healthy, "host": inst.url, "runner": inst.runner_name}


@app.get("/api/ollama/instances")
async def api_ollama_instances(_key=Depends(require_api_key)):
    """List all Ollama instances in the pool with their health and load."""
    instances = get_pool().all_instances()
    return {
        "instances": [
            {
                "runner": i.runner_name,
                "url": i.url,
                "healthy": i.healthy,
                "models": i.models,
                "in_flight": i.in_flight,
                "last_checked": i.last_checked,
            }
            for i in instances
        ]
    }


@app.post("/api/ollama/chat")
async def api_ollama_chat(body: OllamaChatRequest, _key=Depends(require_api_key)):
    """Proxy a chat completion request to the best available Ollama instance."""
    pool = get_pool()
    inst = pool.pick()
    if inst is None:
        raise HTTPException(status_code=503, detail="no Ollama instances available")
    pool.acquire(inst)
    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, lambda: ollama_chat(body.messages, body.model, inst.url)
        )
        return {
            "model": result.model,
            "message": {"role": result.message.role, "content": result.message.content},
            "total_duration": result.total_duration,
            "eval_count": result.eval_count,
            "runner": inst.runner_name,
        }
    except OllamaError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    finally:
        pool.release(inst)


@app.get("/api/ollama/models")
async def api_ollama_models(_key=Depends(require_api_key)):
    """List models available on the best healthy Ollama instance."""
    pool = get_pool()
    inst = pool.pick()
    if inst is None:
        raise HTTPException(status_code=503, detail="no Ollama instances available")
    try:
        loop = asyncio.get_running_loop()
        models = await loop.run_in_executor(None, lambda: ollama_list_models(inst.url))
        return {
            "models": [
                {
                    "name": m.name,
                    "size": m.size,
                    "modified_at": m.modified_at,
                    "digest": m.digest,
                }
                for m in models
            ],
            "runner": inst.runner_name,
        }
    except OllamaError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/api/ollama/pull")
async def api_ollama_pull(body: OllamaPullRequest, _key=Depends(require_api_key)):
    """Pull a model on the best healthy Ollama instance."""
    pool = get_pool()
    inst = pool.pick()
    if inst is None:
        raise HTTPException(status_code=503, detail="no Ollama instances available")
    pool.acquire(inst)
    try:
        loop = asyncio.get_running_loop()
        status = await loop.run_in_executor(None, lambda: ollama_pull_model(body.name, inst.url))
        return {"status": status, "model": body.name, "runner": inst.runner_name}
    except OllamaError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    finally:
        pool.release(inst)


@app.delete("/api/ollama/models/{name:path}")
async def api_ollama_delete_model(name: str, _key=Depends(require_api_key)):
    """Delete a model from the best healthy Ollama instance."""
    pool = get_pool()
    inst = pool.pick()
    if inst is None:
        raise HTTPException(status_code=503, detail="no Ollama instances available")
    try:
        loop = asyncio.get_running_loop()
        status = await loop.run_in_executor(None, lambda: ollama_delete_model(name, inst.url))
        return {"status": status, "model": name, "runner": inst.runner_name}
    except OllamaError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


# ---------------------------------------------------------------------------
# Credential proxy endpoints (for remote Docker mode)
# ---------------------------------------------------------------------------


@app.get("/api/credentials/aws-token")
@limiter.limit("30/minute")
async def api_aws_token(request: Request, _token=Depends(require_token)):
    """Return fresh AWS credentials in credential_process format.

    Called automatically by the AWS SDK inside containers when credentials
    expire. Fetches live credentials from the host's boto3 session so AWS SSO
    tokens are always current without any bind mount.
    """
    try:
        import boto3  # type: ignore[import]

        session = boto3.Session()
        creds = session.get_credentials()
        if creds is None:
            raise HTTPException(status_code=503, detail="No AWS credentials available on host")
        frozen = creds.get_frozen_credentials()
        expiry = getattr(frozen, "_expiry_time", None)
        result: dict[str, Any] = {
            "Version": 1,
            "AccessKeyId": frozen.access_key,
            "SecretAccessKey": frozen.secret_key,
        }
        if frozen.token:
            result["SessionToken"] = frozen.token
        if expiry:
            result["Expiration"] = expiry.isoformat()
        return result
    except HTTPException:
        raise
    except ImportError:
        raise HTTPException(status_code=503, detail="boto3 not installed on host")
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Failed to fetch AWS credentials: {exc}")


@app.websocket("/api/credentials/ssh-agent")
async def api_ssh_agent_relay(websocket):
    """WebSocket relay for SSH agent forwarding to remote Docker containers.

    Bridges the host SSH agent socket to a WebSocket connection so containers
    on remote Docker daemons can use SSH keys without copying private keys.
    """
    import asyncio

    from fastapi import WebSocket
    from fastapi.websockets import WebSocketState

    ws: WebSocket = websocket
    await ws.accept()

    ssh_sock = os.environ.get("SSH_AUTH_SOCK")
    if not ssh_sock:
        await ws.close(1011, "SSH_AUTH_SOCK not available on server")
        return

    try:
        reader, writer = await asyncio.open_unix_connection(ssh_sock)
    except Exception as exc:
        log.warning("ssh_relay.connect_failed", metadata={"reason": str(exc)})
        await ws.close(1011, f"Cannot connect to SSH agent: {exc}")
        return

    async def forward_in() -> None:
        """WebSocket → SSH agent."""
        try:
            async for data in ws.iter_bytes():
                writer.write(data)
                await writer.drain()
        except Exception as exc:
            log.debug("ssh_relay.forward_in_closed", metadata={"reason": str(exc)})

    async def forward_out() -> None:
        """SSH agent → WebSocket."""
        try:
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    break
                if ws.client_state == WebSocketState.CONNECTED:
                    await ws.send_bytes(chunk)
        except Exception as exc:
            log.debug("ssh_relay.forward_out_closed", metadata={"reason": str(exc)})

    try:
        await asyncio.gather(forward_in(), forward_out())
    finally:
        writer.close()
        if ws.client_state == WebSocketState.CONNECTED:
            await ws.close()


@app.post("/api/sessions/{name}/push-config")
@limiter.limit("10/minute")
async def api_push_config(
    request: Request,
    name: str = Depends(validated_session_name),
    _key=Depends(require_api_key),
):
    """Re-inject translated ~/.claude config bundle into a running container.

    Useful when the user updates plugins, skills, or settings mid-session
    without reprovisioning the container.
    """
    from .bundle import build_config_bundle
    from .backends import create_backend
    from .lifecycle import get_session

    ctx = get_session(name)
    if ctx is None:
        # Try to find by container name prefix
        from .lifecycle import _sessions

        for sess_name, sess_ctx in _sessions.items():
            if sess_ctx.container_name == name or sess_name == name:
                ctx = sess_ctx
                break

    if ctx is None:
        raise HTTPException(status_code=404, detail=f"Session '{name}' not found")

    try:
        bundle = build_config_bundle(
            workspace_home=ctx.workspace_home,
            path_map=settings.path_map or None,
        )
        docker_backend = create_backend("docker")
        await docker_backend.inject_config_bundle(ctx, bundle)
        _audit_log(request, "session.push_config", session_name=name, success=True)
        return {"success": True, "session": name}
    except Exception as exc:
        _audit_log(request, "session.push_config", session_name=name, success=False, error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))



# ---------------------------------------------------------------------------
# SPA: serve built dashboard (must be last)
# ---------------------------------------------------------------------------


@app.get("/api/info")
async def api_info():
    """Return API version and basic status. Used as a lightweight health check."""
    return {
        "version": "0.10.2",
        "status": "ok",
    }


# ---------------------------------------------------------------------------
# History and audit
# ---------------------------------------------------------------------------


@app.get("/api/sessions/history", dependencies=[Depends(require_api_key)])
async def api_session_history(
    limit: int = 100,
    offset: int = 0,
    runner: str | None = None,
):
    """Return stopped/recycled sessions in reverse-chronological order."""
    from .store import query_session_history
    return await asyncio.to_thread(query_session_history, limit, offset, runner)


@app.get("/api/audit", dependencies=[Depends(require_api_key)])
async def api_audit_log(
    limit: int = 200,
    offset: int = 0,
    event: str | None = None,
    session: str | None = None,
):
    """Return audit log entries in reverse-chronological order."""
    from .store import query_audit_log
    return await asyncio.to_thread(query_audit_log, limit, offset, event, session)


# ---------------------------------------------------------------------------
# Profile image registry
# ---------------------------------------------------------------------------


@app.get("/api/profiles/{name}/image/status", dependencies=[Depends(require_api_key)])
async def profile_image_status(name: str):
    """Check whether a profile image exists in the configured registry.

    Returns the image tag, digest, and whether the registry is configured.
    The Wails app uses this to show image build status per profile.
    """
    registry = settings.profile_image_tag
    if not registry:
        return {"configured": False, "profile": name, "exists": False, "tag": None, "digest": None}

    tag = f"{registry}/brainbox-profile:{name}"
    try:
        import httpx
        auth = None
        username = settings.registry_username
        password = settings.registry_password.get_secret_value() if settings.registry_password else ""
        if username:
            auth = (username, password)

        # Try HTTPS first, fall back to HTTP (registry may be http-only behind a proxy)
        last_error = ""
        manifest_accept = (
            "application/vnd.oci.image.manifest.v1+json,"
            "application/vnd.oci.image.index.v1+json,"
            "application/vnd.docker.distribution.manifest.v2+json,"
            "application/vnd.docker.distribution.manifest.list.v2+json"
        )
        for scheme in ("https", "http"):
            manifest_url = f"{scheme}://{registry}/v2/brainbox-profile/manifests/{name}"
            try:
                async with httpx.AsyncClient(verify=False, follow_redirects=True) as client:
                    resp = await client.head(
                        manifest_url,
                        auth=auth,
                        headers={"Accept": manifest_accept},
                        timeout=5,
                    )
                if resp.status_code < 300:
                    digest = resp.headers.get("Docker-Content-Digest", "")
                    built_at: str | None = None
                    try:
                        async with httpx.AsyncClient(verify=False, follow_redirects=True) as client:
                            mresp = await client.get(
                                manifest_url,
                                auth=auth,
                                headers={"Accept": manifest_accept},
                                timeout=5,
                            )
                            mdata = mresp.json()
                            # If registry returned an index/list, follow the first entry
                            if "manifests" in mdata and "config" not in mdata:
                                sub = mdata["manifests"][0] if mdata["manifests"] else {}
                                sub_digest = sub.get("digest", "")
                                if sub_digest:
                                    sub_url = f"{scheme}://{registry}/v2/brainbox-profile/manifests/{sub_digest}"
                                    sresp = await client.get(
                                        sub_url,
                                        auth=auth,
                                        headers={"Accept": "application/vnd.oci.image.manifest.v1+json"},
                                        timeout=5,
                                    )
                                    mdata = sresp.json()
                            config_digest = mdata.get("config", {}).get("digest", "")
                            if config_digest:
                                blobs_url = f"{scheme}://{registry}/v2/brainbox-profile/blobs/{config_digest}"
                                cresp = await client.get(blobs_url, auth=auth, timeout=5)
                                built_at = cresp.json().get("created")
                    except Exception:
                        pass
                    return {"configured": True, "profile": name, "exists": True, "tag": tag, "digest": digest, "built_at": built_at}
                last_error = f"HTTP {resp.status_code}"
                break  # got a response from this scheme; no point trying http fallback
            except Exception as exc:
                last_error = str(exc)
                continue
        return {"configured": True, "profile": name, "exists": False, "tag": tag, "digest": None, "error": last_error}
    except Exception as exc:
        return {"configured": True, "profile": name, "exists": False, "tag": tag, "digest": None, "error": str(exc)}


if _dashboard_dist.is_dir():
    # Serve static assets (JS, CSS, etc.)
    app.mount("/assets", StaticFiles(directory=str(_dashboard_dist / "assets")), name="assets")

    # SPA fallback: serve index.html for non-API routes; return JSON 404 for unknown /api/* paths
    @app.get("/{path:path}")
    async def spa_fallback(path: str):
        # Unknown /api/* paths get a JSON 404 so callers can distinguish API errors from HTML
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail=f"API endpoint not found: /{path}")
        # Try to serve exact file first (e.g. favicon.ico)
        file = _dashboard_dist / path
        if path and file.is_file():
            return FileResponse(file)
        return FileResponse(_dashboard_dist / "index.html")

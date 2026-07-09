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
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any

import docker
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
    issue_gateway_token,
    list_agents,
    list_tokens,
    revoke_token,
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
from . import agent_store, event_match, event_rules
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
    achat as ollama_chat,
    adelete_model as ollama_delete_model,
    ahealth_check as ollama_health_check,
    alist_models as ollama_list_models,
    apull_model as ollama_pull_model,
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

    # MCP gateway (#152): seed the server registry from the catalog. New servers
    # are added; existing toggles are preserved. First run enables the legacy
    # CL_GATEWAY__SERVERS set so existing deployments carry forward.
    try:
        gateway_catalog.seed_gateway_servers(default_enabled=settings.gateway.servers)
    except Exception as exc:
        log.warning("gateway.seed_failed", metadata={"reason": str(exc)})

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

    # Forward hub task lifecycle into the agent event bus (P1: in-process producer).
    def _on_hub_task_event(event: str, task: object) -> None:
        try:
            envelope = agent_store.envelope_from_hub_task(event, task)
            agent_store.ingest(envelope)
        except Exception as exc:
            log.warning("agent_bus.ingest_failed", metadata={"event": event, "reason": str(exc)})

    on_event(_on_hub_task_event)

    # Widen durable bus coverage: playbook + channel lifecycle events also
    # become envelopes in agent_events so the rules consumer sees them.
    # Converters returning None (e.g. channel.message) are skipped; the SSE
    # listeners below are untouched.
    def _ingest_converted(converter):
        def _listener(event: str, data: object) -> None:
            try:
                env = converter(event, data)
                if env is not None:
                    agent_store.ingest(env)
            except Exception as exc:
                log.warning("agent_bus.ingest_failed", metadata={"event": event, "reason": str(exc)})
        return _listener

    playbook_on_event(_ingest_converted(agent_store.envelope_from_playbook))
    channel_on_event(_ingest_converted(agent_store.envelope_from_channel))

    # Forward every successful ingest into the SSE bus as a unified 'agent.event'.
    def _on_agent_envelope(env: object) -> None:
        try:
            _broadcast_sse(json.dumps({
                "event": "agent.event",
                "data": env.model_dump() if hasattr(env, "model_dump") else env,
            }))
        except Exception:
            pass

    agent_store.on_event(_on_agent_envelope)

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

    # MCP gateway (ADR-002): run the streamable-HTTP session manager for the
    # app's lifetime. Entered + exited in this same lifespan task (the SDK's
    # anyio cancel scopes require same-task enter/exit).
    await _gateway_exit_stack.enter_async_context(_gateway_session_manager.run())

    log.info("api.started", metadata={"port": settings.api_port})
    yield

    await _gateway_pool.aclose()
    await _gateway_exit_stack.aclose()

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

    from . import store

    store.close_pool()


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

# A2A (Agent-to-Agent) protocol façade. Registered here — well before the SPA
# catch-all (`/{path:path}`) — so /a2a/* and the nested /.well-known/* resolve.
from . import a2a  # noqa: E402

app.include_router(a2a.router)

# MCP gateway (ADR-002): a streamable-HTTP MCP endpoint at /gateway/mcp,
# scoped per profile + token. The session manager is driven by the lifespan.
from . import gateway_catalog, gateway_http, gateway_server  # noqa: E402
from .gateway_pool import GatewayPool  # noqa: E402

_gateway_pool = GatewayPool()
# Downstream servers are resolved per-request from the DB-backed registry
# (#152): the catalog file holds definitions, the gateway_servers table holds
# which are enabled. Passing the resolver (not a static list) lets toggles take
# effect live. The table is seeded from the catalog at startup (see lifespan).
_gateway_mcp_server = gateway_server.build_gateway_server(
    _gateway_pool, gateway_catalog.resolve_enabled_specs
)
_gateway_subapp, _gateway_session_manager = gateway_http.build_gateway_subapp(
    _gateway_mcp_server, gateway_server.BrainboxTokenVerifier()
)
_gateway_exit_stack = AsyncExitStack()
app.mount("/gateway", _gateway_subapp)


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


async def _curl_http_request(
    method: str, host: str, port: int, path: str, headers: dict, body: bytes,
    *, timeout: float = 10.0,
) -> tuple[int, list[tuple[str, str]], bytes]:
    """One HTTP request via a curl subprocess (headers + raw body preserved).

    Deliberately a curl subprocess — NOT httpx and NOT stdlib http.client: the
    long-running daemon on macOS/Python 3.14 hits spurious OSError 65
    ('No route to host') on Python-created sockets to LAN destinations.
    Live-verified against a runner at 192.168.87.101: stdlib http.client got
    errno 65 from inside the daemon while curl from the same host succeeded.
    Runner-hosted sessions live at LAN IPs, so proxied asset/token requests
    were intermittently 502ing → ttyd's page loaded blank. curl subprocesses
    are the one proven-reliable path (ollama.py has run them for weeks). See
    the httpx/macOS known issue in CLAUDE.md.
    """
    import tempfile

    with tempfile.NamedTemporaryFile(prefix="bb-term-hdr-", suffix=".txt") as hdr_file:
        args = ["curl", "-sS", "--max-time", str(timeout), "-D", hdr_file.name, "-o", "-"]
        if method == "HEAD":
            args.append("--head")
        elif method != "GET":
            args += ["-X", method]
        for k, v in headers.items():
            args += ["-H", f"{k}: {v}"]
        if body:
            args += ["--data-binary", "@-"]
        args.append(f"http://{host}:{port}{path}")

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE if body else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=body or None), timeout=timeout + 5
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
        raw_headers = hdr_file.read().decode("utf-8", errors="replace")

    # -D can hold several header blocks (e.g. '100 Continue'); use the last.
    blocks = [b for b in raw_headers.strip().split("\r\n\r\n") if b.strip()]
    lines = blocks[-1].split("\r\n") if blocks else []
    status = 502
    resp_headers: list[tuple[str, str]] = []
    if lines and lines[0].startswith("HTTP/"):
        try:
            status = int(lines[0].split()[1])
        except (IndexError, ValueError):
            pass
    for line in lines[1:]:
        name, sep, value = line.partition(":")
        if sep:
            resp_headers.append((name.strip(), value.strip()))
    return (status, resp_headers, stdout)


@app.api_route(
    "/t/{session_name}/{path:path}",
    methods=["GET", "HEAD", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    include_in_schema=False,
)
async def terminal_proxy_http(session_name: str, path: str, request: Request):
    """Reverse-proxy HTTP requests (ttyd assets) to the session's container port."""
    log.info("terminal.proxy_request", metadata={"session": session_name, "path": path, "method": request.method})
    endpoint = _session_endpoint(session_name)
    if endpoint is None:
        raise HTTPException(404, f"Session '{session_name}' not found or not running")
    host, port, has_base_path = endpoint

    if has_base_path:
        target_path = f"/t/{session_name}/{path}"
    else:
        target_path = f"/{path}"
    if request.url.query:
        target_path += f"?{request.url.query}"

    # Drop hop-by-hop headers before forwarding; force identity encoding so
    # the upstream body passes through without decompression surprises.
    skip = {"host", "connection", "te", "trailers", "transfer-encoding", "upgrade"}
    fwd_headers = {k: v for k, v in request.headers.items() if k.lower() not in skip}
    fwd_headers["accept-encoding"] = "identity"

    import asyncio
    body = await request.body()
    # On the initial page load (empty path) retry for up to ~30 s so that
    # runner-hosted sessions have time for ttyd to bind inside the container.
    # Asset/token paths get 3 fast attempts — errno-65 blips are transient, but
    # a single failed /token is what blanks the terminal, so one retry matters.
    max_attempts = 15 if not path else 3
    last_err: Exception | None = None
    for attempt in range(max_attempts):
        try:
            status, resp_headers, content = await _curl_http_request(
                request.method, host, port, target_path, fwd_headers, body
            )
            skip_resp = {"transfer-encoding", "connection", "content-encoding", "content-length"}
            return Response(
                content=content,
                status_code=status,
                headers={k: v for k, v in resp_headers if k.lower() not in skip_resp},
            )
        except OSError as exc:
            last_err = exc
            if attempt + 1 < max_attempts:
                await asyncio.sleep(2)
                continue
    log.warning(
        "terminal.proxy_unreachable",
        metadata={"session": session_name, "host": host, "port": port, "error": str(last_err)},
    )
    # An HTML body so the failure is visible in the terminal iframe (a JSON 502
    # renders as a blank white page) and retries itself while ttyd comes up.
    return Response(
        content=(
            "<!doctype html><meta http-equiv='refresh' content='3'>"
            "<body style='background:#111;color:#ccc;font:14px monospace;padding:2em'>"
            f"terminal for <b>{session_name}</b> not reachable at {host}:{port} — retrying…</body>"
        ),
        status_code=502,
        media_type="text/html",
    )


@app.websocket("/t/{session_name}/ws")
async def terminal_proxy_ws(session_name: str, websocket: WebSocket):
    """Bidirectional WebSocket relay between client and session's ttyd.

    The upstream leg is a WebSocket-over-nc relay (terminal_relay.NcWebSocket),
    NOT a Python-socket client: on macOS 26 the daemon's Python process is
    denied Local Network access (TCC), so every in-process socket to a LAN
    runner gets OSError 65. Apple-signed subprocesses (curl for HTTP, nc here)
    are exempt — the same pattern as _curl_http_request and ollama.py.

    Forwards the 'tty' subprotocol and both text and binary frames. Tries the
    base-path URL first (sessions started after --base-path was added), then
    falls back to the root /ws path (legacy sessions).
    """
    import shutil

    from fastapi.websockets import WebSocketState

    from .terminal_relay import OP_TEXT, NcWebSocket

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
        candidate_paths = [f"/t/{session_name}/ws", "/ws"]
    else:
        candidate_paths = ["/ws", f"/t/{session_name}/ws"]

    nc = "/usr/bin/nc" if os.path.exists("/usr/bin/nc") else (shutil.which("nc") or "nc")
    backend: NcWebSocket | None = None
    ws_errors: dict[str, str] = {}
    for ws_path in candidate_paths:
        try:
            backend = await NcWebSocket.connect(
                host, port, ws_path, subprotocols=subprotocols, nc_path=nc
            )
            break
        except Exception as exc:
            ws_errors[ws_path] = str(exc) or type(exc).__name__
            continue

    if backend is None:
        # Log why — a silently-rejected WS is indistinguishable from a blank
        # terminal page in the field (this masked the runner white-page bug).
        log.warning(
            "terminal.ws_connect_failed",
            metadata={"session": session_name, "host": host, "port": port, "errors": ws_errors},
        )
        await _reject(1011)
        return

    try:
        negotiated = backend.subprotocol or subprotocols[0]
        await websocket.accept(subprotocol=negotiated)

        async def to_backend():
            try:
                while True:
                    msg = await websocket.receive()
                    if msg.get("type") == "websocket.disconnect":
                        break
                    if msg.get("bytes"):
                        await backend.send_bytes(msg["bytes"])
                    elif msg.get("text"):
                        await backend.send_text(msg["text"])
            except Exception:
                pass

        async def to_client():
            try:
                while True:
                    frame = await backend.recv()
                    if frame is None:
                        break
                    opcode, payload = frame
                    if opcode == OP_TEXT:
                        await websocket.send_text(payload.decode("utf-8", errors="replace"))
                    else:
                        await websocket.send_bytes(payload)
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


@app.post("/api/webhooks/github")
async def github_webhook(request: Request):
    """GitHub App webhook → fire a Loop run on the pr-review-loop template.

    HMAC-SHA256 signature verification via X-Hub-Signature-256. The repo
    allowlist (CL_GITHUB_LOOP_REPOS) can scope which repos trigger; empty
    list means accept any signed payload. Supported events:

      - pull_request.{opened, synchronize, reopened} → trigger
      - issue_comment.created with "/loop" in body  → operator opt-in

    Response shape:
      { "triggered": bool, "loop_id"?: str, "reason"?: str }

    Status codes:
      200 — triggered (loop_id returned) OR understood but no trigger (reason)
      401 — missing/invalid signature
      403 — repo not in allowlist
      404 — pr-review-loop template missing (operator hasn't installed it)
      500 — start_loop raised
    """
    from . import github_webhook as gh
    from . import loop_runner
    from .config import settings
    from .loop_template import TemplateError, load_template
    from .loops import HandoffEnvelope

    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not gh.verify_signature(body, signature, settings.github_webhook_secret):
        raise HTTPException(status_code=401, detail="invalid signature")

    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="malformed JSON body")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="payload must be an object")

    if not gh.allow_repo(payload, settings.github_loop_repos):
        raise HTTPException(status_code=403, detail="repo not in allowlist")

    event_type = request.headers.get("X-GitHub-Event", "")
    trigger = gh.extract_loop_trigger(event_type, payload)
    if trigger is None:
        return {"triggered": False, "reason": f"event {event_type!r} is not a loop trigger"}

    try:
        spec = load_template("pr-review-loop")
    except TemplateError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    envelope = HandoffEnvelope(artifact_refs=trigger)
    try:
        inst = await loop_runner.start_loop(spec, envelope)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"start_loop failed: {exc}")

    log.info(
        "webhook.github.loop_triggered",
        metadata={
            "loop_id": inst.id,
            "event": trigger.get("trigger_event"),
            "repo": trigger.get("repo"),
            "pr_number": trigger.get("pr_number"),
        },
    )
    return {"triggered": True, "loop_id": inst.id, "trigger": trigger}


@app.post("/api/webhooks/{key}")
async def webhook_trigger(key: str, request: Request):
    """Receive an inbound webhook and broadcast it to the SSE stream.

    The key in the URL path is the shared secret — anyone who knows it can
    fire this webhook.  Broadcasts action=webhook.trigger so the desktop app
    can route it to the automation engine.

    Declared AFTER the explicit /api/webhooks/github route so the GitHub
    path doesn't get captured here as key="github".
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


# ---------------------------------------------------------------------------
# Agent event bus (cross-machine envelopes)
# ---------------------------------------------------------------------------


@app.post("/api/agent_events")
async def agent_events_ingest(request: Request, _key=Depends(require_api_key)):
    """Ingest one or more envelopes into the cross-machine agent event bus.

    Body forms:
        - single envelope: { id, kind, title, ... }
        - batch:           { "events": [ {...}, {...} ] }

    Each envelope is upserted into `agent_state` (mutates current snapshot) and
    appended to `agent_events` (audit log). Successful ingest fans out to the
    SSE bus as a unified `agent.event` message.

    Returns: { ingested: N, ids: [...] }
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if isinstance(body, dict) and "events" in body:
        items = body["events"]
        if not isinstance(items, list):
            raise HTTPException(status_code=400, detail="'events' must be an array")
    elif isinstance(body, dict):
        items = [body]
    elif isinstance(body, list):
        items = body
    else:
        raise HTTPException(status_code=400, detail="Body must be an envelope or {events: [...]}")

    # Validate up front so schema errors become 422, not 500.
    # NB: ValidationError imported above is brainbox.validation; envelope validation
    # uses pydantic's ValidationError instead.
    from pydantic import ValidationError as _PydanticValidationError
    validated: list[agent_store.AgentEnvelope] = []
    for raw in items:
        try:
            validated.append(agent_store.AgentEnvelope(**raw) if isinstance(raw, dict) else raw)
        except _PydanticValidationError as exc:
            raise HTTPException(status_code=422, detail=f"Envelope validation: {exc}")

    results = []
    for env in validated:
        try:
            stored = await agent_store.async_ingest(env)
            results.append(stored.id)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Ingest failed: {exc}")

    return {"ingested": len(results), "ids": results}


@app.get("/api/agent_events")
async def agent_events_list(
    id: str | None = Query(None, description="Filter to events for this envelope id"),
    parent_id: str | None = Query(None, description="Filter to events whose parent matches"),
    limit: int = Query(200, ge=1, le=2000),
    _key=Depends(require_api_key),
):
    """List append-only audit log entries. Provide `id` or `parent_id` (or both)
    to scope to a single thing / family. Returns rows ordered by seq ascending
    (oldest first)."""
    rows = await asyncio.to_thread(
        agent_store.list_events,
        envelope_id=id,
        parent_id=parent_id,
        limit=limit,
    )
    return {"events": rows, "count": len(rows)}


@app.get("/api/agent_state")
async def agent_state_list(
    status: str | None = Query(None, description="Comma-separated statuses, e.g. failed,blocked"),
    workspace: str | None = Query(None),
    source: str | None = Query(None),
    parent_id: str | None = Query(None),
    limit: int = Query(200, ge=1, le=2000),
    _key=Depends(require_api_key),
):
    """Current-state snapshot of every envelope id known to the bus. Filterable
    by status (single or comma-list), workspace, source, parent_id."""
    status_filter: str | list[str] | None
    if status:
        parts = [s.strip() for s in status.split(",") if s.strip()]
        status_filter = parts if len(parts) > 1 else (parts[0] if parts else None)
    else:
        status_filter = None
    rows = await asyncio.to_thread(
        agent_store.list_state,
        status=status_filter,
        workspace=workspace,
        source=source,
        parent_id=parent_id,
        limit=limit,
    )
    return {"items": rows, "count": len(rows)}


@app.get("/api/agent_state/{envelope_id}")
async def agent_state_get(envelope_id: str, _key=Depends(require_api_key)):
    row = await asyncio.to_thread(agent_store.get_state, envelope_id)
    if not row:
        raise HTTPException(status_code=404, detail="Envelope not found")
    return row


# ---------------------------------------------------------------------------
# Event rules (EventBridge-style rules over the agent event bus)
# ---------------------------------------------------------------------------


def _validate_rule_body(body: dict, rule_id: str | None = None) -> event_rules.EventRule:
    """Build + validate an EventRule from a request body. Raises HTTPException."""
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Body must be a rule object")
    pattern = body.get("pattern")
    errors = event_match.validate_pattern(pattern)
    if errors:
        raise HTTPException(status_code=400, detail={"pattern_errors": errors})

    payload = {k: v for k, v in body.items() if k in (
        "name", "profile", "enabled", "description", "pattern", "actions",
    )}
    if rule_id:
        payload["id"] = rule_id
    from pydantic import ValidationError as _PydanticValidationError
    try:
        rule = event_rules.EventRule(**payload)
    except _PydanticValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Rule validation: {exc}")

    if not settings.rules.allow_run_script and any(
        a.type == "run_script" for a in rule.actions
    ):
        raise HTTPException(
            status_code=400,
            detail="run_script actions are disabled — set CL_RULES__ALLOW_RUN_SCRIPT=true "
                   "on the daemon to allow host-exec rule actions",
        )
    return rule


@app.get("/api/rules")
async def rules_list(
    profile: str | None = Query(None, description="Profile filter; includes global rules"),
    enabled: bool | None = Query(None),
    _key=Depends(require_api_key),
):
    rules = await asyncio.to_thread(event_rules.list_rules, profile, enabled)
    return {"rules": [r.model_dump() for r in rules], "count": len(rules)}


@app.post("/api/rules", status_code=201)
async def rules_create(request: Request, _key=Depends(require_api_key)):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    rule = _validate_rule_body(body)
    stored = await asyncio.to_thread(event_rules.upsert_rule, rule)
    return stored.model_dump()


@app.get("/api/rules/executions")
async def rules_executions_all(
    status: str | None = Query(None, description="e.g. dead for the DLQ view"),
    rule_id: str | None = Query(None),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    _key=Depends(require_api_key),
):
    rows = await asyncio.to_thread(
        lambda: event_rules.list_executions(
            rule_id=rule_id, status=status, limit=limit, offset=offset
        )
    )
    return {"executions": [e.model_dump() for e in rows], "count": len(rows)}


@app.post("/api/rules/test")
async def rules_test(request: Request, _key=Depends(require_api_key)):
    """Dry-run a pattern. Body: {pattern, event} matches one supplied event
    document; {pattern, sample: {limit}} matches against the most recent
    agent_events rows and returns which matched."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    pattern = body.get("pattern")
    errors = event_match.validate_pattern(pattern)
    if errors:
        return {"valid": False, "errors": errors}

    if "event" in body:
        event_doc = body["event"]
        if not isinstance(event_doc, dict):
            raise HTTPException(status_code=400, detail="'event' must be an object")
        return {"valid": True, "errors": [], "matched": event_match.matches(pattern, event_doc)}

    sample = body.get("sample") or {}
    limit = min(int(sample.get("limit", 50)), 500)

    def _sample_match():
        from .store import _conn
        with _conn() as c:
            rows = c.execute(
                "SELECT seq, id, ts, envelope FROM agent_events ORDER BY seq DESC LIMIT %s",
                (limit,),
            ).fetchall()
        matched = []
        for r in rows:
            doc = json.loads(r["envelope"])
            doc["seq"] = r["seq"]
            doc["ts"] = r["ts"]
            if event_match.matches(pattern, doc):
                matched.append({
                    "seq": r["seq"], "id": r["id"],
                    "type": doc.get("type"), "status": doc.get("status"), "ts": r["ts"],
                })
        return {"valid": True, "errors": [], "matches": matched, "scanned": len(rows)}

    return await asyncio.to_thread(_sample_match)


@app.post("/api/rules/executions/{execution_id}/retry")
async def rules_execution_retry(execution_id: int, _key=Depends(require_api_key)):
    """Requeue a dead/failed/throttled execution (DLQ retry)."""
    existing = await asyncio.to_thread(event_rules.get_execution, execution_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Execution not found")
    if existing.status in ("queued", "running", "ok"):
        raise HTTPException(
            status_code=409, detail=f"Execution is '{existing.status}' — not retryable"
        )
    requeued = await asyncio.to_thread(event_rules.requeue_execution, execution_id)
    if requeued is None:
        raise HTTPException(status_code=409, detail="Execution is no longer retryable")
    event_rules.notify()
    return requeued.model_dump()


@app.get("/api/rules/{rule_id}")
async def rules_get(rule_id: str, _key=Depends(require_api_key)):
    rule = await asyncio.to_thread(event_rules.get_rule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    return rule.model_dump()


@app.put("/api/rules/{rule_id}")
async def rules_update(rule_id: str, request: Request, _key=Depends(require_api_key)):
    existing = await asyncio.to_thread(event_rules.get_rule, rule_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    rule = _validate_rule_body(body, rule_id=rule_id)
    rule.created_at = existing.created_at
    rule.trigger_count = existing.trigger_count
    rule.last_triggered_at = existing.last_triggered_at
    stored = await asyncio.to_thread(event_rules.upsert_rule, rule)
    return stored.model_dump()


@app.delete("/api/rules/{rule_id}", status_code=204)
async def rules_delete(rule_id: str, _key=Depends(require_api_key)):
    deleted = await asyncio.to_thread(event_rules.delete_rule, rule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Rule not found")


@app.post("/api/rules/{rule_id}/enable")
async def rules_enable(rule_id: str, _key=Depends(require_api_key)):
    rule = await asyncio.to_thread(event_rules.set_rule_enabled, rule_id, True)
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"id": rule.id, "enabled": rule.enabled}


@app.post("/api/rules/{rule_id}/disable")
async def rules_disable(rule_id: str, _key=Depends(require_api_key)):
    rule = await asyncio.to_thread(event_rules.set_rule_enabled, rule_id, False)
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"id": rule.id, "enabled": rule.enabled}


@app.get("/api/rules/{rule_id}/executions")
async def rules_executions(
    rule_id: str,
    status: str | None = Query(None),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    _key=Depends(require_api_key),
):
    rows = await asyncio.to_thread(
        lambda: event_rules.list_executions(
            rule_id=rule_id, status=status, limit=limit, offset=offset
        )
    )
    return {"executions": [e.model_dump() for e in rows], "count": len(rows)}


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
    ollama_proxy_port = (
        int(body["ollama_proxy_port"]) if body.get("ollama_proxy_port") else None
    )
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
        ollama_proxy_port=ollama_proxy_port,
    )
    # If runner advertises Ollama and we know its proxy host+port, add it
    # to the pool. The runner authenticates incoming proxy requests with
    # brainbox's API key (shared secret) over HTTPS with a self-signed cert.
    if caps.get("ollama") and host and ollama_proxy_port:
        await get_pool().add_runner(
            name, host, ollama_proxy_port,
            api_key=load_or_create_key(),
            scheme="https",
            verify_tls=False,
        )
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
            # Durably mark inactive — popping _sessions alone left the active=1
            # row behind, so stopped runner sessions resurrected on every daemon
            # restart (load_runner_sessions_from_db). Mirrors recycle()'s
            # bookkeeping on the local path.
            from .store import async_insert_session_history, async_mark_session_inactive
            from .utils import now_ms as _nowms
            await async_mark_session_inactive(session_name, _nowms())
            await async_insert_session_history(ctx, "dashboard_stop")
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
            # Durably mark inactive — same contract as the stop path above;
            # without this, deleted runner sessions returned on every restart.
            from .store import async_insert_session_history, async_mark_session_inactive
            from .utils import now_ms as _nowms
            await async_mark_session_inactive(session_name, _nowms())
            await async_insert_session_history(ctx, "dashboard_delete")
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
        # Always issue a session token so the agent has a real identity — it
        # calls the hub/A2A API under its own token, not a stub. Capabilities
        # come from the role's agent definition, so only roles with task_submit
        # (supervisor, worker) can dispatch A2A. A hub Task is registered only
        # when a task description is provided (so it shows in the dashboard and
        # gets lifecycle management).
        from .registry import issue_session_token
        task_id = None
        tid = str(uuid.uuid4())
        role = body.role or "assistant"
        hub_token = issue_session_token(role, tid)  # None if role isn't a registered agent
        if body.task:
            from .router import _tasks
            from .utils import now_ms as _now_ms
            from .models import Task as HubTask, TaskStatus
            task_id = tid
            _tasks[tid] = HubTask(
                id=tid,
                description=body.task,
                agent_name=role,
                status=TaskStatus.RUNNING,
                created_at=_now_ms(),
                updated_at=_now_ms(),
                token_id=hub_token.token_id if hub_token else None,
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
            model_target=body.model_target,
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
    # Determine actor before doing the work so the recorded outcome reflects
    # whether this came from the human-key path or from the task's own token.
    actor = agent_store.ACTOR_USER if _is_api_key_valid(request) else None
    if actor is None:
        token = get_bearer_token(request)
        if not token:
            raise HTTPException(status_code=401, detail="Missing or invalid API key or Bearer token")
        if token.task_id != task_id:
            raise HTTPException(status_code=403, detail="Token is not the owner of this task")
        actor = f"agent:{token.agent_name}" if getattr(token, "agent_name", None) else agent_store.ACTOR_SYSTEM

    async def _do_cancel():
        return await cancel_task(task_id)

    try:
        task = await agent_store.arecord_action(
            target_id=f"hub-task:{task_id}",
            action_name="cancel",
            actor=actor,
            fn=_do_cancel,
        )
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
    healthy = await ollama_health_check(
        inst.url, headers=inst.request_headers(), verify=inst.verify_tls,
    )
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
        result = await ollama_chat(
            body.messages, body.model, inst.url,
            headers=inst.request_headers(), verify=inst.verify_tls,
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
        models = await ollama_list_models(
            inst.url, headers=inst.request_headers(), verify=inst.verify_tls,
        )
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
        status = await ollama_pull_model(
            body.name, inst.url, headers=inst.request_headers(), verify=inst.verify_tls,
        )
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
        status = await ollama_delete_model(
            name, inst.url, headers=inst.request_headers(), verify=inst.verify_tls,
        )
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


# ---------------------------------------------------------------------------
# Loops — manual trigger + monitor surface for the Phase B loop runner.
# Operators use these to start a pr-review-loop (or any other template) on
# a PR, watch it iterate, and cancel/escalate. Webhook-driven Phase C will
# call /api/loops/start from a HTTP handler later.
# ---------------------------------------------------------------------------


@app.get("/api/loops/templates", dependencies=[Depends(require_api_key)])
async def api_list_loop_templates():
    """Return the names of every Loop template visible to this brainbox install.
    User templates at ~/.config/phantom-ink/brainbox/loop-templates/ shadow
    built-in templates of the same name (loader picks user first)."""
    from .loop_template import list_templates

    return {"templates": list_templates()}


# Static template endpoints declared BEFORE the parameterized /{name} so
# FastAPI's first-match routing picks /schema, /validate, /{name}/dry-run
# correctly. Same pattern as the github-webhook-before-{key} route ordering.


@app.get("/api/loops/templates/schema", dependencies=[Depends(require_api_key)])
async def api_loop_template_schema():
    """Return the frontmatter contract for the markdown loop format.

    Drove a JSON-Schema-style YAML editor in the legacy format; with the
    markdown switch the editor lints via /validate instead. This shape
    is preserved for the AI Assist prompt builder and for a future
    schema-aware editor mode."""
    return {
        "frontmatter": {
            "required": ["name", "trigger", "max_iterations"],
            "optional": [
                "agent",
                "permissions",
                "budget_usd",
                "objective",
                "required_refs",
            ],
        },
        "sections": {
            "required": ["Role", "When to stop", "When to escalate"],
            "optional": ["Tools", "Notes"],
        },
        "permissions": ["inherit", "default", "strict"],
        "required_ref_types": ["int", "string", "sha"],
    }


@app.post("/api/loops/templates/validate", dependencies=[Depends(require_api_key)])
async def api_loop_template_validate(request: Request):
    """Validate raw template markdown without saving.

    Body: ``{"markdown": "<raw text>"}`` (legacy ``{"yaml": ...}`` is
    still accepted for one release to ease the frontend swap).
    Returns the structured error report from ``validate_markdown``.
    """
    from .loop_template import validate_markdown

    body = await request.json()
    raw = body.get("markdown")
    if raw is None:
        raw = body.get("yaml")  # legacy key
    if not isinstance(raw, str):
        raise HTTPException(status_code=400, detail="body.markdown must be a string")
    return validate_markdown(raw)


@app.post("/api/loops/templates/assist", dependencies=[Depends(require_api_key)])
async def api_loop_template_assist(request: Request):
    """AI-assisted YAML generation / refinement / explanation.

    Body shape:
      {
        "mode":         "generate" | "refine" | "explain",
        "prompt":       "<operator natural language>",
        "current_yaml": "<editor contents>",                // optional
        "selection":    {"start_line": N, "end_line": N}    // refine/explain
      }

    Returns the AssistResult shape from loop_assist.AssistResult.to_dict().

    Status codes:
      200 — produced output (may include warnings if retry budget was used)
      400 — bad body (missing prompt, invalid mode)
      502 — upstream session call failed (provisioning or query error)

    No API keys: the request is dispatched to an ephemeral brainbox
    session (see loop_assist module docstring). The session runs Claude
    Code under the operator's existing OAuth credentials.
    """
    from .loop_assist import AssistError, assist

    body = await request.json()
    mode = body.get("mode")
    prompt = body.get("prompt", "")
    if mode not in ("generate", "refine", "explain"):
        raise HTTPException(status_code=400, detail="mode must be generate, refine, or explain")
    if not prompt or not prompt.strip():
        raise HTTPException(status_code=400, detail="prompt is required")
    current_yaml = body.get("current_yaml") or None
    selection = body.get("selection") or None
    save_as = body.get("save_as") or None

    try:
        result = await assist(
            mode=mode,
            prompt=prompt,
            current_yaml=current_yaml,
            selection=selection,
            save_as=save_as,
        )
    except AssistError as exc:
        msg = str(exc)
        if "upstream" in msg or "session" in msg.lower():
            raise HTTPException(status_code=502, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
    return result.to_dict()


@app.post("/api/loops/templates/{name}/dry-run", dependencies=[Depends(require_api_key)])
async def api_loop_template_dry_run(name: str, request: Request):
    """Plan iteration 1 against a sample envelope without enqueueing.

    Body (optional): ``{"envelope": {...}}`` — defaults to empty envelope.
    Returns the dry-run plan — first-iteration target, convergence /
    metric / stop-condition evals against the sample envelope.
    """
    from .loop_template import TemplateError, build_dry_run_plan, load_template

    try:
        spec = load_template(name)
    except TemplateError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    body: dict[str, Any] = {}
    try:
        raw = await request.body()
        if raw:
            body = json.loads(raw)
    except Exception:
        pass
    envelope_data = body.get("envelope") if isinstance(body, dict) else None

    try:
        return build_dry_run_plan(spec, envelope_data)
    except TemplateError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/loops/templates/{name}", dependencies=[Depends(require_api_key)])
async def api_get_loop_template(name: str):
    """Return the raw YAML and metadata for one template."""
    from .loop_template import TemplateError, read_raw_template

    try:
        return read_raw_template(name)
    except TemplateError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.put("/api/loops/templates/{name}", dependencies=[Depends(require_api_key)])
async def api_put_loop_template(
    name: str, request: Request, fork: bool = False
):
    """Write a template to the user templates dir.

    Body: ``{"markdown": "<raw text>"}`` (legacy ``{"yaml": ...}`` is
    still accepted for one release).
    Query: ``?fork=true`` allows creating a user override of a built-in
    template of the same name. Without it, writing to a built-in name
    returns 409.
    """
    from .loop_template import TemplateError, write_user_template

    body = await request.json()
    raw = body.get("markdown")
    if raw is None:
        raw = body.get("yaml")  # legacy
    if not isinstance(raw, str):
        raise HTTPException(status_code=400, detail="body.markdown must be a string")

    try:
        return write_user_template(name, raw, fork_from_builtin=fork)
    except TemplateError as exc:
        msg = str(exc)
        if "fork=true" in msg:
            raise HTTPException(status_code=409, detail=msg)
        if "invalid template name" in msg:
            raise HTTPException(status_code=400, detail=msg)
        if "frontmatter" in msg or "section" in msg or "slug" in msg:
            raise HTTPException(status_code=422, detail=msg)
        raise HTTPException(status_code=400, detail=msg)


@app.delete("/api/loops/templates/{name}", dependencies=[Depends(require_api_key)])
async def api_delete_loop_template(name: str):
    """Delete a user template by name. 403 on built-ins (the operator can
    shadow a built-in by writing a user override, but can never delete
    the built-in itself)."""
    from .loop_template import TemplateError, delete_user_template, template_path

    existing = template_path(name)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"template {name!r} not found")
    from .loop_template import _origin_for  # type: ignore[attr-defined]
    if _origin_for(existing) == "built-in":
        raise HTTPException(
            status_code=403,
            detail=f"{name!r} is built-in and cannot be deleted",
        )
    try:
        delete_user_template(name)
    except TemplateError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"deleted": name}


@app.post("/api/loops/start", dependencies=[Depends(require_api_key)])
async def api_start_loop(request: Request):
    """Start a Loop from a template.

    Body shape:
      {
        "template_name": "pr-review-loop",
        "envelope": {
          "artifact_refs": {"pr_number": 117, "repo": "owner/name"},
          "observations": {...},  // optional
          "findings": {...}        // optional, for resuming/seeding
        },
        "workspace_profile": "...",   // optional
        "workspace_home": "..."       // optional
      }
    Returns the new LoopInstance.
    """
    from . import loop_runner
    from .loop_template import TemplateError, load_template
    from .loops import HandoffEnvelope

    body = await request.json()
    template_name = body.get("template_name", "")
    if not template_name:
        raise HTTPException(status_code=400, detail="template_name is required")

    try:
        spec = load_template(template_name)
    except TemplateError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    envelope_data = body.get("envelope") or {}
    if not isinstance(envelope_data, dict):
        raise HTTPException(status_code=400, detail="envelope must be an object")
    try:
        envelope = HandoffEnvelope.model_validate(envelope_data)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid envelope: {exc}")

    try:
        inst = await loop_runner.start_loop(
            spec,
            envelope,
            workspace_profile=body.get("workspace_profile"),
            workspace_home=body.get("workspace_home"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return inst.model_dump()


def _loop_summary(inst) -> dict:
    """Slim LoopInstance projection for list views — drops the raw
    template text (can be 5–10 KB) and keeps the operator-facing fields.
    Frontmatter values like max_iterations are re-parsed cheaply for
    the list view; pin them on LoopInstance later if it shows up in
    profiling."""
    max_iter = 0
    try:
        from .loop_md import parse as _parse
        max_iter = _parse(inst.template_text).max_iterations
    except Exception:
        pass
    return {
        "id": inst.id,
        "name": inst.template_name,
        "status": inst.status.value,
        "iteration": inst.iteration,
        "max_iterations": max_iter,
        "parent_task_id": inst.parent_task_id,
        "current_child_id": inst.current_child_id,
        "cost_history": inst.cost_history,
        "cost_usd": inst.cost_usd,
        "mermaid": inst.mermaid,
        "stop_reason": inst.stop_reason,
        "error": inst.error,
        "workspace_profile": inst.workspace_profile,
        "created_at": inst.created_at,
        "updated_at": inst.updated_at,
    }


@app.get("/api/loops", dependencies=[Depends(require_api_key)])
async def api_list_loops(status: str | None = None):
    """List loops, optionally filtered by status (e.g. ?status=running)."""
    from . import loop_runner
    from .loops import LoopStatus

    insts = loop_runner.list_instances()
    if status:
        try:
            want = LoopStatus(status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"unknown status '{status}'")
        insts = [i for i in insts if i.status == want]
    insts.sort(key=lambda i: i.updated_at, reverse=True)
    return {"loops": [_loop_summary(i) for i in insts]}


@app.get("/api/loops/{loop_id}", dependencies=[Depends(require_api_key)])
async def api_get_loop(loop_id: str):
    """Return the full LoopInstance including the pinned template snapshot."""
    from . import loop_runner, store

    inst = loop_runner.get_instance(loop_id)
    if inst is None:
        # Fall back to the DB — terminal loops are not in memory but are
        # still queryable for post-hoc audit.
        inst = store.get_loop_instance(loop_id)
    if inst is None:
        raise HTTPException(status_code=404, detail=f"loop '{loop_id}' not found")
    return inst.model_dump()


@app.get("/api/loops/{loop_id}/iterations", dependencies=[Depends(require_api_key)])
async def api_get_loop_iterations(loop_id: str):
    """Return the per-iteration metric rows for a loop. Feeds the convergence
    trend chart in the future Loops Panel."""
    from . import store

    rows = store.query_loop_iteration_metrics(loop_id)
    return {"loop_id": loop_id, "iterations": rows}


@app.post("/api/loops/{loop_id}/cancel", dependencies=[Depends(require_api_key)])
async def api_cancel_loop(loop_id: str, request: Request):
    """Cancel an in-flight loop. Idempotent — terminal loops return their
    current state unchanged."""
    from . import loop_runner

    reason = "operator cancelled"
    try:
        body = await request.json()
        if isinstance(body, dict) and isinstance(body.get("reason"), str):
            reason = body["reason"]
    except Exception:
        pass

    try:
        inst = await loop_runner.cancel_loop(loop_id, reason=reason)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return inst.model_dump()


# ---------------------------------------------------------------------------
# Artifacts (MinIO-backed file browser + presign endpoints)
# ---------------------------------------------------------------------------


@app.get("/api/artifacts/health", dependencies=[Depends(require_api_key)])
async def api_artifacts_health():
    """Probe MinIO. Frontend uses this to decide whether to surface the
    Files panel and whether to render a "MinIO unreachable" state."""
    from . import artifacts

    return artifacts.health()


@app.get("/api/artifacts/buckets", dependencies=[Depends(require_api_key)])
async def api_artifacts_list_buckets(profile: str = ""):
    """Live bucket catalog. ``profile`` scopes the view to the app's
    active profile: <profile>-* buckets show whole, profile-structured
    buckets get a <profile>/ scope_prefix, unrelated buckets are omitted.
    Empty profile ("all") = every bucket, unscoped."""
    from . import artifacts

    if not artifacts.is_enabled():
        raise HTTPException(status_code=503, detail="minio integration is disabled")
    try:
        buckets = await asyncio.to_thread(artifacts.known_buckets, profile)
    except artifacts.ArtifactError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"buckets": buckets}


@app.get("/api/artifacts/{bucket}/list", dependencies=[Depends(require_api_key)])
async def api_artifacts_list_folder(bucket: str, prefix: str = ""):
    """List one folder level. ``bucket`` is the logical name
    (``vault`` | ``artifacts``); ``prefix`` is the path within the
    profile namespace."""
    from . import artifacts

    if not artifacts.is_enabled():
        raise HTTPException(status_code=503, detail="minio integration is disabled")
    try:
        listing = artifacts.list_folder(bucket, prefix)
    except artifacts.ArtifactError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "bucket": listing.bucket,
        "prefix": listing.prefix,
        "truncated": listing.truncated,
        "folders": [
            {"name": f.name, "prefix": f.prefix}
            for f in listing.folders
        ],
        "files": [
            {
                "name": f.name,
                "key": f.key,
                "size": f.size,
                "etag": f.etag,
                "last_modified_ms": f.last_modified_ms,
            }
            for f in listing.files
        ],
    }


@app.get("/api/artifacts/{bucket}/search", dependencies=[Depends(require_api_key)])
async def api_artifacts_search(bucket: str, q: str = "", limit: int = 200, prefix: str = ""):
    """Substring search over object keys under ``prefix`` (the bucket's
    scope_prefix from /buckets; "" = whole bucket) — the find box."""
    from . import artifacts

    if not artifacts.is_enabled():
        raise HTTPException(status_code=503, detail="minio integration is disabled")
    q = q.strip()
    if not q:
        raise HTTPException(status_code=400, detail="q must be non-empty")
    limit = max(1, min(limit, 500))
    try:
        res = await asyncio.to_thread(
            artifacts.search_objects, bucket, q, limit=limit, prefix=prefix
        )
    except artifacts.ArtifactError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "bucket": res["bucket"],
        "query": res["query"],
        "truncated": res["truncated"],
        "scanned": res["scanned"],
        "files": [
            {
                "name": f.name,
                "key": f.key,
                "size": f.size,
                "etag": f.etag,
                "last_modified_ms": f.last_modified_ms,
            }
            for f in res["files"]
        ],
    }


@app.get("/api/artifacts/{bucket}/head", dependencies=[Depends(require_api_key)])
async def api_artifacts_head(bucket: str, key: str):
    """Object metadata for the file detail pane."""
    from . import artifacts

    if not artifacts.is_enabled():
        raise HTTPException(status_code=503, detail="minio integration is disabled")
    try:
        return artifacts.head_object(bucket, key)
    except artifacts.ArtifactError as exc:
        msg = str(exc)
        if "not found" in msg:
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=400, detail=msg)


@app.delete("/api/artifacts/{bucket}/object", dependencies=[Depends(require_api_key)])
async def api_artifacts_delete(bucket: str, key: str):
    from . import artifacts

    if not artifacts.is_enabled():
        raise HTTPException(status_code=503, detail="minio integration is disabled")
    # Soft-delete: objects move to the bucket's .trash/ namespace via a
    # server-side copy. Deleting a key already under .trash/ is permanent —
    # that's how trash gets emptied by hand (ILM expiry ages out the rest).
    try:
        if artifacts.is_trash_key(key):
            await asyncio.to_thread(artifacts.delete_object, bucket, key)
            return {"deleted": key, "permanent": True}
        trash_key = await asyncio.to_thread(artifacts.trash_object, bucket, key)
    except artifacts.ArtifactError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"deleted": key, "permanent": False, "trash_key": trash_key}


@app.get("/api/artifacts/{bucket}/presign", dependencies=[Depends(require_api_key)])
async def api_artifacts_presign(
    bucket: str, key: str, op: str = "get", ttl: int = 3600, host: str = ""
):
    """Mint a presigned URL. ``op=get`` is for the operator opening a
    file; ``op=put`` is reserved for the Phase 4 assist worker writes.
    ``host`` (optional) is the base URL the CLIENT will fetch from —
    SigV4 signs the Host header, so the app passes its MinIO integration
    address (local or remote) to get a URL that works from where it sits.
    Empty falls back to CL_MINIO__PUBLIC_ENDPOINT, then the daemon endpoint."""
    from . import artifacts

    if not artifacts.is_enabled():
        raise HTTPException(status_code=503, detail="minio integration is disabled")
    if op not in ("get", "put"):
        raise HTTPException(status_code=400, detail="op must be 'get' or 'put'")
    try:
        if op == "get":
            url = artifacts.presigned_get_url(bucket, key, expires_seconds=ttl, public_base=host)
        else:
            url = artifacts.presigned_put_url(bucket, key, expires_seconds=ttl, public_base=host)
    except artifacts.ArtifactError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"url": url, "expires_in": ttl}


# ---------------------------------------------------------------------------
# MCP gateway — per-profile encrypted env store (ADR-002, phase 1)
# ---------------------------------------------------------------------------
# Operator-only (require_api_key, full trust). These expose plaintext creds
# for editing; agents never reach them. The single operator key lives in the
# gateway env (CL_GATEWAY__SECRET_KEY); the store holds only ciphertext.
from . import gateway_secrets  # noqa: E402


@app.get("/api/gateway/profiles", dependencies=[Depends(require_api_key)])
async def gateway_list_profiles():
    return {"profiles": gateway_secrets.list_profiles(), "unlocked": gateway_secrets.is_unlocked()}


@app.get("/api/gateway/profiles/{profile}/env", dependencies=[Depends(require_api_key)])
async def gateway_get_profile_env(profile: str):
    try:
        return {"profile": profile, "env": gateway_secrets.get_profile_env(profile)}
    except gateway_secrets.LockedError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except gateway_secrets.GatewaySecretsError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.put("/api/gateway/profiles/{profile}/env", dependencies=[Depends(require_api_key)])
async def gateway_put_profile_env(profile: str, body: dict):
    env = body.get("env")
    if not isinstance(env, dict):
        raise HTTPException(status_code=400, detail='body must be {"env": {KEY: VALUE, ...}}')
    try:
        gateway_secrets.set_profile_env(profile, env)
    except gateway_secrets.LockedError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except gateway_secrets.GatewaySecretsError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # Credentials changed → evict this profile's pooled downstream connections
    # (they were spawned with the OLD env) and clear its negative cache so the
    # next tool call re-spawns with the new creds immediately. Without this,
    # updated secrets only took effect after a daemon restart.
    await _gateway_pool.close(profile)
    log.info("gateway.profile_env_updated", metadata={"profile": profile, "keys": len(env)})
    return {"profile": profile, "saved": True, "count": len(env)}


@app.delete("/api/gateway/profiles/{profile}/env", dependencies=[Depends(require_api_key)])
async def gateway_delete_profile_env(profile: str):
    deleted = gateway_secrets.delete_profile_env(profile)
    # Same live-reload contract as PUT: connections spawned with the removed
    # creds must not outlive them.
    await _gateway_pool.close(profile)
    return {"profile": profile, "deleted": deleted}


# MCP gateway — per-profile credential bundles (files via MinIO)
# ---------------------------------------------------------------------------


def _bundle_http_error(exc: Exception) -> HTTPException:
    from . import gateway_bundle

    if isinstance(exc, gateway_bundle.BundleDisabledError):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, gateway_secrets.LockedError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@app.put("/api/gateway/profiles/{profile}/bundle", dependencies=[Depends(require_api_key)])
async def gateway_put_profile_bundle(profile: str, request: Request):
    """Store a profile's credential bundle (raw tar.gz body, encrypted at rest)."""
    from . import gateway_bundle

    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="body must be a bundle tar.gz")
    app_version = request.headers.get("X-App-Version", "")
    try:
        meta = await asyncio.to_thread(
            gateway_bundle.store_bundle, profile, body, app_version=app_version
        )
    except gateway_secrets.GatewaySecretsError as exc:
        raise _bundle_http_error(exc)
    # Live-reload contract (same as env PUT): pooled servers were spawned with
    # the OLD materialized files — evict them and the stale cache so the next
    # call re-materializes from this upload.
    await _gateway_pool.close(profile)
    await asyncio.to_thread(gateway_bundle.cleanup, profile)
    log.info(
        "gateway.profile_bundle_updated",
        metadata={"profile": profile, "sources": meta.get("sources")},
    )
    return {"profile": profile, "saved": True, **meta}


@app.get("/api/gateway/profiles/{profile}/bundle", dependencies=[Depends(require_api_key)])
async def gateway_get_profile_bundle(profile: str):
    """Bundle object metadata (never the plaintext)."""
    from . import gateway_bundle

    try:
        meta = await asyncio.to_thread(gateway_bundle.get_bundle_meta, profile)
    except gateway_secrets.GatewaySecretsError as exc:
        raise _bundle_http_error(exc)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"no bundle stored for {profile!r}")
    return meta


@app.delete("/api/gateway/profiles/{profile}/bundle", dependencies=[Depends(require_api_key)])
async def gateway_delete_profile_bundle(profile: str):
    from . import gateway_bundle

    try:
        deleted = await asyncio.to_thread(gateway_bundle.delete_bundle, profile)
    except gateway_secrets.GatewaySecretsError as exc:
        raise _bundle_http_error(exc)
    await _gateway_pool.close(profile)
    return {"profile": profile, "deleted": deleted}


# MCP gateway — Tier-0 token minting (ADR-002 phase 3)
# ---------------------------------------------------------------------------
# Operator-only. Mints a profile-bound gateway token so a local client
# (opencode, codex, …) can reach /gateway/mcp scoped to a workspace profile
# with an explicit tool scope, without spawning a task. The token IS the
# secret — it is returned once here and never stored in plaintext elsewhere.
@app.post("/api/gateway/tokens", dependencies=[Depends(require_api_key)])
async def gateway_mint_token(body: dict):
    profile = (body.get("profile") or "").strip()
    if not profile:
        raise HTTPException(status_code=400, detail="profile is required")
    scope = body.get("scope") or []
    if not isinstance(scope, list) or not all(isinstance(s, str) for s in scope):
        raise HTTPException(status_code=400, detail="scope must be a list of tool patterns")
    try:
        ttl = int(body.get("ttl") or 3600)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="ttl must be an integer (seconds)")
    if ttl <= 0:
        raise HTTPException(status_code=400, detail="ttl must be positive")
    ceiling = (body.get("ceiling") or "").strip()
    if ceiling:
        from .trust_zones import TrustZone
        try:
            ceiling = TrustZone.parse(ceiling).name.lower()
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"ceiling must be one of {[z.name.lower() for z in TrustZone]}",
            )
    tok = issue_gateway_token(profile, scope, ttl, residency_ceiling=ceiling)
    return {
        "token": tok.token_id,
        "profile": profile,
        "scope": tok.scope,
        "ceiling": tok.residency_ceiling,
        "expiry": tok.expiry,
    }


@app.delete("/api/gateway/tokens/{token_id}", dependencies=[Depends(require_api_key)])
async def gateway_revoke_token(token_id: str):
    return {"token": token_id, "revoked": revoke_token(token_id)}


# MCP gateway — per-profile tool listing (ADR-002 phase 3, app "Test gateway")
# ---------------------------------------------------------------------------
# Operator-only connectivity/scoping check: returns the namespaced tools a
# profile would actually receive through the gateway right now. This exercises
# the full server-side path (catalog -> pooled spawn with the profile's creds
# -> scope filter), so an empty list means "nothing allowlisted or a server
# failed to spawn" — the exact failure modes worth surfacing in the UI.
@app.get("/api/gateway/profiles/{profile}/tools", dependencies=[Depends(require_api_key)])
async def gateway_list_profile_tools(profile: str):
    from .gateway_server import Identity, list_gateway_tools

    ident = Identity(profile=profile, scope=["*"])
    specs = gateway_catalog.resolve_enabled_specs()
    try:
        tools = await list_gateway_tools(_gateway_pool, specs, ident)
    except Exception as exc:  # pragma: no cover - defensive; per-server errors are already skipped
        raise HTTPException(status_code=502, detail=f"gateway tool listing failed: {exc}")
    return {
        "profile": profile,
        "servers": [s.name for s in specs],
        "tools": [
            {"name": t.name, "description": getattr(t, "description", "") or ""} for t in tools
        ],
    }


# MCP gateway — server registry (enable/disable, ADR-002 #152)
# ---------------------------------------------------------------------------
# Operator-only. The catalog file holds definitions; the DB holds which servers
# are enabled. Toggles take effect live (specs are resolved per request).
@app.get("/api/gateway/servers", dependencies=[Depends(require_api_key)])
async def gateway_list_servers():
    from . import store

    enabled = store.list_gateway_servers()
    return {
        "servers": [
            {
                "name": c["name"],
                "command": c["command"],
                "enabled": enabled.get(c["name"], False),
            }
            for c in gateway_catalog.list_catalog_servers()
        ]
    }


@app.patch("/api/gateway/servers/{name}", dependencies=[Depends(require_api_key)])
async def gateway_set_server_enabled(name: str, body: dict):
    from . import store

    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail='body must be {"enabled": true|false}')
    valid = {c["name"] for c in gateway_catalog.list_catalog_servers()}
    if name not in valid:
        raise HTTPException(status_code=404, detail=f"unknown catalog server {name!r}")
    await store.async_set_gateway_server_enabled(name, enabled)
    return {"name": name, "enabled": enabled}


# Declarative orchestration — trust map + residency planning (operator-only)
# ---------------------------------------------------------------------------
# Per-profile trust map (destination -> zone) + default residency ceiling, and
# a plan-preview endpoint that resolves a step to a compliant provider + tools.
from . import step_planner, trust  # noqa: E402
from . import store  # noqa: E402
from .residency_resolver import Requirement  # noqa: E402
from .trust_zones import TrustZone  # noqa: E402


def _parse_zone(value: str) -> TrustZone:
    try:
        return TrustZone.parse(value)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"zone must be one of {[z.name.lower() for z in TrustZone]}",
        )


@app.get("/api/orchestration/profiles/{profile}/trust", dependencies=[Depends(require_api_key)])
async def orchestration_get_trust(profile: str):
    return {
        "profile": profile,
        "default_ceiling": trust.ceiling_for_profile(profile).name.lower(),
        "rules": store.list_trust_rules(profile),
    }


@app.put("/api/orchestration/profiles/{profile}/trust/rule", dependencies=[Depends(require_api_key)])
async def orchestration_set_trust_rule(profile: str, body: dict):
    pattern = (body.get("pattern") or "").strip()
    if not pattern:
        raise HTTPException(status_code=400, detail="pattern is required")
    zone = _parse_zone(body.get("zone") or "")
    await store.async_set_trust_rule(profile, pattern, zone.name.lower())
    return {"profile": profile, "pattern": pattern, "zone": zone.name.lower()}


@app.delete(
    "/api/orchestration/profiles/{profile}/trust/rule", dependencies=[Depends(require_api_key)]
)
async def orchestration_delete_trust_rule(profile: str, pattern: str):
    return {"profile": profile, "pattern": pattern, "deleted": store.delete_trust_rule(profile, pattern)}


@app.put(
    "/api/orchestration/profiles/{profile}/trust/ceiling", dependencies=[Depends(require_api_key)]
)
async def orchestration_set_ceiling(profile: str, body: dict):
    zone = _parse_zone(body.get("zone") or "")
    await store.async_set_profile_default_ceiling(profile, zone.name.lower())
    return {"profile": profile, "default_ceiling": zone.name.lower()}


@app.get("/api/orchestration/profiles/{profile}/zones", dependencies=[Depends(require_api_key)])
async def orchestration_zones(profile: str):
    from . import provider_catalog

    providers = [
        {"name": r.name, "zone": r.zone.name.lower(), "capabilities": sorted(r.capabilities)}
        for r in provider_catalog.provider_resources(profile)
    ]
    tools = [
        {"name": n, "zone": z.name.lower()}
        for n, z in sorted(step_planner.mcp_zones_for_profile(profile).items())
    ]
    return {"profile": profile, "providers": providers, "tools": tools}


@app.get("/api/orchestration/profiles/{profile}/servers", dependencies=[Depends(require_api_key)])
async def orchestration_servers(profile: str):
    """Per-profile gateway server include/exclude: resolution default + the
    user's manual toggle. This is the simplified per-profile control surface."""
    return {
        "profile": profile,
        "servers": [
            {
                "name": s.name,
                "zone": s.zone.name.lower(),
                "default_enabled": s.default_enabled,
                "override": s.override,          # true/false/null
                "effective": s.effective,
            }
            for s in step_planner.profile_server_states(profile)
        ],
    }


@app.put(
    "/api/orchestration/profiles/{profile}/servers/{name}", dependencies=[Depends(require_api_key)]
)
async def orchestration_set_server(profile: str, name: str, body: dict):
    """Manually include/exclude a server for a profile (overrides resolution)."""
    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail='body must be {"enabled": true|false}')
    await store.async_set_profile_server_override(profile, name, enabled)
    return {"profile": profile, "server": name, "enabled": enabled}


@app.delete(
    "/api/orchestration/profiles/{profile}/servers/{name}", dependencies=[Depends(require_api_key)]
)
async def orchestration_clear_server(profile: str, name: str):
    """Clear a manual override → the server reverts to the resolution default."""
    return {"profile": profile, "server": name, "cleared": store.clear_profile_server_override(profile, name)}


@app.post("/api/orchestration/profiles/{profile}/plan", dependencies=[Depends(require_api_key)])
async def orchestration_plan(profile: str, body: dict):
    """Preview the resolved plan for a step. Body:
    ``{ceiling?, requires?: [caps], prefers?: [caps]}`` — ceiling defaults to the
    profile's configured default."""
    ceiling = _parse_zone(body["ceiling"]) if body.get("ceiling") else trust.ceiling_for_profile(profile)
    requires = frozenset(body.get("requires") or [])
    prefers = tuple(body.get("prefers") or [])
    plan = step_planner.plan_step(profile, Requirement(ceiling, requires=requires, prefers=prefers))
    return {
        "profile": profile,
        "ceiling": plan.ceiling.name.lower(),
        "blocked": plan.blocked,
        "reason": plan.reason,
        "provider": (
            None if plan.provider is None
            else {"name": plan.provider.name, "zone": plan.provider.zone.name.lower()}
        ),
        "eligible_tools": list(plan.eligible_tools),
        "excluded_tools": [{"name": n, "zone": z.name.lower()} for n, z in plan.excluded_tools],
    }


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

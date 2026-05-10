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
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sse_starlette.sse import EventSourceResponse
from starlette.middleware.cors import CORSMiddleware

from datetime import datetime, timezone

from .auth import get_api_key, load_or_create_key, require_api_key
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
    validate_artifact_key,
    validate_session_name,
    ValidationError,
)
from .log import get_logger, setup_logging
from .models import TaskCreate, Token
from .models_api import (
    CompleteChannelRequest,
    CreateAgentRequest,
    CreateChannelRequest,
    CreateRepoRequest,
    CreateSessionRequest,
    DeleteSessionRequest,
    ExecSessionRequest,
    PostChannelMessageRequest,
    QuerySessionRequest,
    StartSessionRequest,
    StopSessionRequest,
    UpdateAgentRequest,
    UpdateRepoRequest,
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
    add_repo,
    cancel_task,
    complete_task,
    ensure_repo_agents,
    get_repo,
    get_task,
    list_repos,
    list_tasks,
    on_event,
    remove_repo,
    submit_task,
    update_repo,
)
from .artifacts import (
    ArtifactError,
    delete_artifact,
    download_artifact,
    health_check as artifact_health_check,
    list_artifacts,
    upload_artifact,
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
    complete_channel,
    create_channel,
    delete_channel,
    get_channel,
    get_messages as channel_get_messages,
    list_channels,
    on_event as channel_on_event,
    post_message as channel_post_message,
)
from .playbooks import (
    cancel_playbook,
    create_playbook,
    delete_playbook,
    get_playbook,
    list_playbooks,
    load_builtins as load_builtin_playbooks,
    on_event as playbook_on_event,
    run_playbook,
)
from .worktrees import (
    attach_session as worktree_attach_session,
    create_worktree,
    delete_worktree,
    get_worktree,
    list_worktrees,
    on_event as worktree_on_event,
)
from .models import ChannelParticipant
from .models_api import OllamaChatRequest, OllamaPullRequest, CreatePlaybookRequest, CreateWorktreeRequest
from .ollama import (
    OllamaError,
    chat as ollama_chat,
    delete_model as ollama_delete_model,
    health_check as ollama_health_check,
    list_models as ollama_list_models,
    pull_model as ollama_pull_model,
)

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

    # Load built-in playbook templates
    n = load_builtin_playbooks()
    if n:
        log.info("api.builtin_playbooks_loaded", metadata={"count": n})

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

    # Forward worktree events to global SSE
    worktree_on_event(
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

    log.info("api.started", metadata={"port": settings.api_port})
    yield

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

    # Return sessions from all backends
    return sessions



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
# Credential bundle queue (Phase 5) — laptop daemon polls these endpoints
# from outside the API host. The docker backend enqueues directly via the
# in-process singleton when BRAINBOX_CC_QUEUE=1.
# ---------------------------------------------------------------------------


@app.get("/api/credentials/pending")
async def credentials_pending(_key=Depends(require_api_key)):
    """Long-poll for the next pending bundle request. 204 if none."""
    from .credentials.queue import get_queue

    queue = get_queue()
    req = await queue.next_pending(timeout=30.0)
    if req is None:
        return Response(status_code=204)
    return {
        "id": req.id,
        "workspace_profile": req.workspace_profile,
        "workspace_home": req.workspace_home,
        "recipient": req.recipient,
        "created_at": req.created_at,
    }


@app.post("/api/credentials/{request_id}/sealed")
async def credentials_sealed(
    request_id: str, request: Request, _key=Depends(require_api_key)
):
    """Daemon uploads sealed ciphertext. Resolves the awaiting producer."""
    from .credentials.queue import get_queue

    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="empty body")
    queue = get_queue()
    if not await queue.fulfill(request_id, body):
        raise HTTPException(status_code=404, detail="request not found or already fulfilled")
    return {"ok": True, "bytes": len(body)}


@app.post("/api/credentials/seal-request")
async def credentials_seal_request(
    request: Request, _key=Depends(require_api_key)
):
    """HTTP bridge to the cc queue — used by remote runners that can't talk
    to the in-process queue. Body: {workspace_profile, workspace_home, recipient}.
    Blocks until the cc poll daemon seals and posts the ciphertext back.
    Returns the sealed bytes as application/octet-stream.
    """
    import asyncio

    from .credentials.queue import get_queue

    body = await request.json()
    recipient = body.get("recipient", "")
    if not isinstance(recipient, str) or not recipient.startswith("age1"):
        raise HTTPException(status_code=400, detail="recipient must be an age1... pubkey")
    queue = get_queue()
    req = await queue.enqueue(
        workspace_profile=body.get("workspace_profile"),
        workspace_home=body.get("workspace_home"),
        recipient=recipient,
    )
    try:
        sealed = await asyncio.wait_for(req.fut, timeout=float(body.get("timeout", 60)))
    except asyncio.TimeoutError as exc:
        await queue.cancel(req.id, "seal-request timed out")
        raise HTTPException(status_code=504, detail="no laptop daemon responded") from exc
    return Response(content=sealed, media_type="application/octet-stream")


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
    info = await reg.register(
        name=name,
        capabilities={k: bool(v) for k, v in caps.items()},
        tags=body.get("tags") or [],
        version=body.get("version") or "",
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
        }
        for r in runners
    ]


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

    body = await request.json()
    reg = get_registry()
    if await reg.get(name) is None:
        raise HTTPException(status_code=404, detail="runner not registered")
    if not await reg.fulfill(work_id, body):
        raise HTTPException(status_code=404, detail="work item not found or already fulfilled")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Session management routes (from dashboard/server.js)
# ---------------------------------------------------------------------------


@app.get("/api/sessions")
async def api_list_sessions():
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _get_sessions_info)


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
    name = body.name
    session_name = _extract_session_name(name)
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
            container = client.containers.get(name)
            container.stop(timeout=1)
            _audit_log(request, "session.stop", session_name=session_name, success=True)
            _broadcast_sse(json.dumps({"action": "session.stop", "session": session_name}))
            return {"success": True}
        except docker.errors.NotFound:
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
    name = body.name
    session_name = _extract_session_name(name)
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
        return {"success": True, "url": f"http://localhost:{ctx.port}"}
    except Exception as exc:
        log.error(
            "session.start_failed.lifecycle", metadata={"session": session_name, "error": str(exc)}
        )
        # Fallback to direct Docker start
        try:
            client = _docker()
            container = client.containers.get(name)
            container.start()

            # Get port
            container.reload()
            ports = container.attrs.get("NetworkSettings", {}).get("Ports") or {}
            port = "7681"
            for bindings in ports.values():
                if bindings:
                    for b in bindings:
                        if b.get("HostPort"):
                            port = b["HostPort"]
                            break

            _audit_log(request, "session.start", session_name=session_name, success=True)
            _broadcast_sse(json.dumps({"action": "session.start", "session": session_name}))
            return {"success": True, "url": f"http://localhost:{port}"}
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
        if body.repo and body.repo.mode == "ci-ratchet":
            from .router import register_ci_ratchet_task
            task_id_result, hub_token = register_ci_ratchet_task(
                description=body.repo.task,
                repo_url=body.repo.url,
                session_name=body.name,
            )
            task_id = task_id_result.id
        elif body.task:
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
            repo=body.repo,
            task_description=body.task,
            task_id=task_id,
            delivery=body.delivery,
            runner=body.runner,
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
            return {
                "success": True,
                "backend": "docker",
                "url": f"http://localhost:{ctx.port}",
            }
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
    # Sanitize command input
    if not body.command or not body.command.strip():
        raise HTTPException(status_code=400, detail="Command cannot be empty")
    if "\x00" in body.command:
        raise HTTPException(status_code=400, detail="Command cannot contain null bytes")
    if len(body.command) > 10_000:
        raise HTTPException(status_code=400, detail="Command too long (max 10000 chars)")

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
async def hub_submit_task(body: TaskCreate, _key=Depends(require_api_key)):
    try:
        task = await submit_task(
            body.description,
            body.agent_name,
            repo_url=body.repo_url,
            workspace_profile=body.workspace_profile,
            workspace_home=body.workspace_home,
            job_id=body.job_id,
        )
        _broadcast_sse(json.dumps({"action": "task.submit", "agent": body.agent_name, "repo": body.repo_url or ""}))
        return task.model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/hub/tasks")
async def hub_list_tasks(status: str | None = None, limit: int = 50, _key=Depends(require_api_key)):
    tasks = list_tasks(status=status, limit=limit)
    return [t.model_dump() for t in tasks]


@app.get("/api/hub/tasks/{task_id}")
async def hub_get_task(task_id: str, _key=Depends(require_api_key)):
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
    return task.model_dump()


@app.delete("/api/hub/tasks/{task_id}")
async def hub_cancel_task(task_id: str, _key=Depends(require_api_key)):
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
        "repos": [r.model_dump() for r in list_repos()],
    }


@app.get("/api/hub/message-log")
async def hub_message_log(_key=Depends(require_api_key)):
    """Return the hub message audit log (admin read-only, no agent token required)."""
    return get_message_log()


# --- Repositories ---


@app.get("/api/hub/repos")
async def hub_list_repos(_key=Depends(require_api_key)):
    return [r.model_dump() for r in list_repos()]


@app.post("/api/hub/repos", status_code=201)
async def hub_add_repo(body: CreateRepoRequest, _key=Depends(require_api_key)):
    try:
        repo = add_repo(
            body.url,
            name=body.name,
            merge_queue=body.merge_queue,
            pr_shepherd=body.pr_shepherd,
            target_branch=body.target_branch,
            is_fork=body.is_fork,
            upstream_url=body.upstream_url,
            workspace_home=body.workspace_home,
            workspace_profile=body.workspace_profile,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Launch persistent agents for this repo
    launched = []
    try:
        launched = await ensure_repo_agents(repo.name)
    except Exception as exc:
        log.warning(
            "hub.repo_agent_launch_failed",
            metadata={"repo": repo.name, "reason": str(exc)},
        )

    _broadcast_sse(json.dumps({"action": "repo.add", "name": repo.name, "profile": body.workspace_profile or ""}))
    return {
        "repo": repo.model_dump(),
        "launched_tasks": [t.model_dump() for t in launched],
    }


@app.get("/api/hub/repos/{name}")
async def hub_get_repo(name: str, _key=Depends(require_api_key)):
    repo = get_repo(name)
    if not repo:
        raise HTTPException(status_code=404, detail=f"Repository '{name}' not found")
    return repo.model_dump()


@app.patch("/api/hub/repos/{name}")
async def hub_update_repo(name: str, body: UpdateRepoRequest, _key=Depends(require_api_key)):
    try:
        repo = update_repo(
            name,
            merge_queue=body.merge_queue,
            pr_shepherd=body.pr_shepherd,
            target_branch=body.target_branch,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Launch any newly enabled agents
    launched = []
    try:
        launched = await ensure_repo_agents(repo.name)
    except Exception as exc:
        log.warning(
            "hub.repo_agent_launch_failed",
            metadata={"repo": repo.name, "reason": str(exc)},
        )

    return {
        "repo": repo.model_dump(),
        "launched_tasks": [t.model_dump() for t in launched],
    }


@app.delete("/api/hub/repos/{name}")
async def hub_remove_repo(name: str, _key=Depends(require_api_key)):
    if not remove_repo(name):
        raise HTTPException(status_code=404, detail=f"Repository '{name}' not found")
    _broadcast_sse(json.dumps({"action": "repo.delete", "name": name}))
    return {"success": True}


# ---------------------------------------------------------------------------
# Group chat channels
# ---------------------------------------------------------------------------


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
    channel = create_channel(body.name, participants)

    # Bootstrap session participants with channel instructions
    api_port = settings.api_port
    api_key_val = get_api_key()
    for p in participants:
        if p.type != "session" or not p.session_name:
            continue
        bootstrap = (
            f"# Group Channel: {channel.name}\n\n"
            f"You are **{p.name}** in a group discussion.\n\n"
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
            "2. Respond to broadcast messages and messages addressed to @" + p.name + "\n"
            "3. When sending, always include `summary=` with a 1-2 sentence brief of your key point\n"
            "4. Use `addressed_to=` to direct a response at a specific participant\n"
            "5. Call `channel_complete` when you believe the discussion has concluded\n"
        )
        if p.system_prompt:
            bootstrap += f"6. Your role: {p.system_prompt}\n"

        try:
            client = _docker()
            container_name = _find_container_name(client, p.session_name)
            container = client.containers.get(container_name)
            loop = asyncio.get_running_loop()
            # Write CHANNEL.md into container using binary copy (no shell, no injection risk)
            import io
            import tarfile

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
            # Send bootstrap prompt to Claude via tmux so the agent starts participating
            tmux_prompt = (
                f"Read /home/developer/CHANNEL.md carefully. "
                f"You are now a participant in group channel '{channel.name}' (ID: {channel.id}). "
                f"Begin participating autonomously: use channel_read to poll for messages, "
                f"respond using channel_send (always include a summary=), and call channel_complete "
                f"when the discussion has concluded. Start now by reading the channel and introducing yourself."
            )
            await loop.run_in_executor(
                None,
                lambda c=container, prompt=tmux_prompt: c.exec_run(
                    ["tmux", "send-keys", "-t", "main", prompt, "Enter"]
                ),
            )
            log.info(
                "channel.bootstrap_sent",
                metadata={"session": p.session_name, "channel_id": channel.id},
            )
        except Exception as exc:
            log.warning(
                "channel.bootstrap_exec_failed",
                metadata={"session": p.session_name, "reason": str(exc)},
            )

    return channel.model_dump()


@app.get("/api/hub/channels")
async def hub_list_channels(_key=Depends(require_api_key)):
    return [c.model_dump() for c in list_channels()]


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


@app.post("/api/hub/channels/{channel_id}/messages")
async def hub_post_channel_message(
    channel_id: str,
    body: PostChannelMessageRequest,
    _key=Depends(require_api_key),
):
    channel = get_channel(channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail=f"Channel '{channel_id}' not found")
    try:
        msg = channel_post_message(
            channel_id,
            from_participant=body.from_participant,
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
    pb = create_playbook(name=body.name, markdown=body.markdown, workspace_profile=body.workspace_profile)
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
        try:
            body = await request.json()
            profile = body.get("workspace_profile") if isinstance(body, dict) else None
        except Exception:
            pass
        pb = await run_playbook(playbook_id, workspace_profile=profile)
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
# Worktrees
# ---------------------------------------------------------------------------


@app.post("/api/hub/worktrees")
async def hub_create_worktree(body: CreateWorktreeRequest, _key=Depends(require_api_key)):
    try:
        wt = await asyncio.to_thread(create_worktree, body.repo_name, body.branch)
        return wt.model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/hub/worktrees")
async def hub_list_worktrees(repo: str | None = None, _key=Depends(require_api_key)):
    return [wt.model_dump() for wt in list_worktrees(repo_name=repo)]


@app.get("/api/hub/worktrees/{worktree_id}")
async def hub_get_worktree(worktree_id: str, _key=Depends(require_api_key)):
    wt = get_worktree(worktree_id)
    if not wt:
        raise HTTPException(status_code=404, detail=f"Worktree '{worktree_id}' not found")
    return wt.model_dump()


@app.delete("/api/hub/worktrees/{worktree_id}")
async def hub_delete_worktree(worktree_id: str, _key=Depends(require_api_key)):
    try:
        await asyncio.to_thread(delete_worktree, worktree_id)
        _broadcast_sse(json.dumps({"action": "worktree.deleted", "worktree_id": worktree_id}))
        return {"ok": True}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/hub/worktrees/{worktree_id}/session")
async def hub_worktree_session(worktree_id: str, request: Request, _key=Depends(require_api_key)):
    """Create a brainbox session mounted on the given worktree."""
    from .lifecycle import run_pipeline
    from .router import get_repo

    wt = get_worktree(worktree_id)
    if not wt:
        raise HTTPException(status_code=404, detail=f"Worktree '{worktree_id}' not found")
    if wt.status == "in_use":
        raise HTTPException(status_code=409, detail=f"Worktree '{worktree_id}' already has an active session")

    repo = get_repo(wt.repo_name)
    if not repo:
        raise HTTPException(status_code=404, detail=f"Repository '{wt.repo_name}' not found")

    session_name = f"wt-{wt.id[:6]}"
    volume = f"{wt.worktree_path}:/home/developer/workspace/repo:rw"

    try:
        ctx = await run_pipeline(
            session_name=session_name,
            role="developer",
            workspace_profile=repo.workspace_profile,
            workspace_home=repo.workspace_home,
            volume_mounts=[volume],
            repo_url=repo.url,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    worktree_attach_session(worktree_id, ctx.session_name)
    _broadcast_sse(json.dumps({"action": "worktree.updated", "worktree_id": worktree_id}))
    return {"worktree_id": worktree_id, "session": ctx.session_name}


# ---------------------------------------------------------------------------
# Artifact store
# ---------------------------------------------------------------------------


async def _artifact_op(operation_fn, *args, **kwargs):
    """Run an artifact operation respecting the configured mode."""
    mode = settings.artifact.mode
    if mode == "off":
        raise HTTPException(status_code=503, detail="Artifact store is disabled")
    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: operation_fn(*args, **kwargs))
    except ArtifactError as exc:
        if "not found" in exc.reason:
            raise HTTPException(status_code=404, detail=str(exc))
        if mode == "enforce":
            raise HTTPException(status_code=502, detail=str(exc))
        log.warning("artifact.operation_failed", metadata={"error": str(exc)})
        return None
    except Exception as exc:
        if mode == "enforce":
            raise HTTPException(status_code=502, detail=str(exc))
        log.warning("artifact.operation_failed", metadata={"error": str(exc)})
        return None


@app.get("/api/artifacts/health")
async def api_artifact_health():
    """Check artifact store connectivity."""
    mode = settings.artifact.mode
    if mode == "off":
        return {"healthy": False, "mode": "off", "url": None, "detail": "Artifact store is disabled"}
    loop = asyncio.get_running_loop()
    healthy = await loop.run_in_executor(None, artifact_health_check)
    return {"healthy": healthy, "mode": mode, "url": settings.artifact.endpoint, "detail": None}


@app.get("/api/artifacts")
async def api_list_artifacts(prefix: str = Query(default=""), _key=Depends(require_api_key)):
    """List artifacts, optionally filtered by key prefix."""
    result = await _artifact_op(list_artifacts, prefix)
    if result is None:
        return []
    return [
        {"key": a.key, "size": a.size, "etag": a.etag, "timestamp": a.timestamp} for a in result
    ]


@app.post("/api/artifacts/{key:path}", status_code=201)
@limiter.limit("30/minute")
async def api_upload_artifact(key: str, request: Request, _key=Depends(require_api_key)):
    """Upload an artifact (raw bytes in request body)."""
    # Validate artifact key to prevent path traversal
    try:
        validated_key = validate_artifact_key(key)
    except ValidationError as val_err:
        log.error("artifact.upload.validation_failed", metadata={"key": key, "error": str(val_err)})
        raise HTTPException(status_code=400, detail=str(val_err))

    # Enforce upload size limit
    max_size = settings.artifact_max_size
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > max_size:
        raise HTTPException(
            status_code=413,
            detail=f"Upload too large ({int(content_length)} bytes). Max: {max_size} bytes.",
        )

    data = await request.body()
    if len(data) > max_size:
        raise HTTPException(
            status_code=413,
            detail=f"Upload too large ({len(data)} bytes). Max: {max_size} bytes.",
        )

    content_type = request.headers.get("content-type", "application/octet-stream")
    metadata = {"content_type": content_type}

    task_id = request.headers.get("x-task-id")
    if task_id:
        metadata["task_id"] = task_id

    result = await _artifact_op(upload_artifact, validated_key, data, metadata)
    if result is None:
        return {"stored": False, "key": validated_key}
    return {"stored": True, "key": result.key, "size": result.size, "etag": result.etag}


@app.get("/api/artifacts/{key:path}")
@limiter.limit("30/minute")
async def api_download_artifact(request: Request, key: str):
    """Download an artifact by key."""
    # Validate artifact key to prevent path traversal
    try:
        validated_key = validate_artifact_key(key)
    except ValidationError as val_err:
        log.error(
            "artifact.download.validation_failed", metadata={"key": key, "error": str(val_err)}
        )
        raise HTTPException(status_code=400, detail=str(val_err))

    result = await _artifact_op(download_artifact, validated_key)
    if result is None:
        raise HTTPException(status_code=404, detail="Artifact not available")
    body, metadata = result
    content_type = metadata.get("content_type", "application/octet-stream")
    return Response(content=body, media_type=content_type)


@app.delete("/api/artifacts/{key:path}")
@limiter.limit("30/minute")
async def api_delete_artifact(request: Request, key: str, _key=Depends(require_api_key)):
    """Delete an artifact by key."""
    try:
        validated_key = validate_artifact_key(key)
    except ValidationError as val_err:
        log.error("artifact.delete.validation_failed", metadata={"key": key, "error": str(val_err)})
        raise HTTPException(status_code=400, detail=str(val_err))
    await _artifact_op(delete_artifact, validated_key)
    return {"success": True, "key": validated_key}


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
    """Check Ollama connectivity."""
    loop = asyncio.get_running_loop()
    healthy = await loop.run_in_executor(None, ollama_health_check)
    return {"healthy": healthy, "host": settings.ollama.host}


@app.post("/api/ollama/chat")
async def api_ollama_chat(body: OllamaChatRequest, _key=Depends(require_api_key)):
    """Proxy a chat completion request to Ollama."""
    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, lambda: ollama_chat(body.messages, body.model))
        return {
            "model": result.model,
            "message": {"role": result.message.role, "content": result.message.content},
            "total_duration": result.total_duration,
            "eval_count": result.eval_count,
        }
    except OllamaError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/api/ollama/models")
async def api_ollama_models(_key=Depends(require_api_key)):
    """List models available on the Ollama server."""
    try:
        loop = asyncio.get_running_loop()
        models = await loop.run_in_executor(None, ollama_list_models)
        return {
            "models": [
                {
                    "name": m.name,
                    "size": m.size,
                    "modified_at": m.modified_at,
                    "digest": m.digest,
                }
                for m in models
            ]
        }
    except OllamaError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/api/ollama/pull")
async def api_ollama_pull(body: OllamaPullRequest, _key=Depends(require_api_key)):
    """Pull a model from the Ollama registry."""
    try:
        loop = asyncio.get_running_loop()
        status = await loop.run_in_executor(None, lambda: ollama_pull_model(body.name))
        return {"status": status, "model": body.name}
    except OllamaError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.delete("/api/ollama/models/{name:path}")
async def api_ollama_delete_model(name: str, _key=Depends(require_api_key)):
    """Delete a model from the Ollama server."""
    try:
        loop = asyncio.get_running_loop()
        status = await loop.run_in_executor(None, lambda: ollama_delete_model(name))
        return {"status": status, "model": name}
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
# NFS export management (UTM volume mounts)
# ---------------------------------------------------------------------------


@app.get("/api/nfs/exports")
async def api_list_nfs_exports(_key: str = Depends(require_api_key)):
    """List current NFS exports from /etc/exports."""
    from .backends.utm.nfs import list_nfs_exports

    return list_nfs_exports()


@app.post("/api/nfs/exports")
async def api_add_nfs_export(request: Request, _key: str = Depends(require_api_key)):
    """Add a directory to /etc/exports for UTM VM access."""
    from .backends.utm.nfs import ensure_nfs_export

    body = await request.json()
    path = body.get("path", "").strip()
    if not path:
        raise HTTPException(status_code=400, detail="path is required")
    if not Path(path).is_absolute():
        raise HTTPException(status_code=400, detail="path must be absolute")
    if not Path(path).exists():
        raise HTTPException(status_code=400, detail=f"path does not exist: {path}")

    try:
        await ensure_nfs_export(path)
        return {"success": True, "path": path}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.delete("/api/nfs/exports")
async def api_remove_nfs_export(
    path: str = Query(..., description="Absolute path to remove from exports"),
    _key: str = Depends(require_api_key),
):
    """Remove a directory from /etc/exports."""
    from .backends.utm.nfs import remove_nfs_export

    path = path.strip()
    if not path:
        raise HTTPException(status_code=400, detail="path is required")

    try:
        await remove_nfs_export(path)
        return {"success": True, "path": path}
    except Exception as exc:
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

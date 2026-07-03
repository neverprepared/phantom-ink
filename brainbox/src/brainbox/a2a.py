"""A2A (Agent-to-Agent) protocol façade over the brainbox hub.

Thin adapters exposing brainbox agents via the A2A protocol, implementing
ADR-001 Phase 1 (core task lifecycle). Each spawnable agent gets an A2A
agent card and a JSON-RPC endpoint; tasks map onto the existing hub router.

Surface (per agent ``{agent}`` from the registry):
  - ``GET  /a2a/{agent}/.well-known/agent-card.json`` — public discovery
  - ``POST /a2a/{agent}``         — JSON-RPC 2.0: message/send, tasks/get, tasks/cancel
  - ``GET  /a2a/{agent}/stream``  — SSE TaskStatusUpdateEvents for ?taskId=

Spec version note: the A2A spec spans multiple transports (JSON-RPC, gRPC,
HTTP+JSON) and is moving fast — the proto-normative v1.0.0 line uses
``TASK_STATE_*`` enum names and removes the ``kind`` discriminator. This
façade deliberately targets the widely-deployed **JSON-RPC binding**
(v0.2.x-compatible) that current A2A clients and the ``a2a-sdk`` speak:
hyphenated TaskState values (``input-required``) and ``kind`` discriminators
present. All A2A<->brainbox translation is centralized in ``_to_a2a_task`` /
``_A2A_STATE`` so a spec bump is a one-place change.

No new dependency: the wire format is hand-rolled over FastAPI. Auth reuses
``require_capability("task_submit")`` — X-API-Key (full trust) OR a hub
Bearer token carrying the capability. Card discovery is unauthenticated.

Task continuation: a task an agent has parked for input surfaces as A2A
``input-required`` (brainbox ``NEEDS_ACTION``); the client continues it by
calling ``message/send`` again with the same ``taskId``, which maps onto
``router.resume_task`` (the supplied text lands in ``resume_payload``).

Brainbox extensions via ``message.metadata`` (A2A's free-form extension
point, so this stays spec-compatible):
  - ``{provider,model,effort}`` — picks the LLM for the new task
    (claude/ollama/codex), mapped onto a per-task ``ModelTarget``. So
    "Claude drafts, Ollama reviews" is just a second send with a different
    provider.
  - ``{workspace_profile,workspace_home}`` — scopes the task to a profile.
    Profiles are foundational; absent these the task is global.

Deliberately NOT mapped: brainbox hub agent-to-agent messaging
(``messages.route``). A2A has no separate "message bus" method — its
``message/send`` *is* the message primitive — so there is nothing to adapt
it onto; internal hub messaging stays internal.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from pydantic import ValidationError

from . import registry
from . import router as task_router
from .auth import require_capability
from .models import AgentDefinition, ModelTarget, Task, TaskStatus

# Targeted A2A protocol version (JSON-RPC binding). See module docstring.
PROTOCOL_VERSION = "0.2.5"

router = APIRouter()

# Bound once at module scope: require_capability() returns a fresh closure per
# call, so binding here keeps the dependency reusable and overridable in tests.
_require_task_submit = require_capability("task_submit")

# JSON-RPC 2.0 standard error codes
_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602
# A2A-specific error codes
_TASK_NOT_FOUND = -32001
_TASK_NOT_CANCELABLE = -32002
_TASK_NOT_RESUMABLE = -32003  # brainbox extension: message/send to a non-input-required task

# brainbox TaskStatus -> A2A TaskState (JSON-RPC binding string values).
_A2A_STATE: dict[TaskStatus, str] = {
    TaskStatus.PENDING: "submitted",
    TaskStatus.RUNNING: "working",
    TaskStatus.BLOCKED: "working",
    TaskStatus.NEEDS_ACTION: "input-required",
    TaskStatus.COMPLETED: "completed",
    TaskStatus.FAILED: "failed",
    TaskStatus.CANCELLED: "canceled",
}

_TERMINAL_STATES = {"completed", "failed", "canceled"}


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        return str(value)


def _to_a2a_task(task: Task) -> dict[str, Any]:
    """Serialize a brainbox Task into an A2A Task object."""
    state = _A2A_STATE.get(task.status, "unknown")
    obj: dict[str, Any] = {
        "id": task.id,
        "contextId": task.job_id or task.id,
        "kind": "task",
        "status": {"state": state, "timestamp": _now_iso()},
    }
    if task.status == TaskStatus.COMPLETED and task.result is not None:
        obj["artifacts"] = [
            {
                "artifactId": f"{task.id}-result",
                "parts": [{"kind": "text", "text": _stringify(task.result)}],
            }
        ]
    return obj


def _agent_card(agent: AgentDefinition, base_url: str) -> dict[str, Any]:
    """Build an A2A AgentCard from a brainbox AgentDefinition."""
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "name": agent.name,
        "description": agent.description or f"Brainbox {agent.name} agent",
        "url": f"{base_url}/a2a/{agent.name}",
        "preferredTransport": "JSONRPC",
        "version": "1.0.0",
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
            "stateTransitionHistory": False,
        },
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        # brainbox capabilities are the agent's skill list.
        "skills": [
            {"id": cap, "name": cap, "description": cap, "tags": [agent.category]}
            for cap in agent.capabilities
        ],
    }


def _jsonrpc_ok(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _jsonrpc_err(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _require_agent(agent: str) -> AgentDefinition:
    defn = registry.get_agent(agent)
    if defn is None:
        raise HTTPException(status_code=404, detail=f"Agent '{agent}' not found")
    return defn


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/a2a/{agent}/.well-known/agent-card.json")
async def agent_card(agent: str, request: Request) -> dict[str, Any]:
    """Public A2A discovery endpoint for a single agent."""
    defn = _require_agent(agent)
    base = str(request.base_url).rstrip("/")
    return _agent_card(defn, base)


@router.post("/a2a/{agent}", dependencies=[Depends(_require_task_submit)])
async def a2a_rpc(agent: str, request: Request) -> dict[str, Any]:
    """A2A JSON-RPC 2.0 endpoint. Returns 200 with a JSON-RPC envelope."""
    _require_agent(agent)
    try:
        body = await request.json()
    except (ValueError, json.JSONDecodeError):
        return _jsonrpc_err(None, _PARSE_ERROR, "Parse error")

    if not isinstance(body, dict) or body.get("jsonrpc") != "2.0":
        req_id = body.get("id") if isinstance(body, dict) else None
        return _jsonrpc_err(req_id, _INVALID_REQUEST, "Invalid Request")

    req_id = body.get("id")
    method = body.get("method")
    params = body.get("params") or {}
    if not isinstance(params, dict):
        return _jsonrpc_err(req_id, _INVALID_PARAMS, "params must be an object")

    if method == "message/send":
        return await _handle_message_send(req_id, agent, params)
    if method == "tasks/get":
        return _handle_tasks_get(req_id, params)
    if method == "tasks/cancel":
        return await _handle_tasks_cancel(req_id, params)
    return _jsonrpc_err(req_id, _METHOD_NOT_FOUND, f"Method not found: {method}")


def _model_target_from_metadata(message: dict) -> ModelTarget | None:
    """Build a ModelTarget from ``message.metadata.{provider,model,effort}``.

    Returns None when no model keys are present. Raises ValidationError on an
    unknown provider/effort (caller maps that to JSON-RPC invalid-params).
    """
    md = message.get("metadata")
    if not isinstance(md, dict):
        return None
    fields = {k: md[k] for k in ("provider", "model", "effort") if md.get(k) is not None}
    if not fields:
        return None
    return ModelTarget(**fields)


async def _handle_message_send(req_id: Any, agent: str, params: dict) -> dict[str, Any]:
    message = params.get("message") or {}
    parts = message.get("parts") or []
    text = "\n".join(
        p.get("text", "") for p in parts if isinstance(p, dict) and p.get("text")
    ).strip()
    if not text:
        return _jsonrpc_err(req_id, _INVALID_PARAMS, "message.parts must contain text")

    # Continuation: a message carrying an existing taskId resumes a task that
    # an agent parked for input (A2A input-required <-> brainbox NEEDS_ACTION).
    task_id = message.get("taskId")
    if task_id:
        existing = task_router.get_task(task_id)
        if existing is None:
            return _jsonrpc_err(req_id, _TASK_NOT_FOUND, f"Task '{task_id}' not found")
        if existing.status != TaskStatus.NEEDS_ACTION:
            state = _A2A_STATE.get(existing.status, "unknown")
            return _jsonrpc_err(
                req_id, _TASK_NOT_RESUMABLE,
                f"Task '{task_id}' is not awaiting input (state: {state})",
            )
        task = task_router.resume_task(task_id, {"input": text})
        return _jsonrpc_ok(req_id, _to_a2a_task(task))

    # New task. contextId continuity across messages is a follow-up; a fresh
    # send starts a new task (its own job root) to avoid router parent/child
    # assumptions. Optional metadata picks the provider/model and the
    # workspace profile this task runs under (profiles are foundational —
    # absent a profile the task is global, which is rarely what a caller wants).
    try:
        model_target = _model_target_from_metadata(message)
    except ValidationError as exc:
        return _jsonrpc_err(req_id, _INVALID_PARAMS, f"invalid model target: {exc.errors()[0]['msg']}")
    md = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}

    # Declarative routing: if the message carries orchestration tags
    # (residency/requires/prefers) and no provider was pinned, resolve a
    # compliant provider (fail-closed). A step that can't be satisfied is a
    # client error, surfaced before the task is created.
    from .step_spec import StepSpec, StepValidationError, compile_step

    spec = StepSpec.from_dict(md)
    if spec.is_declared() and (model_target is None or model_target.provider is None):
        try:
            resolved = compile_step(md.get("workspace_profile") or "", spec)
        except StepValidationError as exc:
            return _jsonrpc_err(req_id, _INVALID_PARAMS, f"step cannot be satisfied: {exc}")
        model_target = (
            ModelTarget(provider=resolved.provider)
            if model_target is None
            else model_target.model_copy(update={"provider": resolved.provider})
        )

    task = await task_router.submit_task(
        text,
        agent,
        workspace_profile=md.get("workspace_profile"),
        workspace_home=md.get("workspace_home"),
        model_target=model_target,
    )
    return _jsonrpc_ok(req_id, _to_a2a_task(task))


def _handle_tasks_get(req_id: Any, params: dict) -> dict[str, Any]:
    task_id = params.get("id")
    if not task_id:
        return _jsonrpc_err(req_id, _INVALID_PARAMS, "params.id is required")
    task = task_router.get_task(task_id)
    if task is None:
        return _jsonrpc_err(req_id, _TASK_NOT_FOUND, f"Task '{task_id}' not found")
    return _jsonrpc_ok(req_id, _to_a2a_task(task))


async def _handle_tasks_cancel(req_id: Any, params: dict) -> dict[str, Any]:
    task_id = params.get("id")
    if not task_id:
        return _jsonrpc_err(req_id, _INVALID_PARAMS, "params.id is required")
    try:
        task = await task_router.cancel_task(task_id)
    except ValueError as exc:
        msg = str(exc)
        code = _TASK_NOT_FOUND if "not found" in msg.lower() else _TASK_NOT_CANCELABLE
        return _jsonrpc_err(req_id, code, msg)
    return _jsonrpc_ok(req_id, _to_a2a_task(task))


def _status_event(task_id: str, state: str, final: bool = False) -> dict[str, Any]:
    return {
        "taskId": task_id,
        "status": {"state": state, "timestamp": _now_iso()},
        "final": final,
    }


def _a2a_state_from_value(status_value: Any) -> str:
    try:
        return _A2A_STATE.get(TaskStatus(status_value), "unknown")
    except ValueError:
        return "unknown"


@router.get("/a2a/{agent}/stream")
async def a2a_stream(agent: str, request: Request, taskId: str) -> EventSourceResponse:
    """SSE stream of A2A TaskStatusUpdateEvents for a single task.

    Subscribes to the hub's SSE fan-out and filters to task events for
    ``taskId``, translating each into an A2A TaskStatusUpdateEvent.
    """
    from . import api as _api  # deferred import avoids the api<->a2a cycle

    _require_agent(agent)
    task = task_router.get_task(taskId)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task '{taskId}' not found")

    queue: asyncio.Queue = asyncio.Queue(maxsize=50)
    _api._sse_queues.add(queue)
    initial_state = _A2A_STATE.get(task.status, "unknown")

    async def event_generator():
        try:
            yield {"data": json.dumps(_status_event(taskId, initial_state,
                                                     initial_state in _TERMINAL_STATES))}
            if initial_state in _TERMINAL_STATES:
                return
            while True:
                raw = await queue.get()
                try:
                    parsed = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                if not (isinstance(parsed, dict) and parsed.get("hub")):
                    continue
                data = parsed.get("data") or {}
                if not isinstance(data, dict) or data.get("id") != taskId:
                    continue
                state = _a2a_state_from_value(data.get("status"))
                final = state in _TERMINAL_STATES
                yield {"data": json.dumps(_status_event(taskId, state, final))}
                if final:
                    break
        except asyncio.CancelledError:
            pass
        finally:
            _api._sse_queues.discard(queue)

    return EventSourceResponse(event_generator())

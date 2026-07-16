"""Cross-machine agent event bus — typed API over the agent_state + agent_events tables.

Two storage layers behind one ingest call:
- `agent_state`: upsert by envelope `id`. One row per logical thing; status mutates
  in place. This is what the attention view and dashboards read.
- `agent_events`: append-only. Every envelope received is written here with an
  auto-increment `seq`. Audit log; supports history drill-down and replay.

`AgentEnvelope` below is the canonical v2.1 model: `contracts/timeline-entry.schema.json`
and the Go/JS bindings are generated from it (see T3), not the other way around. The
envelope is shared with collection-script output, but for agent-bus use the `source` and
`type` fields are required and `parent_id` / `outcome` are commonly populated.

All functions are synchronous (matching `store.py`); async wrappers run them via
`asyncio.to_thread`.
"""

from __future__ import annotations

import asyncio
import json
import time
from enum import Enum
from typing import Any, Callable, Iterable

from pydantic import BaseModel, Field, model_validator

from .store import _conn

# ---------------------------------------------------------------------------
# Canonical envelope model (v2.1)
#
# This is the single source of truth for the timeline-entry contract. The JSON
# Schema (`contracts/timeline-entry.schema.json`) and the Go/JS bindings are
# GENERATED from this model (see T3); do not hand-edit those to add fields —
# edit here and regenerate.
# ---------------------------------------------------------------------------


class EnvelopeStatus(str, Enum):
    """The six display/attention statuses an envelope may carry.

    Distinct from `models.TaskStatus` (the internal task lifecycle, which has
    additional non-display states like `pending`/`running`/`cancelled`).
    Producers map their own lifecycle onto these six; consumers render and
    surface off them. Single source for the enum — no bare status literals.

    - upcoming     — future / queued.
    - active       — in progress.
    - done         — completed (terminal, not attention-worthy).
    - failed       — terminal error (attention-eligible).
    - blocked      — waiting on a dependency or an offline runner
                     (attention-eligible). Producer-emittable: the hub maps
                     `TaskStatus.BLOCKED` here (see `_TASK_STATUS_MAP`).
    - needs_action — waiting on human input (attention-eligible).
    """

    UPCOMING = "upcoming"
    ACTIVE = "active"
    DONE = "done"
    FAILED = "failed"
    BLOCKED = "blocked"
    NEEDS_ACTION = "needs_action"


# Statuses that the attention aggregator surfaces. Producers can use any status
# above; only these pull a card into the user's face. Derived from the enum so
# the set can never drift from a stray string literal.
ATTENTION_STATUSES: tuple[str, ...] = (
    EnvelopeStatus.FAILED.value,
    EnvelopeStatus.BLOCKED.value,
    EnvelopeStatus.NEEDS_ACTION.value,
)


class ActionOutcome(BaseModel):
    ok: bool
    actor: str
    error: str | None = None
    duration_ms: int | None = None


class AgentEnvelope(BaseModel):
    id: str
    kind: str = "event"            # 'metric' | 'event'
    title: str
    source: str | None = None
    type: str | None = None
    status: EnvelopeStatus | None = None
    subtitle: str | None = Field(
        default=None,
        description=(
            "Display-only secondary label, one short line rendered under the "
            "title (e.g. 'developer · session-3'). Never routed or filtered on."
        ),
    )
    description: str | None = Field(
        default=None,
        description=(
            "Display-only supporting detail — longer free text than `subtitle`; "
            "URLs found here render as clickable links. Not routed or filtered on."
        ),
    )
    workspace: str | None = Field(
        default=None,
        description=(
            "Tenancy / routing key — the workspace profile this envelope belongs "
            "to. Routable, not display: it is a column on `agent_state` and a "
            "filter in `/api/agent_events/search`. None means unscoped/global."
        ),
    )
    parent_id: str | None = None
    url: str | None = None
    start_at: int | None = None
    end_at: int | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    actions: list[dict[str, Any]] = Field(default_factory=list)
    outcome: ActionOutcome | None = None


class AgentEventBatch(BaseModel):
    """Wire shape for POST /api/agent_events, matching the Go client's
    `AgentEventBatch` (`{events: [envelope, ...]}`).

    Typing the ingest body as this model is what puts `AgentEnvelope` into
    `/openapi.json` (`components.schemas`) and makes FastAPI validate every
    envelope on ingest — a malformed envelope 422s at the boundary instead of
    reaching `agent_state`. Consumers generate their bindings from that schema
    (see T3), so the model here is the single source of truth for the shape.
    """

    events: list[AgentEnvelope] = Field(
        default_factory=list,
        description="Batch of envelopes to upsert. Empty is accepted (no-op).",
    )

    @model_validator(mode="before")
    @classmethod
    def _accept_bare_shapes(cls, data: Any) -> Any:
        """Back-compat: also accept a bare single envelope (`{id, title, ...}`)
        or a bare list of envelopes, coercing both into `{events: [...]}`.

        The canonical wire shape is `{events: [...]}` (the Go client only ever
        sends that), but existing producers post a single envelope object or a
        raw array. Normalize here so one typed body serves all three without a
        polymorphic union muddying the published schema.
        """
        if isinstance(data, list):
            return {"events": data}
        if isinstance(data, dict) and "events" not in data:
            return {"events": [data]}
        return data


# ---------------------------------------------------------------------------
# Listeners (for SSE fanout)
# ---------------------------------------------------------------------------

_listeners: list[Callable[[AgentEnvelope], None]] = []


def on_event(fn: Callable[[AgentEnvelope], None]) -> None:
    """Register a callback fired after every successful ingest."""
    _listeners.append(fn)


def _fanout(env: AgentEnvelope) -> None:
    for fn in list(_listeners):
        try:
            fn(env)
        except Exception:
            # Listeners must not break ingest. Log later via a hook if needed.
            pass


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------


def ingest(envelope: AgentEnvelope | dict[str, Any]) -> AgentEnvelope:
    """Upsert into agent_state and append to agent_events. Fires listeners.

    Returns the normalized envelope (Pydantic model).
    """
    env = envelope if isinstance(envelope, AgentEnvelope) else AgentEnvelope(**envelope)
    now = int(time.time() * 1000)
    raw_json = env.model_dump_json(exclude_none=False)
    # Persist status as its plain string value so the `status` columns stay
    # queryable by literal (list_state / search_events compare against str).
    status = env.status.value if env.status is not None else None

    # State upsert + event append must land together — an explicit transaction
    # keeps them atomic even on the autocommit pool connection.
    with _conn() as db, db.transaction():
        db.execute(
            """
            INSERT INTO agent_state (
                id, kind, source, type, status, title, subtitle, workspace,
                parent_id, url, start_at, end_at,
                tags_json, metadata_json, actions_json, outcome_json,
                created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                kind          = EXCLUDED.kind,
                source        = EXCLUDED.source,
                type          = EXCLUDED.type,
                status        = COALESCE(EXCLUDED.status, agent_state.status),
                title         = EXCLUDED.title,
                subtitle      = EXCLUDED.subtitle,
                workspace     = COALESCE(EXCLUDED.workspace, agent_state.workspace),
                parent_id     = COALESCE(EXCLUDED.parent_id, agent_state.parent_id),
                url           = EXCLUDED.url,
                start_at      = COALESCE(EXCLUDED.start_at, agent_state.start_at),
                end_at        = EXCLUDED.end_at,
                tags_json     = EXCLUDED.tags_json,
                metadata_json = EXCLUDED.metadata_json,
                actions_json  = EXCLUDED.actions_json,
                outcome_json  = EXCLUDED.outcome_json,
                updated_at    = EXCLUDED.updated_at
            """,
            (
                env.id,
                env.kind,
                env.source,
                env.type,
                status,
                env.title,
                env.subtitle,
                env.workspace,
                env.parent_id,
                env.url,
                env.start_at,
                env.end_at,
                json.dumps(env.tags),
                json.dumps(env.metadata),
                json.dumps(env.actions),
                env.outcome.model_dump_json() if env.outcome else None,
                now,
                now,
            ),
        )
        db.execute(
            """
            INSERT INTO agent_events (id, source, type, status, parent_id, ts, envelope)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (env.id, env.source, env.type, status, env.parent_id, now, raw_json),
        )

    _fanout(env)
    return env


def ingest_batch(envelopes: Iterable[AgentEnvelope | dict[str, Any]]) -> list[AgentEnvelope]:
    return [ingest(e) for e in envelopes]


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def _row_to_state(row: Any) -> dict[str, Any]:
    d = dict(row)
    d["tags"] = json.loads(d.pop("tags_json") or "[]")
    d["metadata"] = json.loads(d.pop("metadata_json") or "{}")
    d["actions"] = json.loads(d.pop("actions_json") or "[]")
    outcome = d.pop("outcome_json", None)
    d["outcome"] = json.loads(outcome) if outcome else None
    return d


def list_state(
    *,
    status: str | list[str] | None = None,
    workspace: str | None = None,
    source: str | None = None,
    parent_id: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if status:
        statuses = [status] if isinstance(status, str) else list(status)
        placeholders = ",".join(["%s"] * len(statuses))
        clauses.append(f"status IN ({placeholders})")
        params.extend(statuses)
    if workspace is not None:
        clauses.append("workspace = %s")
        params.append(workspace)
    if source:
        clauses.append("source = %s")
        params.append(source)
    if parent_id:
        clauses.append("parent_id = %s")
        params.append(parent_id)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with _conn() as c:
        rows = c.execute(
            f"SELECT * FROM agent_state {where} ORDER BY updated_at DESC LIMIT %s",
            (*params, limit),
        ).fetchall()
    return [_row_to_state(r) for r in rows]


def get_state(envelope_id: str) -> dict[str, Any] | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM agent_state WHERE id = %s", (envelope_id,)
        ).fetchone()
    return _row_to_state(row) if row else None


def list_events(
    *,
    envelope_id: str | None = None,
    parent_id: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if envelope_id:
        clauses.append("id = %s")
        params.append(envelope_id)
    if parent_id:
        clauses.append("parent_id = %s")
        params.append(parent_id)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with _conn() as c:
        rows = c.execute(
            f"SELECT seq, id, source, type, status, parent_id, ts, envelope "
            f"FROM agent_events {where} ORDER BY seq ASC LIMIT %s",
            (*params, limit),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        env_raw = d.pop("envelope", None)
        d["envelope"] = json.loads(env_raw) if env_raw else None
        out.append(d)
    return out


def list_attention(workspace: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    """Convenience: state rows the attention aggregator should surface."""
    return list_state(status=list(ATTENTION_STATUSES), workspace=workspace, limit=limit)


def search_events(
    *,
    q: str = "",
    type_prefix: str = "",
    workspace: str = "",
    status: str = "",
    source: str = "",
    since_ms: int = 0,
    until_ms: int = 0,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Postgres fallback for event history search (used when the OpenSearch
    sink is not configured). Filters are exact/prefix; ``q`` degrades to a
    substring scan over the raw envelope JSON — no ranking, fine at this
    scale."""
    clauses: list[str] = []
    params: list[Any] = []
    if type_prefix:
        clauses.append("type LIKE %s")
        params.append(type_prefix + "%")
    if workspace:
        clauses.append("envelope LIKE %s")  # workspace isn't a column on agent_events
        params.append(f'%"workspace":"{workspace}"%')
    if status:
        clauses.append("status = %s")
        params.append(status)
    if source:
        clauses.append("source = %s")
        params.append(source)
    if since_ms:
        clauses.append("ts >= %s")
        params.append(since_ms)
    if until_ms:
        clauses.append("ts <= %s")
        params.append(until_ms)
    if q:
        clauses.append("envelope ILIKE %s")
        params.append(f"%{q}%")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with _conn() as c:
        rows = c.execute(
            f"SELECT seq, id, source, type, status, parent_id, ts, envelope "
            f"FROM agent_events {where} ORDER BY seq DESC LIMIT %s",
            (*params, limit),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        env_raw = d.pop("envelope", None)
        d["envelope"] = json.loads(env_raw) if env_raw else None
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# Async wrappers
# ---------------------------------------------------------------------------


async def async_ingest(envelope: AgentEnvelope | dict[str, Any]) -> AgentEnvelope:
    return await asyncio.to_thread(ingest, envelope)


async def async_ingest_batch(envelopes: list[AgentEnvelope | dict[str, Any]]) -> list[AgentEnvelope]:
    return await asyncio.to_thread(ingest_batch, envelopes)


# ---------------------------------------------------------------------------
# Action outcome recording
# ---------------------------------------------------------------------------

# Standard actor strings. Use ACTOR_USER for HTTP requests originating from a
# human, ACTOR_SYSTEM for daemon/scheduler internals, and "agent:<name>" for
# automation rules.
ACTOR_USER = "user"
ACTOR_SYSTEM = "system"


async def arecord_action(
    target_id: str,
    action_name: str,
    actor: str,
    fn: Callable[[], Any],
) -> Any:
    """Run an action coroutine, time it, and write an `action.<name>` envelope
    to the bus with parent_id linking back to `target_id`. The envelope's
    `status` is always 'done'; `outcome.ok` tells the consumer whether the
    underlying action succeeded.

    Returns whatever `fn` returned; re-raises any exception fn raised after
    recording the failure outcome.
    """
    start_ms = int(time.time() * 1000)
    err_msg: str | None = None
    ok = True
    try:
        result = fn()
        if asyncio.iscoroutine(result):
            result = await result
        return result
    except Exception as exc:
        ok = False
        err_msg = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        end_ms = int(time.time() * 1000)
        duration = end_ms - start_ms
        try:
            await async_ingest(AgentEnvelope(
                id=f"action:{target_id}:{action_name}:{start_ms}",
                kind="event",
                source="brainbox-hub",
                type=f"action.{action_name}",
                status="done",
                title=f"action {action_name}",
                parent_id=target_id,
                tags=["action", action_name],
                start_at=start_ms,
                end_at=end_ms,
                metadata={"target": target_id},
                outcome=ActionOutcome(
                    ok=ok,
                    actor=actor,
                    error=err_msg,
                    duration_ms=duration,
                ),
            ))
        except Exception:
            # Recording must not break the action itself.
            pass


# ---------------------------------------------------------------------------
# Adapters: brainbox-internal producers → envelope
# ---------------------------------------------------------------------------

# TaskStatus.value → envelope status. CANCELLED maps to 'done' (terminal, not
# attention-worthy); BLOCKED/NEEDS_ACTION map through directly so they surface.
_TASK_STATUS_MAP: dict[str, str] = {
    "pending":      "upcoming",
    "running":      "active",
    "completed":    "done",
    "failed":       "failed",
    "cancelled":    "done",
    "blocked":      "blocked",
    "needs_action": "needs_action",
}


def envelope_from_hub_task(event: str, task: Any) -> AgentEnvelope:
    """Translate a brainbox router task-lifecycle event into the unified envelope.

    `event` is the dotted router event name (e.g. 'task.queued', 'task.failed').
    `task` is the `brainbox.models.Task` instance.
    """
    status_raw = getattr(getattr(task, "status", None), "value", None) or "pending"
    mapped = _TASK_STATUS_MAP.get(status_raw, status_raw)

    title = (task.description or "").strip().splitlines()[0][:120] if task.description else task.id
    subtitle_parts = [p for p in (task.agent_name, getattr(task, "session_name", None)) if p]
    subtitle = " · ".join(subtitle_parts) if subtitle_parts else None

    metadata: dict[str, Any] = {
        "agent_name": task.agent_name,
        "attempts": getattr(task, "attempts", 0),
        "max_attempts": getattr(task, "max_attempts", 1),
        "runner_name": getattr(task, "runner_name", None),
        "backend": getattr(task, "backend", None),
        "session_name": getattr(task, "session_name", None),
        "origin_rule_id": getattr(task, "origin_rule_id", None),
    }
    if getattr(task, "last_error", None):
        metadata["last_error"] = task.last_error
    # Only stamp nonzero depths so non-rule tasks keep clean envelopes.
    if getattr(task, "rule_chain_depth", 0):
        metadata["rule_chain_depth"] = task.rule_chain_depth

    return AgentEnvelope(
        id=f"hub-task:{task.id}",
        kind="event",
        source="brainbox-hub",
        type=event,
        status=mapped,
        title=title,
        subtitle=subtitle,
        workspace=getattr(task, "workspace_profile", None),
        parent_id=(f"hub-task:{task.job_id}" if getattr(task, "job_id", None) and task.job_id != task.id else None),
        start_at=getattr(task, "created_at", None),
        end_at=getattr(task, "updated_at", None) if mapped in ("done", "failed") else None,
        tags=["hub-task"],
        metadata={k: v for k, v in metadata.items() if v is not None},
    )


# Playbook.status → envelope status.
_PLAYBOOK_STATUS_MAP: dict[str, str] = {
    "idle":      "upcoming",
    "running":   "active",
    "completed": "done",
    "failed":    "failed",
    "cancelled": "done",
}


def _playbook_workspace(pb: Any) -> str | None:
    """Playbooks store the literal string 'global' for all-profiles; the bus
    convention is workspace=None for unscoped things. Normalize so
    profile-scoped rules don't match global playbooks as if owned."""
    ws = getattr(pb, "workspace_profile", None)
    return None if ws in (None, "", "global") else ws


def envelope_from_playbook(event: str, data: Any) -> AgentEnvelope | None:
    """Translate a playbook lifecycle event into the unified envelope.

    Lifecycle events (playbook.created/updated/started/completed/failed/
    cancelled) carry the Playbook model; per-task events (task_started/
    task_done) carry {"playbook_id", "task_id"[, "status"]} dicts and upsert
    the same `playbook:{id}` envelope so agent_state stays one-row-per-playbook.
    Returns None for shapes we can't resolve.
    """
    if isinstance(data, dict):
        pb_id = data.get("playbook_id")
        if not pb_id:
            return None
        from . import playbooks as _playbooks_mod

        pb = _playbooks_mod.get_playbook(pb_id)
        title = pb.name if pb else pb_id
        metadata: dict[str, Any] = {k: v for k, v in data.items() if k != "playbook_id"}
        if pb and getattr(pb, "origin_rule_id", None):
            metadata["origin_rule_id"] = pb.origin_rule_id
        if pb and getattr(pb, "rule_chain_depth", 0):
            metadata["rule_chain_depth"] = pb.rule_chain_depth
        return AgentEnvelope(
            id=f"playbook:{pb_id}",
            kind="event",
            source="brainbox-hub",
            type=event,
            status=_PLAYBOOK_STATUS_MAP.get(pb.status, pb.status) if pb else None,
            title=title,
            workspace=_playbook_workspace(pb) if pb else None,
            tags=["playbook"],
            metadata=metadata,
        )

    pb = data
    pb_id = getattr(pb, "id", None)
    if not pb_id:
        return None
    status_raw = getattr(pb, "status", "idle")
    tasks = getattr(pb, "tasks", []) or []
    metadata = {
        "tasks_total": len(tasks),
        "tasks_done": sum(1 for t in tasks if getattr(t, "status", "") == "completed"),
        "runner": getattr(pb, "runner", None),
        "origin_rule_id": getattr(pb, "origin_rule_id", None),
    }
    if getattr(pb, "rule_chain_depth", 0):
        metadata["rule_chain_depth"] = pb.rule_chain_depth
    return AgentEnvelope(
        id=f"playbook:{pb_id}",
        kind="event",
        source="brainbox-hub",
        type=event,
        status=_PLAYBOOK_STATUS_MAP.get(status_raw, status_raw),
        title=getattr(pb, "name", pb_id),
        workspace=_playbook_workspace(pb),
        start_at=getattr(pb, "started_at", None),
        end_at=getattr(pb, "finished_at", None),
        tags=["playbook"],
        metadata={k: v for k, v in metadata.items() if v is not None},
    )


def envelope_from_channel(event: str, data: Any) -> AgentEnvelope | None:
    """Translate a channel event into the unified envelope.

    channel.message returns None — it is the only high-frequency event on the
    hub, and pushing every chat message through the durable bus (and through
    rule evaluation) invites rule storms for no attention-model gain. SSE
    delivery of messages is unaffected.
    """
    if event == "channel.message":
        return None

    if isinstance(data, dict):
        ch_id = data.get("channel_id")
        if not ch_id:
            return None
        from . import channels as _channels_mod

        ch = _channels_mod.get_channel(ch_id)
        metadata = {k: v for k, v in data.items() if k != "channel_id" and not hasattr(v, "model_dump")}
        return AgentEnvelope(
            id=f"channel:{ch_id}",
            kind="event",
            source="brainbox-hub",
            type=event,
            status=("done" if ch and ch.status == "completed" else "active") if ch else None,
            title=ch.name if ch else ch_id,
            workspace=getattr(ch, "workspace_profile", None) if ch else None,
            parent_id=(
                f"hub-task:{ch.parent_task_id}" if ch and getattr(ch, "parent_task_id", None) else None
            ),
            tags=["channel"],
            metadata=metadata,
        )

    ch = data
    ch_id = getattr(ch, "id", None)
    if not ch_id:
        return None
    return AgentEnvelope(
        id=f"channel:{ch_id}",
        kind="event",
        source="brainbox-hub",
        type=event,
        status="done" if getattr(ch, "status", "") == "completed" else "active",
        title=getattr(ch, "name", ch_id),
        workspace=getattr(ch, "workspace_profile", None),
        parent_id=(
            f"hub-task:{ch.parent_task_id}" if getattr(ch, "parent_task_id", None) else None
        ),
        start_at=getattr(ch, "created_at", None),
        end_at=getattr(ch, "completed_at", None),
        tags=["channel"],
        metadata={"participants": len(getattr(ch, "participants", []) or [])},
    )

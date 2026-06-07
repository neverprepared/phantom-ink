"""Cross-machine agent event bus — typed API over the agent_state + agent_events tables.

Two storage layers behind one ingest call:
- `agent_state`: upsert by envelope `id`. One row per logical thing; status mutates
  in place. This is what the attention view and dashboards read.
- `agent_events`: append-only. Every envelope received is written here with an
  auto-increment `seq`. Audit log; supports history drill-down and replay.

Envelopes conform to `contracts/timeline-entry.schema.json` (v2). The envelope is
shared with collection-script output, but for agent-bus use the `source` and
`type` fields are required and `parent_id` / `outcome` are commonly populated.

All functions are synchronous (matching `store.py`); async wrappers run them via
`asyncio.to_thread`.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable, Iterable

from pydantic import BaseModel, Field

from .store import _db, _lock

# Statuses that the attention aggregator surfaces. Producers can use any status
# in the schema; only these three pull a card into the user's face.
ATTENTION_STATUSES: tuple[str, ...] = ("failed", "blocked", "needs_action")

# ---------------------------------------------------------------------------
# Pydantic envelope (mirrors timeline-entry.schema.json v2)
# ---------------------------------------------------------------------------


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
    status: str | None = None
    subtitle: str | None = None
    description: str | None = None
    workspace: str | None = None
    parent_id: str | None = None
    url: str | None = None
    start_at: int | None = None
    end_at: int | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    actions: list[dict[str, Any]] = Field(default_factory=list)
    outcome: ActionOutcome | None = None


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

    with _lock:
        db = _db()
        db.execute(
            """
            INSERT INTO agent_state (
                id, kind, source, type, status, title, subtitle, workspace,
                parent_id, url, start_at, end_at,
                tags_json, metadata_json, actions_json, outcome_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                kind          = excluded.kind,
                source        = excluded.source,
                type          = excluded.type,
                status        = COALESCE(excluded.status, agent_state.status),
                title         = excluded.title,
                subtitle      = excluded.subtitle,
                workspace     = COALESCE(excluded.workspace, agent_state.workspace),
                parent_id     = COALESCE(excluded.parent_id, agent_state.parent_id),
                url           = excluded.url,
                start_at      = COALESCE(excluded.start_at, agent_state.start_at),
                end_at        = excluded.end_at,
                tags_json     = excluded.tags_json,
                metadata_json = excluded.metadata_json,
                actions_json  = excluded.actions_json,
                outcome_json  = excluded.outcome_json,
                updated_at    = excluded.updated_at
            """,
            (
                env.id,
                env.kind,
                env.source,
                env.type,
                env.status,
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
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (env.id, env.source, env.type, env.status, env.parent_id, now, raw_json),
        )
        db.commit()

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
        placeholders = ",".join("?" * len(statuses))
        clauses.append(f"status IN ({placeholders})")
        params.extend(statuses)
    if workspace is not None:
        clauses.append("workspace = ?")
        params.append(workspace)
    if source:
        clauses.append("source = ?")
        params.append(source)
    if parent_id:
        clauses.append("parent_id = ?")
        params.append(parent_id)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = _db().execute(
        f"SELECT * FROM agent_state {where} ORDER BY updated_at DESC LIMIT ?",
        (*params, limit),
    ).fetchall()
    return [_row_to_state(r) for r in rows]


def get_state(envelope_id: str) -> dict[str, Any] | None:
    row = _db().execute(
        "SELECT * FROM agent_state WHERE id = ?", (envelope_id,)
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
        clauses.append("id = ?")
        params.append(envelope_id)
    if parent_id:
        clauses.append("parent_id = ?")
        params.append(parent_id)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = _db().execute(
        f"SELECT seq, id, source, type, status, parent_id, ts, envelope "
        f"FROM agent_events {where} ORDER BY seq ASC LIMIT ?",
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


# ---------------------------------------------------------------------------
# Async wrappers
# ---------------------------------------------------------------------------


async def async_ingest(envelope: AgentEnvelope | dict[str, Any]) -> AgentEnvelope:
    return await asyncio.to_thread(ingest, envelope)


async def async_ingest_batch(envelopes: list[AgentEnvelope | dict[str, Any]]) -> list[AgentEnvelope]:
    return await asyncio.to_thread(ingest_batch, envelopes)


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
    }
    if getattr(task, "last_error", None):
        metadata["last_error"] = task.last_error

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

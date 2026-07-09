"""Event rules engine — EventBridge-style rules over the agent event bus.

A durable consumer walks `agent_events` by `seq` with a persisted cursor
(`event_rule_cursor`), matches each envelope against enabled rules
(`event_rules`, patterns per event_match.py), and enqueues one
`event_rule_executions` row per (rule, event, action). Executions run under
bounded concurrency; the audit rows double as a dead-letter queue.

Delivery contract:
- Match-and-enqueue is effectively-once: executions insert + cursor advance
  happen in ONE transaction, and UNIQUE(rule_id, event_seq, action_index)
  absorbs crash-window re-reads.
- Execution is at-least-once: a crash mid-action leaves a 'running' row that
  is requeued on boot (recover_stuck_running), so an action may run twice.

Loop prevention (both required — rule-created work re-enters the bus):
- Chain depth: actions stamp origin_rule_id + rule_chain_depth = parent+1
  into what they create; the consumer refuses to fire on events whose
  metadata carries depth >= settings.rules.max_chain_depth.
- Per-rule rate limit: an in-memory 60s sliding window; over-limit matches
  are recorded as 'throttled' executions (audited, never run).

Single hub process is a hard assumption (matches router/_tasks in-memory
state); the single cursor row is only safe under it.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from collections import deque
from typing import Any, Annotated, Literal

from pydantic import BaseModel, Field

from .config import settings
from .log import get_logger
from .models import ModelTarget
from .store import _conn
from . import agent_store, event_match

log = get_logger()

CURSOR_NAME = "consumer"


def _now_ms() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class SubmitTaskAction(BaseModel):
    type: Literal["submit_task"] = "submit_task"
    description: str  # templated
    agent_name: str
    workspace_profile: str | None = None  # None = inherit event.workspace
    repo_url: str | None = None  # templated
    priority: int = 0
    backend: str = "docker"
    runner: str | None = None
    model_target: ModelTarget | None = None


class RunPlaybookAction(BaseModel):
    type: Literal["run_playbook"] = "run_playbook"
    playbook: str  # playbook id, falling back to unique name match
    workspace_profile: str | None = None  # None = inherit event.workspace
    runner: str | None = None


class StartLoopAction(BaseModel):
    type: Literal["start_loop"] = "start_loop"
    template_name: str
    artifact_refs: dict[str, Any] = Field(default_factory=dict)  # string leaves templated
    workspace_profile: str | None = None
    workspace_home: str | None = None


class WebhookAction(BaseModel):
    type: Literal["webhook"] = "webhook"
    url: str  # fixed destination — never templated
    headers: dict[str, str] = Field(default_factory=dict)  # values templated
    body: dict[str, Any] | None = None  # None = full envelope; string leaves templated
    timeout_s: float | None = None  # None = settings.rules.webhook_timeout_s


class RunScriptAction(BaseModel):
    type: Literal["run_script"] = "run_script"
    # Fixed argv — the event reaches the script via stdin JSON and
    # BRAINBOX_* env vars ONLY. Never string-interpolated into the command.
    argv: list[str] = Field(min_length=1)
    cwd: str | None = None
    timeout_s: float | None = None  # None = settings.rules.script_timeout_s


RuleAction = Annotated[
    SubmitTaskAction | RunPlaybookAction | StartLoopAction | WebhookAction | RunScriptAction,
    Field(discriminator="type"),
]


class EventRule(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str
    profile: str = ""  # "" or "global" = matches all workspaces
    enabled: bool = True
    description: str | None = None
    pattern: dict[str, Any]
    actions: list[RuleAction] = Field(min_length=1)
    created_at: int = Field(default_factory=_now_ms)
    updated_at: int = Field(default_factory=_now_ms)
    last_triggered_at: int | None = None
    trigger_count: int = 0


ExecutionStatus = Literal["queued", "running", "ok", "failed", "throttled", "dead"]


class RuleExecution(BaseModel):
    id: int
    rule_id: str
    event_seq: int
    event_id: str
    action_index: int
    action_type: str
    status: ExecutionStatus
    attempts: int = 0
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: int
    updated_at: int


# ---------------------------------------------------------------------------
# Templating
# ---------------------------------------------------------------------------

_PLACEHOLDER = re.compile(r"\{([a-zA-Z0-9_.-]+)\}")

# Addressable roots. metadata.* / outcome.* resolve by dotted path.
_TOP_FIELDS = (
    "seq", "ts", "id", "kind", "type", "source", "status", "title", "subtitle",
    "description", "workspace", "parent_id", "url", "tags",
)


def _lookup(doc: dict[str, Any], path: str) -> Any:
    if path == "envelope":
        return {k: v for k, v in doc.items() if k not in ("seq", "ts")}
    parts = path.split(".")
    if parts[0] not in _TOP_FIELDS and parts[0] not in ("metadata", "outcome"):
        return None
    cur: Any = doc
    for part in parts:
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _render(template: str, doc: dict[str, Any]) -> str:
    """Single-pass {placeholder} substitution from the event document.

    Missing paths render as "". Non-string values render as JSON (dict/list)
    or str(). '{{' / '}}' escape literal braces. Substituted values are never
    re-expanded — a value containing '{...}' stays literal.
    """
    # Protect escaped braces before placeholder substitution.
    protected = template.replace("{{", "\x00").replace("}}", "\x01")

    def _sub(m: re.Match[str]) -> str:
        value = _lookup(doc, m.group(1))
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return json.dumps(value, separators=(",", ":"))
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    return _PLACEHOLDER.sub(_sub, protected).replace("\x00", "{").replace("\x01", "}")


def _render_leaves(value: Any, doc: dict[str, Any]) -> Any:
    """Recursively template string leaves of a dict/list structure."""
    if isinstance(value, str):
        return _render(value, doc)
    if isinstance(value, dict):
        return {k: _render_leaves(v, doc) for k, v in value.items()}
    if isinstance(value, list):
        return [_render_leaves(v, doc) for v in value]
    return value


# ---------------------------------------------------------------------------
# DAOs — rules
# ---------------------------------------------------------------------------


def _row_to_rule(row: Any) -> EventRule:
    d = dict(row)
    d["enabled"] = bool(d["enabled"])
    d["pattern"] = json.loads(d["pattern"])
    d["actions"] = json.loads(d["actions"])
    return EventRule(**d)


def upsert_rule(rule: EventRule) -> EventRule:
    with _conn() as c:
        c.execute(
            """
            INSERT INTO event_rules (
                id, name, profile, enabled, description, pattern, actions,
                created_at, updated_at, last_triggered_at, trigger_count
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                name        = EXCLUDED.name,
                profile     = EXCLUDED.profile,
                enabled     = EXCLUDED.enabled,
                description = EXCLUDED.description,
                pattern     = EXCLUDED.pattern,
                actions     = EXCLUDED.actions,
                updated_at  = EXCLUDED.updated_at
            """,
            (
                rule.id,
                rule.name,
                rule.profile,
                1 if rule.enabled else 0,
                rule.description,
                json.dumps(rule.pattern),
                json.dumps([a.model_dump(exclude_none=True) for a in rule.actions]),
                rule.created_at,
                _now_ms(),
                rule.last_triggered_at,
                rule.trigger_count,
            ),
        )
    return get_rule(rule.id)  # type: ignore[return-value]


def get_rule(rule_id: str) -> EventRule | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM event_rules WHERE id = %s", (rule_id,)).fetchone()
    return _row_to_rule(row) if row else None


def list_rules(profile: str | None = None, enabled: bool | None = None) -> list[EventRule]:
    """List rules. ``profile`` returns that profile's rules plus global
    (''/'global') ones, mirroring playbooks.list_playbooks."""
    clauses: list[str] = []
    params: list[Any] = []
    if profile is not None:
        clauses.append("profile IN ('', 'global', %s)")
        params.append(profile)
    if enabled is not None:
        clauses.append("enabled = %s")
        params.append(1 if enabled else 0)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with _conn() as c:
        rows = c.execute(
            f"SELECT * FROM event_rules {where} ORDER BY created_at ASC", params
        ).fetchall()
    return [_row_to_rule(r) for r in rows]


def delete_rule(rule_id: str) -> bool:
    with _conn() as c:
        cur = c.execute("DELETE FROM event_rules WHERE id = %s", (rule_id,))
        return cur.rowcount > 0


def set_rule_enabled(rule_id: str, enabled: bool) -> EventRule | None:
    with _conn() as c:
        c.execute(
            "UPDATE event_rules SET enabled = %s, updated_at = %s WHERE id = %s",
            (1 if enabled else 0, _now_ms(), rule_id),
        )
    return get_rule(rule_id)


# ---------------------------------------------------------------------------
# DAOs — cursor
# ---------------------------------------------------------------------------


def get_cursor() -> int | None:
    with _conn() as c:
        row = c.execute(
            "SELECT last_seq FROM event_rule_cursor WHERE name = %s", (CURSOR_NAME,)
        ).fetchone()
    return row["last_seq"] if row else None


def init_cursor_if_absent() -> int:
    """First boot: start at MAX(seq) so enabling the engine on an existing
    deployment never replays history through fresh rules. Replay is an
    explicit operator action (manual cursor UPDATE), not a default."""
    with _conn() as c:
        c.execute(
            """
            INSERT INTO event_rule_cursor (name, last_seq, updated_at)
            SELECT %s, COALESCE(MAX(seq), 0), %s FROM agent_events
            ON CONFLICT (name) DO NOTHING
            """,
            (CURSOR_NAME, _now_ms()),
        )
        row = c.execute(
            "SELECT last_seq FROM event_rule_cursor WHERE name = %s", (CURSOR_NAME,)
        ).fetchone()
    return row["last_seq"]


# ---------------------------------------------------------------------------
# DAOs — executions
# ---------------------------------------------------------------------------


def _row_to_execution(row: Any) -> RuleExecution:
    d = dict(row)
    d["result"] = json.loads(d["result"]) if d.get("result") else None
    return RuleExecution(**d)


def _commit_batch(
    to_insert: list[tuple[str, int, str, int, str, str]],
    triggered_rule_ids: list[str],
    new_cursor: int,
) -> None:
    """Insert executions, bump rule stats, advance the cursor — atomically.

    ``to_insert`` rows are (rule_id, event_seq, event_id, action_index,
    action_type, status). The unique key + DO NOTHING makes crash-window
    re-reads harmless.
    """
    now = _now_ms()
    with _conn() as db, db.transaction():
        for rule_id, event_seq, event_id, action_index, action_type, status in to_insert:
            db.execute(
                """
                INSERT INTO event_rule_executions (
                    rule_id, event_seq, event_id, action_index, action_type,
                    status, attempts, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, 0, %s, %s)
                ON CONFLICT (rule_id, event_seq, action_index) DO NOTHING
                """,
                (rule_id, event_seq, event_id, action_index, action_type, status, now, now),
            )
        for rule_id in triggered_rule_ids:
            db.execute(
                """
                UPDATE event_rules
                SET last_triggered_at = %s, trigger_count = trigger_count + 1
                WHERE id = %s
                """,
                (now, rule_id),
            )
        db.execute(
            "UPDATE event_rule_cursor SET last_seq = %s, updated_at = %s WHERE name = %s",
            (new_cursor, now, CURSOR_NAME),
        )


def claim_queued_executions(limit: int) -> list[RuleExecution]:
    with _conn() as c:
        rows = c.execute(
            """
            UPDATE event_rule_executions
            SET status = 'running', updated_at = %s
            WHERE id IN (
                SELECT id FROM event_rule_executions
                WHERE status = 'queued'
                ORDER BY id
                LIMIT %s
                FOR UPDATE SKIP LOCKED
            )
            RETURNING *
            """,
            (_now_ms(), limit),
        ).fetchall()
    return [_row_to_execution(r) for r in rows]


def finish_execution(
    execution_id: int,
    status: ExecutionStatus,
    *,
    attempts: int,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    with _conn() as c:
        c.execute(
            """
            UPDATE event_rule_executions
            SET status = %s, attempts = %s, result = %s, error = %s, updated_at = %s
            WHERE id = %s
            """,
            (
                status,
                attempts,
                json.dumps(result) if result is not None else None,
                error,
                _now_ms(),
                execution_id,
            ),
        )


def requeue_execution(execution_id: int) -> RuleExecution | None:
    """Requeue a terminal execution (DLQ retry). Returns None when the row is
    missing or not in a requeueable state."""
    with _conn() as c:
        row = c.execute(
            """
            UPDATE event_rule_executions
            SET status = 'queued', attempts = 0, error = NULL, updated_at = %s
            WHERE id = %s AND status IN ('dead', 'failed', 'throttled')
            RETURNING *
            """,
            (_now_ms(), execution_id),
        ).fetchone()
    return _row_to_execution(row) if row else None


def get_execution(execution_id: int) -> RuleExecution | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM event_rule_executions WHERE id = %s", (execution_id,)
        ).fetchone()
    return _row_to_execution(row) if row else None


def list_executions(
    *,
    rule_id: str | None = None,
    status: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[RuleExecution]:
    clauses: list[str] = []
    params: list[Any] = []
    if rule_id:
        clauses.append("rule_id = %s")
        params.append(rule_id)
    if status:
        clauses.append("status = %s")
        params.append(status)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with _conn() as c:
        rows = c.execute(
            f"SELECT * FROM event_rule_executions {where} ORDER BY id DESC LIMIT %s OFFSET %s",
            (*params, limit, offset),
        ).fetchall()
    return [_row_to_execution(r) for r in rows]


def recover_stuck_running(older_than_ms: int) -> int:
    """Requeue 'running' rows older than the threshold (crash recovery)."""
    with _conn() as c:
        cur = c.execute(
            """
            UPDATE event_rule_executions
            SET status = 'queued', updated_at = %s
            WHERE status = 'running' AND updated_at < %s
            """,
            (_now_ms(), _now_ms() - older_than_ms),
        )
        return cur.rowcount


def fetch_events_after(seq: int, limit: int) -> list[dict[str, Any]]:
    with _conn() as c:
        rows = c.execute(
            "SELECT seq, id, ts, envelope FROM agent_events "
            "WHERE seq > %s ORDER BY seq ASC LIMIT %s",
            (seq, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def _load_event_doc(seq: int) -> dict[str, Any] | None:
    with _conn() as c:
        row = c.execute(
            "SELECT seq, ts, envelope FROM agent_events WHERE seq = %s", (seq,)
        ).fetchone()
    if not row:
        return None
    doc = json.loads(row["envelope"])
    doc["seq"] = row["seq"]
    doc["ts"] = row["ts"]
    return doc


# ---------------------------------------------------------------------------
# Matching gates
# ---------------------------------------------------------------------------


def _profile_ok(rule_profile: str, event_workspace: Any) -> bool:
    if rule_profile in ("", "global"):
        return True
    return event_workspace == rule_profile


def _chain_depth(doc: dict[str, Any]) -> int:
    meta = doc.get("metadata")
    if not isinstance(meta, dict):
        return 0
    depth = meta.get("rule_chain_depth", 0)
    return depth if isinstance(depth, int) and not isinstance(depth, bool) else 0


# Per-rule sliding 60s window of fire timestamps (time.monotonic()).
# In-memory is acceptable: single process, and throttles land in the durable
# audit trail anyway.
_rate_window: dict[str, deque[float]] = {}


def _rate_limited(rule_id: str) -> bool:
    window = _rate_window.setdefault(rule_id, deque())
    now = time.monotonic()
    while window and now - window[0] > 60.0:
        window.popleft()
    if len(window) >= settings.rules.rate_limit_per_minute:
        return True
    window.append(now)
    return False


# ---------------------------------------------------------------------------
# Action executors
# ---------------------------------------------------------------------------

# Action types whose failures are worth retrying (transient causes: network
# flake, 5xx, script hiccup). Task / playbook / loop dispatch failures are
# config errors retry can't fix — and retrying them risks double-created
# work; submitted tasks already have their own max_attempts machinery in the
# scheduler.
_RETRYABLE_TYPES: frozenset[str] = frozenset({"webhook", "run_script"})


class _PermanentActionError(RuntimeError):
    """An action failure that must not be retried (e.g. webhook 4xx)."""


def _cap(text: str) -> str:
    cap = settings.rules.output_cap_bytes
    raw = text.encode("utf-8", errors="replace")
    if len(raw) <= cap:
        return text
    return raw[:cap].decode("utf-8", errors="replace") + "…[truncated]"


async def _exec_submit_task(
    action: SubmitTaskAction, rule: EventRule, doc: dict[str, Any]
) -> dict[str, Any]:
    from . import router

    task = await router.submit_task(
        _render(action.description, doc),
        action.agent_name,
        repo_url=_render(action.repo_url, doc) if action.repo_url else None,
        workspace_profile=action.workspace_profile or doc.get("workspace"),
        runner=action.runner,
        backend=action.backend,
        priority=action.priority,
        model_target=action.model_target,
        origin_rule_id=rule.id,
        rule_chain_depth=_chain_depth(doc) + 1,
    )
    return {"task_id": task.id}


async def _exec_run_playbook(
    action: RunPlaybookAction, rule: EventRule, doc: dict[str, Any]
) -> dict[str, Any]:
    from . import playbooks

    pb = playbooks.get_playbook(action.playbook)
    if pb is None:
        named = [p for p in playbooks.list_playbooks() if p.name == action.playbook]
        if len(named) == 1:
            pb = named[0]
        elif len(named) > 1:
            raise _PermanentActionError(
                f"playbook name '{action.playbook}' is ambiguous ({len(named)} matches) — use the id"
            )
    if pb is None:
        raise _PermanentActionError(f"playbook '{action.playbook}' not found")

    started = await playbooks.run_playbook(
        pb.id,
        workspace_profile=action.workspace_profile or doc.get("workspace"),
        runner=action.runner,
        origin_rule_id=rule.id,
        rule_chain_depth=_chain_depth(doc) + 1,
    )
    return {"playbook_id": started.id}


async def _exec_start_loop(
    action: StartLoopAction, rule: EventRule, doc: dict[str, Any]
) -> dict[str, Any]:
    from . import loop_runner
    from .loop_template import load_template
    from .loops import HandoffEnvelope

    spec = load_template(action.template_name)  # TemplateError → dead (permanent)
    envelope = HandoffEnvelope.model_validate(
        {"artifact_refs": _render_leaves(action.artifact_refs, doc)}
    )
    inst = await loop_runner.start_loop(
        spec,
        envelope,
        workspace_profile=action.workspace_profile or doc.get("workspace"),
        workspace_home=action.workspace_home,
        origin_rule_id=rule.id,
        rule_chain_depth=_chain_depth(doc) + 1,
    )
    return {"loop_id": inst.id, "parent_task_id": inst.parent_task_id}


async def _exec_webhook(
    action: WebhookAction, rule: EventRule, doc: dict[str, Any]
) -> dict[str, Any]:
    # LAN-safe outbound HTTP: httpx is broken for LAN destinations on this
    # daemon (see CLAUDE.md known issue) — all outbound calls go via curl.
    from .ollama import acurl_request

    if action.body is not None:
        payload = _render_leaves(action.body, doc)
        if not isinstance(payload, dict):
            payload = {"body": payload}
    else:
        payload = {k: v for k, v in doc.items() if k not in ("seq", "ts")}
    payload["_brainbox"] = {
        "rule_id": rule.id,
        "rule_name": rule.name,
        "event_seq": doc.get("seq"),
        "chain_depth": _chain_depth(doc) + 1,
    }
    headers = {k: _render(v, doc) for k, v in action.headers.items()}
    timeout = action.timeout_s or settings.rules.webhook_timeout_s

    status, body_text = await acurl_request(
        "POST", action.url, "", body=payload, headers=headers, timeout=timeout
    )
    result = {"http_status": status, "body": _cap(body_text)}
    if 200 <= status < 300:
        return result
    if 400 <= status < 500:
        raise _PermanentActionError(f"webhook returned {status}: {_cap(body_text)}")
    raise RuntimeError(f"webhook returned {status}: {_cap(body_text)}")


async def _exec_run_script(
    action: RunScriptAction, rule: EventRule, doc: dict[str, Any]
) -> dict[str, Any]:
    import os

    # Defense in depth: the API gate rejects run_script rules while disabled,
    # but a rule created before the flag was flipped off must not execute.
    if not settings.rules.allow_run_script:
        raise _PermanentActionError(
            "run_script actions are disabled (CL_RULES__ALLOW_RUN_SCRIPT)"
        )

    # The argv is fixed in the rule; the event reaches the script via stdin
    # JSON + env vars only. Nothing event-derived touches the command line.
    env = {
        **os.environ,
        "BRAINBOX_EVENT_ID": str(doc.get("id") or ""),
        "BRAINBOX_EVENT_SEQ": str(doc.get("seq") or ""),
        "BRAINBOX_EVENT_TYPE": str(doc.get("type") or ""),
        "BRAINBOX_EVENT_STATUS": str(doc.get("status") or ""),
        "BRAINBOX_EVENT_WORKSPACE": str(doc.get("workspace") or ""),
        "BRAINBOX_RULE_ID": rule.id,
        "BRAINBOX_RULE_NAME": rule.name,
        "BRAINBOX_CHAIN_DEPTH": str(_chain_depth(doc) + 1),
    }
    timeout = action.timeout_s or settings.rules.script_timeout_s
    proc = await asyncio.create_subprocess_exec(
        *action.argv,
        cwd=action.cwd,
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(json.dumps(doc).encode()), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"script timed out after {timeout}s")

    result = {
        "exit_code": proc.returncode,
        "stdout": _cap(stdout.decode("utf-8", errors="replace")),
        "stderr": _cap(stderr.decode("utf-8", errors="replace")),
    }
    if proc.returncode != 0:
        raise RuntimeError(f"script exited {proc.returncode}: {result['stderr']}")
    return result


async def _execute_action(
    action: Any, rule: EventRule, doc: dict[str, Any]
) -> dict[str, Any]:
    if action.type == "submit_task":
        return await _exec_submit_task(action, rule, doc)
    if action.type == "run_playbook":
        return await _exec_run_playbook(action, rule, doc)
    if action.type == "start_loop":
        return await _exec_start_loop(action, rule, doc)
    if action.type == "webhook":
        return await _exec_webhook(action, rule, doc)
    if action.type == "run_script":
        return await _exec_run_script(action, rule, doc)
    raise RuntimeError(f"no executor for action type '{action.type}' in this build")


def _timeout_for(action: Any) -> float:
    """Outer backstop timeout for one execution attempt. webhook/run_script
    enforce their own precise timeouts internally (curl --max-time,
    proc.kill), so the outer wait_for gets headroom to let those fire first
    and produce their specific error messages."""
    if action.type == "webhook":
        return (action.timeout_s or settings.rules.webhook_timeout_s) + 10.0
    if action.type == "run_script":
        return (action.timeout_s or settings.rules.script_timeout_s) + 10.0
    return settings.rules.dispatch_timeout_s


async def _run_execution(ex: RuleExecution) -> None:
    assert _sema is not None
    async with _sema:
        try:
            rule = await asyncio.to_thread(get_rule, ex.rule_id)
            doc = await asyncio.to_thread(_load_event_doc, ex.event_seq)
            if rule is None or doc is None or ex.action_index >= len(rule.actions):
                await asyncio.to_thread(
                    finish_execution, ex.id, "dead",
                    attempts=ex.attempts,
                    error="rule, event, or action no longer available",
                )
                return
            action = rule.actions[ex.action_index]
            attempts = ex.attempts
            while True:
                attempts += 1
                try:
                    result = await asyncio.wait_for(
                        _execute_action(action, rule, doc), timeout=_timeout_for(action)
                    )
                    await asyncio.to_thread(
                        finish_execution, ex.id, "ok", attempts=attempts, result=result
                    )
                    log.info(
                        "rules.execution_ok",
                        metadata={"rule": rule.id, "seq": ex.event_seq, "action": action.type},
                    )
                    return
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    err = f"{type(exc).__name__}: {exc}"
                    if (
                        action.type in _RETRYABLE_TYPES
                        and not isinstance(exc, _PermanentActionError)
                        and attempts < settings.rules.max_attempts
                    ):
                        await asyncio.sleep(
                            settings.rules.retry_backoff_s * 2 ** (attempts - 1)
                        )
                        continue
                    await asyncio.to_thread(
                        finish_execution, ex.id, "dead", attempts=attempts, error=_cap(err)
                    )
                    log.warning(
                        "rules.execution_dead",
                        metadata={"rule": ex.rule_id, "seq": ex.event_seq, "reason": err},
                    )
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Executor-machinery failure — never lose the row silently.
            try:
                await asyncio.to_thread(
                    finish_execution, ex.id, "dead",
                    attempts=ex.attempts, error=_cap(f"executor error: {exc}"),
                )
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Consumer loop
# ---------------------------------------------------------------------------

_consumer_task: asyncio.Task[None] | None = None
_loop_ref: asyncio.AbstractEventLoop | None = None
_wakeup: asyncio.Event = asyncio.Event()
_inflight: set[asyncio.Task] = set()
_sema: asyncio.Semaphore | None = None
_listener_registered = False


def notify() -> None:
    """Wake the consumer. Thread-safe: agent_store listeners fire in whatever
    thread called ingest (a to_thread worker for POST /api/agent_events)."""
    if _loop_ref is not None and not _loop_ref.is_closed():
        _loop_ref.call_soon_threadsafe(_wakeup.set)


async def _match_events(events: list[dict[str, Any]]) -> None:
    """Match a batch of events against enabled rules and commit executions
    + cursor atomically."""
    rules = await asyncio.to_thread(list_rules, None, True)
    to_insert: list[tuple[str, int, str, int, str, str]] = []
    triggered: list[str] = []
    if rules:
        max_depth = settings.rules.max_chain_depth
        for ev in events:
            try:
                doc = json.loads(ev["envelope"])
            except (json.JSONDecodeError, TypeError):
                continue
            doc["seq"] = ev["seq"]
            doc["ts"] = ev["ts"]
            depth = _chain_depth(doc)
            for rule in rules:
                if not _profile_ok(rule.profile, doc.get("workspace")):
                    continue
                if depth >= max_depth:
                    continue
                if not event_match.matches(rule.pattern, doc):
                    continue
                status = "throttled" if _rate_limited(rule.id) else "queued"
                for idx, action in enumerate(rule.actions):
                    to_insert.append(
                        (rule.id, ev["seq"], ev["id"], idx, action.type, status)
                    )
                triggered.append(rule.id)
    new_cursor = events[-1]["seq"]
    await asyncio.to_thread(_commit_batch, to_insert, triggered, new_cursor)


async def _dispatch_ready() -> list[asyncio.Task]:
    """Claim queued executions and start them under the semaphore."""
    claimed = await asyncio.to_thread(
        claim_queued_executions, settings.rules.max_concurrency * 2
    )
    started: list[asyncio.Task] = []
    loop = asyncio.get_running_loop()
    for ex in claimed:
        t = loop.create_task(_run_execution(ex))
        _inflight.add(t)
        t.add_done_callback(_inflight.discard)
        started.append(t)
    return started


async def run_once() -> int:
    """One consumer pass: drain new events, then run all ready executions to
    completion. Returns the number of events processed. Used by tests and
    available as a manual tick."""
    processed = 0
    cursor = await asyncio.to_thread(init_cursor_if_absent)
    while True:
        events = await asyncio.to_thread(
            fetch_events_after, cursor, settings.rules.batch_size
        )
        if not events:
            break
        await _match_events(events)
        cursor = events[-1]["seq"]
        processed += len(events)
    while True:
        started = await _dispatch_ready()
        if not started:
            break
        await asyncio.gather(*started, return_exceptions=True)
    return processed


async def _consumer_loop() -> None:
    await asyncio.to_thread(recover_stuck_running, settings.rules.stuck_running_ms)
    cursor = await asyncio.to_thread(init_cursor_if_absent)
    log.info("rules.consumer_started", metadata={"cursor": cursor})
    while True:
        try:
            await asyncio.wait_for(
                asyncio.shield(_wakeup.wait()), timeout=settings.rules.poll_interval_s
            )
        except asyncio.TimeoutError:
            pass
        _wakeup.clear()

        try:
            while True:  # drain backlog in batches
                events = await asyncio.to_thread(
                    fetch_events_after, cursor, settings.rules.batch_size
                )
                if not events:
                    break
                await _match_events(events)
                cursor = events[-1]["seq"]
            await _dispatch_ready()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # DB hiccup or similar — log and retry on the next tick. The
            # cursor only advances after a successful commit, so nothing
            # is skipped.
            log.warning("rules.consumer_error", metadata={"reason": str(exc)})


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def start() -> None:
    """Start the rules consumer (called from hub.init)."""
    global _consumer_task, _loop_ref, _sema, _listener_registered
    if not settings.rules.enabled:
        log.info("rules.disabled")
        return
    _loop_ref = asyncio.get_running_loop()
    _sema = asyncio.Semaphore(settings.rules.max_concurrency)
    if not _listener_registered:
        agent_store.on_event(lambda _env: notify())
        _listener_registered = True
    _consumer_task = _loop_ref.create_task(_consumer_loop())


async def stop() -> None:
    """Stop the consumer, draining in-flight executions (bounded)."""
    global _consumer_task
    if _consumer_task and not _consumer_task.done():
        _consumer_task.cancel()
    _consumer_task = None
    if _inflight:
        await asyncio.wait(set(_inflight), timeout=settings.rules.shutdown_drain_s)
    log.info("rules.stopped")


def reset_for_tests() -> None:
    global _consumer_task, _loop_ref, _sema, _listener_registered
    _consumer_task = None
    _loop_ref = None
    _sema = asyncio.Semaphore(settings.rules.max_concurrency)
    _listener_registered = False
    _wakeup.clear()
    _rate_window.clear()
    _inflight.clear()

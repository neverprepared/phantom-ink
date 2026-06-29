"""SQLite persistence layer for brainbox.

Write-through cache: _sessions in lifecycle.py remains the hot path.
The DB is written on mutation and read once at startup.

All SQL functions are synchronous and called via asyncio.to_thread from
async contexts, matching the existing hub.py pattern.
"""

import asyncio
import json
import sqlite3
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .loops import LoopInstance
    from .models import SessionContext
    from .runners import RunnerInfo

_conn: sqlite3.Connection | None = None
_lock = threading.Lock()


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        from .config import settings
        db_path = settings.db_file
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(db_path), check_same_thread=False)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        _conn.row_factory = sqlite3.Row
    return _conn


def reset_store_for_tests() -> None:
    """Replace the DB connection with a fresh in-memory DB and create all tables.

    Call this from test fixtures so tests never touch the real on-disk DB.
    """
    global _conn
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
        _conn = sqlite3.connect(":memory:", check_same_thread=False)
        _conn.execute("PRAGMA foreign_keys=ON")
        _conn.row_factory = sqlite3.Row
    init_db()


def init_db() -> None:
    """Create tables if they don't exist. Safe to call on every startup."""
    db = _db()
    with _lock:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_name  TEXT    PRIMARY KEY,
                runner_name   TEXT    NOT NULL,
                active        INTEGER NOT NULL DEFAULT 1,
                stopped_at    INTEGER,
                blob          TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_active
                ON sessions(active);
            CREATE INDEX IF NOT EXISTS idx_sessions_runner
                ON sessions(runner_name, active);

            CREATE TABLE IF NOT EXISTS runners (
                name           TEXT    PRIMARY KEY,
                capabilities   TEXT    NOT NULL,
                tags           TEXT    NOT NULL,
                version        TEXT    NOT NULL DEFAULT '',
                host           TEXT,
                machine_id     TEXT,
                max_concurrent INTEGER NOT NULL DEFAULT 4,
                last_seal_at   INTEGER,
                registered_at  INTEGER NOT NULL,
                updated_at     INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_runners_machine_id
                ON runners(machine_id);

            CREATE TABLE IF NOT EXISTS session_history (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                session_name TEXT    NOT NULL,
                runner_name  TEXT,
                backend      TEXT    NOT NULL DEFAULT 'docker',
                role         TEXT,
                state_final  TEXT    NOT NULL,
                created_at   INTEGER NOT NULL,
                stopped_at   INTEGER NOT NULL,
                task_id      TEXT,
                job_id       TEXT,
                repo_url     TEXT,
                reason       TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_history_stopped_at
                ON session_history(stopped_at);
            CREATE INDEX IF NOT EXISTS idx_history_runner
                ON session_history(runner_name);

            CREATE TABLE IF NOT EXISTS audit_log (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                ts           INTEGER NOT NULL,
                event        TEXT    NOT NULL,
                session_name TEXT,
                actor        TEXT,
                success      INTEGER NOT NULL DEFAULT 1,
                detail       TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_audit_ts
                ON audit_log(ts);
            CREATE INDEX IF NOT EXISTS idx_audit_event
                ON audit_log(event);

            -- Cross-machine agent event bus: current state (upsert by envelope id).
            -- One row per logical thing (a task, a chain run, a collected entry).
            -- Status mutates in place as events arrive.
            CREATE TABLE IF NOT EXISTS agent_state (
                id          TEXT PRIMARY KEY,
                kind        TEXT NOT NULL,            -- 'metric' | 'event'
                source      TEXT,                     -- '<producer>@<machine>'
                type        TEXT,                     -- last-seen dotted event type
                status      TEXT,                     -- upcoming|active|done|failed|blocked|needs_action
                title       TEXT NOT NULL,
                subtitle    TEXT,
                workspace   TEXT,
                parent_id   TEXT,
                url         TEXT,
                start_at    INTEGER,
                end_at      INTEGER,
                tags_json     TEXT NOT NULL DEFAULT '[]',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                actions_json  TEXT NOT NULL DEFAULT '[]',
                outcome_json  TEXT,
                created_at  INTEGER NOT NULL,
                updated_at  INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_agent_state_status
                ON agent_state(status, workspace, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_agent_state_parent
                ON agent_state(parent_id);

            -- Append-only audit log of every envelope received. Same envelope id
            -- can appear many times (one per state transition).
            CREATE TABLE IF NOT EXISTS agent_events (
                seq         INTEGER PRIMARY KEY AUTOINCREMENT,
                id          TEXT NOT NULL,
                source      TEXT,
                type        TEXT,
                status      TEXT,
                parent_id   TEXT,
                ts          INTEGER NOT NULL,
                envelope    TEXT NOT NULL              -- full JSON envelope as received
            );
            CREATE INDEX IF NOT EXISTS idx_agent_events_id
                ON agent_events(id, seq);
            CREATE INDEX IF NOT EXISTS idx_agent_events_parent
                ON agent_events(parent_id, seq);
            CREATE INDEX IF NOT EXISTS idx_agent_events_ts
                ON agent_events(ts);

            -- One row per Loop instance. The full LoopInstance JSON lives in
            -- ``blob``; the typed columns are projected for index queries
            -- (load-active-on-startup, list-by-status, find-by-child).
            CREATE TABLE IF NOT EXISTS loop_instances (
                id                TEXT    PRIMARY KEY,
                parent_task_id    TEXT    NOT NULL,
                status            TEXT    NOT NULL,
                iteration         INTEGER NOT NULL,
                workspace_profile TEXT,
                current_child_id  TEXT,
                created_at        INTEGER NOT NULL,
                updated_at        INTEGER NOT NULL,
                blob              TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_loop_instances_status
                ON loop_instances(status, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_loop_instances_current_child
                ON loop_instances(current_child_id);

            -- One row per iteration completion. Feeds the convergence-trend
            -- chart and the fleet-level analytics in the future Loops panel.
            -- (loop_id, iteration) is unique so re-running an iteration during
            -- restart recovery overwrites rather than duplicates.
            CREATE TABLE IF NOT EXISTS loop_iteration_metric (
                id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                loop_id                  TEXT    NOT NULL,
                iteration                INTEGER NOT NULL,
                convergence_metric_value REAL,
                duration_ms              INTEGER,
                cost_usd                 REAL,
                tokens                   INTEGER,
                model                    TEXT,
                state_at_end             TEXT,
                timestamp                INTEGER NOT NULL,
                UNIQUE(loop_id, iteration)
            );
            CREATE INDEX IF NOT EXISTS idx_loop_iteration_metric_loop
                ON loop_iteration_metric(loop_id, iteration);

            -- MCP gateway server registry (ADR-002, #152). Definitions live in
            -- the catalog file (mcp-catalog.json); this table holds only which
            -- servers are enabled. Seeded from the catalog on startup without
            -- clobbering existing toggles.
            CREATE TABLE IF NOT EXISTS gateway_servers (
                name       TEXT    PRIMARY KEY,
                enabled    INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
        """)


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

_STRIP_FIELDS: dict[str, Any] = {
    "secrets": {},
    "extra_env": {},
    "env_content": None,
    "codex_api_key": None,
}


def upsert_session(ctx: "SessionContext") -> None:  # type: ignore[name-defined]
    clean = ctx.model_copy(update=_STRIP_FIELDS)
    blob = clean.model_dump_json()
    with _lock:
        _db().execute(
            """
            INSERT INTO sessions (session_name, runner_name, active, blob)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(session_name) DO UPDATE SET
                runner_name = excluded.runner_name,
                active      = 1,
                stopped_at  = NULL,
                blob        = excluded.blob
            """,
            (ctx.session_name, ctx.runner_name or "", blob),
        )


def mark_session_inactive(session_name: str, stopped_at_ms: int) -> None:
    with _lock:
        _db().execute(
            """
            UPDATE sessions
            SET active = 0, stopped_at = ?
            WHERE session_name = ?
            """,
            (stopped_at_ms, session_name),
        )


def load_active_runner_sessions() -> list[dict]:
    rows = _db().execute(
        "SELECT blob FROM sessions WHERE active = 1 AND runner_name != ''"
    ).fetchall()
    result = []
    for row in rows:
        try:
            result.append(json.loads(row["blob"]))
        except Exception:
            pass
    return result


# ---------------------------------------------------------------------------
# Runners
# ---------------------------------------------------------------------------


def upsert_runner(info: "RunnerInfo") -> None:  # type: ignore[name-defined]
    import json as _json
    now = int(__import__("time").time() * 1000)
    with _lock:
        _db().execute(
            """
            INSERT INTO runners
                (name, capabilities, tags, version, host, machine_id,
                 max_concurrent, registered_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                capabilities   = excluded.capabilities,
                tags           = excluded.tags,
                version        = excluded.version,
                host           = excluded.host,
                machine_id     = excluded.machine_id,
                max_concurrent = excluded.max_concurrent,
                registered_at  = excluded.registered_at,
                updated_at     = excluded.updated_at
            """,
            (
                info.name,
                _json.dumps(info.capabilities),
                _json.dumps(info.tags),
                info.version or "",
                info.host,
                info.machine_id,
                info.max_concurrent,
                info.registered_at,
                now,
            ),
        )


def delete_runner(name: str) -> None:
    with _lock:
        _db().execute("DELETE FROM runners WHERE name = ?", (name,))


def load_all_runners() -> list[dict]:
    import json as _json
    rows = _db().execute(
        "SELECT name, capabilities, tags, version, host, machine_id, "
        "max_concurrent, registered_at FROM runners"
    ).fetchall()
    result = []
    for row in rows:
        try:
            result.append({
                "name": row["name"],
                "capabilities": _json.loads(row["capabilities"]),
                "tags": _json.loads(row["tags"]),
                "version": row["version"],
                "host": row["host"],
                "machine_id": row["machine_id"],
                "max_concurrent": row["max_concurrent"],
                "registered_at": row["registered_at"],
                "last_seen": 0,  # force offline; runner must heartbeat to go live
            })
        except Exception:
            pass
    return result


# ---------------------------------------------------------------------------
# MCP gateway server registry (#152)
# ---------------------------------------------------------------------------


def seed_gateway_server(name: str, enabled: bool) -> None:
    """Insert a catalog server if absent; never clobber an existing toggle."""
    now = int(__import__("time").time() * 1000)
    with _lock:
        _db().execute(
            """
            INSERT INTO gateway_servers (name, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(name) DO NOTHING
            """,
            (name, 1 if enabled else 0, now, now),
        )


def set_gateway_server_enabled(name: str, enabled: bool) -> None:
    """Enable/disable a server (upsert)."""
    now = int(__import__("time").time() * 1000)
    with _lock:
        _db().execute(
            """
            INSERT INTO gateway_servers (name, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                enabled    = excluded.enabled,
                updated_at = excluded.updated_at
            """,
            (name, 1 if enabled else 0, now, now),
        )


def list_gateway_servers() -> dict[str, bool]:
    """All known servers → enabled state."""
    rows = _db().execute("SELECT name, enabled FROM gateway_servers").fetchall()
    return {row["name"]: bool(row["enabled"]) for row in rows}


def enabled_gateway_server_names() -> list[str]:
    rows = _db().execute(
        "SELECT name FROM gateway_servers WHERE enabled = 1 ORDER BY name"
    ).fetchall()
    return [row["name"] for row in rows]


# ---------------------------------------------------------------------------
# Async wrappers
# ---------------------------------------------------------------------------

async def async_set_gateway_server_enabled(name: str, enabled: bool) -> None:
    await asyncio.to_thread(set_gateway_server_enabled, name, enabled)


async def async_upsert_session(ctx: "SessionContext") -> None:  # type: ignore[name-defined]
    await asyncio.to_thread(upsert_session, ctx)


async def async_mark_session_inactive(session_name: str, stopped_at_ms: int) -> None:
    await asyncio.to_thread(mark_session_inactive, session_name, stopped_at_ms)


async def async_upsert_runner(info: "RunnerInfo") -> None:  # type: ignore[name-defined]
    await asyncio.to_thread(upsert_runner, info)


async def async_delete_runner(name: str) -> None:
    await asyncio.to_thread(delete_runner, name)


# ---------------------------------------------------------------------------
# Session history
# ---------------------------------------------------------------------------


def insert_session_history(ctx: "SessionContext", reason: str) -> None:  # type: ignore[name-defined]
    import time as _time
    stopped_at = int(_time.time() * 1000)
    with _lock:
        _db().execute(
            """
            INSERT INTO session_history
                (session_name, runner_name, backend, role, state_final,
                 created_at, stopped_at, task_id, job_id, repo_url, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ctx.session_name,
                ctx.runner_name,
                ctx.backend or "docker",
                ctx.role,
                ctx.state.value if ctx.state else "recycled",
                ctx.created_at,
                stopped_at,
                ctx.task_id,
                ctx.job_id,
                ctx.repo_url,
                reason,
            ),
        )


def query_session_history(
    limit: int = 100,
    offset: int = 0,
    runner_name: str | None = None,
) -> list[dict]:
    if runner_name:
        rows = _db().execute(
            "SELECT * FROM session_history WHERE runner_name = ? "
            "ORDER BY stopped_at DESC LIMIT ? OFFSET ?",
            (runner_name, limit, offset),
        ).fetchall()
    else:
        rows = _db().execute(
            "SELECT * FROM session_history ORDER BY stopped_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return [dict(row) for row in rows]


async def async_insert_session_history(ctx: "SessionContext", reason: str) -> None:  # type: ignore[name-defined]
    await asyncio.to_thread(insert_session_history, ctx, reason)


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


def insert_audit(
    event: str,
    *,
    session_name: str | None = None,
    actor: str | None = None,
    success: bool = True,
    detail: dict | None = None,
) -> None:
    import json as _json
    import time as _time
    ts = int(_time.time() * 1000)
    with _lock:
        _db().execute(
            """
            INSERT INTO audit_log (ts, event, session_name, actor, success, detail)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                ts,
                event,
                session_name,
                actor,
                1 if success else 0,
                _json.dumps(detail) if detail else None,
            ),
        )


def query_audit_log(
    limit: int = 200,
    offset: int = 0,
    event: str | None = None,
    session_name: str | None = None,
) -> list[dict]:
    import json as _json
    clauses: list[str] = []
    params: list = []
    if event:
        clauses.append("event = ?")
        params.append(event)
    if session_name:
        clauses.append("session_name = ?")
        params.append(session_name)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = _db().execute(
        f"SELECT * FROM audit_log {where} ORDER BY ts DESC LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        if d.get("detail"):
            try:
                d["detail"] = _json.loads(d["detail"])
            except Exception:
                pass
        result.append(d)
    return result


# ---------------------------------------------------------------------------
# Loop instances + iteration metrics
# ---------------------------------------------------------------------------


def upsert_loop_instance(inst: "LoopInstance") -> None:  # type: ignore[name-defined]
    """Persist a LoopInstance — called on every state transition.

    The typed columns are projected for query (active-on-startup,
    list-by-status, find-by-child); ``blob`` carries the full JSON so
    rehydration round-trips perfectly.
    """
    blob = inst.model_dump_json()
    with _lock:
        _db().execute(
            """
            INSERT INTO loop_instances
                (id, parent_task_id, status, iteration, workspace_profile,
                 current_child_id, created_at, updated_at, blob)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                parent_task_id    = excluded.parent_task_id,
                status            = excluded.status,
                iteration         = excluded.iteration,
                workspace_profile = excluded.workspace_profile,
                current_child_id  = excluded.current_child_id,
                updated_at        = excluded.updated_at,
                blob              = excluded.blob
            """,
            (
                inst.id,
                inst.parent_task_id,
                inst.status.value,
                inst.iteration,
                inst.workspace_profile,
                inst.current_child_id,
                inst.created_at,
                inst.updated_at,
                blob,
            ),
        )


def load_active_loop_instances() -> list["LoopInstance"]:  # type: ignore[name-defined]
    """Return every LoopInstance whose status is RUNNING or PENDING.

    Called on daemon startup to rehydrate the in-memory loop runner state
    so a restart doesn't lose track of in-flight loops.
    """
    from .loops import LoopInstance, LoopStatus

    rows = _db().execute(
        "SELECT blob FROM loop_instances "
        "WHERE status IN (?, ?) "
        "ORDER BY updated_at DESC",
        (LoopStatus.PENDING.value, LoopStatus.RUNNING.value),
    ).fetchall()
    result: list[LoopInstance] = []
    for row in rows:
        try:
            result.append(LoopInstance.model_validate_json(row["blob"]))
        except Exception:
            # Skip rows that don't deserialize cleanly under the current
            # schema. The additive-only envelope discipline means old rows
            # SHOULD deserialize; if they don't, the operator can drop the
            # row and restart the loop. Don't crash the daemon over it.
            pass
    return result


def get_loop_instance(loop_id: str) -> "LoopInstance | None":  # type: ignore[name-defined]
    from .loops import LoopInstance

    row = _db().execute(
        "SELECT blob FROM loop_instances WHERE id = ?", (loop_id,)
    ).fetchone()
    if row is None:
        return None
    try:
        return LoopInstance.model_validate_json(row["blob"])
    except Exception:
        return None


def insert_loop_iteration_metric(
    *,
    loop_id: str,
    iteration: int,
    convergence_metric_value: float,
    timestamp_ms: int,
    duration_ms: int | None = None,
    cost_usd: float | None = None,
    tokens: int | None = None,
    model: str | None = None,
    state_at_end: str | None = None,
) -> None:
    """Write one iteration row. UPSERT semantics on (loop_id, iteration)
    so restart-recovery rewrites instead of duplicating.
    """
    with _lock:
        _db().execute(
            """
            INSERT INTO loop_iteration_metric
                (loop_id, iteration, convergence_metric_value, duration_ms,
                 cost_usd, tokens, model, state_at_end, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(loop_id, iteration) DO UPDATE SET
                convergence_metric_value = excluded.convergence_metric_value,
                duration_ms              = excluded.duration_ms,
                cost_usd                 = excluded.cost_usd,
                tokens                   = excluded.tokens,
                model                    = excluded.model,
                state_at_end             = excluded.state_at_end,
                timestamp                = excluded.timestamp
            """,
            (
                loop_id,
                iteration,
                convergence_metric_value,
                duration_ms,
                cost_usd,
                tokens,
                model,
                state_at_end,
                timestamp_ms,
            ),
        )


def query_loop_iteration_metrics(loop_id: str) -> list[dict]:
    """Return iteration rows for a loop in iteration order. Feeds the
    convergence-trend chart in the future Loops panel.
    """
    rows = _db().execute(
        "SELECT loop_id, iteration, convergence_metric_value, duration_ms, "
        "cost_usd, tokens, model, state_at_end, timestamp "
        "FROM loop_iteration_metric WHERE loop_id = ? ORDER BY iteration ASC",
        (loop_id,),
    ).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Async wrappers (loops)
# ---------------------------------------------------------------------------


async def async_upsert_loop_instance(inst: "LoopInstance") -> None:  # type: ignore[name-defined]
    await asyncio.to_thread(upsert_loop_instance, inst)


async def async_insert_loop_iteration_metric(**kwargs: Any) -> None:
    await asyncio.to_thread(insert_loop_iteration_metric, **kwargs)


async def async_load_active_loop_instances() -> list["LoopInstance"]:  # type: ignore[name-defined]
    return await asyncio.to_thread(load_active_loop_instances)

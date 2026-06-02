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
from typing import Any

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
                 max_concurrent, last_seal_at, registered_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                capabilities   = excluded.capabilities,
                tags           = excluded.tags,
                version        = excluded.version,
                host           = excluded.host,
                machine_id     = excluded.machine_id,
                max_concurrent = excluded.max_concurrent,
                last_seal_at   = excluded.last_seal_at,
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
                info.last_seal_at,
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
        "max_concurrent, last_seal_at, registered_at FROM runners"
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
                "last_seal_at": row["last_seal_at"],
                "registered_at": row["registered_at"],
                "last_seen": 0,  # force offline; runner must heartbeat to go live
            })
        except Exception:
            pass
    return result


# ---------------------------------------------------------------------------
# Async wrappers
# ---------------------------------------------------------------------------

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

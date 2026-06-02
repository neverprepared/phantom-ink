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

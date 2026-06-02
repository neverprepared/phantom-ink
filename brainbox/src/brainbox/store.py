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
# Async wrappers
# ---------------------------------------------------------------------------

async def async_upsert_session(ctx: "SessionContext") -> None:  # type: ignore[name-defined]
    await asyncio.to_thread(upsert_session, ctx)


async def async_mark_session_inactive(session_name: str, stopped_at_ms: int) -> None:
    await asyncio.to_thread(mark_session_inactive, session_name, stopped_at_ms)

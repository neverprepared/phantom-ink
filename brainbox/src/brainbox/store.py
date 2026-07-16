"""Postgres persistence layer for brainbox.

Runs on Postgres via psycopg3 and a connection pool. Production connections are
**autocommit** — every write is immediately durable, matching the behavior of
the prior single long-lived SQLite connection (read-your-writes, persisted
across restarts) without a global write lock, so multiple threads/nodes can
write concurrently.

Tests pin a single connection (``reset_store_for_tests``) and TRUNCATE between
tests for isolation; the conftest autouse fixture calls it before and after
each test. Requires ``CL_DATABASE_URL`` — there is no SQLite fallback.

All SQL functions are synchronous and called via ``asyncio.to_thread`` from
async contexts (the pool is thread-safe). ``async_*`` wrappers preserve the
existing call sites.
"""

from __future__ import annotations

import atexit
import asyncio
import json
import threading
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

if TYPE_CHECKING:
    from .loops import LoopInstance
    from .models import SessionContext
    from .runners import RunnerInfo

_pool: ConnectionPool | None = None
# When set (tests), every store call uses this single connection, serialized by
# _test_lock, instead of the pool.
_test_conn: "psycopg.Connection | None" = None
_test_lock = threading.Lock()


def _get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        from .config import settings

        dsn = settings.database_url
        if not dsn:
            raise RuntimeError(
                "CL_DATABASE_URL (a Postgres DSN) is required — the brainbox store "
                "runs on Postgres, there is no SQLite fallback."
            )
        pool = ConnectionPool(
            dsn,
            min_size=1,
            max_size=10,
            open=True,
            kwargs={"autocommit": True, "row_factory": dict_row},
        )
        _pool = pool  # set before init_db() so the nested _conn() sees the pool
        # Close the pool at normal exit so its worker thread joins before
        # interpreter finalization (Python 3.14 raises in __del__ otherwise).
        atexit.register(close_pool)
        init_db()
    return _pool


def close_pool() -> None:
    """Close the connection pool. Called at exit and available for shutdown hooks."""
    global _pool
    if _pool is not None:
        try:
            _pool.close()
        except Exception:
            pass
        _pool = None


@contextmanager
def _conn() -> "Iterator[psycopg.Connection]":
    """Yield a connection: the pinned test connection (serialized) in test mode,
    otherwise one borrowed from the pool (returned automatically)."""
    if _test_conn is not None:
        with _test_lock:
            yield _test_conn
    else:
        with _get_pool().connection() as c:
            yield c


def reset_store_for_tests() -> None:
    """Pin a Postgres test connection, ensure schema, and truncate all tables.

    Called before AND after each test (see tests/conftest.py). Requires
    ``CL_DATABASE_URL`` to point at a throwaway test database.
    """
    global _test_conn
    if _test_conn is None:
        from .config import settings

        dsn = settings.database_url
        if not dsn:
            raise RuntimeError(
                "tests require CL_DATABASE_URL pointing at a test Postgres database"
            )
        _test_conn = psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
    init_db()
    _truncate_all()


def _truncate_all() -> None:
    with _conn() as c:
        rows = c.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"
        ).fetchall()
        tables = [r["tablename"] for r in rows]
        if tables:
            joined = ", ".join(f'"{t}"' for t in tables)
            c.execute(f"TRUNCATE {joined} RESTART IDENTITY CASCADE")


# ---------------------------------------------------------------------------
# Schema — Postgres DDL. INTEGER columns become BIGINT (epoch-ms values exceed
# int4); the 4 auto-increment PKs use GENERATED AS IDENTITY. Booleans stay as
# 0/1 BIGINT and JSON blobs stay TEXT for byte-for-byte parity with the code.
# ---------------------------------------------------------------------------

_SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS sessions (
        session_name  TEXT   PRIMARY KEY,
        runner_name   TEXT   NOT NULL,
        active        BIGINT NOT NULL DEFAULT 1,
        stopped_at    BIGINT,
        blob          TEXT   NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_sessions_active ON sessions(active)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_runner ON sessions(runner_name, active)",
    """
    CREATE TABLE IF NOT EXISTS runners (
        name           TEXT   PRIMARY KEY,
        capabilities   TEXT   NOT NULL,
        tags           TEXT   NOT NULL,
        version        TEXT   NOT NULL DEFAULT '',
        host           TEXT,
        machine_id     TEXT,
        max_concurrent BIGINT NOT NULL DEFAULT 4,
        last_seal_at   BIGINT,
        registered_at  BIGINT NOT NULL,
        updated_at     BIGINT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_runners_machine_id ON runners(machine_id)",
    """
    CREATE TABLE IF NOT EXISTS session_history (
        id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        session_name TEXT   NOT NULL,
        runner_name  TEXT,
        backend      TEXT   NOT NULL DEFAULT 'docker',
        role         TEXT,
        state_final  TEXT   NOT NULL,
        created_at   BIGINT NOT NULL,
        stopped_at   BIGINT NOT NULL,
        task_id      TEXT,
        job_id       TEXT,
        repo_url     TEXT,
        reason       TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_history_stopped_at ON session_history(stopped_at)",
    "CREATE INDEX IF NOT EXISTS idx_history_runner ON session_history(runner_name)",
    """
    CREATE TABLE IF NOT EXISTS audit_log (
        id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        ts           BIGINT NOT NULL,
        event        TEXT   NOT NULL,
        session_name TEXT,
        actor        TEXT,
        success      BIGINT NOT NULL DEFAULT 1,
        detail       TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts)",
    "CREATE INDEX IF NOT EXISTS idx_audit_event ON audit_log(event)",
    """
    CREATE TABLE IF NOT EXISTS agent_state (
        id            TEXT PRIMARY KEY,
        kind          TEXT NOT NULL,
        source        TEXT,
        type          TEXT,
        status        TEXT,
        title         TEXT NOT NULL,
        subtitle      TEXT,
        workspace     TEXT,
        parent_id     TEXT,
        url           TEXT,
        start_at      BIGINT,
        end_at        BIGINT,
        tags_json     TEXT NOT NULL DEFAULT '[]',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        actions_json  TEXT NOT NULL DEFAULT '[]',
        outcome_json  TEXT,
        created_at    BIGINT NOT NULL,
        updated_at    BIGINT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_agent_state_status "
    "ON agent_state(status, workspace, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_agent_state_parent ON agent_state(parent_id)",
    """
    CREATE TABLE IF NOT EXISTS agent_events (
        seq       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        id        TEXT NOT NULL,
        source    TEXT,
        type      TEXT,
        status    TEXT,
        parent_id TEXT,
        ts        BIGINT NOT NULL,
        envelope  TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_agent_events_id ON agent_events(id, seq)",
    "CREATE INDEX IF NOT EXISTS idx_agent_events_parent ON agent_events(parent_id, seq)",
    "CREATE INDEX IF NOT EXISTS idx_agent_events_ts ON agent_events(ts)",
    """
    CREATE TABLE IF NOT EXISTS loop_instances (
        id                TEXT   PRIMARY KEY,
        parent_task_id    TEXT   NOT NULL,
        status            TEXT   NOT NULL,
        iteration         BIGINT NOT NULL,
        workspace_profile TEXT,
        current_child_id  TEXT,
        created_at        BIGINT NOT NULL,
        updated_at        BIGINT NOT NULL,
        blob              TEXT   NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_loop_instances_status "
    "ON loop_instances(status, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_loop_instances_current_child "
    "ON loop_instances(current_child_id)",
    """
    CREATE TABLE IF NOT EXISTS loop_iteration_metric (
        id                       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        loop_id                  TEXT   NOT NULL,
        iteration                BIGINT NOT NULL,
        convergence_metric_value DOUBLE PRECISION,
        duration_ms              BIGINT,
        cost_usd                 DOUBLE PRECISION,
        tokens                   BIGINT,
        model                    TEXT,
        state_at_end             TEXT,
        timestamp                BIGINT NOT NULL,
        UNIQUE(loop_id, iteration)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_loop_iteration_metric_loop "
    "ON loop_iteration_metric(loop_id, iteration)",
    """
    CREATE TABLE IF NOT EXISTS gateway_servers (
        name       TEXT   PRIMARY KEY,
        enabled    BIGINT NOT NULL DEFAULT 0,
        created_at BIGINT NOT NULL,
        updated_at BIGINT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS gateway_tokens (
        token_id          TEXT   PRIMARY KEY,
        workspace_profile TEXT   NOT NULL DEFAULT '',
        scope_json        TEXT   NOT NULL DEFAULT '[]',
        issued            BIGINT NOT NULL,
        expiry            BIGINT NOT NULL,
        residency_ceiling TEXT   NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS trust_rules (
        profile    TEXT   NOT NULL,
        pattern    TEXT   NOT NULL,
        zone       TEXT   NOT NULL,
        created_at BIGINT NOT NULL,
        PRIMARY KEY (profile, pattern)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS trust_profile_config (
        profile         TEXT   PRIMARY KEY,
        default_ceiling TEXT   NOT NULL,
        updated_at      BIGINT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS profile_server_override (
        profile TEXT   NOT NULL,
        server  TEXT   NOT NULL,
        enabled BIGINT NOT NULL,
        PRIMARY KEY (profile, server)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS event_rules (
        id                TEXT   PRIMARY KEY,
        name              TEXT   NOT NULL,
        profile           TEXT   NOT NULL DEFAULT '',
        enabled           BIGINT NOT NULL DEFAULT 1,
        description       TEXT,
        pattern           TEXT   NOT NULL,
        actions           TEXT   NOT NULL,
        created_at        BIGINT NOT NULL,
        updated_at        BIGINT NOT NULL,
        last_triggered_at BIGINT,
        trigger_count     BIGINT NOT NULL DEFAULT 0
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_event_rules_profile ON event_rules(profile, enabled)",
    """
    CREATE TABLE IF NOT EXISTS event_rule_cursor (
        name       TEXT   PRIMARY KEY,
        last_seq   BIGINT NOT NULL,
        updated_at BIGINT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS event_rule_executions (
        id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        rule_id      TEXT   NOT NULL,
        event_seq    BIGINT NOT NULL,
        event_id     TEXT   NOT NULL,
        action_index BIGINT NOT NULL,
        action_type  TEXT   NOT NULL,
        status       TEXT   NOT NULL,
        attempts     BIGINT NOT NULL DEFAULT 0,
        result       TEXT,
        error        TEXT,
        created_at   BIGINT NOT NULL,
        updated_at   BIGINT NOT NULL,
        UNIQUE (rule_id, event_seq, action_index)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_rule_exec_rule ON event_rule_executions(rule_id, id DESC)",
    "CREATE INDEX IF NOT EXISTS idx_rule_exec_status ON event_rule_executions(status, updated_at)",
    """
    CREATE TABLE IF NOT EXISTS session_store (
        session_name TEXT   NOT NULL,
        key          TEXT   NOT NULL,
        profile      TEXT   NOT NULL DEFAULT '',
        task_id      TEXT,
        token_id     TEXT,
        content      BYTEA  NOT NULL,
        content_type TEXT   NOT NULL DEFAULT 'application/json',
        created_at   BIGINT NOT NULL,
        updated_at   BIGINT NOT NULL,
        PRIMARY KEY (session_name, key)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_session_store_task ON session_store(task_id)",
    "CREATE INDEX IF NOT EXISTS idx_session_store_token ON session_store(token_id)",
    # Profile tokens (T11): persistent, revocable, per-profile service tokens.
    # We store ONLY sha256(raw) — never the bearer value — and no expiry column:
    # these are long-lived until explicitly revoked. capabilities/scope are JSON
    # TEXT for parity with the rest of the store's JSON-blob discipline.
    """
    CREATE TABLE IF NOT EXISTS profile_tokens (
        token_id          TEXT   PRIMARY KEY,
        token_hash        TEXT   NOT NULL,
        workspace_profile TEXT   NOT NULL,
        capabilities_json TEXT   NOT NULL DEFAULT '[]',
        scope_json        TEXT   NOT NULL DEFAULT '[]',
        label             TEXT   NOT NULL DEFAULT '',
        issued            BIGINT NOT NULL,
        revoked           BIGINT NOT NULL DEFAULT 0,
        revoked_at        BIGINT,
        last_used         BIGINT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_profile_tokens_hash ON profile_tokens(token_hash)",
    "CREATE INDEX IF NOT EXISTS idx_profile_tokens_profile ON profile_tokens(workspace_profile)",
)


def init_db() -> None:
    """Create tables/indexes if they don't exist. Safe to call on every startup."""
    with _conn() as c:
        for stmt in _SCHEMA:
            c.execute(stmt)
        # Additive column migration for pre-existing DBs (Postgres supports
        # ADD COLUMN IF NOT EXISTS, so this is idempotent without a try/except).
        c.execute(
            "ALTER TABLE gateway_tokens "
            "ADD COLUMN IF NOT EXISTS residency_ceiling TEXT NOT NULL DEFAULT ''"
        )


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
    with _conn() as c:
        c.execute(
            """
            INSERT INTO sessions (session_name, runner_name, active, blob)
            VALUES (%s, %s, 1, %s)
            ON CONFLICT (session_name) DO UPDATE SET
                runner_name = EXCLUDED.runner_name,
                active      = 1,
                stopped_at  = NULL,
                blob        = EXCLUDED.blob
            """,
            (ctx.session_name, ctx.runner_name or "", blob),
        )


def mark_session_inactive(session_name: str, stopped_at_ms: int) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE sessions SET active = 0, stopped_at = %s WHERE session_name = %s",
            (stopped_at_ms, session_name),
        )


def load_active_runner_sessions() -> list[dict]:
    with _conn() as c:
        rows = c.execute(
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
    now = int(time.time() * 1000)
    with _conn() as c:
        c.execute(
            """
            INSERT INTO runners
                (name, capabilities, tags, version, host, machine_id,
                 max_concurrent, registered_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (name) DO UPDATE SET
                capabilities   = EXCLUDED.capabilities,
                tags           = EXCLUDED.tags,
                version        = EXCLUDED.version,
                host           = EXCLUDED.host,
                machine_id     = EXCLUDED.machine_id,
                max_concurrent = EXCLUDED.max_concurrent,
                registered_at  = EXCLUDED.registered_at,
                updated_at     = EXCLUDED.updated_at
            """,
            (
                info.name,
                json.dumps(info.capabilities),
                json.dumps(info.tags),
                info.version or "",
                info.host,
                info.machine_id,
                info.max_concurrent,
                info.registered_at,
                now,
            ),
        )


def delete_runner(name: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM runners WHERE name = %s", (name,))


def load_all_runners() -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT name, capabilities, tags, version, host, machine_id, "
            "max_concurrent, registered_at FROM runners"
        ).fetchall()
    result = []
    for row in rows:
        try:
            result.append(
                {
                    "name": row["name"],
                    "capabilities": json.loads(row["capabilities"]),
                    "tags": json.loads(row["tags"]),
                    "version": row["version"],
                    "host": row["host"],
                    "machine_id": row["machine_id"],
                    "max_concurrent": row["max_concurrent"],
                    "registered_at": row["registered_at"],
                    "last_seen": 0,  # force offline; runner must heartbeat to go live
                }
            )
        except Exception:
            pass
    return result


# ---------------------------------------------------------------------------
# MCP gateway server registry (#152)
# ---------------------------------------------------------------------------


def seed_gateway_server(name: str, enabled: bool) -> None:
    """Insert a catalog server if absent; never clobber an existing toggle."""
    now = int(time.time() * 1000)
    with _conn() as c:
        c.execute(
            """
            INSERT INTO gateway_servers (name, enabled, created_at, updated_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (name) DO NOTHING
            """,
            (name, 1 if enabled else 0, now, now),
        )


def set_gateway_server_enabled(name: str, enabled: bool) -> None:
    """Enable/disable a server (upsert)."""
    now = int(time.time() * 1000)
    with _conn() as c:
        c.execute(
            """
            INSERT INTO gateway_servers (name, enabled, created_at, updated_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (name) DO UPDATE SET
                enabled    = EXCLUDED.enabled,
                updated_at = EXCLUDED.updated_at
            """,
            (name, 1 if enabled else 0, now, now),
        )


def list_gateway_servers() -> dict[str, bool]:
    """All known servers → enabled state."""
    with _conn() as c:
        rows = c.execute("SELECT name, enabled FROM gateway_servers").fetchall()
    return {row["name"]: bool(row["enabled"]) for row in rows}


def enabled_gateway_server_names() -> list[str]:
    with _conn() as c:
        rows = c.execute(
            "SELECT name FROM gateway_servers WHERE enabled = 1 ORDER BY name"
        ).fetchall()
    return [row["name"] for row in rows]


def save_gateway_token(
    token_id: str,
    workspace_profile: str,
    scope: list[str],
    issued: int,
    expiry: int,
    residency_ceiling: str = "",
) -> None:
    with _conn() as c:
        c.execute(
            """
            INSERT INTO gateway_tokens
                (token_id, workspace_profile, scope_json, issued, expiry, residency_ceiling)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (token_id) DO UPDATE SET
                workspace_profile = EXCLUDED.workspace_profile,
                scope_json        = EXCLUDED.scope_json,
                issued            = EXCLUDED.issued,
                expiry            = EXCLUDED.expiry,
                residency_ceiling = EXCLUDED.residency_ceiling
            """,
            (token_id, workspace_profile, json.dumps(scope), issued, expiry, residency_ceiling),
        )


def delete_gateway_token(token_id: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM gateway_tokens WHERE token_id = %s", (token_id,))


def load_gateway_tokens() -> list[dict]:
    """Non-expired persisted gateway tokens (also prunes expired rows)."""
    now = int(time.time() * 1000)
    with _conn() as c:
        c.execute("DELETE FROM gateway_tokens WHERE expiry <= %s", (now,))
        rows = c.execute(
            "SELECT token_id, workspace_profile, scope_json, issued, expiry, residency_ceiling "
            "FROM gateway_tokens"
        ).fetchall()
    return [
        {
            "token_id": r["token_id"],
            "workspace_profile": r["workspace_profile"],
            "scope": json.loads(r["scope_json"]),
            "issued": r["issued"],
            "expiry": r["expiry"],
            "residency_ceiling": r["residency_ceiling"],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Profile tokens (T11) — persistent, revocable, per-profile service tokens
# ---------------------------------------------------------------------------


def insert_profile_token(
    *,
    token_id: str,
    token_hash: str,
    workspace_profile: str,
    capabilities: list[str],
    scope: list[str],
    label: str,
    issued: int,
) -> None:
    """Persist a newly minted profile token. Only the hash is stored."""
    with _conn() as c:
        c.execute(
            """
            INSERT INTO profile_tokens
                (token_id, token_hash, workspace_profile, capabilities_json,
                 scope_json, label, issued)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                token_id,
                token_hash,
                workspace_profile,
                json.dumps(capabilities),
                json.dumps(scope),
                label,
                issued,
            ),
        )


def _profile_token_from_row(row: dict) -> dict:
    return {
        "token_id": row["token_id"],
        "workspace_profile": row["workspace_profile"],
        "capabilities": json.loads(row["capabilities_json"]),
        "scope": json.loads(row["scope_json"]),
        "label": row["label"],
        "issued": row["issued"],
        "revoked": bool(row["revoked"]),
        "revoked_at": row["revoked_at"],
        "last_used": row["last_used"],
    }


def find_profile_token_by_hash(token_hash: str) -> dict | None:
    """Look up a profile token by sha256(raw). Returns the full row (including
    ``revoked``) so the caller can distinguish revoked (401) from unknown."""
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM profile_tokens WHERE token_hash = %s", (token_hash,)
        ).fetchone()
    return _profile_token_from_row(row) if row else None


def touch_profile_token_last_used(token_id: str, when_ms: int) -> None:
    """Record the most recent successful validation timestamp (best-effort)."""
    with _conn() as c:
        c.execute(
            "UPDATE profile_tokens SET last_used = %s WHERE token_id = %s",
            (when_ms, token_id),
        )


def list_profile_tokens(include_revoked: bool = True) -> list[dict]:
    """List profile tokens as masked rows (never the raw token or its hash)."""
    with _conn() as c:
        if include_revoked:
            rows = c.execute(
                "SELECT * FROM profile_tokens ORDER BY issued DESC"
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM profile_tokens WHERE revoked = 0 ORDER BY issued DESC"
            ).fetchall()
    return [_profile_token_from_row(r) for r in rows]


def revoke_profile_token(token_id: str, when_ms: int) -> bool:
    """Mark a profile token revoked. Returns True if a live row was flipped."""
    with _conn() as c:
        cur = c.execute(
            "UPDATE profile_tokens SET revoked = 1, revoked_at = %s "
            "WHERE token_id = %s AND revoked = 0",
            (when_ms, token_id),
        )
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Declarative orchestration — per-profile trust map + default ceiling
# ---------------------------------------------------------------------------


def set_trust_rule(profile: str, pattern: str, zone: str) -> None:
    """Upsert one ``pattern -> zone`` rule for a profile's trust map."""
    now = int(time.time() * 1000)
    with _conn() as c:
        c.execute(
            """
            INSERT INTO trust_rules (profile, pattern, zone, created_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (profile, pattern) DO UPDATE SET zone = EXCLUDED.zone
            """,
            (profile, pattern, zone, now),
        )


def delete_trust_rule(profile: str, pattern: str) -> bool:
    with _conn() as c:
        cur = c.execute(
            "DELETE FROM trust_rules WHERE profile = %s AND pattern = %s", (profile, pattern)
        )
        return cur.rowcount > 0


def list_trust_rules(profile: str) -> list[dict]:
    """A profile's trust rules as ``[{pattern, zone}, ...]``."""
    with _conn() as c:
        rows = c.execute(
            "SELECT pattern, zone FROM trust_rules WHERE profile = %s ORDER BY pattern",
            (profile,),
        ).fetchall()
    return [{"pattern": r["pattern"], "zone": r["zone"]} for r in rows]


def set_profile_default_ceiling(profile: str, zone: str) -> None:
    now = int(time.time() * 1000)
    with _conn() as c:
        c.execute(
            """
            INSERT INTO trust_profile_config (profile, default_ceiling, updated_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (profile) DO UPDATE SET
                default_ceiling = EXCLUDED.default_ceiling,
                updated_at      = EXCLUDED.updated_at
            """,
            (profile, zone, now),
        )


def get_profile_default_ceiling(profile: str) -> str | None:
    """The profile's configured default ceiling zone name, or None if unset."""
    with _conn() as c:
        row = c.execute(
            "SELECT default_ceiling FROM trust_profile_config WHERE profile = %s", (profile,)
        ).fetchone()
    return row["default_ceiling"] if row else None


def set_profile_server_override(profile: str, server: str, enabled: bool) -> None:
    """Manually include/exclude a server for a profile (overrides resolution)."""
    with _conn() as c:
        c.execute(
            """
            INSERT INTO profile_server_override (profile, server, enabled)
            VALUES (%s, %s, %s)
            ON CONFLICT (profile, server) DO UPDATE SET enabled = EXCLUDED.enabled
            """,
            (profile, server, 1 if enabled else 0),
        )


def clear_profile_server_override(profile: str, server: str) -> bool:
    """Remove a manual override → the server reverts to the resolution default."""
    with _conn() as c:
        cur = c.execute(
            "DELETE FROM profile_server_override WHERE profile = %s AND server = %s",
            (profile, server),
        )
        return cur.rowcount > 0


def list_profile_server_overrides(profile: str) -> dict[str, bool]:
    """A profile's manual overrides as ``{server: enabled}``."""
    with _conn() as c:
        rows = c.execute(
            "SELECT server, enabled FROM profile_server_override WHERE profile = %s", (profile,)
        ).fetchall()
    return {r["server"]: bool(r["enabled"]) for r in rows}


# ---------------------------------------------------------------------------
# Async wrappers
# ---------------------------------------------------------------------------


async def async_set_gateway_server_enabled(name: str, enabled: bool) -> None:
    await asyncio.to_thread(set_gateway_server_enabled, name, enabled)


async def async_set_trust_rule(profile: str, pattern: str, zone: str) -> None:
    await asyncio.to_thread(set_trust_rule, profile, pattern, zone)


async def async_set_profile_default_ceiling(profile: str, zone: str) -> None:
    await asyncio.to_thread(set_profile_default_ceiling, profile, zone)


async def async_set_profile_server_override(profile: str, server: str, enabled: bool) -> None:
    await asyncio.to_thread(set_profile_server_override, profile, server, enabled)


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
    stopped_at = int(time.time() * 1000)
    with _conn() as c:
        c.execute(
            """
            INSERT INTO session_history
                (session_name, runner_name, backend, role, state_final,
                 created_at, stopped_at, task_id, job_id, repo_url, reason)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
    with _conn() as c:
        if runner_name:
            rows = c.execute(
                "SELECT * FROM session_history WHERE runner_name = %s "
                "ORDER BY stopped_at DESC LIMIT %s OFFSET %s",
                (runner_name, limit, offset),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM session_history ORDER BY stopped_at DESC LIMIT %s OFFSET %s",
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
    ts = int(time.time() * 1000)
    with _conn() as c:
        c.execute(
            """
            INSERT INTO audit_log (ts, event, session_name, actor, success, detail)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                ts,
                event,
                session_name,
                actor,
                1 if success else 0,
                json.dumps(detail) if detail else None,
            ),
        )


def query_audit_log(
    limit: int = 200,
    offset: int = 0,
    event: str | None = None,
    session_name: str | None = None,
) -> list[dict]:
    clauses: list[str] = []
    params: list = []
    if event:
        clauses.append("event = %s")
        params.append(event)
    if session_name:
        clauses.append("session_name = %s")
        params.append(session_name)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with _conn() as c:
        rows = c.execute(
            f"SELECT * FROM audit_log {where} ORDER BY ts DESC LIMIT %s OFFSET %s",
            (*params, limit, offset),
        ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        if d.get("detail"):
            try:
                d["detail"] = json.loads(d["detail"])
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
    with _conn() as c:
        c.execute(
            """
            INSERT INTO loop_instances
                (id, parent_task_id, status, iteration, workspace_profile,
                 current_child_id, created_at, updated_at, blob)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                parent_task_id    = EXCLUDED.parent_task_id,
                status            = EXCLUDED.status,
                iteration         = EXCLUDED.iteration,
                workspace_profile = EXCLUDED.workspace_profile,
                current_child_id  = EXCLUDED.current_child_id,
                updated_at        = EXCLUDED.updated_at,
                blob              = EXCLUDED.blob
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

    with _conn() as c:
        rows = c.execute(
            "SELECT blob FROM loop_instances "
            "WHERE status IN (%s, %s) "
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

    with _conn() as c:
        row = c.execute(
            "SELECT blob FROM loop_instances WHERE id = %s", (loop_id,)
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
    with _conn() as c:
        c.execute(
            """
            INSERT INTO loop_iteration_metric
                (loop_id, iteration, convergence_metric_value, duration_ms,
                 cost_usd, tokens, model, state_at_end, timestamp)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (loop_id, iteration) DO UPDATE SET
                convergence_metric_value = EXCLUDED.convergence_metric_value,
                duration_ms              = EXCLUDED.duration_ms,
                cost_usd                 = EXCLUDED.cost_usd,
                tokens                   = EXCLUDED.tokens,
                model                    = EXCLUDED.model,
                state_at_end             = EXCLUDED.state_at_end,
                timestamp                = EXCLUDED.timestamp
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
    with _conn() as c:
        rows = c.execute(
            "SELECT loop_id, iteration, convergence_metric_value, duration_ms, "
            "cost_usd, tokens, model, state_at_end, timestamp "
            "FROM loop_iteration_metric WHERE loop_id = %s ORDER BY iteration ASC",
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

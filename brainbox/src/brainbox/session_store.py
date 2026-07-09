"""Session store — durable task/result/handoff objects per session.

Replaces the racy exec-injected ~/.brainbox/task.txt push model with a pull
model: the task is stored durably BEFORE the container exists, and the
container fetches it at startup via the hub (one curl with its session
bearer token — works identically for local and runner-dispatched sessions).
The same per-session namespace holds the result summary and handoff
documents, enabling cross-machine session handoffs (`continue_from`).

Backend: **Postgres-primary with a best-effort MinIO write-through mirror.**
- Postgres is the source of truth for every hub-served read/write: one code
  path regardless of MinIO state, read-your-write consistency, and PG is
  already a hard daemon requirement. A PG write failure on task.json fails
  the session create — task delivery is the point.
- The MinIO mirror lands at `{profile}/sessions/{session}/{key}` in the
  artifacts bucket: browsable in the app's Files panel, IAM-scopable per
  profile (phantom-{profile} users), and the tree future A2A instructions
  can reference. Mirror failures are warn-and-continue — the daemon's own
  MinIO credential may be profile-scoped, so cross-profile 403s must never
  break a create.
- Reads fall back to MinIO when the PG row is absent, covering objects an
  agent wrote DIRECTLY to MinIO with its scoped credentials (e.g. a
  handoff.md authored in-session).

Object keys are a closed set (task.json / result.json / handoff.md) and
capped at 1 MB — these are prompts and summaries, not artifacts.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from .config import settings
from .log import get_logger
from .store import _conn

log = get_logger()

KEY_TASK = "task.json"
KEY_RESULT = "result.json"
KEY_HANDOFF = "handoff.md"
KNOWN_KEYS = (KEY_TASK, KEY_RESULT, KEY_HANDOFF)

MAX_OBJECT_BYTES = 1_048_576


def _now_ms() -> int:
    return int(time.time() * 1000)


def object_key(profile: str, session_name: str, key: str) -> str:
    """MinIO mirror key — profile-first so per-profile scopes line up with
    the Files panel and the phantom-{profile} IAM policies."""
    return f"{profile or '_none'}/sessions/{session_name}/{key}"


def put(
    session_name: str,
    key: str,
    content: bytes,
    *,
    profile: str = "",
    content_type: str = "application/json",
    task_id: str | None = None,
    token_id: str | None = None,
) -> None:
    """Upsert into Postgres (raises on failure — the caller decides whether
    that is fatal), then mirror to MinIO best-effort."""
    if len(content) > MAX_OBJECT_BYTES:
        raise ValueError(f"session-store object exceeds {MAX_OBJECT_BYTES} bytes")
    now = _now_ms()
    with _conn() as c:
        c.execute(
            """
            INSERT INTO session_store (
                session_name, key, profile, task_id, token_id,
                content, content_type, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (session_name, key) DO UPDATE SET
                profile      = EXCLUDED.profile,
                task_id      = COALESCE(EXCLUDED.task_id, session_store.task_id),
                token_id     = COALESCE(EXCLUDED.token_id, session_store.token_id),
                content      = EXCLUDED.content,
                content_type = EXCLUDED.content_type,
                updated_at   = EXCLUDED.updated_at
            """,
            (session_name, key, profile, task_id, token_id, content, content_type, now, now),
        )

    if settings.minio.enabled:
        try:
            from . import artifacts

            artifacts.put_object(
                "artifacts",
                object_key(profile, session_name, key),
                content,
                content_type=content_type,
                metadata={"session": session_name, **({"task-id": task_id} if task_id else {})},
            )
        except Exception as exc:
            log.warning(
                "session_store.mirror_failed",
                metadata={"session": session_name, "key": key, "reason": str(exc)},
            )


def get(session_name: str, key: str) -> tuple[bytes, str] | None:
    """Return (content, content_type). Postgres first; falls back to the
    MinIO mirror location — this covers objects an agent wrote directly to
    MinIO with its profile-scoped credentials."""
    with _conn() as c:
        row = c.execute(
            "SELECT content, content_type FROM session_store "
            "WHERE session_name = %s AND key = %s",
            (session_name, key),
        ).fetchone()
    if row:
        return bytes(row["content"]), row["content_type"]

    if settings.minio.enabled:
        # The profile prefix is unknown here — look it up from any stored
        # row for this session, else scan the known layout is not possible;
        # try the task row's profile, then the unscoped fallback.
        profile = _profile_for_session(session_name)
        from . import artifacts

        for candidate in dict.fromkeys([profile, "_none"]):
            if candidate is None:
                continue
            try:
                data = artifacts.get_object(
                    "artifacts", object_key(candidate, session_name, key)
                )
                ctype = "text/markdown" if key.endswith(".md") else "application/json"
                return data, ctype
            except Exception:
                continue
    return None


def _profile_for_session(session_name: str) -> str | None:
    with _conn() as c:
        row = c.execute(
            "SELECT profile FROM session_store WHERE session_name = %s LIMIT 1",
            (session_name,),
        ).fetchone()
    return row["profile"] if row else None


def _row_to_dict(row: Any) -> dict[str, Any]:
    d = dict(row)
    d["content"] = bytes(d["content"])
    return d


def get_by_task_id(task_id: str) -> dict[str, Any] | None:
    """Full task.json row for a hub task id — the token-relative fetch path.
    Postgres-only: restart-proof without depending on router._tasks."""
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM session_store WHERE task_id = %s AND key = %s",
            (task_id, KEY_TASK),
        ).fetchone()
    return _row_to_dict(row) if row else None


def get_by_token_id(token_id: str) -> dict[str, Any] | None:
    """Task row matched by the token minted at create — lets a long session
    whose bearer token has expired still prove original possession when
    PUTting its result."""
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM session_store WHERE token_id = %s AND key = %s",
            (token_id, KEY_TASK),
        ).fetchone()
    return _row_to_dict(row) if row else None


def list_keys(session_name: str) -> list[dict[str, Any]]:
    with _conn() as c:
        rows = c.execute(
            "SELECT key, content_type, LENGTH(content) AS size, updated_at "
            "FROM session_store WHERE session_name = %s ORDER BY key",
            (session_name,),
        ).fetchall()
    return [dict(r) for r in rows]


def delete(session_name: str, key: str | None = None) -> int:
    """Tests/ops only — session deletion deliberately keeps store rows
    (they are the handoff source for continue_from, plus audit)."""
    with _conn() as c:
        if key is None:
            cur = c.execute(
                "DELETE FROM session_store WHERE session_name = %s", (session_name,)
            )
        else:
            cur = c.execute(
                "DELETE FROM session_store WHERE session_name = %s AND key = %s",
                (session_name, key),
            )
        return cur.rowcount


# ---------------------------------------------------------------------------
# Async wrappers (store.py convention)
# ---------------------------------------------------------------------------


async def async_put(*args: Any, **kwargs: Any) -> None:
    await asyncio.to_thread(put, *args, **kwargs)


async def async_get(session_name: str, key: str) -> tuple[bytes, str] | None:
    return await asyncio.to_thread(get, session_name, key)


async def async_get_by_task_id(task_id: str) -> dict[str, Any] | None:
    return await asyncio.to_thread(get_by_task_id, task_id)


async def async_get_by_token_id(token_id: str) -> dict[str, Any] | None:
    return await asyncio.to_thread(get_by_token_id, token_id)

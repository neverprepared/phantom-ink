"""OpenSearch sink — indexes the agent event stream for history/search.

A second durable consumer over `agent_events` (same name-keyed cursor table
as the rules engine, cursor name "opensearch-sink") bulk-indexes every
envelope into monthly indices `{prefix}-YYYY.MM` on the shared OpenSearch
cluster. Design invariants:

- The cursor initializes at seq 0: enabling the sink on an existing
  deployment indexes FULL history (that's the point of a search store), and
  catch-up after downtime — cluster outage, integration toggled off for a
  week — is automatic: the cursor sits where it stopped.
- `_id = str(seq)` makes at-least-once delivery idempotent.
- OpenSearch is never in a hot path. ingest() and the rules engine only
  touch Postgres; when the cluster is down this loop stalls its own cursor
  and retries on the next tick. `last_error` is surfaced via
  /api/rules/status → sink.
- Per-document mapping rejections retry once with a stripped fallback doc,
  then advance — a poison document must never wedge the stream.
- Monthly indices make retention = index deletion (no delete_by_query
  churn); search fans out over `{prefix}-*`.

Config: CL_OPENSEARCH__* (see OpenSearchSettings). Enabled purely by
addresses being set. opensearch-py is synchronous (urllib3 — unaffected by
the daemon's httpx→LAN issue); every client call runs in asyncio.to_thread.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any

from .config import settings
from .log import get_logger
from .store import _conn
from . import agent_store
from .event_rules import fetch_events_after

log = get_logger()

CURSOR_NAME = "opensearch-sink"

_sink_task: asyncio.Task[None] | None = None
_loop_ref: asyncio.AbstractEventLoop | None = None
_wakeup: asyncio.Event = asyncio.Event()
_listener_registered = False
_last_error: str | None = None
_client_cache: Any = None

# Explicit, closed mapping (dynamic: false) — arbitrary envelope metadata
# must not explode the index field count. Full-text search over metadata
# still works via envelope_json (the raw envelope as one text field).
_INDEX_TEMPLATE: dict[str, Any] = {
    "index_patterns": ["__PREFIX__-*"],
    "template": {
        "settings": {"number_of_shards": 1, "number_of_replicas": 0},
        "mappings": {
            "dynamic": False,
            "properties": {
                "seq": {"type": "long"},
                "ts": {"type": "date", "format": "epoch_millis"},
                "id": {"type": "keyword"},
                "kind": {"type": "keyword"},
                "source": {"type": "keyword"},
                "type": {"type": "keyword"},
                "status": {"type": "keyword"},
                "parent_id": {"type": "keyword"},
                "workspace": {"type": "keyword"},
                "tags": {"type": "keyword"},
                "title": {"type": "text"},
                "subtitle": {"type": "text"},
                "description": {"type": "text"},
                "envelope_json": {"type": "text"},
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Client wrappers — thin and module-level so tests monkeypatch them
# ---------------------------------------------------------------------------


def _client() -> Any:
    global _client_cache
    if _client_cache is not None:
        return _client_cache
    from opensearchpy import OpenSearch

    cfg = settings.opensearch
    kwargs: dict[str, Any] = {
        "hosts": cfg.addresses,
        "timeout": cfg.request_timeout_s,
        "verify_certs": not cfg.insecure_skip_verify,
    }
    if cfg.username:
        kwargs["http_auth"] = (cfg.username, cfg.password.get_secret_value())
    _client_cache = OpenSearch(**kwargs)
    return _client_cache


def _put_template() -> None:
    prefix = settings.opensearch.index_prefix
    body = json.loads(json.dumps(_INDEX_TEMPLATE).replace("__PREFIX__", prefix))
    _client().indices.put_index_template(name=f"{prefix}-template", body=body)


def _bulk(actions: list[dict[str, Any]]) -> dict[str, Any]:
    """Raw _bulk call. `actions` are [{"index": {...meta}}, doc, ...] pairs
    already flattened into the list."""
    lines = "\n".join(json.dumps(a, separators=(",", ":")) for a in actions) + "\n"
    return _client().bulk(body=lines)


def _os_search(index: str, body: dict[str, Any]) -> dict[str, Any]:
    return _client().search(index=index, body=body, ignore_unavailable=True)


# ---------------------------------------------------------------------------
# Cursor + docs
# ---------------------------------------------------------------------------


def _now_ms() -> int:
    return int(time.time() * 1000)


def init_cursor_if_absent() -> int:
    """Unlike the rules consumer (which starts at head to avoid replaying
    history through fresh rules), the sink starts at 0: indexing full
    history is exactly what a search store is for, and idempotent _ids make
    it safe."""
    with _conn() as c:
        c.execute(
            """
            INSERT INTO event_rule_cursor (name, last_seq, updated_at)
            VALUES (%s, 0, %s)
            ON CONFLICT (name) DO NOTHING
            """,
            (CURSOR_NAME, _now_ms()),
        )
        row = c.execute(
            "SELECT last_seq FROM event_rule_cursor WHERE name = %s", (CURSOR_NAME,)
        ).fetchone()
    return row["last_seq"]


def get_cursor() -> int | None:
    with _conn() as c:
        row = c.execute(
            "SELECT last_seq FROM event_rule_cursor WHERE name = %s", (CURSOR_NAME,)
        ).fetchone()
    return row["last_seq"] if row else None


def _save_cursor(seq: int) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE event_rule_cursor SET last_seq = %s, updated_at = %s WHERE name = %s",
            (seq, _now_ms(), CURSOR_NAME),
        )


def index_for(ts_ms: int) -> str:
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    return f"{settings.opensearch.index_prefix}-{dt.strftime('%Y.%m')}"


def _doc_for(row: dict[str, Any]) -> dict[str, Any]:
    envelope_raw = row["envelope"]
    try:
        env = json.loads(envelope_raw)
    except (json.JSONDecodeError, TypeError):
        env = {}
    return {
        "seq": row["seq"],
        "ts": row["ts"],
        "id": env.get("id") or row.get("id"),
        "kind": env.get("kind"),
        "source": env.get("source"),
        "type": env.get("type"),
        "status": env.get("status"),
        "parent_id": env.get("parent_id"),
        "workspace": env.get("workspace"),
        "tags": env.get("tags") or [],
        "title": env.get("title"),
        "subtitle": env.get("subtitle"),
        "description": env.get("description"),
        "envelope_json": envelope_raw,
    }


def _fallback_doc(row: dict[str, Any]) -> dict[str, Any]:
    """Minimal doc for documents the mapping rejects — keeps the seq
    represented in the index rather than wedging the sink."""
    doc = _doc_for(row)
    return {
        "seq": doc["seq"],
        "ts": doc["ts"],
        "id": doc["id"],
        "type": doc["type"],
        "status": doc["status"],
        "envelope_json": doc["envelope_json"],
    }


# ---------------------------------------------------------------------------
# Sink loop
# ---------------------------------------------------------------------------


def notify() -> None:
    """Thread-safe wake (agent_store listeners may fire off-loop)."""
    if _loop_ref is not None and not _loop_ref.is_closed():
        _loop_ref.call_soon_threadsafe(_wakeup.set)


def _index_batch(rows: list[dict[str, Any]]) -> None:
    """Bulk-index one batch; retries per-item rejections with fallback docs.
    Raises on transport-level failure (caller stalls the cursor)."""
    actions: list[dict[str, Any]] = []
    for row in rows:
        actions.append({"index": {"_index": index_for(row["ts"]), "_id": str(row["seq"])}})
        actions.append(_doc_for(row))
    resp = _bulk(actions)

    if not resp.get("errors"):
        return
    # Per-item failures: retry once with the stripped fallback doc, then
    # advance regardless — deliberate lossy-degrade, never a wedge.
    rejected: list[dict[str, Any]] = []
    for item, row in zip(resp.get("items", []), rows):
        status = item.get("index", {}).get("status", 200)
        if status >= 400:
            rejected.append(row)
    if not rejected:
        return
    retry_actions: list[dict[str, Any]] = []
    for row in rejected:
        retry_actions.append({"index": {"_index": index_for(row["ts"]), "_id": str(row["seq"])}})
        retry_actions.append(_fallback_doc(row))
    retry_resp = _bulk(retry_actions)
    if retry_resp.get("errors"):
        log.warning(
            "os_sink.docs_dropped",
            metadata={"count": len(rejected), "first_seq": rejected[0]["seq"]},
        )


async def run_once() -> int:
    """One sink pass: drain everything past the cursor. Returns rows
    indexed. Used by tests and available as a manual tick."""
    global _last_error
    indexed = 0
    cursor = await asyncio.to_thread(init_cursor_if_absent)
    while True:
        rows = await asyncio.to_thread(
            fetch_events_after, cursor, settings.opensearch.batch_size
        )
        if not rows:
            break
        await asyncio.to_thread(_index_batch, rows)
        cursor = rows[-1]["seq"]
        await asyncio.to_thread(_save_cursor, cursor)
        _last_error = None
        indexed += len(rows)
    return indexed


async def _sink_loop() -> None:
    global _last_error
    try:
        await asyncio.to_thread(_put_template)
    except Exception as exc:
        # Template failure is not fatal — indices still get created with
        # dynamic defaults; retried implicitly on next daemon start.
        _last_error = f"template: {exc}"
        log.warning("os_sink.template_failed", metadata={"reason": str(exc)})
    cursor = await asyncio.to_thread(init_cursor_if_absent)
    log.info("os_sink.started", metadata={"cursor": cursor})
    while True:
        try:
            await asyncio.wait_for(
                asyncio.shield(_wakeup.wait()),
                timeout=settings.opensearch.poll_interval_s,
            )
        except asyncio.TimeoutError:
            pass
        _wakeup.clear()

        try:
            while True:
                rows = await asyncio.to_thread(
                    fetch_events_after, cursor, settings.opensearch.batch_size
                )
                if not rows:
                    break
                await asyncio.to_thread(_index_batch, rows)
                cursor = rows[-1]["seq"]
                await asyncio.to_thread(_save_cursor, cursor)
                _last_error = None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Transport/cluster failure: cursor stalls, identical batch
            # retries next tick (idempotent by _id).
            _last_error = str(exc)
            log.warning("os_sink.index_failed", metadata={"reason": str(exc)})


# ---------------------------------------------------------------------------
# Status + search (read path)
# ---------------------------------------------------------------------------


def get_sink_status() -> dict[str, Any]:
    if not settings.opensearch.enabled:
        return {"enabled": False, "cursor": 0, "lag": 0, "last_error": None}
    with _conn() as c:
        head_row = c.execute("SELECT MAX(seq) AS head FROM agent_events").fetchone()
        cursor_row = c.execute(
            "SELECT last_seq FROM event_rule_cursor WHERE name = %s", (CURSOR_NAME,)
        ).fetchone()
    head = head_row["head"] or 0
    cursor = cursor_row["last_seq"] if cursor_row else 0
    return {
        "enabled": True,
        "cursor": cursor,
        "lag": max(0, head - cursor),
        "last_error": _last_error,
    }


def search(
    *,
    q: str = "",
    type_prefix: str = "",
    workspace: str = "",
    status: str = "",
    source: str = "",
    since_ms: int = 0,
    until_ms: int = 0,
    limit: int = 100,
) -> dict[str, Any]:
    """Query the sink indices. Returns {items, total} with items shaped like
    agent_store.list_events rows. Raises on cluster failure — the API layer
    falls back to Postgres."""
    filters: list[dict[str, Any]] = []
    if type_prefix:
        filters.append({"prefix": {"type": type_prefix}})
    if workspace:
        filters.append({"term": {"workspace": workspace}})
    if status:
        filters.append({"term": {"status": status}})
    if source:
        filters.append({"term": {"source": source}})
    if since_ms or until_ms:
        rng: dict[str, Any] = {}
        if since_ms:
            rng["gte"] = since_ms
        if until_ms:
            rng["lte"] = until_ms
        filters.append({"range": {"ts": rng}})

    query: dict[str, Any] = {"bool": {"filter": filters}}
    if q:
        query["bool"]["must"] = [{
            "simple_query_string": {
                "query": q,
                "fields": ["title^2", "subtitle", "description", "id", "envelope_json"],
                "default_operator": "and",
            }
        }]

    body = {"query": query, "sort": [{"ts": "desc"}], "size": limit}
    resp = _os_search(f"{settings.opensearch.index_prefix}-*", body)

    items: list[dict[str, Any]] = []
    for hit in resp.get("hits", {}).get("hits", []):
        src = hit.get("_source", {})
        try:
            envelope = json.loads(src.get("envelope_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            envelope = {}
        items.append({
            "seq": src.get("seq"),
            "id": src.get("id"),
            "source": src.get("source"),
            "type": src.get("type"),
            "status": src.get("status"),
            "parent_id": src.get("parent_id"),
            "ts": src.get("ts"),
            "envelope": envelope,
        })
    total = resp.get("hits", {}).get("total", {})
    total_value = total.get("value") if isinstance(total, dict) else total
    return {"items": items, "total": total_value}


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def start() -> None:
    """Start the sink (called from hub.init). No-op when unconfigured."""
    global _sink_task, _loop_ref, _listener_registered
    if not settings.opensearch.enabled:
        log.info("os_sink.disabled")
        return
    _loop_ref = asyncio.get_running_loop()
    if not _listener_registered:
        agent_store.on_event(lambda _env: notify())
        _listener_registered = True
    _sink_task = _loop_ref.create_task(_sink_loop())


async def stop() -> None:
    global _sink_task
    if _sink_task and not _sink_task.done():
        _sink_task.cancel()
        try:
            await _sink_task
        except (asyncio.CancelledError, Exception):
            pass
    _sink_task = None
    log.info("os_sink.stopped")


def reset_for_tests() -> None:
    global _sink_task, _loop_ref, _listener_registered, _last_error, _client_cache
    _sink_task = None
    _loop_ref = None
    _listener_registered = False
    _last_error = None
    _client_cache = None
    _wakeup.clear()

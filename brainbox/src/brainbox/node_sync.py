"""Node sync — merge remote store rows into the local store (local-first / P2P).

Slice 2. This is the transport-agnostic MERGE engine: given rows exported from
another node's store, apply them locally with the correct per-class CRDT
semantics. The network/peer transport that actually moves rows between nodes is
deliberately NOT here — it encodes deployment decisions (peer discovery, push vs
pull, inter-node auth) that need an explicit call; see
docs/p2p-local-first-slice2.md. This module is the deterministic, unit-tested
core that transport will sit on. Both halves (export + import) run locally so
two "nodes" can be simulated in one process for tests.

Two merge classes (table taxonomy in docs/p2p-local-first-slice1.md):

- OP-LOG (agent_events, session_history, audit_log): append-only. Merge = union
  keyed on the globally-unique ULID. ``INSERT ... ON CONFLICT (ulid) DO NOTHING``
  is idempotent and order-independent. A merged agent_events row is assigned a
  fresh LOCAL ``seq`` on insert, so the existing seq-based consumers (rules
  engine, OpenSearch sink) pick merged events up with no cursor changes.

- OWNER-KEYED (sessions, runners, loop_instances, session_store): one writer per
  key. Merge = last-writer-wins by ``updated_at``, tombstone-aware. Because each
  key has a single owner, a "conflict" is only ever "which update is newest"; a
  tombstone (``deleted_at`` set) is just a row whose newest update marks it
  deleted, so the same rule handles deletion.

NOTE (deferred): merging agent_events unions the LOG only. Re-projecting merged
remote events into the derived ``agent_state`` table is a follow-up (agent_state
is meant to be rebuilt from the log). Until then, local reads of agent_state
reflect local-origin state; merged events are present in the log for any
consumer that reads it directly.
"""

from __future__ import annotations

from typing import Callable, Iterable, Sequence

from .store import _conn

# ---------------------------------------------------------------------------
# Op-log: agent_events (the canonical event log)
# ---------------------------------------------------------------------------

# seq is LOCAL (GENERATED ALWAYS AS IDENTITY) and intentionally omitted — the
# receiving node assigns its own. event_ulid is the global dedup identity.
_EVENT_COLS: tuple[str, ...] = (
    "id", "source", "type", "status", "parent_id", "ts", "envelope",
    "event_ulid", "node_id",
)


def export_events(since_ulid: str | None = None, limit: int = 500) -> list[dict]:
    """Export agent_events with ``event_ulid > since_ulid`` in ULID (time) order.

    Feeds a peer's :func:`import_events`. ULID ordering gives a stable, resumable
    cursor across nodes without relying on any node's local ``seq``.
    """
    cols = ", ".join(_EVENT_COLS)
    with _conn() as c:
        if since_ulid:
            rows = c.execute(
                f"SELECT {cols} FROM agent_events "
                "WHERE event_ulid IS NOT NULL AND event_ulid > %s "
                "ORDER BY event_ulid ASC LIMIT %s",
                (since_ulid, limit),
            ).fetchall()
        else:
            rows = c.execute(
                f"SELECT {cols} FROM agent_events "
                "WHERE event_ulid IS NOT NULL "
                "ORDER BY event_ulid ASC LIMIT %s",
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


def import_events(rows: Iterable[dict]) -> int:
    """Union-merge remote agent_events. Idempotent, order-independent.

    Returns the number of NEW rows inserted (``ON CONFLICT (event_ulid) DO
    NOTHING`` absorbs re-imports). Rows lacking an ``event_ulid`` (pre-Slice-1,
    un-backfilled) are skipped — without a stable identity they cannot be
    safely de-duplicated.
    """
    cols = ", ".join(_EVENT_COLS)
    placeholders = ", ".join(["%s"] * len(_EVENT_COLS))
    inserted = 0
    with _conn() as c:
        for r in rows:
            if not r.get("event_ulid"):
                continue
            cur = c.execute(
                f"INSERT INTO agent_events ({cols}) VALUES ({placeholders}) "
                "ON CONFLICT (event_ulid) DO NOTHING",
                tuple(r.get(k) for k in _EVENT_COLS),
            )
            inserted += cur.rowcount
    return inserted


# ---------------------------------------------------------------------------
# Owner-keyed: last-writer-wins by updated_at, tombstone-aware
# ---------------------------------------------------------------------------


def merge_owner_row(table: str, pk_cols: Sequence[str], remote: dict) -> bool:
    """Merge one owner-keyed row: apply ``remote`` iff it is newer than the
    local row (or no local row exists). Returns True if applied.

    Newness is ``updated_at`` (epoch-ms). Tombstones need no special case: a
    deleted row simply carries ``deleted_at`` set and an ``updated_at`` of the
    delete time, so "newest wins" deletes locally when the tombstone is newest,
    and a later re-creation (larger ``updated_at``) revives it. On an exact
    ``updated_at`` tie the local row is kept — deterministic per node.

    ``remote`` must carry every column the table requires (exporters select the
    full row). The SELECT-then-write is not atomic, which is fine: sync runs
    single-threaded in one node's merge tick.
    """
    where = " AND ".join(f"{k} = %s" for k in pk_cols)
    key_vals = tuple(remote[k] for k in pk_cols)
    with _conn() as c:
        local = c.execute(
            f"SELECT updated_at FROM {table} WHERE {where}", key_vals
        ).fetchone()
        if local is not None and local["updated_at"] is not None:
            if (remote.get("updated_at") or 0) <= local["updated_at"]:
                return False
        cols = list(remote.keys())
        collist = ", ".join(cols)
        placeholders = ", ".join(["%s"] * len(cols))
        updates = ", ".join(
            f"{col} = EXCLUDED.{col}" for col in cols if col not in pk_cols
        )
        c.execute(
            f"INSERT INTO {table} ({collist}) VALUES ({placeholders}) "
            f"ON CONFLICT ({', '.join(pk_cols)}) DO UPDATE SET {updates}",
            tuple(remote[col] for col in cols),
        )
    return True


def merge_owner_rows(table: str, pk_cols: Sequence[str], rows: Iterable[dict]) -> int:
    """Merge many owner-keyed rows; returns how many were applied."""
    return sum(1 for r in rows if merge_owner_row(table, pk_cols, r))


# ---------------------------------------------------------------------------
# Pull orchestration — transport-agnostic
# ---------------------------------------------------------------------------

# fetch(since_ulid, limit) -> list[dict]; any callable (HTTP client, in-process
# peer, or a test stub) that returns a peer's exported event batch.
EventFetch = Callable[[str | None, int], list[dict]]


def sync_pull_events(
    fetch: EventFetch, cursor_ulid: str | None, limit: int = 500
) -> tuple[int, str | None]:
    """Pull one event batch from a peer, merge it, and advance the cursor.

    Returns ``(num_new, new_cursor_ulid)``. The cursor is the max ULID seen, so
    the next call resumes after it; an empty batch leaves the cursor unmoved.
    Transport lives entirely in ``fetch`` — this function is pure orchestration.
    """
    rows = fetch(cursor_ulid, limit)
    if not rows:
        return 0, cursor_ulid
    new_count = import_events(rows)
    new_cursor = max(
        (r["event_ulid"] for r in rows if r.get("event_ulid")),
        default=cursor_ulid,
    )
    return new_count, new_cursor

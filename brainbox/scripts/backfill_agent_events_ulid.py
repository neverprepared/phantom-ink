"""One-time backfill: stamp event_ulid + node_id on pre-Slice-1 agent_events rows.

New rows get a ULID at insert (agent_store.ingest). This fills historical rows so
they carry a time-sortable identity for the Slice-2 ULID cursor. Idempotent —
only touches rows where event_ulid IS NULL, so it is safe to re-run.

Ordering of backfilled rows is best-effort (each ULID's time component is the
row's own ``ts``); exact append-order is NOT required here because both durable
consumers (rules engine, OpenSearch sink) start from ``seq`` — head, or
full-history-by-``_id`` — not from ``event_ulid``, until the Slice-2 cutover.

Run:  cd brainbox && uv run python scripts/backfill_agent_events_ulid.py
Requires CL_DATABASE_URL.
"""

from __future__ import annotations

import sys

from brainbox.node_identity import node_id, ulid
from brainbox.store import _conn, init_db


def backfill(batch: int = 1000) -> int:
    """Stamp NULL-ulid agent_events rows in seq order. Returns rows touched."""
    init_db()
    nid = node_id()
    total = 0
    while True:
        with _conn() as c:
            rows = c.execute(
                "SELECT seq, ts FROM agent_events WHERE event_ulid IS NULL "
                "ORDER BY seq ASC LIMIT %s",
                (batch,),
            ).fetchall()
            if not rows:
                break
            for r in rows:
                c.execute(
                    "UPDATE agent_events "
                    "SET event_ulid = %s, node_id = COALESCE(node_id, %s) "
                    "WHERE seq = %s",
                    (ulid(r["ts"]), nid, r["seq"]),
                )
        total += len(rows)
        print(f"backfilled {total} rows...", file=sys.stderr)
    return total


if __name__ == "__main__":
    n = backfill()
    print(f"done: {n} agent_events rows backfilled")

"""Slice 1 (local-first substrate): node identity + additive column stamping.

Covers node_identity primitives (pure) and that each op-log write path now
populates its ULID/node_id columns, plus the agent_events backfill script.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from brainbox import agent_store
from brainbox.node_identity import node_id, ulid
from brainbox.store import _conn, insert_audit


def _load_backfill():
    path = Path(__file__).resolve().parent.parent / "scripts" / "backfill_agent_events_ulid.py"
    spec = importlib.util.spec_from_file_location("backfill_agent_events_ulid", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.backfill


# ---------------------------------------------------------------------------
# node_identity — pure unit tests
# ---------------------------------------------------------------------------


def test_ulid_length_and_alphabet():
    u = ulid()
    assert len(u) == 26
    assert all(ch in "0123456789ABCDEFGHJKMNPQRSTVWXYZ" for ch in u)


def test_ulid_is_time_sortable():
    # Earlier timestamp must sort lexicographically before a later one.
    assert ulid(1_000) < ulid(2_000) < ulid(1_700_000_000_000)


def test_ulid_unique_within_same_ms():
    ts = 1_700_000_000_000
    assert ulid(ts) != ulid(ts)  # 80 bits of randomness in the low half


def test_node_id_is_stable():
    assert node_id() == node_id()


def test_node_id_env_override(monkeypatch):
    node_id.cache_clear()
    monkeypatch.setenv("CL_NODE_ID", "node-alpha")
    try:
        assert node_id() == "node-alpha"
    finally:
        node_id.cache_clear()  # don't leak the override into other tests


# ---------------------------------------------------------------------------
# Write-path stamping (DB) — uses the autouse store-reset fixture from conftest
# ---------------------------------------------------------------------------


def _one(sql: str) -> dict:
    with _conn() as c:
        return c.execute(sql).fetchone()


def test_insert_audit_stamps_ulid_and_node():
    insert_audit("test.event", actor="tester")
    row = _one("SELECT row_ulid, node_id FROM audit_log LIMIT 1")
    assert row["row_ulid"] and len(row["row_ulid"]) == 26
    assert row["node_id"] == node_id()


def test_audit_ulids_distinct_and_node_shared():
    insert_audit("e1")
    insert_audit("e2")
    with _conn() as c:
        rows = c.execute("SELECT row_ulid, node_id FROM audit_log ORDER BY ts").fetchall()
    ulids = [r["row_ulid"] for r in rows]
    assert len(set(ulids)) == 2
    assert {r["node_id"] for r in rows} == {node_id()}


def test_agent_events_insert_stamps_ulid_and_node():
    agent_store.ingest(
        {
            "id": "task:t1",
            "kind": "event",
            "source": "test@node",
            "type": "task.queued",
            "status": "upcoming",
            "title": "T",
            "workspace": "personal",
        }
    )
    row = _one("SELECT event_ulid, node_id FROM agent_events LIMIT 1")
    assert row["event_ulid"] and len(row["event_ulid"]) == 26
    assert row["node_id"] == node_id()


# ---------------------------------------------------------------------------
# Backfill script
# ---------------------------------------------------------------------------


def test_backfill_stamps_legacy_rows():
    # Simulate a pre-Slice-1 row: NULL event_ulid / node_id.
    with _conn() as c:
        c.execute(
            "INSERT INTO agent_events (id, source, type, status, parent_id, ts, envelope) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            ("legacy:1", "old@node", "task.done", "done", None, 1_600_000_000_000, "{}"),
        )
    touched = _load_backfill()()
    assert touched >= 1
    row = _one("SELECT event_ulid, node_id FROM agent_events WHERE id = 'legacy:1'")
    assert row["event_ulid"] and len(row["event_ulid"]) == 26
    assert row["node_id"] == node_id()

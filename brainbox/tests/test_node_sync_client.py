"""Slice 3b: owner-row export/merge + the peer-sync pull client.

Owner-row merge and the pull orchestration are exercised with two "nodes" in one
process — remote rows are plain dicts, and the pull client's transport is
monkeypatched — so the merge + cursor semantics are tested without any network.
"""

from __future__ import annotations

import json

from brainbox import node_sync, node_sync_client, store
from brainbox.config import settings
from brainbox.runners import RunnerInfo
from brainbox.store import _conn, delete_runner, load_all_runners, upsert_runner


def _mk_runner(name: str) -> None:
    upsert_runner(
        RunnerInfo(name=name, capabilities={}, tags=[], version="",
                   registered_at=1, last_seen=0)
    )


def _remote_runner_row(name: str, updated_at: int, *, deleted_at=None) -> dict:
    """A full runners row as a peer would export it (every _OWNER_COLS column)."""
    return {
        "name": name, "capabilities": "{}", "tags": "[]", "version": "",
        "host": "h", "machine_id": None, "max_concurrent": 4,
        "last_seal_at": None, "registered_at": updated_at, "updated_at": updated_at,
        "owner_node": "B", "deleted_at": deleted_at,
    }


def _owner_item(name: str, updated_at: int, *, deleted_at=None) -> dict:
    return {
        "table": "runners", "pk_cols": ["name"], "updated_at": updated_at,
        "row": _remote_runner_row(name, updated_at, deleted_at=deleted_at),
    }


def _runner_names() -> set[str]:
    return {r["name"] for r in load_all_runners()}


# ---------------------------------------------------------------------------
# Peer spec parsing
# ---------------------------------------------------------------------------


def test_parse_peer_full():
    p = node_sync_client.parse_peer("m3=http://m3-64:8790|secrettoken")
    assert (p.label, p.base_url, p.token) == ("m3", "http://m3-64:8790", "secrettoken")


def test_parse_peer_strips_trailing_slash():
    assert node_sync_client.parse_peer("a=http://h:1/").base_url == "http://h:1"


def test_parse_peer_token_defaults_to_api_key(monkeypatch):
    monkeypatch.setattr(node_sync_client, "get_api_key", lambda: "LOCALKEY")
    assert node_sync_client.parse_peer("a=http://h:1").token == "LOCALKEY"


def test_parse_peers_skips_malformed():
    peers = node_sync_client.parse_peers(
        ["good=http://h:1|t", "no-equals-here", "=http://h"]
    )
    assert [p.label for p in peers] == ["good"]


# ---------------------------------------------------------------------------
# Cursor persistence
# ---------------------------------------------------------------------------


def test_sync_cursor_roundtrip_and_stream_isolation():
    assert store.get_sync_cursor("m3", "events") is None
    store.set_sync_cursor("m3", "events", "01AAA")
    store.set_sync_cursor("m3", "owner_rows", "1700")
    assert store.get_sync_cursor("m3", "events") == "01AAA"
    assert store.get_sync_cursor("m3", "owner_rows") == "1700"  # independent streams
    store.set_sync_cursor("m3", "events", "01BBB")               # upsert
    assert store.get_sync_cursor("m3", "events") == "01BBB"
    assert store.get_sync_cursor("other", "events") is None      # per-peer isolation


# ---------------------------------------------------------------------------
# Owner-row export
# ---------------------------------------------------------------------------


def test_export_owner_rows_includes_runner_and_tombstone():
    _mk_runner("r1")
    items = node_sync.export_owner_rows(since_ms=0, limit=10)
    assert len(items) == 1
    it = items[0]
    assert it["table"] == "runners" and it["pk_cols"] == ["name"]
    assert it["row"]["name"] == "r1" and it["row"]["deleted_at"] is None

    delete_runner("r1")  # tombstones (deleted_at set, updated_at bumped)
    tomb = node_sync.export_owner_rows(since_ms=0, limit=10)
    # tombstones MUST be exported (no deleted_at IS NULL filter) so deletes sync.
    assert tomb[0]["row"]["deleted_at"] is not None


def test_export_owner_rows_respects_since_cursor():
    _mk_runner("r1")
    items = node_sync.export_owner_rows(since_ms=0, limit=10)
    cur = items[-1]["updated_at"]
    assert node_sync.export_owner_rows(since_ms=cur + 1, limit=10) == []


# ---------------------------------------------------------------------------
# Owner-row pull orchestration (transport stubbed as a plain callable)
# ---------------------------------------------------------------------------


def test_sync_pull_owner_rows_merges_and_advances_cursor():
    batch = [_owner_item("rR", 100)]

    def fetch(since, limit):
        return [it for it in batch if it["updated_at"] >= (since or 0)][:limit]

    n, cur = node_sync.sync_pull_owner_rows(fetch, 0)
    assert n == 1 and cur == 100
    assert "rR" in _runner_names()

    # Idempotent re-pull: >= cursor re-sends the boundary row, equal updated_at
    # loses LWW, so nothing is applied and the cursor is stable.
    n2, cur2 = node_sync.sync_pull_owner_rows(fetch, cur)
    assert n2 == 0 and cur2 == 100


def test_sync_pull_owner_rows_propagates_tombstone():
    node_sync.sync_pull_owner_rows(lambda _s, _n: [_owner_item("rT", 100)], 0)
    assert "rT" in _runner_names()
    node_sync.sync_pull_owner_rows(
        lambda _s, _n: [_owner_item("rT", 200, deleted_at=200)], 100
    )
    assert "rT" not in _runner_names()  # tombstone hides it from reads


# ---------------------------------------------------------------------------
# Pull client end-to-end with a monkeypatched curl transport
# ---------------------------------------------------------------------------


def _fake_acurl(payload: dict, expect_path: str):
    async def _acurl(method, base_url, path, *, headers=None, timeout=10.0, **kw):
        assert expect_path in path
        assert headers and headers.get("X-API-Key") == "tok"
        return 200, json.dumps(payload)
    return _acurl


async def test_pull_owner_rows_merges_and_persists_cursor(monkeypatch):
    payload = {"rows": [_owner_item("rP", 500)], "count": 1, "cursor": 500}
    monkeypatch.setattr(node_sync_client.ollama, "acurl_request",
                        _fake_acurl(payload, "/api/sync/owner-rows"))
    peer = node_sync_client.Peer("m3", "http://peer", "tok")

    n = await node_sync_client._pull_owner_rows(peer, 500)
    assert n == 1
    assert "rP" in _runner_names()
    assert store.get_sync_cursor("m3", "owner_rows") == "500"


async def test_pull_events_merges_and_persists_cursor(monkeypatch):
    ev = {"id": "task:x", "source": "s", "type": "t", "status": "done",
          "parent_id": None, "ts": 1, "envelope": "{}",
          "event_ulid": "01ZZZ", "node_id": "B"}
    payload = {"events": [ev], "count": 1, "cursor": "01ZZZ"}
    monkeypatch.setattr(node_sync_client.ollama, "acurl_request",
                        _fake_acurl(payload, "/api/sync/events"))
    peer = node_sync_client.Peer("m3", "http://peer", "tok")

    n = await node_sync_client._pull_events(peer, 500)
    assert n == 1
    with _conn() as c:
        got = c.execute(
            "SELECT COUNT(*) AS n FROM agent_events WHERE event_ulid = '01ZZZ'"
        ).fetchone()["n"]
    assert got == 1
    assert store.get_sync_cursor("m3", "events") == "01ZZZ"


async def test_pull_skips_disabled_peer_without_advancing(monkeypatch):
    async def _acurl(*a, **kw):
        return 404, ""
    monkeypatch.setattr(node_sync_client.ollama, "acurl_request", _acurl)
    peer = node_sync_client.Peer("m3", "http://peer", "tok")

    assert await node_sync_client._pull_owner_rows(peer, 500) == 0
    assert store.get_sync_cursor("m3", "owner_rows") is None  # no cursor written


# ---------------------------------------------------------------------------
# Owner-row export endpoint (flag-gated)
# ---------------------------------------------------------------------------


async def test_owner_rows_export_404_when_disabled(client):
    assert settings.sync.enabled is False  # default
    async with client as c:
        r = await c.get("/api/sync/owner-rows")
    assert r.status_code == 404


async def test_owner_rows_export_returns_rows_when_enabled(client, monkeypatch):
    _mk_runner("rE")
    monkeypatch.setattr(settings.sync, "enabled", True)
    async with client as c:
        r = await c.get("/api/sync/owner-rows", params={"since": 0})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["rows"][0]["row"]["name"] == "rE"

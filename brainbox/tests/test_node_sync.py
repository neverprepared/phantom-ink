"""Slice 2: node_sync merge engine + runner tombstone semantics.

Two "nodes" are simulated in one process — remote rows are plain dicts fed to
the import side, so the merge semantics are exercised without any transport.
"""

from __future__ import annotations

from brainbox import node_sync
from brainbox.config import settings
from brainbox.runners import RunnerInfo
from brainbox.store import _conn, delete_runner, load_all_runners, upsert_runner


def _remote_event(ulid, *, id="task:x", node="B") -> dict:
    return {
        "id": id,
        "source": "s@B",
        "type": "task.done",
        "status": "done",
        "parent_id": None,
        "ts": 1_700_000_000_000,
        "envelope": "{}",
        "event_ulid": ulid,
        "node_id": node,
    }


def _remote_runner(name, updated_at, *, host="h", deleted_at=None) -> dict:
    return {
        "name": name,
        "capabilities": "{}",
        "tags": "[]",
        "version": "",
        "host": host,
        "machine_id": None,
        "max_concurrent": 4,
        "registered_at": updated_at,
        "updated_at": updated_at,
        "owner_node": "B",
        "deleted_at": deleted_at,
    }


def _count_events() -> int:
    with _conn() as c:
        return c.execute("SELECT COUNT(*) AS n FROM agent_events").fetchone()["n"]


def _runner_field(name, field):
    with _conn() as c:
        r = c.execute(f"SELECT {field} FROM runners WHERE name = %s", (name,)).fetchone()
    return r[field] if r else None


def _runner_names() -> set[str]:
    return {r["name"] for r in load_all_runners()}


# ---------------------------------------------------------------------------
# Op-log union merge
# ---------------------------------------------------------------------------


def test_import_events_is_idempotent():
    rows = [_remote_event("01AAA"), _remote_event("01BBB")]
    assert node_sync.import_events(rows) == 2
    assert node_sync.import_events(rows) == 0  # dedup on event_ulid
    assert _count_events() == 2


def test_import_skips_rows_without_ulid():
    assert node_sync.import_events([_remote_event(None)]) == 0
    assert _count_events() == 0


def test_imported_event_gets_local_seq():
    node_sync.import_events([_remote_event("01CCC", id="task:c")])
    with _conn() as c:
        row = c.execute(
            "SELECT seq FROM agent_events WHERE event_ulid = '01CCC'"
        ).fetchone()
    assert row["seq"] is not None  # local IDENTITY assigned → local consumers see it


def test_export_events_respects_cursor():
    node_sync.import_events(
        [_remote_event("01AAA"), _remote_event("01BBB"), _remote_event("01CCC")]
    )
    got = node_sync.export_events(since_ulid="01AAA", limit=10)
    assert [r["event_ulid"] for r in got] == ["01BBB", "01CCC"]


def test_sync_pull_events_advances_cursor():
    batch = [_remote_event("01AAA"), _remote_event("01BBB")]

    def fetch(since, limit):
        return [r for r in batch if since is None or r["event_ulid"] > since][:limit]

    n, cur = node_sync.sync_pull_events(fetch, None)
    assert n == 2 and cur == "01BBB"
    n2, cur2 = node_sync.sync_pull_events(fetch, cur)
    assert n2 == 0 and cur2 == "01BBB"  # nothing new, cursor stable


# ---------------------------------------------------------------------------
# Owner-keyed LWW + tombstone merge
# ---------------------------------------------------------------------------


def test_merge_owner_row_newer_wins():
    node_sync.merge_owner_row("runners", ["name"], _remote_runner("r1", 100, host="old"))
    applied = node_sync.merge_owner_row(
        "runners", ["name"], _remote_runner("r1", 200, host="new")
    )
    assert applied is True
    assert _runner_field("r1", "host") == "new"


def test_merge_owner_row_older_skipped():
    node_sync.merge_owner_row("runners", ["name"], _remote_runner("r1", 100, host="keep"))
    applied = node_sync.merge_owner_row(
        "runners", ["name"], _remote_runner("r1", 50, host="stale")
    )
    assert applied is False
    assert _runner_field("r1", "host") == "keep"


def test_merge_owner_row_tie_keeps_local():
    node_sync.merge_owner_row("runners", ["name"], _remote_runner("r1", 100, host="local"))
    applied = node_sync.merge_owner_row(
        "runners", ["name"], _remote_runner("r1", 100, host="remote")
    )
    assert applied is False
    assert _runner_field("r1", "host") == "local"


def test_merge_owner_row_tombstone_wins():
    node_sync.merge_owner_row("runners", ["name"], _remote_runner("r1", 100))
    assert "r1" in _runner_names()
    applied = node_sync.merge_owner_row(
        "runners", ["name"], _remote_runner("r1", 200, deleted_at=200)
    )
    assert applied is True
    assert "r1" not in _runner_names()  # tombstone hides it from reads


# ---------------------------------------------------------------------------
# Runner tombstone via store functions
# ---------------------------------------------------------------------------


def test_delete_runner_tombstones_and_reregister_revives():
    info = RunnerInfo(
        name="rX", capabilities={}, tags=[], version="", registered_at=1, last_seen=0
    )
    upsert_runner(info)
    assert "rX" in _runner_names()

    delete_runner("rX")
    assert "rX" not in _runner_names()  # hidden from reads
    assert _runner_field("rX", "deleted_at") is not None  # row still there, tombstoned

    upsert_runner(info)  # re-register clears the tombstone
    assert "rX" in _runner_names()
    assert _runner_field("rX", "deleted_at") is None


# ---------------------------------------------------------------------------
# Slice 3a: flag-gated export endpoint (server half of pull-sync)
# ---------------------------------------------------------------------------


async def test_sync_export_endpoint_404_when_disabled(client):
    assert settings.sync.enabled is False  # default
    async with client as c:
        r = await c.get("/api/sync/events")
    assert r.status_code == 404


async def test_sync_export_endpoint_returns_events_when_enabled(client, monkeypatch):
    node_sync.import_events([_remote_event("01AAA"), _remote_event("01BBB")])
    monkeypatch.setattr(settings.sync, "enabled", True)
    async with client as c:
        r = await c.get("/api/sync/events", params={"since": "01AAA"})
    assert r.status_code == 200
    body = r.json()
    assert [e["event_ulid"] for e in body["events"]] == ["01BBB"]
    assert body["cursor"] == "01BBB"

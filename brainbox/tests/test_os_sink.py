"""Tests for the OpenSearch sink (fake client — no cluster in CI)."""

from __future__ import annotations

import pytest

import brainbox.event_rules as er
import brainbox.os_sink as sink
from brainbox import agent_store
from brainbox.agent_store import AgentEnvelope
from brainbox.config import settings


def _ingest(n: int = 1, type_: str = "task.failed"):
    for i in range(n):
        agent_store.ingest(AgentEnvelope(
            id=f"sink-test:{type_}:{i}", title=f"event {i}", source="test",
            type=type_, status="failed", workspace="personal",
        ))


class FakeBulk:
    """Records bulk actions; scripted failures."""

    def __init__(self):
        self.calls: list[list[dict]] = []
        self.fail_transport = False
        self.reject_seqs: set[int] = set()  # per-item 400s on first attempt

    def __call__(self, actions):
        if self.fail_transport:
            raise ConnectionError("cluster down")
        self.calls.append(actions)
        items = []
        errors = False
        docs = actions[1::2]
        metas = actions[0::2]
        for meta, doc in zip(metas, docs):
            seq = int(meta["index"]["_id"])
            # fallback docs are stripped (no 'title' key) — accept those
            if seq in self.reject_seqs and "title" in doc:
                items.append({"index": {"status": 400, "error": {"type": "mapper_parsing_exception"}}})
                errors = True
            else:
                items.append({"index": {"status": 201}})
        return {"errors": errors, "items": items}

    @property
    def indexed(self) -> list[tuple[str, str, dict]]:
        out = []
        for actions in self.calls:
            for meta, doc in zip(actions[0::2], actions[1::2]):
                out.append((meta["index"]["_index"], meta["index"]["_id"], doc))
        return out


@pytest.fixture
def fake_os(monkeypatch):
    fake = FakeBulk()
    monkeypatch.setattr(sink, "_bulk", fake)
    monkeypatch.setattr(sink, "_put_template", lambda: None)
    monkeypatch.setattr(settings.opensearch, "addresses", ["http://localhost:9200"])
    return fake


class TestCursor:
    def test_initializes_at_zero_not_max(self, fake_os):
        _ingest(3)
        assert sink.init_cursor_if_absent() == 0  # full-history backfill
        # ...while the rules consumer would start at head:
        assert er.init_cursor_if_absent() == 3

    def test_independent_of_rules_cursor(self, fake_os):
        _ingest(2)
        er.init_cursor_if_absent()
        sink.init_cursor_if_absent()
        assert er.get_cursor() == 2
        assert sink.get_cursor() == 0


class TestIndexing:
    async def test_indexes_full_history_idempotent_ids(self, fake_os):
        _ingest(3)
        indexed = await sink.run_once()
        assert indexed == 3
        assert sink.get_cursor() == 3
        entries = fake_os.indexed
        assert [e[1] for e in entries] == ["1", "2", "3"]  # _id = str(seq)
        idx, _, doc = entries[0]
        assert idx.startswith(settings.opensearch.index_prefix + "-")
        assert len(idx.split("-")[-1]) == 7  # YYYY.MM
        assert doc["type"] == "task.failed"
        assert doc["workspace"] == "personal"
        assert '"title"' in doc["envelope_json"]

    async def test_nothing_new_is_noop(self, fake_os):
        _ingest(1)
        await sink.run_once()
        assert await sink.run_once() == 0
        assert len(fake_os.calls) == 1

    async def test_transport_failure_stalls_cursor(self, fake_os):
        _ingest(2)
        fake_os.fail_transport = True
        with pytest.raises(ConnectionError):
            await sink.run_once()
        assert sink.get_cursor() == 0  # unchanged — same batch retries later
        fake_os.fail_transport = False
        assert await sink.run_once() == 2
        assert sink.get_cursor() == 2

    async def test_per_item_rejection_falls_back_then_advances(self, fake_os):
        _ingest(2)
        fake_os.reject_seqs = {1}
        indexed = await sink.run_once()
        assert indexed == 2
        assert sink.get_cursor() == 2  # poison doc never wedges the stream
        # seq 1 was written twice: full doc (rejected) then fallback doc
        ids = [e[1] for e in fake_os.indexed]
        assert ids.count("1") == 2
        fallback = fake_os.indexed[-1][2]
        assert "title" not in fallback and "envelope_json" in fallback

    def test_index_for_monthly_naming(self):
        # 2026-07-09 UTC
        assert sink.index_for(1783900000000).endswith("-2026.07")


class TestLifecycle:
    def test_start_noop_when_disabled(self, monkeypatch):
        monkeypatch.setattr(settings.opensearch, "addresses", [])
        sink.start()  # must not raise, must not need a running loop
        assert sink._sink_task is None

    def test_sink_status_disabled(self, monkeypatch):
        monkeypatch.setattr(settings.opensearch, "addresses", [])
        assert sink.get_sink_status() == {
            "enabled": False, "cursor": 0, "lag": 0, "last_error": None,
        }

    def test_sink_status_enabled_lag(self, fake_os):
        _ingest(4)
        sink.init_cursor_if_absent()
        status = sink.get_sink_status()
        assert status["enabled"] is True
        assert status["cursor"] == 0 and status["lag"] == 4

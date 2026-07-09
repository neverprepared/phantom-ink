"""Tests for GET /api/agent_events/search (OpenSearch path + PG fallback)."""

from __future__ import annotations

import pytest

import brainbox.os_sink as sink
from brainbox import agent_store
from brainbox.agent_store import AgentEnvelope
from brainbox.config import settings


@pytest.fixture
def seeded():
    agent_store.ingest(AgentEnvelope(
        id="hub-task:a", title="deploy the frontend", source="brainbox-hub",
        type="task.failed", status="failed", workspace="personal",
        metadata={"agent_name": "worker-1"},
    ))
    agent_store.ingest(AgentEnvelope(
        id="hub-task:b", title="run backups", source="brainbox-hub",
        type="task.completed", status="done", workspace="gsa",
    ))
    agent_store.ingest(AgentEnvelope(
        id="rule-exec:1", title="triage → webhook", source="brainbox-rules",
        type="rule.execution", status="failed", workspace="personal",
    ))


class TestPostgresFallback:
    async def test_type_prefix(self, client, seeded):
        r = await client.get("/api/agent_events/search", params={"type": "task."})
        body = r.json()
        assert body["backend"] == "postgres"
        assert {i["type"] for i in body["items"]} == {"task.failed", "task.completed"}

    async def test_full_text_q(self, client, seeded):
        r = await client.get("/api/agent_events/search", params={"q": "frontend"})
        body = r.json()
        assert len(body["items"]) == 1
        assert body["items"][0]["id"] == "hub-task:a"
        assert body["items"][0]["envelope"]["title"] == "deploy the frontend"

    async def test_workspace_and_status_filters(self, client, seeded):
        r = await client.get(
            "/api/agent_events/search", params={"workspace": "personal", "status": "failed"}
        )
        ids = {i["id"] for i in r.json()["items"]}
        assert ids == {"hub-task:a", "rule-exec:1"}

    async def test_source_filter(self, client, seeded):
        r = await client.get("/api/agent_events/search", params={"source": "brainbox-rules"})
        assert [i["id"] for i in r.json()["items"]] == ["rule-exec:1"]

    async def test_time_range(self, client, seeded):
        r = await client.get("/api/agent_events/search", params={"until_ms": 1})
        assert r.json()["items"] == []

    async def test_newest_first_and_limit(self, client, seeded):
        r = await client.get("/api/agent_events/search", params={"limit": 2})
        items = r.json()["items"]
        assert len(items) == 2
        assert items[0]["seq"] > items[1]["seq"]


class TestOpenSearchPath:
    async def test_uses_os_when_enabled(self, client, seeded, monkeypatch):
        monkeypatch.setattr(settings.opensearch, "addresses", ["http://x:9200"])
        captured = {}

        def _fake_search(**kwargs):
            captured.update(kwargs)
            return {"items": [{"seq": 1, "id": "os-hit", "type": "task.failed",
                               "status": "failed", "source": "brainbox-hub",
                               "parent_id": None, "ts": 1, "envelope": {}}],
                    "total": 1}

        monkeypatch.setattr(sink, "search", _fake_search)
        r = await client.get(
            "/api/agent_events/search",
            params={"q": "deploy", "type": "task.", "workspace": "personal"},
        )
        body = r.json()
        assert body["backend"] == "opensearch"
        assert body["total"] == 1
        assert body["items"][0]["id"] == "os-hit"
        assert captured["q"] == "deploy"
        assert captured["type_prefix"] == "task."
        assert captured["workspace"] == "personal"

    async def test_os_failure_falls_back_to_postgres(self, client, seeded, monkeypatch):
        monkeypatch.setattr(settings.opensearch, "addresses", ["http://x:9200"])

        def _boom(**kwargs):
            raise ConnectionError("cluster down")

        monkeypatch.setattr(sink, "search", _boom)
        r = await client.get("/api/agent_events/search", params={"q": "frontend"})
        body = r.json()
        assert body["backend"] == "postgres"
        assert body["items"][0]["id"] == "hub-task:a"

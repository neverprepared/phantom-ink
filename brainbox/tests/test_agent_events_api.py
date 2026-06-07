"""API tests for the agent event bus endpoints."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_ingest_single_envelope(client):
    async with client as c:
        resp = await c.post(
            "/api/agent_events",
            json={
                "id": "task:t1",
                "kind": "event",
                "source": "wails-queue@laptop",
                "type": "task.queued",
                "status": "upcoming",
                "title": "Test",
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"ingested": 1, "ids": ["task:t1"]}


@pytest.mark.asyncio
async def test_ingest_batch(client):
    async with client as c:
        resp = await c.post(
            "/api/agent_events",
            json={"events": [
                {"id": "a", "kind": "event", "title": "A", "source": "x", "type": "task.queued", "status": "upcoming"},
                {"id": "b", "kind": "event", "title": "B", "source": "x", "type": "task.queued", "status": "upcoming"},
            ]},
        )
    assert resp.status_code == 200
    assert resp.json() == {"ingested": 2, "ids": ["a", "b"]}


@pytest.mark.asyncio
async def test_state_transition_via_ingest(client):
    async with client as c:
        await c.post("/api/agent_events", json={
            "id": "task:t1", "kind": "event", "title": "T",
            "source": "x", "type": "task.queued", "status": "upcoming",
        })
        await c.post("/api/agent_events", json={
            "id": "task:t1", "kind": "event", "title": "T",
            "source": "x", "type": "task.failed", "status": "failed",
        })

        state = await c.get("/api/agent_state/task:t1")
        assert state.json()["status"] == "failed"

        events = await c.get("/api/agent_events", params={"id": "task:t1"})
        body = events.json()
        assert body["count"] == 2
        assert [e["type"] for e in body["events"]] == ["task.queued", "task.failed"]


@pytest.mark.asyncio
async def test_attention_filter(client):
    async with client as c:
        for entry in [
            {"id": "a", "status": "failed"},
            {"id": "b", "status": "done"},
            {"id": "c", "status": "needs_action"},
        ]:
            await c.post("/api/agent_events", json={
                "kind": "event", "title": entry["id"], "source": "x", "type": "t", **entry,
            })

        resp = await c.get("/api/agent_state", params={"status": "failed,needs_action,blocked"})
        ids = {r["id"] for r in resp.json()["items"]}
        assert ids == {"a", "c"}


@pytest.mark.asyncio
async def test_bad_body_returns_400(client):
    async with client as c:
        resp = await c.post("/api/agent_events", content="not json", headers={"content-type": "application/json"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_invalid_envelope_returns_422(client):
    async with client as c:
        # missing required 'id' and 'title'
        resp = await c.post("/api/agent_events", json={"kind": "event"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_unknown_envelope_returns_404(client):
    async with client as c:
        resp = await c.get("/api/agent_state/never-existed")
    assert resp.status_code == 404

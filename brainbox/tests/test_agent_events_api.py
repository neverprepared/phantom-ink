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
async def test_malformed_json_body_rejected(client):
    # The body is now typed as AgentEventBatch, so FastAPI validates it and a
    # non-JSON body is a 422 (request-body validation error) rather than the
    # old hand-rolled 400.
    async with client as c:
        resp = await c.post(
            "/api/agent_events",
            content="not json",
            headers={"content-type": "application/json"},
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_invalid_envelope_returns_422(client):
    async with client as c:
        # missing required 'id' and 'title'
        resp = await c.post("/api/agent_events", json={"kind": "event"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_bad_envelope_in_batch_rejected_and_not_stored(client):
    """A batch with one non-conforming envelope is rejected wholesale (422) and
    the *valid* sibling in the same batch is not silently stored — ingest is
    all-or-nothing at the validation boundary."""
    async with client as c:
        resp = await c.post(
            "/api/agent_events",
            json={"events": [
                {"id": "good", "kind": "event", "title": "Good", "source": "x", "type": "t"},
                {"kind": "event"},  # missing required id + title
            ]},
        )
        assert resp.status_code == 422

        # The valid sibling must not have leaked into state.
        state = await c.get("/api/agent_state/good")
        assert state.status_code == 404


@pytest.mark.asyncio
async def test_unknown_status_rejected(client):
    """`status` is the EnvelopeStatus enum (T1); a genuinely-unknown status is
    rejected at ingest rather than coerced or stored — the route is the contract
    enforcement point."""
    async with client as c:
        resp = await c.post("/api/agent_events", json={
            "id": "task:x", "kind": "event", "title": "X",
            "source": "x", "type": "t", "status": "not-a-real-status",
        })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_valid_batch_upserts_by_id(client):
    """A valid batch is accepted and upserts by id — re-posting the same id in a
    later batch mutates the existing state row rather than duplicating it."""
    async with client as c:
        r1 = await c.post("/api/agent_events", json={"events": [
            {"id": "dup", "kind": "event", "title": "First",
             "source": "x", "type": "t", "status": "upcoming"},
        ]})
        assert r1.status_code == 200
        assert r1.json() == {"ingested": 1, "ids": ["dup"]}

        r2 = await c.post("/api/agent_events", json={"events": [
            {"id": "dup", "kind": "event", "title": "Second",
             "source": "x", "type": "t", "status": "active"},
        ]})
        assert r2.status_code == 200

        state = (await c.get("/api/agent_state/dup")).json()
        assert state["status"] == "active"
        assert state["title"] == "Second"

        # Two events in the audit log (append-only), one state row (upsert).
        events = (await c.get("/api/agent_events", params={"id": "dup"})).json()
        assert events["count"] == 2


@pytest.mark.asyncio
async def test_get_unknown_envelope_returns_404(client):
    async with client as c:
        resp = await c.get("/api/agent_state/never-existed")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_openapi_exposes_envelope_schema(client):
    """Typing the ingest body as AgentEventBatch publishes the envelope schema
    into /openapi.json. T4 formally verifies this; asserted here to lock the
    behavior at its source."""
    async with client as c:
        spec = (await c.get("/openapi.json")).json()
    schemas = spec["components"]["schemas"]
    assert "AgentEnvelope" in schemas
    assert "AgentEventBatch" in schemas

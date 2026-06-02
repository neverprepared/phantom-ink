"""Tests for the runner registry, work queue, and Phase-6 API endpoints."""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from brainbox.runners import RunnerRegistry, get_registry, reset_registry_for_tests


@pytest.fixture(autouse=True)
def _reset_registry():
    reset_registry_for_tests()
    yield
    reset_registry_for_tests()


async def test_register_then_enqueue_then_drain_resolves_future():
    reg = RunnerRegistry()
    await reg.register(name="r1", capabilities={"docker": True, "utm": False})

    item = await reg.enqueue(runner="r1", kind="session.create", payload={"x": 1})

    async def producer():
        return await asyncio.wait_for(item.fut, timeout=2.0)

    async def consumer():
        pulled = await reg.next_pending("r1", timeout=1.0)
        assert pulled is not None and pulled.id == item.id
        assert pulled.kind == "session.create"
        assert pulled.payload == {"x": 1}
        assert await reg.fulfill(pulled.id, {"ok": True, "data": {"hi": "bye"}}) is True

    result, _ = await asyncio.gather(producer(), consumer())
    assert result == {"ok": True, "data": {"hi": "bye"}}


async def test_enqueue_unregistered_runner_raises():
    reg = RunnerRegistry()
    with pytest.raises(RuntimeError, match="not registered"):
        await reg.enqueue(runner="ghost", kind="x", payload={})


async def test_next_pending_returns_none_on_timeout():
    reg = RunnerRegistry()
    await reg.register(name="r1", capabilities={"docker": True})
    pulled = await reg.next_pending("r1", timeout=0.05)
    assert pulled is None


async def test_fulfill_unknown_work_returns_false():
    reg = RunnerRegistry()
    await reg.register(name="r1", capabilities={"docker": True})
    assert await reg.fulfill("nope", {"ok": True}) is False


async def test_cancel_propagates_exception_to_producer():
    reg = RunnerRegistry()
    await reg.register(name="r1", capabilities={"docker": True})
    item = await reg.enqueue(runner="r1", kind="session.create", payload={})
    await reg.cancel(item.id, "test cancel")
    with pytest.raises(RuntimeError, match="test cancel"):
        await asyncio.wait_for(item.fut, timeout=1.0)


async def test_per_runner_isolation():
    reg = RunnerRegistry()
    await reg.register(name="a", capabilities={"docker": True})
    await reg.register(name="b", capabilities={"docker": True})
    item_a = await reg.enqueue(runner="a", kind="x", payload={"to": "a"})
    item_b = await reg.enqueue(runner="b", kind="x", payload={"to": "b"})

    pulled_a = await reg.next_pending("a", timeout=1.0)
    pulled_b = await reg.next_pending("b", timeout=1.0)
    assert pulled_a is not None and pulled_a.id == item_a.id
    assert pulled_b is not None and pulled_b.id == item_b.id


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------


@pytest.fixture()
def asgi_client():
    from brainbox.api import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_register_endpoint_returns_ok(asgi_client):
    async with asgi_client as client:
        r = await client.post(
            "/api/runners/register",
            json={"name": "r1", "capabilities": {"docker": True}},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["runner"]["name"] == "r1"


async def test_register_endpoint_rejects_missing_name(asgi_client):
    async with asgi_client as client:
        r = await client.post(
            "/api/runners/register",
            json={"capabilities": {"docker": True}},
        )
    assert r.status_code == 400


async def test_pending_endpoint_404_for_unknown(asgi_client):
    async with asgi_client as client:
        r = await client.get("/api/runners/ghost/pending")
    assert r.status_code == 404


async def test_full_session_dispatch_roundtrip(asgi_client):
    """Enqueue session.create work for a runner, the (mock) runner picks it
    up via GET /pending, posts the result, the producer future resolves."""
    reg = get_registry()
    await reg.register(name="r1", capabilities={"docker": True})
    item = await reg.enqueue(
        runner="r1", kind="session.create", payload={"name": "demo"}
    )

    async with asgi_client as client:
        get_resp = await client.get("/api/runners/r1/pending")
        assert get_resp.status_code == 200
        work = get_resp.json()
        assert work["id"] == item.id
        assert work["kind"] == "session.create"
        assert work["payload"] == {"name": "demo"}

        post_resp = await client.post(
            f"/api/runners/r1/result/{work['id']}",
            json={"ok": True, "data": {"session_name": "demo"}},
        )
        assert post_resp.status_code == 200

    resolved = await asyncio.wait_for(item.fut, timeout=1.0)
    assert resolved == {"ok": True, "data": {"session_name": "demo"}}


async def test_list_runners_endpoint(asgi_client):
    reg = get_registry()
    await reg.register(name="a", capabilities={"docker": True})
    await reg.register(name="b", capabilities={"docker": True, "utm": True})
    async with asgi_client as client:
        r = await client.get("/api/runners")
    assert r.status_code == 200
    names = {e["name"] for e in r.json()}
    assert names == {"a", "b"}



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


# ---------------------------------------------------------------------------
# secret_authority capability + identified polling + seal-request fail-fast
# ---------------------------------------------------------------------------


async def test_credentials_pending_with_as_touches_last_seen(asgi_client):
    """?as=<name> on /api/credentials/pending should touch the runner's
    last_seen so a long-running secret authority appears live."""
    import time

    from brainbox.credentials.queue import reset_queue_for_tests

    reset_queue_for_tests()
    reg = get_registry()
    info = await reg.register(
        name="laptop",
        capabilities={"secret_authority": True},
    )
    stale_at = info.last_seen
    # Make sure subsequent epoch_ms differs.
    time.sleep(0.01)

    async with asgi_client as client:
        # Short-circuit the 30s long-poll by triggering a 204 via timeout=0;
        # the endpoint touches last_seen *before* it waits, so 204 is fine.
        await client.get("/api/credentials/pending?as=laptop", timeout=2.0)

    refreshed = await reg.get("laptop")
    assert refreshed is not None
    assert refreshed.last_seen > stale_at


async def test_seal_request_fast_fails_when_authorities_all_stale(asgi_client):
    """If at least one agent declared secret_authority but none are live,
    503 immediately instead of timing out."""
    import time

    from brainbox.credentials.queue import reset_queue_for_tests

    reset_queue_for_tests()
    reg = get_registry()
    info = await reg.register(
        name="laptop",
        capabilities={"secret_authority": True},
    )
    # Backdate so the authority looks stale (>90s old).
    info.last_seen = int(time.time() * 1000) - 200_000

    async with asgi_client as client:
        r = await client.post(
            "/api/credentials/seal-request",
            json={
                "workspace_profile": "p",
                "workspace_home": "/x",
                "recipient": "age1xxx",
                "timeout": 1,
            },
            timeout=3.0,
        )
    assert r.status_code == 503
    assert "stale" in r.json().get("detail", "").lower()


async def test_seal_request_passes_through_when_no_authority_registered(asgi_client):
    """When no agent has declared secret_authority, the endpoint falls back
    to the legacy queue-and-wait behaviour (504 on timeout, not 503)."""
    from brainbox.credentials.queue import reset_queue_for_tests

    reset_queue_for_tests()
    # Registry empty — no authorities, anonymous cc poll could still drain.
    async with asgi_client as client:
        r = await client.post(
            "/api/credentials/seal-request",
            json={
                "workspace_profile": "p",
                "workspace_home": "/x",
                "recipient": "age1xxx",
                "timeout": 1,
            },
            timeout=4.0,
        )
    # 504 = timed out waiting for an anonymous poller (legacy path),
    # NOT 503 (which means we know no authority is alive).
    assert r.status_code == 504


async def test_seal_request_proceeds_when_a_live_authority_exists(asgi_client):
    """Live registered authority + working consumer → seal-request returns
    sealed bytes, doesn't 503."""
    import asyncio

    from brainbox.credentials.queue import get_queue, reset_queue_for_tests

    reset_queue_for_tests()
    reg = get_registry()
    await reg.register(name="laptop", capabilities={"secret_authority": True})

    async def fake_poller():
        await asyncio.sleep(0.1)
        queue = get_queue()
        # Drain whatever was enqueued and fulfill with fake ciphertext.
        for _ in range(5):
            req = await queue.next_pending(timeout=0.5)
            if req is not None:
                await queue.fulfill(req.id, b"sealed-bytes")
                return

    async with asgi_client as client:
        poll_task = asyncio.create_task(fake_poller())
        r = await client.post(
            "/api/credentials/seal-request",
            json={
                "workspace_profile": "p",
                "workspace_home": "/x",
                "recipient": "age1xxx",
                "timeout": 5,
            },
            timeout=10.0,
        )
        await poll_task
    assert r.status_code == 200
    assert r.content == b"sealed-bytes"

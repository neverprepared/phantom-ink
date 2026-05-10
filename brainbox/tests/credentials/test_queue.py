"""Tests for the bundle-request queue + Phase 5 API endpoints."""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from brainbox.credentials.queue import BundleRequestQueue, reset_queue_for_tests


@pytest.fixture(autouse=True)
def _reset_queue():
    reset_queue_for_tests()
    yield
    reset_queue_for_tests()


async def test_enqueue_then_fulfill_resolves_future():
    q = BundleRequestQueue()
    req = await q.enqueue(workspace_profile="p", workspace_home="/h", recipient="age1xxx")
    assert q.pending_count == 1

    # Concurrently: producer awaits future, consumer drains and fulfills.
    async def producer():
        return await asyncio.wait_for(req.fut, timeout=2.0)

    async def consumer():
        pulled = await q.next_pending(timeout=1.0)
        assert pulled is not None and pulled.id == req.id
        ok = await q.fulfill(req.id, b"sealed-bytes")
        assert ok

    result, _ = await asyncio.gather(producer(), consumer())
    assert result == b"sealed-bytes"
    assert q.pending_count == 0


async def test_next_pending_returns_none_on_timeout():
    q = BundleRequestQueue()
    pulled = await q.next_pending(timeout=0.05)
    assert pulled is None


async def test_fulfill_unknown_id_returns_false():
    q = BundleRequestQueue()
    assert await q.fulfill("nope", b"x") is False


async def test_cancel_propagates_exception_to_producer():
    q = BundleRequestQueue()
    req = await q.enqueue(workspace_profile=None, workspace_home=None, recipient="age1x")
    await q.cancel(req.id, "test cancel")
    with pytest.raises(RuntimeError, match="test cancel"):
        await asyncio.wait_for(req.fut, timeout=1.0)


async def test_double_fulfill_idempotent():
    q = BundleRequestQueue()
    req = await q.enqueue(workspace_profile=None, workspace_home=None, recipient="age1x")
    assert await q.fulfill(req.id, b"a") is True
    # request_id is removed after first fulfill, so second returns False.
    assert await q.fulfill(req.id, b"b") is False
    assert (await asyncio.wait_for(req.fut, timeout=1.0)) == b"a"


@pytest.fixture()
def asgi_client():
    from brainbox.api import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_full_seal_request_roundtrip(asgi_client, monkeypatch: pytest.MonkeyPatch):
    """Producer enqueues, async client (mock daemon) drains + posts sealed."""
    from brainbox.credentials.queue import get_queue

    queue = get_queue()
    req = await queue.enqueue(
        workspace_profile="work", workspace_home="/h", recipient="age1xyz"
    )

    async with asgi_client as client:
        # Consumer-side: pull pending and post sealed.
        get_resp = await client.get("/api/credentials/pending")
        assert get_resp.status_code == 200
        body = get_resp.json()
        assert body["id"] == req.id
        assert body["recipient"] == "age1xyz"
        assert body["workspace_profile"] == "work"

        post_resp = await client.post(
            f"/api/credentials/{body['id']}/sealed", content=b"FAKE-CIPHERTEXT"
        )
        assert post_resp.status_code == 200
        assert post_resp.json() == {"ok": True, "bytes": 15}

    # Producer's future should be resolved now.
    assert (await asyncio.wait_for(req.fut, timeout=1.0)) == b"FAKE-CIPHERTEXT"


async def test_endpoint_sealed_unknown_id_404(asgi_client):
    async with asgi_client as client:
        r = await client.post("/api/credentials/nope/sealed", content=b"x")
    assert r.status_code == 404


async def test_endpoint_sealed_empty_body_400(asgi_client):
    async with asgi_client as client:
        r = await client.post("/api/credentials/anything/sealed", content=b"")
    assert r.status_code == 400

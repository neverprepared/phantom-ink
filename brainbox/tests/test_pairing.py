"""Tests for runner pairing — one-time tickets that hand an api_url + api_key
from an authenticated caller (the Wails app, typically) to a new runner."""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from brainbox.runners import PairingStore, reset_pairing_store_for_tests


@pytest.fixture(autouse=True)
def _reset_store():
    reset_pairing_store_for_tests()
    yield
    reset_pairing_store_for_tests()


# ---------------------------------------------------------------------------
# PairingStore unit tests
# ---------------------------------------------------------------------------


async def test_issue_then_claim_returns_payload():
    store = PairingStore()
    ticket = await store.issue(api_url="https://api.example.com", api_key="abc123")
    assert ticket.token
    assert ticket.expires_at > 0
    claimed = await store.claim(ticket.token)
    assert claimed is not None
    assert claimed.api_url == "https://api.example.com"
    assert claimed.api_key == "abc123"


async def test_claim_is_single_use():
    store = PairingStore()
    ticket = await store.issue(api_url="https://api.example.com", api_key="abc123")
    first = await store.claim(ticket.token)
    second = await store.claim(ticket.token)
    assert first is not None
    assert second is None


async def test_claim_unknown_token_returns_none():
    store = PairingStore()
    assert await store.claim("does-not-exist") is None


async def test_expired_ticket_cannot_be_claimed():
    store = PairingStore()
    ticket = await store.issue(
        api_url="https://api.example.com", api_key="abc", ttl_seconds=0.05
    )
    await asyncio.sleep(0.1)
    assert await store.claim(ticket.token) is None


async def test_issue_includes_runner_name_suggestion():
    store = PairingStore()
    ticket = await store.issue(
        api_url="https://api.example.com",
        api_key="abc",
        runner_name_suggestion="mac-mini-1",
    )
    claimed = await store.claim(ticket.token)
    assert claimed is not None
    assert claimed.runner_name_suggestion == "mac-mini-1"


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------


@pytest.fixture()
def asgi_client():
    from brainbox.api import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_pair_start_returns_token(asgi_client):
    async with asgi_client as client:
        r = await client.post(
            "/api/runners/pair/start",
            json={"api_url": "https://api.example.com", "api_key": "k"},
        )
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["token"], str) and len(body["token"]) >= 6
    assert body["api_url"] == "https://api.example.com"
    assert body["expires_at"] > 0


async def test_pair_start_returns_500_when_no_key_anywhere(asgi_client):
    """If caller omits api_key and the server has no key configured, refuse —
    we don't issue empty-key tickets."""
    async with asgi_client as client:
        r = await client.post(
            "/api/runners/pair/start",
            json={"api_url": "https://api.example.com"},
        )
    assert r.status_code == 500


async def test_pair_start_rejects_missing_url(asgi_client):
    async with asgi_client as client:
        r = await client.post("/api/runners/pair/start", json={})
    assert r.status_code == 400


async def test_pair_start_rejects_bad_ttl(asgi_client):
    async with asgi_client as client:
        r = await client.post(
            "/api/runners/pair/start",
            json={"api_url": "https://api.example.com", "api_key": "k", "ttl": 5000},
        )
    assert r.status_code == 400


async def test_pair_claim_roundtrip(asgi_client):
    async with asgi_client as client:
        start = await client.post(
            "/api/runners/pair/start",
            json={
                "api_url": "https://api.example.com",
                "api_key": "test-key-xyz",
                "runner_name_suggestion": "mac-mini-1",
            },
        )
        assert start.status_code == 200
        token = start.json()["token"]

        # No auth header on claim — that's the point.
        claim = await client.post("/api/runners/pair/claim", json={"token": token})
    assert claim.status_code == 200
    body = claim.json()
    assert body["api_url"] == "https://api.example.com"
    assert body["api_key"] == "test-key-xyz"
    assert body["runner_name_suggestion"] == "mac-mini-1"


async def test_pair_claim_is_single_use(asgi_client):
    async with asgi_client as client:
        start = await client.post(
            "/api/runners/pair/start",
            json={"api_url": "https://api.example.com", "api_key": "k"},
        )
        token = start.json()["token"]
        first = await client.post("/api/runners/pair/claim", json={"token": token})
        second = await client.post("/api/runners/pair/claim", json={"token": token})
    assert first.status_code == 200
    assert second.status_code == 404


async def test_pair_claim_unknown_token(asgi_client):
    async with asgi_client as client:
        r = await client.post("/api/runners/pair/claim", json={"token": "nope"})
    assert r.status_code == 404


async def test_pair_claim_missing_token(asgi_client):
    async with asgi_client as client:
        r = await client.post("/api/runners/pair/claim", json={})
    assert r.status_code == 400

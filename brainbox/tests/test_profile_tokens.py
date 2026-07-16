"""Tests for T11 profile token minting: persistent, revocable, per-profile
service tokens + the backward-compatible agent_events ingest auth switch."""

from __future__ import annotations

import hashlib

import pytest

import brainbox.auth as auth_module
import brainbox.registry as reg_module
import brainbox.store as store


# ---------------------------------------------------------------------------
# Minting + persistence (hash, never raw)
# ---------------------------------------------------------------------------


class TestMintPersistsHash:
    def test_mint_persists_hash_not_raw(self):
        raw, token = reg_module.issue_profile_token(
            "personal", ["agent_events:write"], label="ci"
        )
        # Raw is a 64-char hex secret, NOT a uuid.
        assert len(raw) == 64
        int(raw, 16)  # hex
        assert raw != token.token_id

        rows = store.list_profile_tokens()
        assert len(rows) == 1
        row = rows[0]
        # The masked listing never leaks the raw or the hash.
        assert "token" not in row
        assert "token_hash" not in row
        assert row["workspace_profile"] == "personal"
        assert row["capabilities"] == ["agent_events:write"]
        assert row["label"] == "ci"
        assert row["revoked"] is False

        # What is stored is sha256(raw), discoverable only via hash lookup.
        expected_hash = hashlib.sha256(raw.encode()).hexdigest()
        found = store.find_profile_token_by_hash(expected_hash)
        assert found is not None
        assert found["token_id"] == token.token_id

    def test_token_is_profile_bound_tier0_no_task(self):
        _raw, token = reg_module.issue_profile_token("sandbox", ["agent_events:read"])
        assert token.workspace_profile == "sandbox"
        assert token.task_id == ""
        assert token.agent_name == "profile"
        assert token.capabilities == ["agent_events:read"]

    def test_rejects_unknown_capability(self):
        with pytest.raises(ValueError, match="Unknown capabilities"):
            reg_module.issue_profile_token("personal", ["not:a:real:cap"])

    def test_rejects_blank_profile(self):
        with pytest.raises(ValueError, match="workspace_profile is required"):
            reg_module.issue_profile_token("   ", ["agent_events:write"])


# ---------------------------------------------------------------------------
# Validation: minted token validates; revoked → None; legacy path intact
# ---------------------------------------------------------------------------


class TestValidateProfileToken:
    def test_validate_accepts_minted_token(self):
        raw, token = reg_module.issue_profile_token("personal", ["agent_events:write"])
        resolved = reg_module.validate_token(raw)
        assert resolved is not None
        assert resolved.token_id == token.token_id
        assert resolved.workspace_profile == "personal"
        assert "agent_events:write" in resolved.capabilities

    def test_validate_stamps_last_used(self):
        raw, token = reg_module.issue_profile_token("personal", ["agent_events:write"])
        assert store.list_profile_tokens()[0]["last_used"] is None
        reg_module.validate_token(raw)
        assert store.list_profile_tokens()[0]["last_used"] is not None

    def test_revoked_token_is_deleted_and_fails_validation(self):
        raw, token = reg_module.issue_profile_token("personal", ["agent_events:write"])
        assert reg_module.validate_token(raw) is not None
        # Revoke hard-deletes the row: it fails validation (unknown → None) and
        # drops out of the listing entirely — no soft-flagged row lingers.
        assert store.revoke_profile_token(token.token_id) is True
        assert reg_module.validate_token(raw) is None
        assert store.list_profile_tokens() == []
        # A second revoke of the now-gone id removes nothing.
        assert store.revoke_profile_token(token.token_id) is False

    def test_unknown_bearer_returns_none(self):
        assert reg_module.validate_token("00" * 32) is None

    def test_legacy_in_memory_token_still_validates(self):
        """The gateway/session in-memory path must be untouched by the hashed
        fallback — a Tier-0 gateway token still round-trips through the registry."""
        tok = reg_module.issue_gateway_token("personal", ["phantom-brain__*"])
        resolved = reg_module.validate_token(tok.token_id)
        assert resolved is not None
        assert resolved.token_id == tok.token_id
        assert resolved.workspace_profile == "personal"

    def test_survives_registry_restart(self):
        """Profile tokens are persistent: clearing the in-memory registry
        (daemon restart) does not invalidate them — they resolve from Postgres."""
        raw, _token = reg_module.issue_profile_token("personal", ["agent_events:write"])
        reg_module._tokens.clear()  # simulate restart: memory gone, DB intact
        assert reg_module.validate_token(raw) is not None


# ---------------------------------------------------------------------------
# Admin endpoints (mint / list masked / revoke)
# ---------------------------------------------------------------------------


class TestAdminEndpoints:
    @pytest.mark.asyncio
    async def test_mint_returns_raw_once(self, client):
        async with client as c:
            resp = await c.post(
                "/api/tokens",
                json={
                    "workspace_profile": "personal",
                    "capabilities": ["agent_events:write"],
                    "label": "laptop",
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["token"]) == 64
        assert body["workspace_profile"] == "personal"
        assert body["capabilities"] == ["agent_events:write"]
        assert body["token_id"]

    @pytest.mark.asyncio
    async def test_list_is_masked(self, client):
        async with client as c:
            mint = await c.post(
                "/api/tokens",
                json={"workspace_profile": "personal", "capabilities": []},
            )
            raw = mint.json()["token"]
            listing = await c.get("/api/tokens")
        rows = listing.json()["tokens"]
        assert len(rows) == 1
        # Neither the raw token nor its hash is ever returned by the listing.
        assert raw not in listing.text
        assert "token_hash" not in rows[0]
        assert "token" not in rows[0]

    @pytest.mark.asyncio
    async def test_revoke_endpoint_deletes_row(self, client):
        async with client as c:
            mint = await c.post(
                "/api/tokens",
                json={"workspace_profile": "personal", "capabilities": ["agent_events:write"]},
            )
            token_id = mint.json()["token_id"]
            raw = mint.json()["token"]
            revoke = await c.delete(f"/api/tokens/{token_id}")
            listing = await c.get("/api/tokens")
            # A second DELETE of the now-deleted id 404s: the row is gone.
            revoke_again = await c.delete(f"/api/tokens/{token_id}")
        assert revoke.status_code == 200
        assert reg_module.validate_token(raw) is None
        # The revoked token has disappeared from the listing (hard delete).
        assert listing.json()["tokens"] == []
        assert revoke_again.status_code == 404

    @pytest.mark.asyncio
    async def test_revoke_unknown_is_404(self, client):
        async with client as c:
            resp = await c.delete("/api/tokens/does-not-exist")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_capabilities_catalog_exposed(self, client):
        async with client as c:
            resp = await c.get("/api/tokens/capabilities")
        assert resp.status_code == 200
        assert "agent_events:write" in resp.json()["capabilities"]

    @pytest.mark.asyncio
    async def test_bad_profile_rejected(self, client):
        async with client as c:
            resp = await c.post(
                "/api/tokens",
                json={"workspace_profile": "../etc", "capabilities": []},
            )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Back-compat auth switch on POST /api/agent_events
# ---------------------------------------------------------------------------


def _real_capability_auth(monkeypatch, key: str) -> None:
    """Exercise the REAL agent_events capability guard.

    The autouse conftest fixture overrides the ingest capability dep to
    full-trust; drop that override so the genuine ``_dep`` runs, and pin the
    server's shared API key so the full-trust X-API-Key branch is testable."""
    from brainbox.api import app, _require_agent_events_write

    monkeypatch.setattr(auth_module, "_api_key", key)
    app.dependency_overrides.pop(_require_agent_events_write, None)


_INGEST_ENVELOPE = {
    "id": "task:t1",
    "kind": "event",
    "source": "x",
    "type": "task.queued",
    "status": "upcoming",
    "title": "T",
}


class TestIngestBackCompat:
    @pytest.mark.asyncio
    async def test_shared_api_key_still_works(self, client, monkeypatch):
        """The full-trust shared API key path is unchanged — nothing that
        worked before the switch breaks."""
        _real_capability_auth(monkeypatch, "server-key")
        async with client as c:
            resp = await c.post(
                "/api/agent_events",
                json=_INGEST_ENVELOPE,
                headers={"X-API-Key": "server-key"},
            )
        assert resp.status_code == 200
        assert resp.json()["ingested"] == 1

    @pytest.mark.asyncio
    async def test_scoped_token_with_capability_works(self, client, monkeypatch):
        _real_capability_auth(monkeypatch, "server-key")
        raw, _tok = reg_module.issue_profile_token("personal", ["agent_events:write"])
        async with client as c:
            resp = await c.post(
                "/api/agent_events",
                json=_INGEST_ENVELOPE,
                headers={"Authorization": f"Bearer {raw}"},
            )
        assert resp.status_code == 200
        assert resp.json()["ingested"] == 1

    @pytest.mark.asyncio
    async def test_token_without_capability_is_403(self, client, monkeypatch):
        _real_capability_auth(monkeypatch, "server-key")
        raw, _tok = reg_module.issue_profile_token("personal", ["agent_events:read"])
        async with client as c:
            resp = await c.post(
                "/api/agent_events",
                json=_INGEST_ENVELOPE,
                headers={"Authorization": f"Bearer {raw}"},
            )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_revoked_token_is_401(self, client, monkeypatch):
        """A revoked (hard-deleted) token is unknown on the next validate → 401."""
        _real_capability_auth(monkeypatch, "server-key")
        raw, tok = reg_module.issue_profile_token("personal", ["agent_events:write"])
        store.revoke_profile_token(tok.token_id)
        async with client as c:
            resp = await c.post(
                "/api/agent_events",
                json=_INGEST_ENVELOPE,
                headers={"Authorization": f"Bearer {raw}"},
            )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_no_auth_is_401(self, client, monkeypatch):
        _real_capability_auth(monkeypatch, "server-key")
        async with client as c:
            resp = await c.post("/api/agent_events", json=_INGEST_ENVELOPE)
        assert resp.status_code == 401

"""Tests for the MCP gateway per-profile encrypted env store (ADR-002 phase 1)."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

import brainbox.gateway_secrets as gw
from brainbox.config import settings


@pytest.fixture
def unlocked(tmp_path, monkeypatch):
    """Fresh secrets dir + an operator key (age passphrase) for the test."""
    monkeypatch.setattr(settings.gateway, "secrets_dir", str(tmp_path))
    monkeypatch.setattr(settings.gateway, "secret_key", SecretStr("operator-passphrase-A"))
    return tmp_path


class TestStore:
    def test_round_trip(self, unlocked):
        gw.set_profile_env("personal", {"SLACK_TOKEN": "xoxb-1", "GH_TOKEN": "ghp_2"})
        assert gw.get_profile_env("personal") == {"SLACK_TOKEN": "xoxb-1", "GH_TOKEN": "ghp_2"}

    def test_set_replaces(self, unlocked):
        gw.set_profile_env("personal", {"A": "1"})
        gw.set_profile_env("personal", {"B": "2"})
        assert gw.get_profile_env("personal") == {"B": "2"}

    def test_list_and_delete(self, unlocked):
        gw.set_profile_env("personal", {"A": "1"})
        gw.set_profile_env("work", {"B": "2"})
        assert gw.list_profiles() == ["personal", "work"]
        assert gw.delete_profile_env("work") is True
        assert gw.list_profiles() == ["personal"]
        assert gw.delete_profile_env("work") is False

    def test_ciphertext_at_rest(self, unlocked):
        gw.set_profile_env("personal", {"SECRET": "supersecret-value"})
        blob = (unlocked / "personal.env.enc").read_bytes()
        assert b"supersecret-value" not in blob  # stored encrypted, not plaintext

    def test_unknown_profile(self, unlocked):
        with pytest.raises(gw.GatewaySecretsError):
            gw.get_profile_env("ghost")

    def test_invalid_profile_name(self, unlocked):
        with pytest.raises(gw.GatewaySecretsError):
            gw.set_profile_env("../etc", {"A": "1"})

    def test_env_must_be_str(self, unlocked):
        with pytest.raises(gw.GatewaySecretsError):
            gw.set_profile_env("personal", {"A": 1})  # type: ignore[dict-item]


class TestLocking:
    def test_locked_without_key(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings.gateway, "secrets_dir", str(tmp_path))
        monkeypatch.setattr(settings.gateway, "secret_key", SecretStr(""))
        assert gw.is_unlocked() is False
        with pytest.raises(gw.LockedError):
            gw.set_profile_env("personal", {"A": "1"})

    def test_wrong_key_cannot_decrypt(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings.gateway, "secrets_dir", str(tmp_path))
        monkeypatch.setattr(settings.gateway, "secret_key", SecretStr("passphrase-A"))
        gw.set_profile_env("personal", {"A": "1"})
        # rotate to a different passphrase — old ciphertext must not decrypt
        monkeypatch.setattr(settings.gateway, "secret_key", SecretStr("passphrase-B"))
        with pytest.raises(gw.LockedError):
            gw.get_profile_env("personal")


class TestApi:
    @pytest.mark.asyncio
    async def test_crud_via_api(self, client, unlocked):
        async with client as c:
            r = await c.put("/api/gateway/profiles/personal/env", json={"env": {"SLACK_TOKEN": "x"}})
            assert r.status_code == 200 and r.json()["saved"] is True
            r = await c.get("/api/gateway/profiles/personal/env")
            assert r.json()["env"] == {"SLACK_TOKEN": "x"}
            r = await c.get("/api/gateway/profiles")
            body = r.json()
            assert "personal" in body["profiles"] and body["unlocked"] is True
            r = await c.delete("/api/gateway/profiles/personal/env")
            assert r.json()["deleted"] is True

    @pytest.mark.asyncio
    async def test_put_locked_returns_409(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr(settings.gateway, "secrets_dir", str(tmp_path))
        monkeypatch.setattr(settings.gateway, "secret_key", SecretStr(""))
        async with client as c:
            r = await c.put("/api/gateway/profiles/personal/env", json={"env": {"A": "1"}})
            assert r.status_code == 409

    @pytest.mark.asyncio
    async def test_put_bad_body_returns_400(self, client, unlocked):
        async with client as c:
            r = await c.put("/api/gateway/profiles/personal/env", json={"not_env": {}})
            assert r.status_code == 400


class TestEnvUpdateLiveReload:
    """Credential writes must take effect without a daemon restart."""

    async def test_put_env_evicts_profile_pool(self, unlocked, client):
        from unittest.mock import AsyncMock, patch

        evict = AsyncMock()
        with patch("brainbox.api._gateway_pool") as pool:
            pool.close = evict
            async with client as c:
                r = await c.put(
                    "/api/gateway/profiles/personal/env",
                    json={"env": {"JIRA_API_TOKEN": "new-secret"}},
                )
        assert r.status_code == 200
        evict.assert_awaited_once_with("personal")

    async def test_delete_env_evicts_profile_pool(self, unlocked, client):
        from unittest.mock import AsyncMock, patch

        import brainbox.gateway_secrets as gws
        gws.set_profile_env("personal", {"K": "v"})
        evict = AsyncMock()
        with patch("brainbox.api._gateway_pool") as pool:
            pool.close = evict
            async with client as c:
                r = await c.delete("/api/gateway/profiles/personal/env")
        assert r.status_code == 200
        evict.assert_awaited_once_with("personal")

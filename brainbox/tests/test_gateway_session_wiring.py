"""Tests for injecting the MCP gateway into a session's container .mcp.json (ADR-002 phase 3)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from docker.errors import NotFound

from brainbox.config import settings
from brainbox.lifecycle import (
    _gateway_server_entry,
    _gateway_session_env,
    _gateway_url_for,
    _generate_container_mcp_json,
)


@pytest.fixture
def gateway_on(monkeypatch):
    # Injection gates on the DB-backed registry, not the CL_GATEWAY__SERVERS seed.
    from brainbox import store
    store.set_gateway_server_enabled("brainbox", True)
    monkeypatch.setattr(settings.gateway, "inject_sessions", True)
    monkeypatch.setattr(settings.gateway, "container_url", "http://host.docker.internal:9999/gateway/mcp")
    monkeypatch.setattr(settings.gateway, "session_token_ttl", 3600)


class TestGatewayServerEntry:
    def test_entry_when_configured(self, gateway_on):
        entry = _gateway_server_entry("personal")
        assert entry is not None
        assert entry["type"] == "http"
        assert entry["url"] == "http://host.docker.internal:9999/gateway/mcp"
        auth = entry["headers"]["Authorization"]
        assert auth.startswith("Bearer ")
        # the bearer is a real, resolvable gateway token bound to the profile
        from brainbox import registry
        tok = registry.validate_token(auth.removeprefix("Bearer "))
        assert tok is not None and tok.workspace_profile == "personal"

    def test_token_carries_profile_default_ceiling(self, gateway_on):
        from brainbox import registry, store
        store.set_profile_default_ceiling("personal", "infra")
        entry = _gateway_server_entry("personal")
        tok = registry.validate_token(entry["headers"]["Authorization"].removeprefix("Bearer "))
        assert tok.residency_ceiling == "infra"

    def test_token_unrestricted_when_default_public(self, gateway_on, monkeypatch):
        from brainbox import registry
        monkeypatch.setattr(settings.orchestration, "default_ceiling", "public")
        entry = _gateway_server_entry("personal")  # no per-profile default set
        tok = registry.validate_token(entry["headers"]["Authorization"].removeprefix("Bearer "))
        assert tok.residency_ceiling == ""  # public → no restriction

    def test_no_entry_without_profile(self, gateway_on):
        assert _gateway_server_entry("") is None

    def test_no_entry_when_registry_empty(self, monkeypatch):
        # The legacy env allowlist is only a first-boot seed — an empty DB
        # registry means no injection even when the env var is populated.
        monkeypatch.setattr(settings.gateway, "servers", ["brainbox"])
        monkeypatch.setattr(settings.gateway, "inject_sessions", True)
        assert _gateway_server_entry("personal") is None

    def test_entry_when_env_allowlist_empty(self, gateway_on, monkeypatch):
        # Regression: post-#161 deployments manage servers via the DB registry
        # and may drop CL_GATEWAY__SERVERS entirely; injection must still fire.
        monkeypatch.setattr(settings.gateway, "servers", [])
        assert _gateway_server_entry("personal") is not None

    def test_no_entry_when_injection_disabled(self, monkeypatch):
        from brainbox import store
        store.set_gateway_server_enabled("brainbox", True)
        monkeypatch.setattr(settings.gateway, "inject_sessions", False)
        assert _gateway_server_entry("personal") is None


class TestGatewayUrlForTransport:
    """Each transport needs a different route to the same gateway."""

    def test_local_docker_uses_container_url(self, gateway_on, monkeypatch):
        monkeypatch.setattr(settings, "public_url", "https://phantom-api.example.com")
        # A local container shares the daemon's host: the public URL would work
        # but the docker alias is the direct route, and it needs no DNS/TLS.
        assert _gateway_url_for(backend="docker", runner=None) == settings.gateway.container_url
        assert _gateway_url_for(backend="docker", runner="local") == settings.gateway.container_url

    def test_runner_uses_public_url(self, gateway_on, monkeypatch):
        monkeypatch.setattr(settings, "public_url", "https://phantom-api.example.com")
        url = _gateway_url_for(backend="docker", runner="m3-64")
        assert url == "https://phantom-api.example.com/gateway/mcp"

    def test_runner_falls_back_to_container_url_without_public_url(self, gateway_on, monkeypatch):
        monkeypatch.setattr(settings, "public_url", "")
        assert _gateway_url_for(backend="docker", runner="m3-64") == settings.gateway.container_url

    def test_utm_uses_vm_url(self, gateway_on, monkeypatch):
        monkeypatch.setattr(settings.gateway, "vm_url", "http://192.168.64.1:9999/gateway/mcp")
        monkeypatch.setattr(settings, "public_url", "https://phantom-api.example.com")
        # A VM guest can resolve neither host.docker.internal nor, necessarily,
        # the public name — vm_url wins over both.
        assert _gateway_url_for(backend="utm", runner=None) == settings.gateway.vm_url
        assert _gateway_url_for(backend="utm", runner="m3-64") == settings.gateway.vm_url


class TestGatewaySessionEnv:
    """Env-pair delivery for a session guest (baked-declaration design)."""

    def test_env_pair_uses_public_url_for_a_runner(self, gateway_on, monkeypatch):
        from brainbox import registry
        monkeypatch.setattr(settings, "public_url", "https://phantom-api.example.com")
        env = _gateway_session_env("personal", runner="m3-64")
        assert env["PHANTOM_GATEWAY_URL"] == "https://phantom-api.example.com/gateway/mcp"
        tok = registry.validate_token(env["PHANTOM_GATEWAY_TOKEN"])
        assert tok is not None and tok.workspace_profile == "personal"

    def test_env_pair_delivered_for_a_local_session(self, gateway_on, monkeypatch):
        """Regression: the pair used to be runner-only, so a local container
        read the literal ``${PHANTOM_GATEWAY_URL}`` out of its baked
        declaration and Claude Code failed the server with INVALID_CONFIG."""
        from brainbox import registry
        monkeypatch.setattr(settings, "public_url", "https://phantom-api.example.com")
        env = _gateway_session_env("personal")  # no runner → local docker
        assert env["PHANTOM_GATEWAY_URL"] == settings.gateway.container_url
        assert "$" not in env["PHANTOM_GATEWAY_URL"]
        tok = registry.validate_token(env["PHANTOM_GATEWAY_TOKEN"])
        assert tok is not None and tok.workspace_profile == "personal"

    def test_falls_back_to_container_url_without_public_url(self, gateway_on, monkeypatch):
        monkeypatch.setattr(settings, "public_url", "")
        env = _gateway_session_env("personal", runner="m3-64")
        assert env["PHANTOM_GATEWAY_URL"] == settings.gateway.container_url

    def test_reuses_a_supplied_entry_without_minting(self, gateway_on):
        """provision() mints one token per session and delivers it twice (env
        pair + generated .mcp.json); passing the entry in must not mint a
        second one, or the two copies name different live tokens."""
        entry = _gateway_server_entry("personal")
        env = _gateway_session_env("personal", entry=entry)
        assert env["PHANTOM_GATEWAY_URL"] == entry["url"]
        assert env["PHANTOM_GATEWAY_TOKEN"] == entry["headers"]["Authorization"].removeprefix(
            "Bearer "
        )

    def test_empty_when_gateway_inactive(self, monkeypatch):
        # DB registry empty → no token minted, no env delivered
        monkeypatch.setattr(settings, "public_url", "https://phantom-api.example.com")
        assert _gateway_session_env("personal") == {}

    def test_empty_without_profile(self, gateway_on):
        assert _gateway_session_env("") == {}


class TestContainerMcpJson:
    def test_injects_gateway_even_with_no_profile_servers(self, gateway_on, tmp_path):
        # no .claude.json at all → previously nothing written; now the gateway
        # alone is enough to produce a file.
        dest = tmp_path / "out" / "workspace-mcp.json"
        wrote = _generate_container_mcp_json(tmp_path / "cfg", dest, workspace_profile="personal")
        assert wrote is True
        servers = json.loads(dest.read_text())["mcpServers"]
        assert set(servers) == {"phantom-gateway"}

    def test_merges_gateway_with_profile_servers(self, gateway_on, tmp_path):
        cfg = tmp_path / "cfg"
        cfg.mkdir()
        (cfg / ".claude.json").write_text(json.dumps({
            "mcpServers": {"slack": {"command": "npx", "args": ["-y", "slack-mcp"]}}
        }))
        dest = tmp_path / "workspace-mcp.json"
        assert _generate_container_mcp_json(cfg, dest, workspace_profile="personal") is True
        servers = json.loads(dest.read_text())["mcpServers"]
        assert set(servers) == {"slack", "phantom-gateway"}

    def test_nothing_written_when_no_servers_and_gateway_off(self, tmp_path):
        # DB registry is empty (truncated per test) → gateway inactive
        dest = tmp_path / "workspace-mcp.json"
        assert _generate_container_mcp_json(tmp_path / "cfg", dest, workspace_profile="personal") is False
        assert not dest.exists()

    def test_exclusive_drops_profile_servers(self, gateway_on, monkeypatch, tmp_path):
        monkeypatch.setattr(settings.gateway, "exclusive", True)
        cfg = tmp_path / "cfg"
        cfg.mkdir()
        (cfg / ".claude.json").write_text(json.dumps({
            "mcpServers": {"slack": {"command": "npx", "args": ["-y", "slack-mcp"]}}
        }))
        dest = tmp_path / "workspace-mcp.json"
        assert _generate_container_mcp_json(cfg, dest, workspace_profile="personal") is True
        servers = json.loads(dest.read_text())["mcpServers"]
        assert set(servers) == {"phantom-gateway"}  # profile's slack dropped

    def test_supplied_entry_is_written_verbatim(self, gateway_on, tmp_path):
        """The caller's minted entry must land in the file unchanged, so the
        mounted .mcp.json and the env pair agree on one live token."""
        entry = _gateway_server_entry("personal")
        dest = tmp_path / "workspace-mcp.json"
        assert _generate_container_mcp_json(
            tmp_path / "cfg", dest, workspace_profile="personal", gateway_entry=entry
        ) is True
        assert json.loads(dest.read_text())["mcpServers"]["phantom-gateway"] == entry

    def test_exclusive_falls_back_to_profile_when_gateway_inactive(self, monkeypatch, tmp_path):
        # exclusive on, but gateway not active (empty DB registry) → keep profile servers
        monkeypatch.setattr(settings.gateway, "exclusive", True)
        cfg = tmp_path / "cfg"
        cfg.mkdir()
        (cfg / ".claude.json").write_text(json.dumps({
            "mcpServers": {"slack": {"command": "npx", "args": ["-y", "slack-mcp"]}}
        }))
        dest = tmp_path / "workspace-mcp.json"
        assert _generate_container_mcp_json(cfg, dest, workspace_profile="personal") is True
        servers = json.loads(dest.read_text())["mcpServers"]
        assert set(servers) == {"slack"}  # gateway inactive → not stranded


class TestProvisionDeliversGatewayEnv:
    """provision() is the one choke point that must deliver the env pair.

    The original bug was not in any of the helpers above — those all worked.
    It was that NOTHING called them on the local-docker path: the merge lived
    inside _provision_via_runner, and the generated .mcp.json that would
    otherwise have carried a literal entry is gated on workspace_home. A hub
    task submitted with a profile but no workspace_home therefore booted with
    the baked ``${PHANTOM_GATEWAY_URL}`` declaration and no variable to expand
    it, so Claude Code dropped the server (INVALID_CONFIG) and every gateway
    tool with it. These tests assert on the SessionContext provision() built,
    which is what actually reaches the guest.
    """

    @staticmethod
    def _mock_docker():
        client = MagicMock()
        image = MagicMock()
        image.attrs = {"RepoDigests": []}
        client.images.get.return_value = image
        client.containers.get.side_effect = NotFound("not found")
        client.containers.create.return_value = MagicMock()
        return client

    @pytest.mark.asyncio
    async def test_local_session_gets_a_literal_url(self, gateway_on):
        client = self._mock_docker()
        with (
            patch("brainbox.lifecycle._docker", return_value=client),
            patch("brainbox.backends.docker._docker", return_value=client),
            patch("brainbox.lifecycle._find_available_port", return_value=7681),
            patch("brainbox.lifecycle._verify_cosign", new_callable=AsyncMock),
            patch("brainbox.lifecycle._resolve_profile_mounts", return_value={}),
        ):
            from brainbox.lifecycle import provision

            ctx = await provision(session_name="gw-local", workspace_profile="personal")

        url = ctx.extra_env.get("PHANTOM_GATEWAY_URL")
        assert url == settings.gateway.container_url
        assert "$" not in url  # the regression: a literal, not a placeholder
        from brainbox import registry
        tok = registry.validate_token(ctx.extra_env["PHANTOM_GATEWAY_TOKEN"])
        assert tok is not None and tok.workspace_profile == "personal"

    @pytest.mark.asyncio
    async def test_caller_env_is_preserved_alongside_the_pair(self, gateway_on):
        client = self._mock_docker()
        with (
            patch("brainbox.lifecycle._docker", return_value=client),
            patch("brainbox.backends.docker._docker", return_value=client),
            patch("brainbox.lifecycle._find_available_port", return_value=7681),
            patch("brainbox.lifecycle._verify_cosign", new_callable=AsyncMock),
            patch("brainbox.lifecycle._resolve_profile_mounts", return_value={}),
        ):
            from brainbox.lifecycle import provision

            ctx = await provision(
                session_name="gw-local-env",
                workspace_profile="personal",
                extra_env={"BRAINBOX_TOKEN": "caller-supplied"},
            )

        # The hub's env contract rides in on extra_env; the gateway pair must
        # be added to it, never replace it.
        assert ctx.extra_env["BRAINBOX_TOKEN"] == "caller-supplied"
        assert ctx.extra_env["PHANTOM_GATEWAY_URL"] == settings.gateway.container_url

    @pytest.mark.asyncio
    async def test_no_pair_when_gateway_inactive(self):
        # Gateway off (empty DB registry): deliver nothing rather than a
        # half-configured server the guest would fail on.
        client = self._mock_docker()
        with (
            patch("brainbox.lifecycle._docker", return_value=client),
            patch("brainbox.backends.docker._docker", return_value=client),
            patch("brainbox.lifecycle._find_available_port", return_value=7681),
            patch("brainbox.lifecycle._verify_cosign", new_callable=AsyncMock),
            patch("brainbox.lifecycle._resolve_profile_mounts", return_value={}),
        ):
            from brainbox.lifecycle import provision

            ctx = await provision(session_name="gw-off", workspace_profile="personal")

        assert "PHANTOM_GATEWAY_URL" not in ctx.extra_env

    @pytest.mark.asyncio
    async def test_runner_dispatch_still_carries_the_pair(self, gateway_on, monkeypatch):
        """Guards the refactor that moved the merge out of _provision_via_runner."""
        monkeypatch.setattr(settings, "public_url", "https://phantom-api.example.com")
        captured: dict = {}

        async def fake_via_runner(*, runner, **payload):
            captured["runner"] = runner
            captured["extra_env"] = payload.get("extra_env") or {}
            from brainbox.models import SessionContext
            return SessionContext(
                session_name=payload["session_name"],
                container_name="c",
                port=0,
                created_at=0,
                ttl=0,
            )

        monkeypatch.setattr("brainbox.lifecycle._provision_via_runner", fake_via_runner)
        from brainbox.lifecycle import provision

        await provision(session_name="gw-runner", workspace_profile="personal", runner="m3-64")

        assert captured["runner"] == "m3-64"
        # A runner container is off-host, so it must get the public URL — not
        # the host.docker.internal alias it cannot resolve.
        assert (
            captured["extra_env"]["PHANTOM_GATEWAY_URL"]
            == "https://phantom-api.example.com/gateway/mcp"
        )

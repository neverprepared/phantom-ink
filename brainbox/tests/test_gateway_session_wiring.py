"""Tests for injecting the MCP gateway into a session's container .mcp.json (ADR-002 phase 3)."""

from __future__ import annotations

import json

import pytest

from brainbox.config import settings
from brainbox.lifecycle import (
    _gateway_server_entry,
    _gateway_session_env,
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


class TestGatewaySessionEnv:
    """Env-pair delivery for runner-hosted sessions (baked-declaration design)."""

    def test_env_pair_uses_public_url(self, gateway_on, monkeypatch):
        from brainbox import registry
        monkeypatch.setattr(settings, "public_url", "https://phantom-api.example.com")
        env = _gateway_session_env("personal")
        assert env["PHANTOM_GATEWAY_URL"] == "https://phantom-api.example.com/gateway/mcp"
        tok = registry.validate_token(env["PHANTOM_GATEWAY_TOKEN"])
        assert tok is not None and tok.workspace_profile == "personal"

    def test_falls_back_to_container_url_without_public_url(self, gateway_on, monkeypatch):
        monkeypatch.setattr(settings, "public_url", "")
        env = _gateway_session_env("personal")
        assert env["PHANTOM_GATEWAY_URL"] == settings.gateway.container_url

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

"""Tests for injecting the MCP gateway into a session's container .mcp.json (ADR-002 phase 3)."""

from __future__ import annotations

import json

import pytest

from brainbox.config import settings
from brainbox.lifecycle import _gateway_server_entry, _generate_container_mcp_json


@pytest.fixture
def gateway_on(monkeypatch):
    monkeypatch.setattr(settings.gateway, "servers", ["brainbox"])
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

    def test_no_entry_without_profile(self, gateway_on):
        assert _gateway_server_entry("") is None

    def test_no_entry_when_no_servers(self, monkeypatch):
        monkeypatch.setattr(settings.gateway, "servers", [])
        monkeypatch.setattr(settings.gateway, "inject_sessions", True)
        assert _gateway_server_entry("personal") is None

    def test_no_entry_when_injection_disabled(self, monkeypatch):
        monkeypatch.setattr(settings.gateway, "servers", ["brainbox"])
        monkeypatch.setattr(settings.gateway, "inject_sessions", False)
        assert _gateway_server_entry("personal") is None


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

    def test_nothing_written_when_no_servers_and_gateway_off(self, monkeypatch, tmp_path):
        monkeypatch.setattr(settings.gateway, "servers", [])
        dest = tmp_path / "workspace-mcp.json"
        assert _generate_container_mcp_json(tmp_path / "cfg", dest, workspace_profile="personal") is False
        assert not dest.exists()

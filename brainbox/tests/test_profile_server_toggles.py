"""Tests for per-profile gateway server toggles (resolution default + override)."""

from __future__ import annotations

import json

import pytest
from pydantic import SecretStr

import brainbox.gateway_secrets as gw
from brainbox import step_planner, store
from brainbox.config import settings
from brainbox.trust_zones import TrustZone


@pytest.fixture
def env(tmp_path, monkeypatch):
    cat = {
        "version": "1",
        "servers": {
            "brain": {"definition": {"command": "node", "args": ["x"],
                                     "env": {"MCP_BRAIN_LOG_LEVEL": "info"}}},
            "atlassian": {"definition": {"command": "uvx", "args": ["a"],
                                         "env": {"JIRA_URL": "${JIRA_URL}"}}},
        },
    }
    (tmp_path / "cat.json").write_text(json.dumps(cat))
    monkeypatch.setattr(settings.gateway, "catalog_path", str(tmp_path / "cat.json"))
    store.set_gateway_server_enabled("brain", True)
    store.set_gateway_server_enabled("atlassian", True)
    monkeypatch.setattr(settings.gateway, "secrets_dir", str(tmp_path / "secrets"))
    monkeypatch.setattr(settings.gateway, "secret_key", SecretStr("pp"))
    gw.set_profile_env("work", {"JIRA_URL": "https://jira.corp.internal"})
    store.set_trust_rule("work", "*.corp.internal", "infra")
    monkeypatch.setattr(settings.orchestration, "default_ceiling", "public")


class TestStates:
    def test_defaults_from_resolution(self, env):
        states = {s.name: s for s in step_planner.profile_server_states("work")}
        # brain=local, atlassian=infra; ceiling public → both default-enabled
        assert states["brain"].zone is TrustZone.LOCAL
        assert states["atlassian"].zone is TrustZone.INFRA
        assert states["brain"].default_enabled and states["atlassian"].default_enabled
        assert states["brain"].override is None
        assert states["brain"].effective and states["atlassian"].effective

    def test_ceiling_seeds_exclusion(self, env):
        store.set_profile_default_ceiling("work", "local")
        states = {s.name: s for s in step_planner.profile_server_states("work")}
        assert states["brain"].default_enabled is True          # local ≤ local
        assert states["atlassian"].default_enabled is False     # infra > local
        assert step_planner.effective_enabled_servers("work") == {"brain"}

    def test_user_override_wins(self, env):
        # ceiling excludes atlassian by default; user re-includes it
        store.set_profile_default_ceiling("work", "local")
        store.set_profile_server_override("work", "atlassian", True)
        states = {s.name: s for s in step_planner.profile_server_states("work")}
        assert states["atlassian"].default_enabled is False
        assert states["atlassian"].override is True
        assert states["atlassian"].effective is True            # override wins
        assert step_planner.effective_enabled_servers("work") == {"brain", "atlassian"}

    def test_override_can_exclude(self, env):
        store.set_profile_server_override("work", "brain", False)  # exclude a default-on server
        assert step_planner.effective_enabled_servers("work") == {"atlassian"}

    def test_clear_reverts_to_default(self, env):
        store.set_profile_server_override("work", "brain", False)
        assert "brain" not in step_planner.effective_enabled_servers("work")
        store.clear_profile_server_override("work", "brain")
        assert "brain" in step_planner.effective_enabled_servers("work")


class TestGatewayGating:
    @pytest.mark.asyncio
    async def test_disabled_server_hidden_and_denied(self, monkeypatch):
        import sys
        from pathlib import Path

        from brainbox.gateway_pool import GatewayPool, ServerSpec
        from brainbox.gateway_server import Identity, call_gateway_tool, list_gateway_tools
        from brainbox.step_planner import ProfileServerState

        fixture = str(Path(__file__).parent / "_mcp_fixture_server.py")
        specs = [ServerSpec("local", sys.executable, [fixture]),
                 ServerSpec("off", sys.executable, [fixture])]
        # 'off' is effectively disabled for the profile; 'local' enabled.
        monkeypatch.setattr(step_planner, "profile_server_states", lambda p: [
            ProfileServerState("local", TrustZone.LOCAL, True, None, True),
            ProfileServerState("off", TrustZone.LOCAL, True, False, False),
        ])
        pool = GatewayPool()
        try:
            ident = Identity("work", ["*"])
            names = {t.name for t in await list_gateway_tools(pool, specs, ident)}
            assert any(n.startswith("local__") for n in names)
            assert not any(n.startswith("off__") for n in names)   # toggled off
            with pytest.raises(PermissionError):
                await call_gateway_tool(pool, specs, ident, "off__echo", {"text": "x"})
        finally:
            await pool.aclose()

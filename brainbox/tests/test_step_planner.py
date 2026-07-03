"""Integration tests for the step planner (composes providers + tool zones)."""

from __future__ import annotations

import json

import pytest
from pydantic import SecretStr

import brainbox.gateway_secrets as gw
from brainbox import step_planner, store
from brainbox.config import settings
from brainbox.residency_resolver import Requirement
from brainbox.trust_zones import TrustZone


@pytest.fixture
def env(tmp_path, monkeypatch):
    # Catalog: a local 'brain' (no dest env) + 'atlassian' whose JIRA_URL is a placeholder.
    cat = {
        "version": "1",
        "servers": {
            "brain": {"definition": {"command": "node", "args": ["x"],
                                     "env": {"MCP_BRAIN_LOG_LEVEL": "info"}}},
            "atlassian": {"definition": {"command": "uvx", "args": ["a"],
                                         "env": {"JIRA_URL": "${JIRA_URL}", "LOG_LEVEL": "info"}}},
        },
    }
    (tmp_path / "cat.json").write_text(json.dumps(cat))
    monkeypatch.setattr(settings.gateway, "catalog_path", str(tmp_path / "cat.json"))

    # Both servers enabled.
    store.set_gateway_server_enabled("brain", True)
    store.set_gateway_server_enabled("atlassian", True)

    # Gateway secrets: JIRA points at self-hosted infra.
    monkeypatch.setattr(settings.gateway, "secrets_dir", str(tmp_path / "secrets"))
    monkeypatch.setattr(settings.gateway, "secret_key", SecretStr("pp"))
    gw.set_profile_env("work", {"JIRA_URL": "https://jira.corp.internal"})

    # Trust map: *.corp.internal = infra.
    store.set_trust_rule("work", "*.corp.internal", "infra")

    # Deterministic provider destinations.
    monkeypatch.setattr(settings.orchestration, "ollama_url", "http://localhost:11434")
    monkeypatch.setattr(settings.orchestration, "claude_url", "https://api.anthropic.com")
    monkeypatch.setattr(settings.orchestration, "codex_url", "https://api.openai.com")


def test_zones_scoped_per_server(env):
    zones = step_planner.mcp_zones_for_profile("work")
    assert zones["brain"] is TrustZone.LOCAL           # curated hint, no dest env
    assert zones["atlassian"] is TrustZone.INFRA       # JIRA_URL → *.corp.internal → infra
    # (atlassian is NOT tainted by any other server's URL — per-server env scoping)


def test_infra_ceiling_admits_both_tools_and_local_provider(env):
    plan = step_planner.plan_step("work", Requirement(TrustZone.INFRA, requires=frozenset({"coding"})))
    assert not plan.blocked
    assert plan.provider.name == "ollama"              # local, within infra
    assert set(plan.eligible_tools) == {"brain", "atlassian"}
    assert plan.excluded_tools == ()


def test_local_ceiling_excludes_infra_tool(env):
    plan = step_planner.plan_step("work", Requirement(TrustZone.LOCAL, requires=frozenset({"coding"})))
    assert not plan.blocked
    assert plan.provider.name == "ollama"
    assert plan.eligible_tools == ("brain",)           # atlassian (infra) excluded under LOCAL
    assert plan.excluded_tools == (("atlassian", TrustZone.INFRA),)


def test_blocked_when_no_compliant_provider(env):
    # 'vision' only claude has; claude is PUBLIC → LOCAL ceiling has no provider → blocked
    plan = step_planner.plan_step("work", Requirement(TrustZone.LOCAL, requires=frozenset({"vision"})))
    assert plan.blocked
    assert plan.provider is None
    assert "fail-closed" in plan.reason

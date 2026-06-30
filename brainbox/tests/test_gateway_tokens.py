"""Tests for Tier-0 gateway token minting + token-carried scope (ADR-002 phase 3)."""

from __future__ import annotations

import pytest

import brainbox.registry as reg_module
import brainbox.router as router_module
from brainbox.gateway_server import BrainboxTokenVerifier
from brainbox.models import AgentDefinition, Task, TaskStatus


class TestGatewayTokenMinting:
    def test_mints_profile_bound_token_without_agent_or_task(self):
        tok = reg_module.issue_gateway_token("personal", ["phantom-brain__*"])
        assert tok.workspace_profile == "personal"
        assert tok.scope == ["phantom-brain__*"]
        assert tok.task_id == ""
        assert tok.agent_name == "gateway"
        # round-trips through the registry
        assert reg_module.validate_token(tok.token_id) is not None

    def test_default_scope_is_empty(self):
        tok = reg_module.issue_gateway_token("personal")
        assert tok.scope == []

    def test_revoke(self):
        tok = reg_module.issue_gateway_token("personal")
        assert reg_module.revoke_token(tok.token_id) is True
        assert reg_module.validate_token(tok.token_id) is None

    def test_survives_daemon_restart(self):
        # A gateway token is persisted, so it still validates after the in-memory
        # registry is cleared (simulating a daemon restart) and reloaded — the
        # fix for containers 401-ing on their baked token after a restart.
        tok = reg_module.issue_gateway_token("sandbox", ["markitdown__*"])
        reg_module._tokens.clear()  # simulate restart: memory gone, DB intact
        assert reg_module.validate_token(tok.token_id) is None  # not in memory yet
        loaded = reg_module.load_persisted_gateway_tokens()
        assert loaded >= 1
        rehydrated = reg_module.validate_token(tok.token_id)
        assert rehydrated is not None
        assert rehydrated.workspace_profile == "sandbox"
        assert rehydrated.scope == ["markitdown__*"]

    def test_revoke_removes_from_persistence(self):
        tok = reg_module.issue_gateway_token("sandbox")
        reg_module.revoke_token(tok.token_id)
        reg_module._tokens.clear()
        reg_module.load_persisted_gateway_tokens()  # must NOT bring it back
        assert reg_module.validate_token(tok.token_id) is None


class TestVerifierReadsTokenFields:
    @pytest.mark.asyncio
    async def test_tier0_token_maps_profile_and_scope(self):
        tok = reg_module.issue_gateway_token("personal", ["fixture__echo"])
        at = await BrainboxTokenVerifier().verify_token(tok.token_id)
        assert at is not None
        assert at.client_id == "personal"
        assert at.scopes == ["fixture__echo"]

    @pytest.mark.asyncio
    async def test_empty_scope_falls_back_to_all(self):
        tok = reg_module.issue_gateway_token("personal")
        at = await BrainboxTokenVerifier().verify_token(tok.token_id)
        assert at is not None and at.scopes == ["*"]

    @pytest.mark.asyncio
    async def test_task_token_still_derives_profile_from_task(self):
        # back-compat: a Tier-1 task token (no workspace_profile on the token)
        # still resolves the profile via its task, with permissive scope.
        reg_module._agents["worker"] = AgentDefinition(
            name="worker", image="t", capabilities=["task_submit"]
        )
        router_module._tasks["task-tok"] = Task(
            id="task-tok", description="d", agent_name="worker", status=TaskStatus.RUNNING,
            created_at=0, updated_at=0, workspace_profile="personal",
        )
        tok = reg_module.issue_token("worker", "task-tok")
        at = await BrainboxTokenVerifier().verify_token(tok.token_id)
        assert at is not None
        assert at.client_id == "personal"
        assert at.scopes == ["*"]

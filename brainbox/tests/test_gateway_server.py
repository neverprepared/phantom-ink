"""Tests for the MCP gateway server-plane policy layer (ADR-002 phase 2b-core).

Exercises the pure list/call functions (namespacing, scope filtering, profile
routing + env injection, down-server resilience) against the real stdio
fixture server, and the token→profile verifier.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import SecretStr

import brainbox.gateway_secrets as gw
import brainbox.registry as reg_module
import brainbox.router as router_module
from brainbox.config import settings
from brainbox.gateway_pool import GatewayPool, ServerSpec
from brainbox.gateway_server import (
    BrainboxTokenVerifier,
    GatewayError,
    Identity,
    call_gateway_tool,
    list_gateway_tools,
)
from brainbox.models import AgentDefinition, Task, TaskStatus

_FIXTURE = str(Path(__file__).parent / "_mcp_fixture_server.py")


def _spec() -> ServerSpec:
    return ServerSpec(name="fixture", command=sys.executable, args=[_FIXTURE])


def _bad_spec() -> ServerSpec:
    return ServerSpec(name="bad", command=sys.executable, args=["-c", "import sys; sys.exit(1)"])


def _texts(blocks) -> list[str]:
    return [b.text for b in blocks if getattr(b, "type", "") == "text"]


class TestToolListing:
    @pytest.mark.asyncio
    async def test_namespaced_and_permissive(self):
        pool = GatewayPool()
        try:
            tools = await list_gateway_tools(pool, [_spec()], Identity("personal", ["*"]))
            assert {t.name for t in tools} == {"fixture__echo", "fixture__getenv"}
        finally:
            await pool.aclose()

    @pytest.mark.asyncio
    async def test_scope_filters_to_one_tool(self):
        pool = GatewayPool()
        try:
            tools = await list_gateway_tools(pool, [_spec()], Identity("personal", ["fixture__echo"]))
            assert {t.name for t in tools} == {"fixture__echo"}
        finally:
            await pool.aclose()

    @pytest.mark.asyncio
    async def test_server_wildcard_scope(self):
        pool = GatewayPool()
        try:
            tools = await list_gateway_tools(pool, [_spec()], Identity("personal", ["fixture__*"]))
            assert {t.name for t in tools} == {"fixture__echo", "fixture__getenv"}
        finally:
            await pool.aclose()

    @pytest.mark.asyncio
    async def test_down_server_is_skipped_not_fatal(self):
        pool = GatewayPool()
        try:
            tools = await list_gateway_tools(pool, [_bad_spec(), _spec()], Identity("personal", ["*"]))
            assert {t.name for t in tools} == {"fixture__echo", "fixture__getenv"}
        finally:
            await pool.aclose()


class TestToolCall:
    @pytest.mark.asyncio
    async def test_routes_and_injects_profile_env(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings.gateway, "secrets_dir", str(tmp_path))
        monkeypatch.setattr(settings.gateway, "secret_key", SecretStr("pp"))
        gw.set_profile_env("personal", {"MY_SECRET": "sekret"})
        pool = GatewayPool()
        try:
            out = await call_gateway_tool(
                pool, [_spec()], Identity("personal", ["*"]), "fixture__getenv", {"name": "MY_SECRET"}
            )
            assert any("sekret" in t for t in _texts(out))
        finally:
            await pool.aclose()

    @pytest.mark.asyncio
    async def test_out_of_scope_denied(self):
        pool = GatewayPool()
        try:
            with pytest.raises(PermissionError):
                await call_gateway_tool(
                    pool, [_spec()], Identity("personal", ["fixture__echo"]),
                    "fixture__getenv", {"name": "X"},
                )
        finally:
            await pool.aclose()

    @pytest.mark.asyncio
    async def test_bad_tool_name(self):
        pool = GatewayPool()
        try:
            with pytest.raises(GatewayError):
                await call_gateway_tool(pool, [_spec()], Identity("personal", ["*"]), "noseptool", {})
        finally:
            await pool.aclose()

    @pytest.mark.asyncio
    async def test_unknown_server(self):
        pool = GatewayPool()
        try:
            with pytest.raises(GatewayError):
                await call_gateway_tool(pool, [_spec()], Identity("personal", ["*"]), "ghost__tool", {})
        finally:
            await pool.aclose()


class TestTokenVerifier:
    @pytest.mark.asyncio
    async def test_valid_token_maps_to_profile(self):
        reg_module._agents["worker"] = AgentDefinition(
            name="worker", image="t", capabilities=["task_submit"]
        )
        router_module._tasks["task-1"] = Task(
            id="task-1", description="d", agent_name="worker", status=TaskStatus.RUNNING,
            created_at=0, updated_at=0, workspace_profile="personal",
        )
        tok = reg_module.issue_token("worker", "task-1")
        at = await BrainboxTokenVerifier().verify_token(tok.token_id)
        assert at is not None and at.client_id == "personal"

    @pytest.mark.asyncio
    async def test_invalid_token_returns_none(self):
        assert await BrainboxTokenVerifier().verify_token("not-a-real-token") is None

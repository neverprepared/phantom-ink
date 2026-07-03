"""Tests for gateway residency enforcement (token ceiling → tool filtering)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import brainbox.gateway_server as gs
import brainbox.registry as reg
from brainbox.gateway_pool import GatewayPool, ServerSpec
from brainbox.gateway_server import Identity, call_gateway_tool, list_gateway_tools
from brainbox.trust_zones import TrustZone

_FIXTURE = str(Path(__file__).parent / "_mcp_fixture_server.py")


def _specs() -> list[ServerSpec]:
    # Two identical fixture servers, distinct names — 'local' and 'cloudy'.
    return [
        ServerSpec("local", sys.executable, [_FIXTURE]),
        ServerSpec("cloudy", sys.executable, [_FIXTURE]),
    ]


@pytest.fixture
def zones(monkeypatch):
    monkeypatch.setattr(
        gs, "_residency_zones",
        lambda ident: {"local": TrustZone.LOCAL, "cloudy": TrustZone.PUBLIC},
    )


class TestListFiltering:
    @pytest.mark.asyncio
    async def test_ceiling_excludes_out_of_zone_server(self, zones):
        pool = GatewayPool()
        try:
            ident = Identity("personal", ["*"], ceiling=TrustZone.LOCAL)
            names = {t.name for t in await list_gateway_tools(pool, _specs(), ident)}
            assert any(n.startswith("local__") for n in names)
            assert not any(n.startswith("cloudy__") for n in names)  # PUBLIC > LOCAL
        finally:
            await pool.aclose()

    @pytest.mark.asyncio
    async def test_no_ceiling_lists_all(self, zones):
        pool = GatewayPool()
        try:
            ident = Identity("personal", ["*"], ceiling=None)  # back-compat
            names = {t.name for t in await list_gateway_tools(pool, _specs(), ident)}
            assert any(n.startswith("cloudy__") for n in names)
        finally:
            await pool.aclose()

    @pytest.mark.asyncio
    async def test_higher_ceiling_admits_public(self, zones):
        pool = GatewayPool()
        try:
            ident = Identity("personal", ["*"], ceiling=TrustZone.PUBLIC)
            names = {t.name for t in await list_gateway_tools(pool, _specs(), ident)}
            assert any(n.startswith("cloudy__") for n in names)
        finally:
            await pool.aclose()


class TestCallEnforcement:
    @pytest.mark.asyncio
    async def test_call_out_of_zone_denied(self, zones):
        pool = GatewayPool()
        try:
            ident = Identity("personal", ["*"], ceiling=TrustZone.LOCAL)
            with pytest.raises(PermissionError):
                await call_gateway_tool(pool, _specs(), ident, "cloudy__echo", {"text": "x"})
        finally:
            await pool.aclose()

    @pytest.mark.asyncio
    async def test_call_in_zone_allowed(self, zones):
        pool = GatewayPool()
        try:
            ident = Identity("personal", ["*"], ceiling=TrustZone.LOCAL)
            content, _ = await call_gateway_tool(pool, _specs(), ident, "local__echo", {"text": "ok"})
            assert any("ok" in getattr(b, "text", "") for b in content)
        finally:
            await pool.aclose()


class TestTokenCeilingPersistence:
    def test_ceiling_stored_on_token_and_survives_restart(self):
        tok = reg.issue_gateway_token("sandbox", ["*"], residency_ceiling="infra")
        assert tok.residency_ceiling == "infra"
        reg._tokens.clear()  # simulate restart
        reg.load_persisted_gateway_tokens()
        assert reg.validate_token(tok.token_id).residency_ceiling == "infra"

    def test_default_no_ceiling(self):
        tok = reg.issue_gateway_token("sandbox")
        assert tok.residency_ceiling == ""

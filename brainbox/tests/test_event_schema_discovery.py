"""Native schema-discovery surfaces for the timeline-entry (AgentEnvelope) contract.

Three ways an agent can find the live event schema without being told where it
lives, all backed by the same pydantic model so none can drift:

  - MCP resource   `contract://events/timeline-entry`
  - MCP tool       `get_event_schema()`
  - well-known     `GET /.well-known/phantom-events.json` (unauthenticated)

Plus the contract guarantee that `/openapi.json` carries the schema now that the
ingest route is typed (T2) — with the T1 fields `workspace` and `subtitle`.
"""

from __future__ import annotations

import json

import pytest

from brainbox import mcp_server
from brainbox.agent_store import AgentEnvelope


def _live_schema() -> dict:
    return AgentEnvelope.model_json_schema()


# ---------------------------------------------------------------------------
# MCP resource + tool
# ---------------------------------------------------------------------------


class TestMcpDiscovery:
    @pytest.mark.asyncio
    async def test_resource_is_listed(self):
        resources = await mcp_server.mcp.list_resources()
        uris = {str(r.uri) for r in resources}
        assert "contract://events/timeline-entry" in uris

    @pytest.mark.asyncio
    async def test_resource_returns_live_schema(self):
        contents = list(
            await mcp_server.mcp.read_resource("contract://events/timeline-entry")
        )
        assert contents, "resource returned no contents"
        payload = json.loads(contents[0].content)
        assert payload == _live_schema()
        props = payload["properties"]
        assert "workspace" in props
        assert "subtitle" in props

    @pytest.mark.asyncio
    async def test_tool_returns_live_schema(self):
        _blocks, structured = await mcp_server.mcp.call_tool("get_event_schema", {})
        assert structured == _live_schema()

    @pytest.mark.asyncio
    async def test_tool_and_resource_agree(self):
        contents = list(
            await mcp_server.mcp.read_resource("contract://events/timeline-entry")
        )
        resource_schema = json.loads(contents[0].content)
        _blocks, tool_schema = await mcp_server.mcp.call_tool("get_event_schema", {})
        assert resource_schema == tool_schema


# ---------------------------------------------------------------------------
# .well-known discovery (unauthenticated, like the A2A agent card)
# ---------------------------------------------------------------------------


class TestWellKnownDiscovery:
    @pytest.mark.asyncio
    async def test_returns_200_with_schema(self, client):
        async with client as c:
            resp = await c.get("/.well-known/phantom-events.json")
        assert resp.status_code == 200
        body = resp.json()
        assert body["schema"] == _live_schema()
        assert body["schema_ref"] == "/openapi.json#/components/schemas/AgentEnvelope"

    @pytest.mark.asyncio
    async def test_is_public_no_auth(self):
        # A fresh client with a real API key enforced (no test override) must
        # still get the schema — discovery is unauthenticated by design.
        import brainbox.auth as auth_module
        from httpx import ASGITransport, AsyncClient
        from brainbox.api import app

        auth_module._api_key = "a-real-key"
        app.dependency_overrides.clear()
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                resp = await c.get("/.well-known/phantom-events.json")  # no header
            assert resp.status_code == 200
            assert resp.json()["schema"] == _live_schema()
        finally:
            auth_module._api_key = ""


# ---------------------------------------------------------------------------
# OpenAPI carries the schema (the T2 typing guarantee this task verifies)
# ---------------------------------------------------------------------------


class TestOpenApiContainsSchema:
    @pytest.mark.asyncio
    async def test_openapi_exposes_agent_envelope(self, client):
        async with client as c:
            resp = await c.get("/openapi.json")
        assert resp.status_code == 200
        schemas = resp.json()["components"]["schemas"]
        assert "AgentEnvelope" in schemas
        props = schemas["AgentEnvelope"]["properties"]
        assert "workspace" in props
        assert "subtitle" in props

"""End-to-end test for the MCP gateway streamable-HTTP mount (ADR-002 2c).

Runs a minimal host app (mounts the gateway sub-app + drives the session
manager lifespan) under a real uvicorn server in a thread, then connects a
real MCP streamable-HTTP client with a Bearer token and exercises the full
stack: transport → bearer auth → profile resolution → scoped tool list →
profile-routed call with env injection. Also checks that no token → 401.
"""

from __future__ import annotations

import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from pydantic import SecretStr
from starlette.applications import Starlette
from starlette.routing import Mount

import brainbox.gateway_secrets as gw
import brainbox.registry as reg_module
import brainbox.router as router_module
from brainbox.config import settings
from brainbox.gateway_pool import GatewayPool, ServerSpec
from brainbox.gateway_http import build_gateway_subapp
from brainbox.gateway_server import BrainboxTokenVerifier, build_gateway_server
from brainbox.models import AgentDefinition, Task, TaskStatus

_FIXTURE = str(Path(__file__).parent / "_mcp_fixture_server.py")


def _free_port() -> int:
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@asynccontextmanager
async def _serve(host_app, port):
    config = uvicorn.Config(host_app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    # wait for readiness
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    try:
        yield
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.mark.asyncio
async def test_streamable_http_end_to_end(tmp_path, monkeypatch):
    # profile env (proves injection through the whole stack)
    monkeypatch.setattr(settings.gateway, "secrets_dir", str(tmp_path))
    monkeypatch.setattr(settings.gateway, "secret_key", SecretStr("pp"))
    gw.set_profile_env("personal", {"MY_SECRET": "sekret"})

    # a hub token bound to a task in the 'personal' profile
    reg_module._agents["worker"] = AgentDefinition(name="worker", image="t", capabilities=["task_submit"])
    router_module._tasks["task-1"] = Task(
        id="task-1", description="d", agent_name="worker", status=TaskStatus.RUNNING,
        created_at=0, updated_at=0, workspace_profile="personal",
    )
    token = reg_module.issue_token("worker", "task-1").token_id

    # gateway over one downstream fixture server
    pool = GatewayPool()
    server = build_gateway_server(pool, [ServerSpec(name="fixture", command=sys.executable, args=[_FIXTURE])])
    subapp, sm = build_gateway_subapp(server, BrainboxTokenVerifier())

    @asynccontextmanager
    async def lifespan(app):
        async with sm.run():
            yield

    host = Starlette(routes=[Mount("/gateway", app=subapp)], lifespan=lifespan)
    port = _free_port()
    url = f"http://127.0.0.1:{port}/gateway/mcp"

    try:
        async with _serve(host, port):
            # 1. no token -> 401
            async with httpx.AsyncClient() as c:
                r = await c.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
                assert r.status_code == 401

            # 2. valid token -> full MCP round-trip
            headers = {"Authorization": f"Bearer {token}"}
            async with streamablehttp_client(url, headers=headers) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = {t.name for t in (await session.list_tools()).tools}
                    assert {"fixture__echo", "fixture__getenv"} <= tools

                    res = await session.call_tool("fixture__getenv", {"name": "MY_SECRET"})
                    assert not res.isError
                    texts = [c.text for c in res.content if getattr(c, "type", "") == "text"]
                    assert any("sekret" in t for t in texts)
    finally:
        await pool.aclose()

"""Tests for the MCP gateway downstream client pool (ADR-002 phase 2a).

Spawns the trivial stdio fixture server (_mcp_fixture_server.py) as a real
downstream MCP server and exercises connect / list / call, per-profile env
injection + isolation, concurrency, and cross-task safety.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from pydantic import SecretStr

import brainbox.gateway_secrets as gw
from brainbox.config import settings
from brainbox.gateway_pool import GatewayPool, ServerSpec

_FIXTURE = str(Path(__file__).parent / "_mcp_fixture_server.py")


def _spec() -> ServerSpec:
    return ServerSpec(name="fixture", command=sys.executable, args=[_FIXTURE])


def _texts(result) -> list[str]:
    return [c.text for c in result.content if getattr(c, "type", "") == "text"]


@pytest.mark.asyncio
async def test_list_tools():
    pool = GatewayPool()
    try:
        tools = await pool.list_tools("personal", _spec())
        assert {"echo", "getenv"} <= {t.name for t in tools}
    finally:
        await pool.aclose()


@pytest.mark.asyncio
async def test_call_echo():
    pool = GatewayPool()
    try:
        res = await pool.call_tool("personal", _spec(), "echo", {"text": "hi"})
        assert any("echo: hi" in t for t in _texts(res))
    finally:
        await pool.aclose()


@pytest.mark.asyncio
async def test_profile_env_injected(tmp_path, monkeypatch):
    monkeypatch.setattr(settings.gateway, "secrets_dir", str(tmp_path))
    monkeypatch.setattr(settings.gateway, "secret_key", SecretStr("pp"))
    gw.set_profile_env("personal", {"MY_SECRET": "sekret"})
    pool = GatewayPool()
    try:
        res = await pool.call_tool("personal", _spec(), "getenv", {"name": "MY_SECRET"})
        assert any("sekret" in t for t in _texts(res))
    finally:
        await pool.aclose()


@pytest.mark.asyncio
async def test_profile_isolation(tmp_path, monkeypatch):
    # personal has the secret; 'work' has no stored env -> must NOT see it.
    monkeypatch.setattr(settings.gateway, "secrets_dir", str(tmp_path))
    monkeypatch.setattr(settings.gateway, "secret_key", SecretStr("pp"))
    gw.set_profile_env("personal", {"MY_SECRET": "sekret"})
    pool = GatewayPool()
    try:
        res = await pool.call_tool("work", _spec(), "getenv", {"name": "MY_SECRET"})
        assert any("<unset>" in t for t in _texts(res))
    finally:
        await pool.aclose()


@pytest.mark.asyncio
async def test_concurrent_calls():
    pool = GatewayPool()
    try:
        results = await asyncio.gather(
            *[pool.call_tool("personal", _spec(), "echo", {"text": str(i)}) for i in range(8)]
        )
        got = sorted(t for r in results for t in _texts(r))
        assert got == sorted(f"echo: {i}" for i in range(8))
    finally:
        await pool.aclose()


@pytest.mark.asyncio
async def test_cross_task_calls_then_close():
    # The owner-task design must let calls come from tasks OTHER than the one
    # that triggered connect, and close cleanly — no anyio cross-task cancel
    # scope errors.
    pool = GatewayPool()
    try:
        await pool.list_tools("personal", _spec())  # connect (this task)

        async def call(i: int) -> str:
            r = await pool.call_tool("personal", _spec(), "echo", {"text": str(i)})
            return _texts(r)[0]

        out = await asyncio.gather(*[asyncio.create_task(call(i)) for i in range(4)])
        assert sorted(out) == sorted(f"echo: {i}" for i in range(4))
        await pool.close("personal", "fixture")  # close from a different task than connect
    finally:
        await pool.aclose()


@pytest.mark.asyncio
async def test_connect_failure_raises():
    pool = GatewayPool()
    try:
        with pytest.raises(Exception):
            await pool.list_tools("personal", ServerSpec(name="bad", command=sys.executable,
                                                         args=["-c", "import sys; sys.exit(1)"]))
    finally:
        await pool.aclose()


@pytest.mark.asyncio
async def test_hanging_server_times_out_and_is_negative_cached():
    # A server that never speaks MCP (just sleeps) must NOT block forever — it
    # times out, then is skipped fast on the next call (negative cache). This is
    # the resilience fix: one bad server can't hold the whole gateway hostage.
    import time as _time
    pool = GatewayPool(connect_timeout=0.5, failure_ttl=30.0)
    hang = ServerSpec(name="hang", command=sys.executable, args=["-c", "import time; time.sleep(60)"])
    try:
        t0 = _time.monotonic()
        with pytest.raises(Exception):
            await pool.list_tools("personal", hang)
        assert _time.monotonic() - t0 < 5, "first attempt should time out quickly, not hang"

        # second call returns near-instantly via the negative cache
        t1 = _time.monotonic()
        with pytest.raises(Exception):
            await pool.list_tools("personal", hang)
        assert _time.monotonic() - t1 < 0.3, "negative cache should skip without re-spawning"
    finally:
        await pool.aclose()


@pytest.mark.asyncio
async def test_spawn_inherits_path():
    # The spawned subprocess must receive PATH (from get_default_environment),
    # else bare commands like `uvx`/`npx` can't be found. The fixture reports
    # its own env, so assert it actually got a non-empty PATH.
    pool = GatewayPool()
    try:
        res = await pool.call_tool("personal", _spec(), "getenv", {"name": "PATH"})
        assert any(t.strip() for t in _texts(res)), "subprocess received no PATH"
    finally:
        await pool.aclose()


def test_unwrap_error_drills_into_exception_group():
    from brainbox.gateway_pool import _unwrap_error

    leaf = FileNotFoundError("uvx not found")
    grouped = ExceptionGroup("unhandled errors in a TaskGroup", [leaf])
    msg = _unwrap_error(grouped)
    assert "FileNotFoundError" in msg and "uvx not found" in msg
    # plain exceptions pass through
    assert _unwrap_error(ValueError("boom")) == "ValueError: boom"

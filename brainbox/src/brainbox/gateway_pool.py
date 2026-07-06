"""MCP gateway — downstream client pool (ADR-002, phase 2a).

Holds long-lived MCP ClientSessions to downstream catalog servers, keyed by
``(profile, server)``. Each session is spawned with the profile's env merged
in (the spec's base env + per-profile creds from ``gateway_secrets``), so a
server runs under the right profile's credentials. Lazy-connect, cached.

Lifecycle note (important): the MCP SDK's ``stdio_client`` / ``ClientSession``
use anyio cancel scopes, which must be entered and exited **in the same
task**. A naive ``AsyncExitStack`` cached on the pool breaks under a
multi-task server (FastAPI), raising "exit cancel scope in a different task".
So each connection runs in its own **owner task**: that task enters the
contexts, initializes, and serves requests off a queue (callers await
futures), then exits the contexts itself on shutdown. Enter + exit happen in
one task → no cross-task cancel-scope error.

Phase 2a scope: connect / list_tools / call_tool / close, with per-profile
env injection. Idle-reaping and catalog→spec resolution are phase 2b.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, get_default_environment, stdio_client

from . import gateway_secrets
from .log import get_logger

log = get_logger()


@dataclass
class ServerSpec:
    """How to start one downstream MCP server (catalog→spec wiring is phase 2b)."""

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    base_env: dict[str, str] = field(default_factory=dict)


def _unwrap_error(exc: BaseException, _depth: int = 0) -> str:
    """Render an exception for logging, drilling into ExceptionGroups.

    The owner-task pattern runs the MCP client inside an anyio task group, so a
    spawn failure surfaces as an ExceptionGroup whose ``str`` is just
    "unhandled errors in a TaskGroup" — useless for diagnosis. Recurse into the
    first sub-exception so the real cause (e.g. FileNotFoundError: uvx) shows.
    """
    inner = getattr(exc, "exceptions", None)
    if inner and _depth < 5:
        return _unwrap_error(inner[0], _depth + 1)
    return f"{type(exc).__name__}: {exc}"


def _profile_env(profile: str) -> dict[str, str]:
    """Per-profile creds from the encrypted store; {} if none/locked."""
    try:
        if gateway_secrets.is_unlocked() and profile in gateway_secrets.list_profiles():
            return gateway_secrets.get_profile_env(profile)
    except gateway_secrets.GatewaySecretsError:
        pass
    return {}


class _Connection:
    """One downstream session, owned by a single task."""

    def __init__(self, spec: ServerSpec, env: dict[str, str]) -> None:
        self._spec = spec
        self._env = env
        self._requests: asyncio.Queue = asyncio.Queue()
        self._ready = asyncio.Event()
        self._error: BaseException | None = None
        self._task: asyncio.Task | None = None

    async def start(self, timeout: float) -> None:
        self._task = asyncio.create_task(self._run(), name=f"mcp-conn:{self._spec.name}")
        try:
            await asyncio.wait_for(self._ready.wait(), timeout)
        except (asyncio.TimeoutError, TimeoutError):
            # A server that never initializes (e.g. one that speaks SSE, not
            # stdio) would otherwise block forever. Cancel it and surface a
            # bounded failure so the aggregate tool listing isn't held hostage.
            self._error = TimeoutError(
                f"{self._spec.name} did not initialize within {timeout}s"
            )
            if self._task is not None and not self._task.done():
                self._task.cancel()
        if self._error is not None:
            raise self._error

    async def _run(self) -> None:
        try:
            params = StdioServerParameters(
                command=self._spec.command, args=self._spec.args, env=self._env
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self._ready.set()
                    while True:
                        item = await self._requests.get()
                        if item is None:  # shutdown sentinel
                            break
                        fut, method, payload = item
                        if fut.cancelled():
                            continue
                        try:
                            if method == "list_tools":
                                result: Any = (await session.list_tools()).tools
                            elif method == "call_tool":
                                result = await session.call_tool(payload["tool"], payload["args"])
                            else:  # pragma: no cover - guarded by callers
                                raise ValueError(f"unknown method {method!r}")
                            fut.set_result(result)
                        except Exception as exc:  # downstream error → caller
                            if not fut.done():
                                fut.set_exception(exc)
        except BaseException as exc:  # startup/transport failure
            self._error = exc
            self._ready.set()
        finally:
            self._drain_pending(self._error or RuntimeError("connection closed"))

    def _drain_pending(self, exc: BaseException) -> None:
        while not self._requests.empty():
            item = self._requests.get_nowait()
            if item is None:
                continue
            fut, _, _ = item
            if not fut.done():
                fut.set_exception(exc)

    async def request(self, method: str, payload: dict) -> Any:
        if self._error is not None:
            raise self._error
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        await self._requests.put((fut, method, payload))
        return await fut

    async def close(self) -> None:
        await self._requests.put(None)
        if self._task is not None:
            await self._task


class GatewayPool:
    """Lazy, cached pool of downstream MCP sessions keyed by (profile, server)."""

    def __init__(self, *, connect_timeout: float | None = None, failure_ttl: float | None = None) -> None:
        from .config import settings

        self._conns: dict[tuple[str, str], _Connection] = {}
        self._lock = asyncio.Lock()
        # Negative cache: (profile, server) → monotonic time of last failure.
        # Skip a server that recently failed so it can't repeatedly block the
        # aggregate tool listing while the failure_ttl window is open.
        self._failed: dict[tuple[str, str], float] = {}
        self._connect_timeout = (
            connect_timeout if connect_timeout is not None else settings.gateway.connect_timeout
        )
        self._failure_ttl = failure_ttl if failure_ttl is not None else settings.gateway.failure_ttl

    async def _get(self, profile: str, spec: ServerSpec) -> _Connection:
        key = (profile, spec.name)
        conn = self._conns.get(key)
        if conn is not None:
            return conn
        # Skip recently-failed servers fast (don't re-pay the connect timeout).
        failed_at = self._failed.get(key)
        if failed_at is not None and (time.monotonic() - failed_at) < self._failure_ttl:
            raise RuntimeError(f"{spec.name} recently failed to connect; skipping (negative cache)")
        async with self._lock:
            conn = self._conns.get(key)
            if conn is not None:
                return conn
            # Seed from the SDK's default environment (PATH, HOME, …) so the
            # spawned command — usually a bare `uvx`/`npx`/`node` — is
            # resolvable; then layer the catalog literals and per-profile
            # creds on top. Without this the subprocess gets no PATH and every
            # non-absolute command fails to spawn.
            env = {**get_default_environment(), **spec.base_env, **_profile_env(profile)}
            conn = _Connection(spec, env)
            try:
                await conn.start(self._connect_timeout)
            except BaseException as exc:
                self._failed[key] = time.monotonic()
                log.warning(
                    "gateway_pool.connect_failed",
                    metadata={
                        "profile": profile,
                        "server": spec.name,
                        "error": _unwrap_error(exc),
                    },
                )
                raise
            self._failed.pop(key, None)
            self._conns[key] = conn
            log.info("gateway_pool.connected", metadata={"profile": profile, "server": spec.name})
            return conn

    async def list_tools(self, profile: str, spec: ServerSpec) -> list:
        conn = await self._get(profile, spec)
        return await conn.request("list_tools", {})

    async def call_tool(self, profile: str, spec: ServerSpec, tool: str, args: dict) -> Any:
        conn = await self._get(profile, spec)
        return await conn.request("call_tool", {"tool": tool, "args": args})

    async def close(self, profile: str | None = None, server: str | None = None) -> None:
        """Evict matching pooled connections AND their negative-cache entries.

        An explicit eviction is a full reset: callers close a profile's pool
        because its credentials just changed, so both the live subprocesses
        (spawned with the old env) and any recent-failure marks (a server that
        failed *because* creds were missing) must go — the next call re-spawns
        immediately with the fresh env instead of waiting out failure_ttl.
        """
        def _matches(k: tuple[str, str]) -> bool:
            return (profile is None or k[0] == profile) and (server is None or k[1] == server)

        for k in [k for k in list(self._conns) if _matches(k)]:
            conn = self._conns.pop(k, None)
            if conn is not None:
                await conn.close()
        for k in [k for k in list(self._failed) if _matches(k)]:
            self._failed.pop(k, None)

    async def aclose(self) -> None:
        await self.close()

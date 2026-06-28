"""MCP gateway — the server plane policy layer (ADR-002, phase 2b-core).

This is the security boundary: it turns a connecting agent's identity into
(profile, scope) and exposes ONLY the tools that agent may see, routed to
that profile's downstream sessions via the GatewayPool.

Pieces:
- ``BrainboxTokenVerifier`` — validates the agent's Bearer token against the
  hub token registry and returns an MCP ``AccessToken`` carrying the
  ``profile`` (in ``client_id``) and tool ``scope`` (in ``scopes``).
- ``list_gateway_tools`` / ``call_gateway_tool`` — pure functions: aggregate
  the pool's downstream tools (namespaced ``<server>__<tool>``), filter by
  scope, and route a call to the right profile's session.
- ``build_gateway_server`` — wires those into a low-level MCP ``Server`` whose
  handlers read the per-request identity via ``get_access_token()``.

Tool namespacing is ``<server>__<tool>``. Scope is a list of allowed tool
patterns carried on the token (``Token.scope``); empty or ``["*"]`` means
"all". Operators mint narrowly-scoped Tier-0 tokens via
``registry.issue_gateway_token`` (ADR-002 phase 3). The HTTP transport mount
(streamable-HTTP + auth middleware, into FastAPI) is phase 2c.
"""

from __future__ import annotations

from dataclasses import dataclass

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.lowlevel import Server

from .gateway_pool import GatewayPool, ServerSpec
from .log import get_logger

log = get_logger()

_SEP = "__"


class GatewayError(Exception):
    """Bad tool name / unknown server / unauthenticated."""


@dataclass
class Identity:
    profile: str
    scope: list[str]  # allowed tool patterns; [] or ["*"] = all


def _allowed(qualified: str, server: str, scope: list[str]) -> bool:
    if not scope or "*" in scope:
        return True
    return qualified in scope or f"{server}{_SEP}*" in scope


async def list_gateway_tools(pool: GatewayPool, specs: list[ServerSpec], ident: Identity) -> list:
    """Aggregate downstream tools for the identity's profile, namespaced + scoped.

    A downstream server that fails to list does not break the whole catalog —
    it is logged and skipped.
    """
    out: list = []
    for spec in specs:
        try:
            tools = await pool.list_tools(ident.profile, spec)
        except Exception as exc:
            log.warning(
                "gateway.list_skip",
                metadata={"profile": ident.profile, "server": spec.name, "reason": str(exc)},
            )
            continue
        for tool in tools:
            qualified = f"{spec.name}{_SEP}{tool.name}"
            if _allowed(qualified, spec.name, ident.scope):
                out.append(tool.model_copy(update={"name": qualified}))
    return out


async def call_gateway_tool(
    pool: GatewayPool, specs: list[ServerSpec], ident: Identity, name: str, arguments: dict
) -> tuple[list, dict | None]:
    """Route a namespaced tool call to the identity's profile session.

    Returns ``(content, structured_content)`` — passing the downstream's
    structured output through faithfully. This matters because we re-expose
    each tool's ``outputSchema`` (via ``model_copy``), so the low-level
    Server validates that structured output is present for those tools.
    """
    server, sep, tool = name.partition(_SEP)
    if not sep:
        raise GatewayError(f"tool name must be '<server>{_SEP}<tool>': {name!r}")
    if not _allowed(name, server, ident.scope):
        raise PermissionError(f"tool {name!r} is not in scope")
    spec = next((s for s in specs if s.name == server), None)
    if spec is None:
        raise GatewayError(f"unknown server {server!r}")
    result = await pool.call_tool(ident.profile, spec, tool, arguments)
    return result.content, result.structuredContent


def _identity_from_auth() -> Identity:
    token = get_access_token()
    if token is None:
        raise GatewayError("unauthenticated")
    return Identity(profile=token.client_id or "", scope=list(token.scopes or []))


def build_gateway_server(
    pool: GatewayPool, specs: list[ServerSpec], *, name: str = "phantom-ink-gateway"
) -> Server:
    """A low-level MCP Server whose tool list/calls are scoped per connection."""
    server: Server = Server(name)

    @server.list_tools()
    async def _list():  # pragma: no cover - thin auth wrapper over list_gateway_tools
        return await list_gateway_tools(pool, specs, _identity_from_auth())

    @server.call_tool()
    async def _call(tool_name: str, arguments: dict | None):  # pragma: no cover - thin wrapper
        return await call_gateway_tool(pool, specs, _identity_from_auth(), tool_name, arguments or {})

    return server


class BrainboxTokenVerifier(TokenVerifier):
    """Validate an agent Bearer token (hub token) → MCP AccessToken.

    Maps the hub token to ``(profile, scope)``:
    - **profile** — a Tier-0 token carries ``workspace_profile`` directly
      (``issue_gateway_token``); a Tier-1 task token derives it from the
      token's task (``workspace_profile``).
    - **scope** — the token's ``scope`` (allowed tool patterns); empty falls
      back to ``["*"]`` (all tools) for back-compat with task tokens.

    Returns None for invalid/expired tokens (→ 401 at the transport).
    """

    async def verify_token(self, token: str) -> AccessToken | None:
        from . import registry
        from . import router as task_router

        hub = registry.validate_token(token)
        if hub is None:
            return None
        profile = hub.workspace_profile or ""
        if not profile and hub.task_id:
            task = task_router.get_task(hub.task_id)
            if task is not None and task.workspace_profile:
                profile = task.workspace_profile
        scopes = list(hub.scope) or ["*"]
        expires_at = int(hub.expiry / 1000) if getattr(hub, "expiry", 0) else None
        return AccessToken(token=token, client_id=profile, scopes=scopes, expires_at=expires_at)

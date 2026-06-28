"""MCP gateway — streamable-HTTP transport mount (ADR-002, phase 2c).

Wires the low-level gateway Server (``gateway_server.build_gateway_server``)
onto a streamable-HTTP endpoint with the SDK's bearer-auth + auth-context
middleware, as a mountable Starlette sub-app.

Assembly mirrors ``FastMCP.streamable_http_app``: a
``StreamableHTTPSessionManager`` wraps the Server; its ``handle_request`` is
the ASGI endpoint, wrapped in ``RequireAuthMiddleware`` (enforces a valid
token); ``AuthenticationMiddleware(BearerAuthBackend(verifier))`` authenticates
the Bearer token and ``AuthContextMiddleware`` publishes it so the gateway's
handlers can read it via ``get_access_token()``.

IMPORTANT: the session manager's ``run()`` context must wrap serving. When
this sub-app is mounted into another app, the parent does NOT auto-run the
sub-app's lifespan — so the host must drive ``session_manager.run()`` from
its own lifespan (see ``api.py``). ``build_gateway_subapp`` returns the
manager for exactly that.
"""

from __future__ import annotations

from mcp.server.auth.middleware.auth_context import AuthContextMiddleware
from mcp.server.auth.middleware.bearer_auth import BearerAuthBackend, RequireAuthMiddleware
from mcp.server.auth.provider import TokenVerifier
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.routing import Route

MCP_PATH = "/mcp"


def build_gateway_subapp(
    mcp_server: Server, verifier: TokenVerifier
) -> tuple[Starlette, StreamableHTTPSessionManager]:
    """Build the mountable gateway sub-app + its session manager.

    The caller mounts the returned Starlette app and MUST drive
    ``session_manager.run()`` from the host lifespan.
    """
    session_manager = StreamableHTTPSessionManager(
        app=mcp_server, json_response=False, stateless=False
    )

    async def _handle(scope, receive, send) -> None:
        await session_manager.handle_request(scope, receive, send)

    routes = [
        Route(
            MCP_PATH,
            endpoint=RequireAuthMiddleware(_handle, required_scopes=[], resource_metadata_url=None),
        )
    ]
    middleware = [
        Middleware(AuthenticationMiddleware, backend=BearerAuthBackend(verifier)),
        Middleware(AuthContextMiddleware),
    ]
    return Starlette(routes=routes, middleware=middleware), session_manager

"""Trivial stdio MCP server used as a downstream target in gateway-pool tests.

Not a test module (leading underscore → pytest won't collect it). Spawned as
a subprocess by test_gateway_pool.py via StdioServerParameters.
"""

import os

from mcp.server.fastmcp import FastMCP

srv = FastMCP("fixture")


@srv.tool()
def echo(text: str) -> str:
    """Echo the input back."""
    return f"echo: {text}"


@srv.tool()
def getenv(name: str) -> str:
    """Return an env var — proves per-profile env injection reaches the server."""
    return os.environ.get(name, "<unset>")


if __name__ == "__main__":
    srv.run()

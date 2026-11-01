#!/usr/bin/env python3
"""Write the session's MCP server entries into the guest's Claude config, with
REAL values rather than ``${VAR}`` references.

Why this exists: Claude Code expands ``${VAR}`` in an MCP entry's **url** but
**not** in its **headers**. The image used to bake

    {"url": "${PHANTOM_GATEWAY_URL}",
     "headers": {"Authorization": "Bearer ${PHANTOM_GATEWAY_TOKEN}"}}

and assume both would be expanded at connect time. The url was; the header was
not, so the bearer went out as that literal string, the gateway answered
``401 invalid_token``, and the container silently lost every gateway tool.

Two servers are written here, and they are NOT interchangeable:

``phantom-gateway`` (http)
    The ADR-002 tool plane: a per-session Tier-0 bearer resolves to
    (profile, scope) and the gateway aggregates the downstream servers.

``brainbox`` (stdio)
    The session's OWN hub identity — ``channel_join`` / ``channel_read`` /
    ``channel_send`` / ``channel_complete`` (conversations), plus task and agent
    tools. This is NOT a gateway server and never was: the gateway catalog does
    not contain it, so a session whose only MCP entry is the gateway has no
    channel tools at all. It cannot be gateway-side either, because its tools
    are scoped to THIS session's bearer token.

Contract (all optional; each server is written only if its inputs are present):
    CLAUDE_JSON            path to the config to patch (required)
    PHANTOM_GATEWAY_URL    gateway endpoint      } both required for
    PHANTOM_GATEWAY_TOKEN  session bearer        } phantom-gateway
    BRAINBOX_URL (or BRAINBOX_HUB_URL_PUBLIC/BRAINBOX_HUB_URL)  } both
    BRAINBOX_TOKEN_ID (or BRAINBOX_TOKEN)                       } for brainbox

Values arrive through the environment, never argv, so tokens cannot appear in
``ps`` output. Exit 0 if at least one server was written, 3 if none could be,
and non-zero without writing anything if the config itself is unusable — a
half-written ~/.claude.json would cost the guest its oauth state, which is a far
worse failure than a missing MCP server.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile


def gateway_entry(url: str, token: str) -> dict:
    """The http MCP entry for the ADR-002 gateway."""
    return {
        "type": "http",
        "url": url,
        "headers": {"Authorization": f"Bearer {token}"},
    }


# Absolute path, not a bare name: the uv tool shim lives here, and an MCP server
# is spawned with whatever PATH the agent process happened to inherit.
BRAINBOX_MCP_BIN = "/home/developer/.local/bin/brainbox-mcp"


def brainbox_entry(hub_url: str, token: str, profile: str = "") -> dict:
    """The stdio MCP entry for this session's own hub tools.

    The variable NAMES matter. The guest brainbox-mcp reads ``BRAINBOX_URL`` and
    ``BRAINBOX_TOKEN_ID`` — NOT the ``BRAINBOX_HUB_URL`` / ``BRAINBOX_TOKEN``
    pair that the session environment also carries. Passing the wrong pair makes
    the server exit on startup, and Claude Code reports only
    ``CONNECTION_CLOSED``, which says nothing about why.
    """
    vars_ = {"BRAINBOX_URL": hub_url, "BRAINBOX_TOKEN_ID": token}
    if profile:
        vars_["CL_WORKSPACE_PROFILE"] = profile
    return {
        "type": "stdio",
        "command": BRAINBOX_MCP_BIN,
        "args": [],
        "env": vars_,
    }


def build_servers(src: dict) -> dict:
    """The MCP entries this session can actually use, from its environment."""
    servers: dict = {}

    gw_url, gw_token = src.get("PHANTOM_GATEWAY_URL"), src.get("PHANTOM_GATEWAY_TOKEN")
    if gw_url and gw_token:
        servers["phantom-gateway"] = gateway_entry(gw_url, gw_token)

    # Accept either spelling from the session environment, but emit the names
    # the guest server actually reads (see brainbox_entry).
    hub = (
        src.get("BRAINBOX_URL")
        or src.get("BRAINBOX_HUB_URL_PUBLIC")
        or src.get("BRAINBOX_HUB_URL")
    )
    hub_token = src.get("BRAINBOX_TOKEN_ID") or src.get("BRAINBOX_TOKEN")
    if hub and hub_token:
        servers["brainbox"] = brainbox_entry(
            hub,
            hub_token,
            src.get("CL_WORKSPACE_PROFILE") or src.get("BRAINBOX_PROFILE", ""),
        )

    return servers


def materialize(path: pathlib.Path, servers: dict) -> None:
    """Merge *servers* into *path*, preserving everything else in the document.

    The same file holds oauthAccount and the profile's other MCP servers, so
    this merges rather than replaces, and refuses outright on a config it
    cannot parse.
    """
    if not servers:
        raise ValueError("no MCP server inputs present in the environment")

    doc: object = {}
    if path.exists():
        # A malformed file is NOT overwritten — see the module docstring.
        doc = json.loads(path.read_text())
    if not isinstance(doc, dict):
        raise ValueError(f"{path} is not a JSON object")

    existing = doc.setdefault("mcpServers", {})
    if not isinstance(existing, dict):
        raise ValueError(f"{path}: mcpServers is not an object")
    existing.update(servers)

    # Temp file + rename, so an interrupted write cannot leave Claude Code a
    # truncated config. 0600 because the file now carries live bearers.
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".claude.json.")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(doc, fh, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main() -> int:
    target = os.environ.get("CLAUDE_JSON", "")
    if not target:
        print("materialize-mcp: CLAUDE_JSON is not set", file=sys.stderr)
        return 2

    servers = build_servers(os.environ)
    if not servers:
        print(
            "materialize-mcp: no MCP inputs in the environment — "
            "writing nothing (gateway and hub tools will be unavailable)",
            file=sys.stderr,
        )
        return 3

    try:
        materialize(pathlib.Path(target), servers)
    except Exception as exc:  # noqa: BLE001 — must never include a token
        print(f"materialize-mcp: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print("materialize-mcp: wrote " + ", ".join(sorted(servers)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Write the `phantom-gateway` MCP entry into the guest's Claude config, with
REAL values rather than ``${VAR}`` references.

Why this exists: the profile image bakes the entry as

    {"type": "http",
     "url": "${PHANTOM_GATEWAY_URL}",
     "headers": {"Authorization": "Bearer ${PHANTOM_GATEWAY_TOKEN}"}}

on the assumption that Claude Code expands both references when it connects. It
expands the **url** and **not** the headers, so the Authorization header went
out as that literal string, the gateway answered ``401 invalid_token``, and the
container silently lost every gateway tool — including the ``channel_*``
conversation tools. Nothing in the guest said why; the only clue was an
``AUTH_HEADER_REJECTED`` line in Claude Code's MCP status.

So the substitution happens here instead, at container start, where both
variables are in scope. This runs in every container (local, runner-dispatched
and VM alike), which matters because a runner-hosted guest is on another host:
the daemon cannot bind-mount a generated .mcp.json into it and never runs
``configure()`` for it either.

Contract:
  CLAUDE_JSON            path to the config to patch (required)
  PHANTOM_GATEWAY_URL    the gateway endpoint (required, non-empty)
  PHANTOM_GATEWAY_TOKEN  the session's bearer (required, non-empty)

Values arrive through the environment, never argv, so the token cannot show up
in ``ps`` output. Exit 0 on success, non-zero without writing anything if the
inputs are missing or the existing config is unusable — a half-written
~/.claude.json would cost the guest its oauth state, which is a far worse
failure than no gateway.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile


def materialize(path: pathlib.Path, url: str, token: str) -> None:
    """Patch *path* so phantom-gateway carries *url* and *token* literally.

    Everything else in the document is preserved: it is the same file that
    holds oauthAccount and the profile's other MCP servers.
    """
    if not url or not token:
        raise ValueError("PHANTOM_GATEWAY_URL and PHANTOM_GATEWAY_TOKEN must both be set")

    doc: object = {}
    if path.exists():
        # A malformed file is NOT overwritten — see the module docstring.
        doc = json.loads(path.read_text())
    if not isinstance(doc, dict):
        raise ValueError(f"{path} is not a JSON object")

    servers = doc.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError(f"{path}: mcpServers is not an object")
    servers["phantom-gateway"] = {
        "type": "http",
        "url": url,
        "headers": {"Authorization": f"Bearer {token}"},
    }

    # Temp file + rename, so an interrupted write cannot leave Claude Code a
    # truncated config. 0600 because the file now carries a live bearer.
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
        print("materialize-gateway-mcp: CLAUDE_JSON is not set", file=sys.stderr)
        return 2
    try:
        materialize(
            pathlib.Path(target),
            os.environ.get("PHANTOM_GATEWAY_URL", ""),
            os.environ.get("PHANTOM_GATEWAY_TOKEN", ""),
        )
    except Exception as exc:  # noqa: BLE001 — the message must never include the token
        print(f"materialize-gateway-mcp: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

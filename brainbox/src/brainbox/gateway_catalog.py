"""MCP gateway — catalog → ServerSpec resolution (ADR-002, phase 2d).

Reads the curated MCP server catalog (reflex's ``mcp-catalog.json``) and turns
the operator-allowlisted entries into ``ServerSpec``s the gateway can spawn.

Catalog entry shape: ``{definition: {command, args, env}, requires, ...}``. The
definition's env may contain ``"${VAR}"`` placeholders — those are provided per
profile by the encrypted env store (injected wholesale at spawn by
``GatewayPool``), so only LITERAL env values are kept here as the spec's
``base_env``.

Which servers are exposed is an operator allowlist (``CL_GATEWAY__SERVERS``)
for this iteration; a DB-backed, app-editable per-profile registry is issue
#152. The catalog stays a file (see ADR-002 + #152).
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import settings
from .gateway_pool import ServerSpec
from .log import get_logger

log = get_logger()


def _spec(name: str, definition: dict) -> ServerSpec | None:
    command = definition.get("command")
    if not command:
        return None
    base_env = {
        k: v
        for k, v in (definition.get("env") or {}).items()
        if isinstance(v, str) and "${" not in v  # literal only; ${VAR} comes from the profile env
    }
    return ServerSpec(
        name=name,
        command=command,
        args=list(definition.get("args") or []),
        base_env=base_env,
    )


def load_catalog_specs(
    allowlist: list[str] | None = None, *, path: str | None = None
) -> list[ServerSpec]:
    """Resolve catalog entries → ServerSpecs.

    ``allowlist``: which server names to include. ``None`` = all; ``[]`` = none
    (the default, via ``settings.gateway.servers``). ``path`` overrides the
    configured catalog path (for tests).
    """
    cat_path = path or settings.gateway.catalog_path
    if not cat_path:
        return []
    p = Path(cat_path)
    if not p.exists():
        log.warning("gateway_catalog.missing", metadata={"path": str(p)})
        return []
    try:
        data = json.loads(p.read_text())
    except (ValueError, OSError) as exc:
        log.warning("gateway_catalog.parse_failed", metadata={"path": str(p), "reason": str(exc)})
        return []

    servers = data.get("servers", {}) if isinstance(data, dict) else {}
    allow = None if allowlist is None else set(allowlist)
    out: list[ServerSpec] = []
    for name, entry in servers.items():
        if allow is not None and name not in allow:
            continue
        spec = _spec(name, (entry or {}).get("definition") or {})
        if spec is None:
            log.warning("gateway_catalog.skip_no_command", metadata={"server": name})
            continue
        out.append(spec)
    return out

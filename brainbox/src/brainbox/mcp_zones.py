"""MCP server → trust zone derivation (declarative orchestration).

Classifies a gateway catalog server into a trust zone from **where it sends
data**. A server's destination usually lives in its resolved env (a profile's
gateway secrets), e.g. ``JIRA_URL``, ``GITHUB_HOST``, ``GRAFANA_URL``. Each such
destination is classified via the profile's trust map; the server's effective
zone is the **least-trusted (max)** among them — one leaky destination taints
the whole server.

Two fallbacks for servers whose destination isn't in an env var:
- a small **curated hint** map for well-known servers (phantom-brain/git =
  local; slack = public; github = vendor default; markitdown/playwright =
  public because they can fetch arbitrary URLs at *runtime* — conditional
  egress, treated conservatively);
- otherwise **PUBLIC** (fail-safe — an unclassifiable server may egress).

Env-derived destinations always take precedence over the curated hint (a GHES
``GITHUB_HOST`` in the profile env reclassifies ``github`` from the vendor hint
to infra). This module is pure — callers supply the resolved env + trust map.
"""

from __future__ import annotations

from .trust_zones import TrustMap, TrustZone, host_of

# Env keys whose value is a destination even without an http(s):// prefix.
_URL_KEY_SUFFIXES = ("_URL", "_URI", "_HOST", "_ENDPOINT", "_BASE", "_BASE_URL", "_SERVER", "_ADDR")

# Fallback zones for servers with no destination in env. Env-derived always wins.
# markitdown/playwright/web-fetch can reach arbitrary URLs at runtime
# (conditional egress) — conservatively PUBLIC until per-call classification.
CURATED_SERVER_ZONE_HINTS: dict[str, TrustZone] = {
    "phantom-brain": TrustZone.LOCAL,
    "mcp-brain": TrustZone.LOCAL,
    "brain": TrustZone.LOCAL,
    "git": TrustZone.LOCAL,
    "filesystem": TrustZone.LOCAL,
    "github": TrustZone.VENDOR,          # default; a GHES host in env → infra
    "markitdown": TrustZone.PUBLIC,      # conditional egress (can fetch http)
    "playwright": TrustZone.PUBLIC,      # browses arbitrary sites
    "slack": TrustZone.PUBLIC,
    "atlassian": TrustZone.PUBLIC,       # default; a self-hosted URL in env → infra
    "grafana": TrustZone.PUBLIC,
    "cloudflare-dns": TrustZone.PUBLIC,
    "new-relic": TrustZone.PUBLIC,
    "argocd": TrustZone.PUBLIC,
}


def _is_destination(key: str, value: str) -> bool:
    """True if an env pair names a network destination (URL/host), guarding
    against non-URL values like ``LOG_LEVEL=info``."""
    if not value:
        return False
    if value.strip().startswith(("http://", "https://")):
        return True
    ku = key.upper()
    return any(ku.endswith(s) for s in _URL_KEY_SUFFIXES)


def destinations_from_env(env: dict[str, str]) -> list[str]:
    """Destination values found among a server's resolved env pairs."""
    return [v for k, v in (env or {}).items() if _is_destination(k, v)]


def server_zone(server_name: str, env: dict[str, str], trust_map: TrustMap) -> TrustZone:
    """Effective trust zone of a server for a profile.

    Env-derived destinations (least-trusted wins) take precedence; else the
    curated hint; else PUBLIC (fail-safe).
    """
    dests = [d for d in destinations_from_env(env) if host_of(d)]
    if dests:
        return max((trust_map.zone_of_target(d) for d in dests), key=int)
    return CURATED_SERVER_ZONE_HINTS.get(server_name, TrustZone.PUBLIC)

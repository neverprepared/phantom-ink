"""Step planner — resolve a job step to an inspectable, compliant plan.

The composition capstone of declarative orchestration. Given a profile and a
step's ``Requirement`` (residency ceiling + capabilities), produces a
``StepPlan``:

- the chosen **provider** (fail-closed — ``None`` blocks the step),
- the **eligible tools** (gateway servers whose trust zone ≤ ceiling) and the
  **excluded** ones (with their zone, for inspectability),

by composing the provider catalog, per-server zone derivation, and the profile's
trust map. This is what an app "resolved plan" view and the gateway-scoping path
call. Enforcement of tool scope at the gateway is a following slice.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import gateway_catalog, gateway_secrets, mcp_zones, provider_catalog, store, trust
from .residency_resolver import Requirement, Resource
from .trust_zones import TrustZone, within_ceiling


def _profile_gateway_env(profile: str) -> dict[str, str]:
    try:
        if gateway_secrets.is_unlocked():
            return gateway_secrets.get_profile_env(profile)
    except gateway_secrets.GatewaySecretsError:
        pass
    return {}


def mcp_zones_for_profile(profile: str, *, enabled_only: bool = True) -> dict[str, TrustZone]:
    """Trust zone of each gateway server for a profile.

    Each server is classified by *its own* destinations only: its catalog env
    definition is resolved against the profile's gateway env, then run through
    ``mcp_zones.server_zone``.
    """
    tmap = trust.map_for_profile(profile)
    prof_env = _profile_gateway_env(profile)
    if enabled_only:
        names = store.enabled_gateway_server_names()
    else:
        names = [s["name"] for s in gateway_catalog.list_catalog_servers()]
    out: dict[str, TrustZone] = {}
    for name in names:
        env_def = gateway_catalog.server_env_def(name)
        resolved = mcp_zones.resolve_env_def(env_def, prof_env)
        out[name] = mcp_zones.server_zone(name, resolved, tmap)
    return out


@dataclass(frozen=True)
class ProfileServerState:
    """One gateway server's include/exclude state for a profile."""

    name: str
    zone: TrustZone
    default_enabled: bool          # from the residency resolution (zone <= ceiling)
    override: bool | None          # the user's manual on/off, or None if unset
    effective: bool                # what actually applies (override else default)


def profile_server_states(profile: str) -> list[ProfileServerState]:
    """Per-server include/exclude for a profile: resolution default + user override.

    The residency resolution seeds each server's default (its zone ≤ the
    profile's ceiling). A manual override (the user's toggle) wins when present.
    """
    from .trust_zones import within_ceiling

    zones = mcp_zones_for_profile(profile)
    ceiling = trust.ceiling_for_profile(profile)
    overrides = store.list_profile_server_overrides(profile)
    out: list[ProfileServerState] = []
    for name, zone in sorted(zones.items()):
        default_enabled = within_ceiling(zone, ceiling)
        ov = overrides.get(name)
        out.append(ProfileServerState(
            name=name, zone=zone, default_enabled=default_enabled,
            override=ov, effective=(ov if ov is not None else default_enabled),
        ))
    return out


def effective_enabled_servers(profile: str) -> set[str]:
    """The set of gateway servers effectively available to a profile."""
    return {s.name for s in profile_server_states(profile) if s.effective}


@dataclass(frozen=True)
class StepPlan:
    ceiling: TrustZone
    provider: Resource | None                             # None ⇒ blocked
    provider_reason: str
    eligible_tools: tuple[str, ...]                        # servers with zone ≤ ceiling
    excluded_tools: tuple[tuple[str, TrustZone], ...]      # (name, zone) above ceiling
    blocked: bool
    reason: str


def plan_step(profile: str, req: Requirement) -> StepPlan:
    """Resolve a step's requirement into an inspectable plan for a profile.

    Blocked iff no compliant **provider** exists (a provider is mandatory).
    An empty tool set is not a block — a step may need no tools.
    """
    pres = provider_catalog.resolve_provider(profile, req)
    zones = mcp_zones_for_profile(profile)
    eligible = tuple(sorted(n for n, z in zones.items() if within_ceiling(z, req.ceiling)))
    excluded = tuple(sorted(((n, z) for n, z in zones.items() if not within_ceiling(z, req.ceiling)),
                            key=lambda t: t[0]))
    blocked = pres.blocked
    reason = (
        pres.reason
        if blocked
        else f"provider={pres.resource.name} ({pres.resource.zone.name}); "
        f"{len(eligible)} tool(s) within {req.ceiling.name}"
    )
    return StepPlan(
        ceiling=req.ceiling,
        provider=pres.resource,
        provider_reason=pres.reason,
        eligible_tools=eligible,
        excluded_tools=excluded,
        blocked=blocked,
        reason=reason,
    )

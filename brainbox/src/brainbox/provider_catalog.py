"""Provider catalog — classify LLM providers into zoned, capability-tagged Resources.

Turns the fixed set of providers (claude / ollama / codex) into
``residency_resolver.Resource``s: each provider's **trust zone** is derived from
its configured destination via the profile's trust map, and each carries a small
**curated** capability tag set. ``resolve_provider`` then picks a compliant
provider for a step's requirement (fail-closed).

Capability taxonomy is deliberately small (coding, reasoning, cheap, vision) —
keep it curated; avoid tag sprawl.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import trust
from .config import settings
from .residency_resolver import Requirement, Resolution, Resource, resolve


@dataclass(frozen=True)
class ProviderDescriptor:
    """A provider's classification inputs: a destination (for zone derivation)
    and its curated capability tags."""

    name: str
    destination: str
    capabilities: frozenset[str] = field(default_factory=frozenset)


def default_providers() -> list[ProviderDescriptor]:
    """The built-in providers with curated capabilities. Destinations come from
    ``settings.orchestration`` so operators can point them at their own hosts."""
    o = settings.orchestration
    return [
        ProviderDescriptor("ollama", o.ollama_url, frozenset({"coding", "reasoning", "cheap"})),
        ProviderDescriptor("claude", o.claude_url, frozenset({"coding", "reasoning", "vision"})),
        ProviderDescriptor("codex", o.codex_url, frozenset({"coding", "reasoning"})),
    ]


def provider_resources(profile: str) -> list[Resource]:
    """Classify each provider into a zoned Resource using ``profile``'s trust map."""
    tmap = trust.map_for_profile(profile)
    return [
        Resource(p.name, tmap.zone_of_target(p.destination), p.capabilities)
        for p in default_providers()
    ]


def resolve_provider(profile: str, req: Requirement) -> Resolution:
    """Pick a compliant provider for a step's requirement (fail-closed)."""
    return resolve(provider_resources(profile), req)

"""Per-profile trust configuration — compose a TrustMap + default ceiling.

Bridges the pure ``trust_zones`` primitives to persisted, per-profile config
(``store.trust_rules`` + ``store.trust_profile_config``). A profile's effective
trust map = the safe base (``default_trust_map`` — loopback LOCAL, else PUBLIC)
with the profile's own rules layered on top (profile rules win on conflict, and
override the base ``*`` default).

The default residency ceiling for a step that omits one resolves in order:
per-profile config → global ``settings.orchestration.default_ceiling``.
"""

from __future__ import annotations

from . import store
from .config import settings
from .trust_zones import TrustMap, TrustRule, TrustZone, default_trust_map


def map_for_profile(profile: str) -> TrustMap:
    """Build the effective trust map for a profile.

    Base safe defaults first, then the profile's DB rules appended — so a
    profile rule at equal-or-higher specificity wins (last-wins tiebreak in
    ``TrustMap``), and its rules override the base ``*`` catch-all.
    """
    rules: list[TrustRule] = default_trust_map().rules
    for row in store.list_trust_rules(profile):
        try:
            rules.append(TrustRule(row["pattern"], TrustZone.parse(row["zone"])))
        except ValueError:
            continue  # skip a malformed stored zone rather than fail the whole map
    return TrustMap(rules)


def ceiling_for_profile(profile: str) -> TrustZone:
    """The default residency ceiling for a step that doesn't declare one:
    per-profile config, else the global fallback."""
    configured = store.get_profile_default_ceiling(profile)
    source = configured or settings.orchestration.default_ceiling
    try:
        return TrustZone.parse(source)
    except ValueError:
        return TrustZone.PUBLIC  # fail-safe if misconfigured

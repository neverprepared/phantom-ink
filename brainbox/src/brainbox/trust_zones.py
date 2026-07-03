"""Trust zones — data-residency classification for declarative orchestration.

Foundational layer for tag-based agent orchestration (see the design note
``BUILDME/declarative-tagged-orchestration.md``). Every destination an agent
step might touch — the model's ``baseURL``, an MCP server's target host, a log
sink endpoint — is classified into an ordered **trust zone**:

    LOCAL < INFRA < VENDOR < PUBLIC   (most trusted → least trusted)

A step declares a **residency ceiling** (the least-trusted zone its data may
reach). A resource is eligible iff ``zone <= ceiling`` — enforced **fail-closed**
(the default for any unknown destination is PUBLIC, the least-trusted zone, so a
misconfiguration blocks rather than leaks).

The classification is *derived*, never hand-asserted: an operator declares a
**trust map** of ``{destination pattern -> zone}`` (their approved-processor
list), and each resource's zone is looked up from its destination. This is the
only user-adjustable input; you cannot relabel ``api.anthropic.com`` as LOCAL —
you can only place it into a zone in the map, an explicit governance decision.

This module is pure (no I/O, no config coupling). Config wiring and resource
classification build on top of it.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from enum import IntEnum
from urllib.parse import urlparse

# Schemes whose destination never leaves the machine.
_LOCAL_SCHEMES = {"unix", "file"}


class TrustZone(IntEnum):
    """Ordered trust zones. Lower value = more trusted.

    The order matters: eligibility is ``resource_zone <= ceiling``. PUBLIC is
    the least-trusted, fail-safe default for anything unclassified.
    """

    LOCAL = 0   # never leaves the machine (localhost, unix socket, on-box file)
    INFRA = 1   # your self-hosted infrastructure (LAN, self-hosted services, GHES)
    VENDOR = 2  # approved third-party processors (under agreement / DPA)
    PUBLIC = 3  # open internet / unapproved — fail-safe default

    @classmethod
    def parse(cls, name: str) -> "TrustZone":
        """Parse a case-insensitive zone name; raises ValueError if unknown."""
        try:
            return cls[name.strip().upper()]
        except KeyError as exc:
            raise ValueError(f"unknown trust zone {name!r}") from exc


def within_ceiling(zone: TrustZone, ceiling: TrustZone) -> bool:
    """True iff a resource in ``zone`` may be used by a step whose residency
    ceiling is ``ceiling`` (i.e. the resource is at least as trusted)."""
    return zone <= ceiling


def host_of(target: str) -> str:
    """Extract a lowercased hostname from a destination string.

    Accepts full URLs (``http://localhost:11434/v1``), ``host:port``, or a bare
    host. ``unix:``/``file:`` destinations resolve to ``localhost`` (they never
    leave the machine). Returns ``""`` when no host can be determined — callers
    treat empty as PUBLIC (fail-safe).
    """
    if not target:
        return ""
    t = target.strip()

    scheme = ""
    if "://" in t:
        scheme = t.split("://", 1)[0].lower()
    elif ":" in t and t.split(":", 1)[0].lower() in _LOCAL_SCHEMES:
        scheme = t.split(":", 1)[0].lower()
    if scheme in _LOCAL_SCHEMES:
        return "localhost"

    if "://" not in t:
        t = "//" + t  # let urlparse read a bare host[:port] as a netloc
    return (urlparse(t).hostname or "").lower()


def _specificity(pattern: str) -> tuple[int, int]:
    """Rank a pattern's specificity so the most-specific match wins.

    Exact patterns (no glob) always beat globs; among globs, more literal
    characters = more specific; ``*`` (catch-all) is least specific.
    """
    p = pattern.lower()
    if "*" not in p and "?" not in p:
        return (2, len(p))          # exact match — most specific
    if p == "*":
        return (0, 0)               # catch-all — least specific
    return (1, len(p.replace("*", "").replace("?", "")))  # glob by literal length


@dataclass(frozen=True)
class TrustRule:
    """One ``pattern -> zone`` entry. ``pattern`` is an fnmatch glob over the
    hostname (e.g. ``localhost``, ``*.neverprepared.com``, ``192.168.*``)."""

    pattern: str
    zone: TrustZone


class TrustMap:
    """An ordered set of ``TrustRule``s resolving a host to its trust zone.

    Most-specific match wins; on equal specificity the last-defined rule wins
    (so later config overrides earlier). Any host that matches nothing — or is
    empty — resolves to PUBLIC (fail-safe).
    """

    def __init__(self, rules: list[TrustRule] | None = None) -> None:
        self._rules: list[TrustRule] = list(rules or [])

    @property
    def rules(self) -> list[TrustRule]:
        """A copy of the rules, in definition order (for composition/inspection)."""
        return list(self._rules)

    def zone_of(self, host: str) -> TrustZone:
        """Classify a hostname. Empty/unknown → PUBLIC."""
        host = (host or "").lower()
        if not host:
            return TrustZone.PUBLIC
        best_zone: TrustZone | None = None
        best_score: tuple[int, int] | None = None
        for rule in self._rules:
            if fnmatch.fnmatch(host, rule.pattern.lower()):
                score = _specificity(rule.pattern)
                if best_score is None or score >= best_score:
                    best_score = score
                    best_zone = rule.zone
        return best_zone if best_zone is not None else TrustZone.PUBLIC

    def zone_of_target(self, target: str) -> TrustZone:
        """Classify a destination string (URL / host:port / bare host)."""
        return self.zone_of(host_of(target))

    def eligible(self, target: str, ceiling: TrustZone) -> bool:
        """True iff ``target``'s zone is at/below the residency ceiling."""
        return within_ceiling(self.zone_of_target(target), ceiling)


def default_trust_map() -> TrustMap:
    """A minimal, safe starting map: loopback is LOCAL, everything else PUBLIC.

    Operators extend this with their INFRA (self-hosted / LAN) and VENDOR
    (approved third parties) rules — those are deliberately NOT assumed here, so
    the out-of-the-box behavior never over-trusts a destination.
    """
    return TrustMap(
        [
            TrustRule("localhost", TrustZone.LOCAL),
            TrustRule("127.0.0.1", TrustZone.LOCAL),
            TrustRule("::1", TrustZone.LOCAL),
            TrustRule("*", TrustZone.PUBLIC),
        ]
    )

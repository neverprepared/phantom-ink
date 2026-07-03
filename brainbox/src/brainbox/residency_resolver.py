"""Residency resolver — pick a compliant resource for an orchestration step.

The core of declarative tag-based orchestration (design note:
``BUILDME/declarative-tagged-orchestration.md``). A step declares a
**requirement** (a residency ceiling + hard/soft capability tags); this resolves
it against a set of candidate **resources** (each already classified into a
trust zone — see ``trust_zones`` — and carrying capability tags).

Same shape as ``RunnerRegistry.select_runner``: **filter** to eligible, then
**rank**. Two invariants:

- **Fail-closed.** A candidate is eligible only if its zone is at/below the
  ceiling AND it has every *required* (hard) capability. If nothing is
  eligible, resolution returns ``None`` — the caller **blocks the step**; it
  never falls back to a less-trusted resource.
- **Conservative tiebreak.** Among eligible candidates, rank by most *preferred*
  (soft) capabilities matched, then by **most-trusted zone** (prefer keeping
  data closer when preferences are equal), then by name for determinism.

Resource-agnostic: the same resolver serves providers (model ``baseURL``), MCP
servers (target host), and log sinks (endpoint). Deriving ``Resource``s from
real config is a separate wiring layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .trust_zones import TrustZone, within_ceiling


@dataclass(frozen=True)
class Resource:
    """A candidate resource, already classified into a trust zone."""

    name: str
    zone: TrustZone
    capabilities: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class Requirement:
    """A step's residency ceiling plus hard/soft capability tags."""

    ceiling: TrustZone
    requires: frozenset[str] = field(default_factory=frozenset)   # hard — must have all
    prefers: tuple[str, ...] = ()                                  # soft — ranked, best-effort


@dataclass(frozen=True)
class Resolution:
    """Outcome of resolving a requirement against candidates.

    ``resource is None`` means **blocked** (nothing eligible — fail-closed).
    ``eligible`` is every candidate that passed the hard filter (for
    inspectability); ``reason`` explains the outcome.
    """

    resource: Resource | None
    eligible: tuple[Resource, ...]
    reason: str

    @property
    def blocked(self) -> bool:
        return self.resource is None


def _is_eligible(resource: Resource, req: Requirement) -> bool:
    return within_ceiling(resource.zone, req.ceiling) and req.requires <= resource.capabilities


def _rank_key(req: Requirement):
    def key(r: Resource) -> tuple:
        pref_matches = sum(1 for p in req.prefers if p in r.capabilities)
        # -pref_matches: more preferred caps first. r.zone: most-trusted first
        # (conservative). r.name: deterministic final tiebreak.
        return (-pref_matches, int(r.zone), r.name)

    return key


def resolve(candidates: list[Resource], req: Requirement) -> Resolution:
    """Resolve a requirement to a single compliant resource, or block.

    Filters to eligible (zone <= ceiling AND all hard capabilities present),
    then ranks by preferred capabilities, trust, and name. Returns a
    ``Resolution`` whose ``resource`` is ``None`` iff nothing is eligible.
    """
    eligible = tuple(c for c in candidates if _is_eligible(c, req))
    if not eligible:
        missing = ", ".join(sorted(req.requires)) or "(none)"
        return Resolution(
            resource=None,
            eligible=(),
            reason=(
                f"no candidate within ceiling {req.ceiling.name} with required "
                f"capabilities [{missing}] — blocked (fail-closed)"
            ),
        )
    best = min(eligible, key=_rank_key(req))
    return Resolution(
        resource=best,
        eligible=eligible,
        reason=f"selected {best.name} (zone {best.zone.name}) from {len(eligible)} eligible",
    )

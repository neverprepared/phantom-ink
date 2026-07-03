"""Step spec — compile a declarative job step into a concrete binding.

The manifest surface of declarative orchestration. A job step (a loop node, an
A2A message, a playbook step) carries a small **declarative spec**::

    residency: infra          # trust-zone ceiling (optional → profile default)
    requires: [reasoning]     # hard capabilities
    prefers:  [cheap]         # soft capabilities

``compile_step`` resolves that against a profile — choosing a compliant
provider and the tools within the ceiling — and returns a ``ResolvedStep`` (the
provider becomes the task's ``ModelTarget``; the ceiling becomes the session's
gateway-token ceiling). A step that can't be satisfied raises
``StepValidationError`` at **compile time** — a contradiction (e.g. a private
ceiling that needs a public-only tool, or no capable provider within the
ceiling) fails before dispatch, not silently at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import step_planner, trust
from .residency_resolver import Requirement
from .step_planner import StepPlan
from .trust_zones import TrustZone


class StepValidationError(Exception):
    """A declarative step cannot be satisfied for a profile (compile-time)."""


@dataclass(frozen=True)
class StepSpec:
    """The declarative tags on a job step."""

    residency: str | None = None            # zone name; None → the profile's default ceiling
    requires: tuple[str, ...] = ()          # hard capabilities
    prefers: tuple[str, ...] = ()           # soft capabilities

    @classmethod
    def from_dict(cls, data: dict | None) -> "StepSpec":
        d = data or {}
        req = d.get("requires") or []
        pref = d.get("prefers") or []
        residency = d.get("residency")
        return cls(
            residency=str(residency) if residency else None,
            requires=tuple(str(x) for x in req),
            prefers=tuple(str(x) for x in pref),
        )

    def is_declared(self) -> bool:
        """True if the step carries any orchestration tags at all."""
        return bool(self.residency or self.requires or self.prefers)


@dataclass(frozen=True)
class ResolvedStep:
    """A compiled step: the concrete provider + ceiling + tool surface."""

    provider: str                            # → task ModelTarget.provider
    ceiling: str                             # zone name → gateway-token residency ceiling
    eligible_tools: tuple[str, ...]
    plan: StepPlan = field(repr=False)


def compile_step(profile: str, spec: StepSpec) -> ResolvedStep:
    """Resolve a declarative step for a profile, or raise StepValidationError.

    The residency ceiling comes from the spec, else the profile's default.
    Blocks (no compliant provider within the ceiling) are compile-time errors.
    """
    if spec.residency:
        try:
            ceiling = TrustZone.parse(spec.residency)
        except ValueError as exc:
            raise StepValidationError(str(exc)) from exc
    else:
        ceiling = trust.ceiling_for_profile(profile)

    req = Requirement(ceiling, requires=frozenset(spec.requires), prefers=tuple(spec.prefers))
    plan = step_planner.plan_step(profile, req)
    if plan.blocked:
        raise StepValidationError(plan.reason)
    assert plan.provider is not None  # not blocked ⇒ provider present
    return ResolvedStep(
        provider=plan.provider.name,
        ceiling=ceiling.name.lower(),
        eligible_tools=plan.eligible_tools,
        plan=plan,
    )

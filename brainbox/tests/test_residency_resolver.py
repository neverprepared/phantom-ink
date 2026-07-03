"""Tests for the residency resolver (declarative orchestration)."""

from __future__ import annotations

from brainbox.residency_resolver import Requirement, Resource, resolve
from brainbox.trust_zones import TrustZone


def _providers() -> list[Resource]:
    return [
        Resource("ollama", TrustZone.LOCAL, frozenset({"coding", "reasoning", "cheap"})),
        Resource("claude", TrustZone.VENDOR, frozenset({"coding", "reasoning"})),
        Resource("codex", TrustZone.VENDOR, frozenset({"coding"})),
    ]


class TestFailClosed:
    def test_blocks_when_nothing_within_ceiling(self):
        # LOCAL ceiling, but only vendor providers have 'vision' → block
        cands = [Resource("claude", TrustZone.VENDOR, frozenset({"vision"}))]
        res = resolve(cands, Requirement(TrustZone.LOCAL, requires=frozenset({"vision"})))
        assert res.blocked
        assert res.resource is None
        assert "fail-closed" in res.reason

    def test_blocks_when_hard_capability_missing(self):
        # ollama is within ceiling but lacks 'vision'
        cands = [Resource("ollama", TrustZone.LOCAL, frozenset({"coding"}))]
        res = resolve(cands, Requirement(TrustZone.LOCAL, requires=frozenset({"vision"})))
        assert res.blocked

    def test_never_falls_back_below_ceiling(self):
        # INFRA ceiling: the only coding provider is VENDOR → must block, not use it
        cands = [Resource("claude", TrustZone.VENDOR, frozenset({"coding"}))]
        res = resolve(cands, Requirement(TrustZone.INFRA, requires=frozenset({"coding"})))
        assert res.blocked


class TestSelection:
    def test_local_ceiling_picks_local_provider(self):
        res = resolve(_providers(), Requirement(TrustZone.LOCAL, requires=frozenset({"coding"})))
        assert res.resource is not None
        assert res.resource.name == "ollama"  # only one within a LOCAL ceiling

    def test_prefers_soft_capability_match(self):
        # VENDOR ceiling admits all three; prefer 'cheap' → ollama (only cheap one)
        res = resolve(
            _providers(),
            Requirement(TrustZone.VENDOR, requires=frozenset({"coding"}), prefers=("cheap",)),
        )
        assert res.resource.name == "ollama"

    def test_conservative_tiebreak_prefers_more_trusted_zone(self):
        # Two equally-capable providers, no soft prefs → pick the more-trusted zone
        cands = [
            Resource("cloudA", TrustZone.VENDOR, frozenset({"coding"})),
            Resource("infraB", TrustZone.INFRA, frozenset({"coding"})),
        ]
        res = resolve(cands, Requirement(TrustZone.PUBLIC, requires=frozenset({"coding"})))
        assert res.resource.name == "infraB"

    def test_deterministic_name_tiebreak(self):
        cands = [
            Resource("zeta", TrustZone.VENDOR, frozenset({"coding"})),
            Resource("alpha", TrustZone.VENDOR, frozenset({"coding"})),
        ]
        res = resolve(cands, Requirement(TrustZone.VENDOR, requires=frozenset({"coding"})))
        assert res.resource.name == "alpha"

    def test_eligible_set_reported_for_inspectability(self):
        res = resolve(_providers(), Requirement(TrustZone.VENDOR, requires=frozenset({"coding"})))
        assert {r.name for r in res.eligible} == {"ollama", "claude", "codex"}
        assert not res.blocked


class TestEmptyRequirements:
    def test_no_hard_caps_any_eligible_by_zone(self):
        res = resolve(_providers(), Requirement(TrustZone.LOCAL))
        assert res.resource.name == "ollama"

    def test_no_candidates_blocks(self):
        res = resolve([], Requirement(TrustZone.PUBLIC))
        assert res.blocked

"""Tests for the trust-zone data-residency layer (declarative orchestration)."""

from __future__ import annotations

import pytest

from brainbox.trust_zones import (
    TrustMap,
    TrustRule,
    TrustZone,
    default_trust_map,
    host_of,
    within_ceiling,
)


class TestZoneOrdering:
    def test_ordered_most_to_least_trusted(self):
        assert TrustZone.LOCAL < TrustZone.INFRA < TrustZone.VENDOR < TrustZone.PUBLIC

    def test_parse_case_insensitive(self):
        assert TrustZone.parse("infra") is TrustZone.INFRA
        assert TrustZone.parse(" Public ") is TrustZone.PUBLIC

    def test_parse_unknown_raises(self):
        with pytest.raises(ValueError):
            TrustZone.parse("trusted")

    def test_within_ceiling_is_fail_closed_semantics(self):
        # ceiling INFRA: local + infra allowed; vendor + public blocked
        assert within_ceiling(TrustZone.LOCAL, TrustZone.INFRA)
        assert within_ceiling(TrustZone.INFRA, TrustZone.INFRA)
        assert not within_ceiling(TrustZone.VENDOR, TrustZone.INFRA)
        assert not within_ceiling(TrustZone.PUBLIC, TrustZone.INFRA)


class TestHostOf:
    def test_full_url(self):
        assert host_of("http://localhost:11434/v1") == "localhost"
        assert host_of("https://api.anthropic.com/v1/messages") == "api.anthropic.com"

    def test_host_port_and_bare_host(self):
        assert host_of("192.168.1.5:9999") == "192.168.1.5"
        assert host_of("github.com") == "github.com"

    def test_lowercases(self):
        assert host_of("HTTPS://GitHub.COM") == "github.com"

    def test_local_schemes_resolve_local(self):
        assert host_of("unix:/var/run/x.sock") == "localhost"
        assert host_of("file:///home/dev/notes.md") == "localhost"

    def test_empty(self):
        assert host_of("") == ""
        assert host_of(None) == ""  # type: ignore[arg-type]


class TestTrustMapResolution:
    def test_default_map_loopback_local_else_public(self):
        m = default_trust_map()
        assert m.zone_of("localhost") is TrustZone.LOCAL
        assert m.zone_of("127.0.0.1") is TrustZone.LOCAL
        assert m.zone_of("api.anthropic.com") is TrustZone.PUBLIC  # fail-safe default

    def test_empty_host_is_public(self):
        assert default_trust_map().zone_of("") is TrustZone.PUBLIC

    def test_exact_beats_glob(self):
        m = TrustMap([
            TrustRule("*.neverprepared.com", TrustZone.INFRA),
            TrustRule("public.neverprepared.com", TrustZone.PUBLIC),  # more specific
        ])
        assert m.zone_of("app.neverprepared.com") is TrustZone.INFRA
        assert m.zone_of("public.neverprepared.com") is TrustZone.PUBLIC

    def test_more_specific_glob_wins(self):
        m = TrustMap([
            TrustRule("*", TrustZone.PUBLIC),
            TrustRule("*.internal", TrustZone.INFRA),
        ])
        assert m.zone_of("db.internal") is TrustZone.INFRA
        assert m.zone_of("random.com") is TrustZone.PUBLIC

    def test_ip_prefix_glob(self):
        m = TrustMap([TrustRule("192.168.*", TrustZone.INFRA), TrustRule("*", TrustZone.PUBLIC)])
        assert m.zone_of("192.168.87.200") is TrustZone.INFRA
        assert m.zone_of("8.8.8.8") is TrustZone.PUBLIC

    def test_equal_specificity_last_wins(self):
        m = TrustMap([
            TrustRule("*.co", TrustZone.INFRA),
            TrustRule("*.co", TrustZone.VENDOR),  # same specificity, later overrides
        ])
        assert m.zone_of("x.co") is TrustZone.VENDOR


class TestRealWorldMap:
    """The scenario from the design note: enterprise GitHub + self-hosted infra
    are trusted; consumer destinations are not — none of which the naive
    local/cloud binary could express."""

    def _map(self) -> TrustMap:
        return TrustMap([
            TrustRule("localhost", TrustZone.LOCAL),
            TrustRule("*.neverprepared.com", TrustZone.INFRA),   # self-hosted (logging, ollama, etc.)
            TrustRule("192.168.*", TrustZone.INFRA),
            TrustRule("github.enterprise.internal", TrustZone.INFRA),
            TrustRule("github.com", TrustZone.VENDOR),           # approved w/ enterprise agreement
            TrustRule("*", TrustZone.PUBLIC),
        ])

    def test_self_hosted_is_infra_not_public(self):
        m = self._map()
        assert m.zone_of_target("https://langfuse.neverprepared.com") is TrustZone.INFRA
        assert m.zone_of_target("http://ollama.neverprepared.com:11435") is TrustZone.INFRA

    def test_enterprise_github_trusted_consumer_slack_not(self):
        m = self._map()
        assert m.zone_of_target("https://github.enterprise.internal") is TrustZone.INFRA
        assert m.zone_of_target("https://github.com/org/repo") is TrustZone.VENDOR
        assert m.zone_of_target("https://slack.com/api") is TrustZone.PUBLIC

    def test_eligibility_fail_closed(self):
        m = self._map()
        # An INFRA-ceiling step: self-hosted ok, vendor/public blocked
        assert m.eligible("http://ollama.neverprepared.com", TrustZone.INFRA)
        assert not m.eligible("https://github.com/org/repo", TrustZone.INFRA)
        assert not m.eligible("https://api.anthropic.com", TrustZone.INFRA)
        # A VENDOR-ceiling step admits the approved vendor
        assert m.eligible("https://github.com/org/repo", TrustZone.VENDOR)

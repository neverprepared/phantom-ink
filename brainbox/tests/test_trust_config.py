"""Tests for per-profile trust config (DB rules + default ceiling)."""

from __future__ import annotations

from brainbox import store, trust
from brainbox.config import settings
from brainbox.trust_zones import TrustZone


class TestTrustRulesStore:
    def test_set_list_delete(self):
        store.set_trust_rule("work", "*.corp.internal", "infra")
        store.set_trust_rule("work", "github.com", "vendor")
        rules = {r["pattern"]: r["zone"] for r in store.list_trust_rules("work")}
        assert rules == {"*.corp.internal": "infra", "github.com": "vendor"}
        assert store.delete_trust_rule("work", "github.com") is True
        assert "github.com" not in {r["pattern"] for r in store.list_trust_rules("work")}

    def test_upsert_updates_zone(self):
        store.set_trust_rule("work", "x.com", "vendor")
        store.set_trust_rule("work", "x.com", "public")
        assert store.list_trust_rules("work") == [{"pattern": "x.com", "zone": "public"}]

    def test_rules_are_per_profile(self):
        store.set_trust_rule("a", "only-a.com", "infra")
        assert store.list_trust_rules("b") == []


class TestMapForProfile:
    def test_base_defaults_present(self):
        m = trust.map_for_profile("fresh")
        assert m.zone_of("localhost") is TrustZone.LOCAL      # from base
        assert m.zone_of("random.com") is TrustZone.PUBLIC    # base catch-all

    def test_profile_rules_layer_over_base(self):
        store.set_trust_rule("work", "*.neverprepared.com", "infra")
        store.set_trust_rule("work", "github.com", "vendor")
        m = trust.map_for_profile("work")
        assert m.zone_of_target("https://logs.neverprepared.com") is TrustZone.INFRA
        assert m.zone_of_target("https://github.com/x") is TrustZone.VENDOR
        assert m.zone_of("localhost") is TrustZone.LOCAL      # base still applies

    def test_malformed_stored_zone_skipped(self):
        store.set_trust_rule("work", "bad.com", "not-a-zone")
        m = trust.map_for_profile("work")  # must not raise
        assert m.zone_of("bad.com") is TrustZone.PUBLIC       # falls through to catch-all


class TestCeilingForProfile:
    def test_per_profile_default_wins(self, monkeypatch):
        monkeypatch.setattr(settings.orchestration, "default_ceiling", "public")
        store.set_profile_default_ceiling("work", "infra")
        assert trust.ceiling_for_profile("work") is TrustZone.INFRA

    def test_falls_back_to_global(self, monkeypatch):
        monkeypatch.setattr(settings.orchestration, "default_ceiling", "vendor")
        assert trust.ceiling_for_profile("unconfigured") is TrustZone.VENDOR

    def test_misconfigured_global_is_failsafe_public(self, monkeypatch):
        monkeypatch.setattr(settings.orchestration, "default_ceiling", "garbage")
        assert trust.ceiling_for_profile("unconfigured") is TrustZone.PUBLIC

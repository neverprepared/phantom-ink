"""Tests for provider classification + resolution (declarative orchestration)."""

from __future__ import annotations

import pytest

from brainbox import provider_catalog, store
from brainbox.config import settings
from brainbox.residency_resolver import Requirement
from brainbox.trust_zones import TrustZone


@pytest.fixture(autouse=True)
def _provider_urls(monkeypatch):
    # Deterministic destinations for classification.
    monkeypatch.setattr(settings.orchestration, "ollama_url", "http://localhost:11434")
    monkeypatch.setattr(settings.orchestration, "claude_url", "https://api.anthropic.com")
    monkeypatch.setattr(settings.orchestration, "codex_url", "https://api.openai.com")


class TestClassification:
    def test_default_map_local_ollama_public_cloud(self):
        res = {r.name: r.zone for r in provider_catalog.provider_resources("fresh")}
        assert res["ollama"] is TrustZone.LOCAL     # localhost
        assert res["claude"] is TrustZone.PUBLIC    # cloud, unmapped → fail-safe
        assert res["codex"] is TrustZone.PUBLIC

    def test_profile_rule_reclassifies_a_provider(self):
        # Approve Anthropic as a vendor for 'work' → claude becomes VENDOR
        store.set_trust_rule("work", "api.anthropic.com", "vendor")
        res = {r.name: r.zone for r in provider_catalog.provider_resources("work")}
        assert res["claude"] is TrustZone.VENDOR
        assert res["ollama"] is TrustZone.LOCAL

    def test_remote_ollama_classified_by_map(self, monkeypatch):
        monkeypatch.setattr(settings.orchestration, "ollama_url", "http://ollama.corp.internal:11435")
        store.set_trust_rule("work", "*.corp.internal", "infra")
        res = {r.name: r.zone for r in provider_catalog.provider_resources("work")}
        assert res["ollama"] is TrustZone.INFRA     # remote self-hosted → infra, not local


class TestResolveProvider:
    def test_local_ceiling_only_ollama(self):
        r = provider_catalog.resolve_provider(
            "fresh", Requirement(TrustZone.LOCAL, requires=frozenset({"coding"}))
        )
        assert r.resource.name == "ollama"

    def test_vision_forces_claude_but_blocks_under_local_ceiling(self):
        # Only claude has 'vision'; it's PUBLIC under the default map → LOCAL ceiling blocks
        r = provider_catalog.resolve_provider(
            "fresh", Requirement(TrustZone.LOCAL, requires=frozenset({"vision"}))
        )
        assert r.blocked
        # ...but a PUBLIC ceiling admits it
        r2 = provider_catalog.resolve_provider(
            "fresh", Requirement(TrustZone.PUBLIC, requires=frozenset({"vision"}))
        )
        assert r2.resource.name == "claude"

    def test_prefers_cheap_picks_ollama_at_public_ceiling(self):
        r = provider_catalog.resolve_provider(
            "fresh",
            Requirement(TrustZone.PUBLIC, requires=frozenset({"coding"}), prefers=("cheap",)),
        )
        assert r.resource.name == "ollama"

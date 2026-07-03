"""Tests for MCP server → trust zone derivation (declarative orchestration)."""

from __future__ import annotations

from brainbox.mcp_zones import destinations_from_env, server_zone
from brainbox.trust_zones import TrustMap, TrustRule, TrustZone


def _map() -> TrustMap:
    return TrustMap([
        TrustRule("*.corp.internal", TrustZone.INFRA),
        TrustRule("github.com", TrustZone.VENDOR),
        TrustRule("*", TrustZone.PUBLIC),
    ])


class TestDestinationsFromEnv:
    def test_http_values_and_url_keys(self):
        env = {
            "JIRA_URL": "https://jira.corp.internal",
            "GITHUB_HOST": "github.enterprise.internal",
            "GRAFANA_URL": "https://grafana.corp.internal",
        }
        assert set(destinations_from_env(env)) == set(env.values())

    def test_ignores_non_destination_values(self):
        env = {"LOG_LEVEL": "info", "TIMEOUT": "30", "JIRA_URL": "https://x.com"}
        assert destinations_from_env(env) == ["https://x.com"]

    def test_empty(self):
        assert destinations_from_env({}) == []


class TestServerZone:
    def test_env_derived_zone(self):
        z = server_zone("github", {"GITHUB_HOST": "github.enterprise.internal"},
                        TrustMap([TrustRule("*.internal", TrustZone.INFRA), TrustRule("*", TrustZone.PUBLIC)]))
        assert z is TrustZone.INFRA  # env destination reclassifies below the vendor hint

    def test_least_trusted_destination_wins(self):
        env = {"A_URL": "https://svc.corp.internal", "B_URL": "https://slack.com"}
        assert server_zone("multi", env, _map()) is TrustZone.PUBLIC  # slack taints it

    def test_curated_hint_when_no_env_destination(self):
        assert server_zone("phantom-brain", {}, _map()) is TrustZone.LOCAL
        assert server_zone("git", {"LOG_LEVEL": "debug"}, _map()) is TrustZone.LOCAL  # non-dest env ignored
        assert server_zone("slack", {}, _map()) is TrustZone.PUBLIC

    def test_unknown_server_no_env_is_public_failsafe(self):
        assert server_zone("mystery", {}, _map()) is TrustZone.PUBLIC

    def test_env_overrides_curated_hint(self):
        # github hint is VENDOR, but a self-hosted GITHUB_HOST → INFRA
        z = server_zone("github", {"GITHUB_HOST": "git.corp.internal"}, _map())
        assert z is TrustZone.INFRA

    def test_conditional_egress_server_is_public(self):
        assert server_zone("markitdown", {}, _map()) is TrustZone.PUBLIC
        assert server_zone("playwright", {}, _map()) is TrustZone.PUBLIC

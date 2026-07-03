"""Tests for the declarative-orchestration operator API (trust map + plan preview)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

import brainbox.auth as auth_module
from brainbox.config import settings


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(auth_module, "_api_key", "test-key")
    monkeypatch.setattr(settings.orchestration, "default_ceiling", "public")
    monkeypatch.setattr(settings.orchestration, "ollama_url", "http://localhost:11434")
    monkeypatch.setattr(settings.orchestration, "claude_url", "https://api.anthropic.com")
    monkeypatch.setattr(settings.orchestration, "codex_url", "https://api.openai.com")
    from brainbox.api import app

    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"X-API-Key": "test-key"}
    )


async def test_trust_rule_crud_and_ceiling(client):
    async with client as c:
        # set a rule + a ceiling
        r = await c.put("/api/orchestration/profiles/work/trust/rule",
                        json={"pattern": "*.corp.internal", "zone": "infra"})
        assert r.status_code == 200 and r.json()["zone"] == "infra"
        await c.put("/api/orchestration/profiles/work/trust/ceiling", json={"zone": "infra"})

        got = (await c.get("/api/orchestration/profiles/work/trust")).json()
        assert got["default_ceiling"] == "infra"
        assert got["rules"] == [{"pattern": "*.corp.internal", "zone": "infra"}]

        # delete
        d = await c.delete("/api/orchestration/profiles/work/trust/rule", params={"pattern": "*.corp.internal"})
        assert d.json()["deleted"] is True


async def test_bad_zone_rejected(client):
    async with client as c:
        r = await c.put("/api/orchestration/profiles/work/trust/rule",
                        json={"pattern": "x.com", "zone": "trusted"})
    assert r.status_code == 400


async def test_zones_view(client):
    async with client as c:
        z = (await c.get("/api/orchestration/profiles/fresh/zones")).json()
    provs = {p["name"]: p["zone"] for p in z["providers"]}
    assert provs["ollama"] == "local"        # localhost
    assert provs["claude"] == "public"       # cloud, unmapped


async def test_plan_preview(client):
    async with client as c:
        # LOCAL ceiling + coding → ollama; blocked case with vision
        ok = (await c.post("/api/orchestration/profiles/fresh/plan",
                           json={"ceiling": "local", "requires": ["coding"]})).json()
        assert ok["blocked"] is False and ok["provider"]["name"] == "ollama"

        blocked = (await c.post("/api/orchestration/profiles/fresh/plan",
                                json={"ceiling": "local", "requires": ["vision"]})).json()
    assert blocked["blocked"] is True
    assert blocked["provider"] is None
    assert "fail-closed" in blocked["reason"]

"""Tests for MCP gateway catalog → ServerSpec resolution (ADR-002 phase 2d)."""

from __future__ import annotations

import json

import pytest

from brainbox.config import settings
from brainbox.gateway_catalog import load_catalog_specs

_CATALOG = {
    "version": "1",
    "servers": {
        "atlassian": {
            "definition": {
                "command": "uvx",
                "args": ["mcp-atlassian==0.21.1"],
                "env": {"JIRA_URL": "${JIRA_URL}", "LOG_LEVEL": "info"},
            }
        },
        "diagrams": {"definition": {"command": "npx", "args": ["-y", "mcp-diagrams"]}},
        "broken": {"definition": {"args": ["x"]}},  # no command → skipped
    },
}


@pytest.fixture
def catalog(tmp_path):
    p = tmp_path / "mcp-catalog.json"
    p.write_text(json.dumps(_CATALOG))
    return str(p)


def test_allowlist_selects_and_maps(catalog):
    specs = load_catalog_specs(["atlassian"], path=catalog)
    assert len(specs) == 1
    s = specs[0]
    assert s.name == "atlassian"
    assert s.command == "uvx"
    assert s.args == ["mcp-atlassian==0.21.1"]
    # ${VAR} dropped (comes from the profile env store); literal kept
    assert s.base_env == {"LOG_LEVEL": "info"}


def test_none_allowlist_loads_all_launchable(catalog):
    specs = load_catalog_specs(None, path=catalog)
    assert {s.name for s in specs} == {"atlassian", "diagrams"}  # 'broken' skipped (no command)


def test_empty_allowlist_loads_none(catalog):
    assert load_catalog_specs([], path=catalog) == []


def test_entry_without_command_skipped(catalog):
    assert load_catalog_specs(["broken"], path=catalog) == []


def test_missing_path_returns_empty():
    assert load_catalog_specs(["atlassian"], path="/no/such/catalog.json") == []


def test_unset_path_returns_empty(monkeypatch):
    monkeypatch.setattr(settings.gateway, "catalog_path", "")
    assert load_catalog_specs(["atlassian"]) == []


def test_reads_configured_path(catalog, monkeypatch):
    monkeypatch.setattr(settings.gateway, "catalog_path", catalog)
    specs = load_catalog_specs(["diagrams"])
    assert [s.name for s in specs] == ["diagrams"]


@pytest.mark.asyncio
async def test_catalog_specs_drive_the_gateway(tmp_path):
    # Full path: a catalog entry pointing at the real fixture server →
    # load_catalog_specs → the gateway exposes its tools.
    import sys
    from pathlib import Path

    from brainbox.gateway_pool import GatewayPool
    from brainbox.gateway_server import Identity, list_gateway_tools

    fixture = str(Path(__file__).parent / "_mcp_fixture_server.py")
    cat = {"version": "1", "servers": {"fixture": {"definition": {"command": sys.executable, "args": [fixture]}}}}
    p = tmp_path / "cat.json"
    p.write_text(json.dumps(cat))

    specs = load_catalog_specs(["fixture"], path=str(p))
    pool = GatewayPool()
    try:
        tools = await list_gateway_tools(pool, specs, Identity("personal", ["*"]))
        assert {t.name for t in tools} == {"fixture__echo", "fixture__getenv"}
    finally:
        await pool.aclose()

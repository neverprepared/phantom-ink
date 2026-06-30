"""Tests for MCP gateway catalog → ServerSpec resolution (ADR-002 phase 2d)."""

from __future__ import annotations

import json

import pytest

from brainbox import store
from brainbox.config import settings
from brainbox.gateway_catalog import (
    list_catalog_servers,
    load_catalog_specs,
    resolve_enabled_specs,
    seed_gateway_servers,
)

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


# --- DB-backed server registry (#152) ---------------------------------------


def test_list_catalog_servers_skips_no_command(catalog):
    got = {s["name"]: s for s in list_catalog_servers(path=catalog)}
    assert set(got) == {"atlassian", "diagrams"}  # 'broken' (no command) dropped
    assert got["atlassian"]["command"] == "uvx"


def test_seed_enables_default_set_only(catalog, monkeypatch):
    monkeypatch.setattr(settings.gateway, "catalog_path", catalog)
    seed_gateway_servers(default_enabled=["atlassian"])
    state = store.list_gateway_servers()
    assert state == {"atlassian": True, "diagrams": False}  # both seeded; only default enabled
    assert store.enabled_gateway_server_names() == ["atlassian"]


def test_seed_does_not_clobber_existing_toggle(catalog, monkeypatch):
    monkeypatch.setattr(settings.gateway, "catalog_path", catalog)
    seed_gateway_servers(default_enabled=["atlassian"])
    store.set_gateway_server_enabled("diagrams", True)   # operator turns one on
    store.set_gateway_server_enabled("atlassian", False)  # and another off
    seed_gateway_servers(default_enabled=["atlassian"])  # re-seed must not reset
    assert store.list_gateway_servers() == {"atlassian": False, "diagrams": True}


def test_resolve_enabled_specs_tracks_toggles(catalog, monkeypatch):
    monkeypatch.setattr(settings.gateway, "catalog_path", catalog)
    seed_gateway_servers(default_enabled=[])
    assert resolve_enabled_specs() == []          # nothing enabled → no specs
    store.set_gateway_server_enabled("atlassian", True)
    specs = resolve_enabled_specs()
    assert [s.name for s in specs] == ["atlassian"]  # toggle reflected live

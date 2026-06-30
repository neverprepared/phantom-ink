"""Tests for container settings bundling (gateway pre-approval, #152)."""

from __future__ import annotations

import json

from brainbox.bundle import FORCED_SETTINGS, _build_container_settings


def test_forced_settings_preapprove_gateway():
    # The gateway server must be pre-approved so autonomous/headless container
    # agents skip the .mcp.json "Pending approval" gate.
    assert FORCED_SETTINGS.get("enabledMcpjsonServers") == ["phantom-gateway"]
    assert FORCED_SETTINGS.get("bypassPermissions") is True


def test_build_container_settings_includes_gateway_approval(tmp_path):
    # No source settings/.claude.json → result is just the forced overrides.
    out = _build_container_settings(
        tmp_path / "missing-settings.json", tmp_path / "missing.claude.json", {}
    )
    parsed = json.loads(out)
    assert parsed["enabledMcpjsonServers"] == ["phantom-gateway"]
    assert parsed["bypassPermissions"] is True


def test_user_settings_do_not_override_gateway_approval(tmp_path):
    # Forced settings win over user settings (update() applied last).
    sp = tmp_path / "settings.json"
    sp.write_text(json.dumps({"enabledMcpjsonServers": [], "theme": "light"}))
    out = _build_container_settings(sp, tmp_path / "none.json", {})
    parsed = json.loads(out)
    assert parsed["enabledMcpjsonServers"] == ["phantom-gateway"]
    assert parsed["theme"] == "light"  # non-conflicting user settings preserved

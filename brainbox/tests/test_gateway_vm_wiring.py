"""Tests for injecting the MCP gateway into a VM (UTM SSH / QEMU) session.

VMs can't bind-mount the host-generated .mcp.json the way docker does, so the
gateway entry is written into the guest's workspace via exec. This exercises
`configure.inject_gateway_mcp` — the VM equivalent of `_generate_container_mcp_json`.
"""

from __future__ import annotations

import json
import re

import pytest

from brainbox import registry
from brainbox.backends.configure import inject_gateway_mcp
from brainbox.config import settings


class RecordingExecutor:
    """Minimal GuestExecutor duck-type that records exec_shell commands."""

    def __init__(self, *, home_dir="/home/developer", guest_os="linux"):
        self.home_dir = home_dir
        self.guest_os = guest_os
        self.commands: list[str] = []

    async def exec_shell(self, cmd, timeout=None):
        self.commands.append(cmd)
        return ("", "", 0)


@pytest.fixture
def gateway_on(monkeypatch):
    monkeypatch.setattr(settings.gateway, "servers", ["brainbox"])
    monkeypatch.setattr(settings.gateway, "inject_sessions", True)
    monkeypatch.setattr(settings.gateway, "vm_url", "http://192.168.64.1:9999/gateway/mcp")
    monkeypatch.setattr(settings.gateway, "session_token_ttl", 3600)


def _payload_from(cmd: str) -> dict:
    """Extract the JSON piped into python3 from an `echo '<json>' | python3` command."""
    m = re.search(r"echo '(.*?)' \| python3", cmd, re.DOTALL)
    assert m, f"no echo payload in: {cmd}"
    return json.loads(m.group(1))


class TestInjectGatewayMcp:
    async def test_writes_gateway_entry_to_all_workspaces(self, gateway_on):
        ex = RecordingExecutor(home_dir="/Users/developer", guest_os="macos")
        await inject_gateway_mcp(ex, "personal", gateway_url=settings.gateway.vm_url)

        # one write per working dir (workspace, task-repo, home)
        assert len(ex.commands) == 3
        targets = [re.search(r"pathlib\.Path\(\\\"(.*?)\\\"\)", c).group(1) for c in ex.commands]
        assert targets == [
            "/Users/developer/workspace/.mcp.json",
            "/Users/developer/task-repo/.mcp.json",
            "/Users/developer/.mcp.json",
        ]

        entry = _payload_from(ex.commands[0])["phantom-gateway"]
        assert entry["type"] == "http"
        assert entry["url"] == "http://192.168.64.1:9999/gateway/mcp"  # vm_url, not docker alias
        auth = entry["headers"]["Authorization"]
        assert auth.startswith("Bearer ")
        # the bearer is a real gateway token bound to the profile
        tok = registry.validate_token(auth.removeprefix("Bearer "))
        assert tok is not None and tok.workspace_profile == "personal"

    async def test_token_carries_profile_ceiling(self, gateway_on):
        from brainbox import store

        store.set_profile_default_ceiling("personal", "infra")
        ex = RecordingExecutor()
        await inject_gateway_mcp(ex, "personal", gateway_url=settings.gateway.vm_url)
        entry = _payload_from(ex.commands[0])["phantom-gateway"]
        tok = registry.validate_token(entry["headers"]["Authorization"].removeprefix("Bearer "))
        assert tok.residency_ceiling == "infra"

    async def test_noop_without_profile(self, gateway_on):
        ex = RecordingExecutor()
        await inject_gateway_mcp(ex, "", gateway_url=settings.gateway.vm_url)
        assert ex.commands == []

    async def test_noop_when_gateway_inactive(self, monkeypatch):
        monkeypatch.setattr(settings.gateway, "servers", [])
        monkeypatch.setattr(settings.gateway, "inject_sessions", True)
        ex = RecordingExecutor()
        await inject_gateway_mcp(ex, "personal", gateway_url="http://192.168.64.1:9999/gateway/mcp")
        assert ex.commands == []

    async def test_noop_on_windows(self, gateway_on):
        ex = RecordingExecutor(guest_os="windows")
        await inject_gateway_mcp(ex, "personal", gateway_url=settings.gateway.vm_url)
        assert ex.commands == []

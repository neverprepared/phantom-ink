"""Tests for GuestExecutor protocol and shared configure functions."""

from __future__ import annotations

import json
import pytest

from brainbox.backends.executor import GuestExecutor
from brainbox.backends import configure


class RecordingExecutor:
    """Mock executor that records all calls for assertion."""

    def __init__(
        self,
        *,
        home_dir: str = "/home/developer",
        guest_os: str = "linux",
    ):
        self._home_dir = home_dir
        self._guest_os = guest_os
        self.calls: list[tuple[str, dict]] = []

    @property
    def home_dir(self) -> str:
        return self._home_dir

    @property
    def guest_os(self) -> str:
        return self._guest_os

    async def exec_shell(self, command: str, *, timeout: int = 30) -> tuple[int, str]:
        self.calls.append(("exec_shell", {"command": command, "timeout": timeout}))
        return 0, ""

    async def write_file(self, path: str, content: bytes, *, mode: int = 0o644) -> None:
        self.calls.append(("write_file", {"path": path, "content": content, "mode": mode}))

    async def append_file(self, path: str, content: bytes) -> None:
        self.calls.append(("append_file", {"path": path, "content": content}))

    async def write_stdin(
        self, command: str, stdin_data: bytes, *, timeout: int = 30
    ) -> tuple[int, str]:
        self.calls.append(
            ("write_stdin", {"command": command, "stdin_data": stdin_data, "timeout": timeout})
        )
        return 0, ""


def test_recording_executor_satisfies_protocol():
    """RecordingExecutor should satisfy the GuestExecutor protocol."""
    executor = RecordingExecutor()
    assert isinstance(executor, GuestExecutor)


@pytest.mark.asyncio
async def test_inject_env_file_linux():
    """inject_env_file should write secrets and LANGFUSE_SESSION_ID on Linux."""
    executor = RecordingExecutor()
    secrets = {"ANTHROPIC_API_KEY": "sk-test-123", "agent-token": "tok-abc"}
    await configure.inject_env_file(executor, secrets, "my-session")

    commands = [c[1]["command"] for c in executor.calls if c[0] == "exec_shell"]

    # Should create .env
    assert any("rm -f" in cmd and ".env" in cmd for cmd in commands)
    # Should write ANTHROPIC_API_KEY
    assert any("ANTHROPIC_API_KEY" in cmd for cmd in commands)
    # Should write agent-token
    assert any(".agent-token" in cmd for cmd in commands)
    # Should write LANGFUSE_SESSION_ID
    assert any("LANGFUSE_SESSION_ID=my-session" in cmd for cmd in commands)


@pytest.mark.asyncio
async def test_inject_env_file_windows():
    """inject_env_file should use PowerShell on Windows."""
    executor = RecordingExecutor(guest_os="windows")
    secrets = {"API_KEY": "test"}
    await configure.inject_env_file(executor, secrets, "session-1")

    commands = [c[1]["command"] for c in executor.calls if c[0] == "exec_shell"]
    assert all("powershell" in cmd for cmd in commands)


@pytest.mark.asyncio
async def test_inject_claude_config_linux():
    """inject_claude_config should patch .claude.json on Linux."""
    executor = RecordingExecutor()
    oauth = {"accountUuid": "test-uuid", "accessToken": "tok"}
    await configure.inject_claude_config(executor, oauth)

    commands = [c[1]["command"] for c in executor.calls if c[0] == "exec_shell"]
    assert any("python3" in cmd and ".claude.json" in cmd for cmd in commands)


@pytest.mark.asyncio
async def test_inject_claude_config_macos():
    """inject_claude_config should write .credentials.json on macOS."""
    executor = RecordingExecutor(home_dir="/Users/developer", guest_os="macos")
    oauth = {"accountUuid": "test-uuid"}
    await configure.inject_claude_config(executor, oauth)

    commands = [c[1]["command"] for c in executor.calls if c[0] == "exec_shell"]
    assert any(".credentials.json" in cmd for cmd in commands)


@pytest.mark.asyncio
async def test_inject_claude_config_none_oauth():
    """inject_claude_config with no oauth should still set onboarding on Linux."""
    executor = RecordingExecutor()
    await configure.inject_claude_config(executor, None)

    commands = [c[1]["command"] for c in executor.calls if c[0] == "exec_shell"]
    assert any("hasCompletedOnboarding" in cmd for cmd in commands)


@pytest.mark.asyncio
async def test_inject_claude_settings_linux():
    """inject_claude_settings should set bypassPermissions via python3."""
    executor = RecordingExecutor()
    await configure.inject_claude_settings(executor)

    commands = [c[1]["command"] for c in executor.calls if c[0] == "exec_shell"]
    assert any("bypassPermissions" in cmd for cmd in commands)
    assert any("skipDangerousModePermissionPrompt" in cmd for cmd in commands)
    assert any("bypassPermissionsModeAccepted" in cmd for cmd in commands)
    # gateway server is pre-approved so autonomous agents skip the .mcp.json
    # "Pending approval" gate (#152)
    assert any("enabledMcpjsonServers" in cmd and "phantom-gateway" in cmd for cmd in commands)


@pytest.mark.asyncio
async def test_inject_profile_env():
    """inject_profile_env should append env vars to .env."""
    executor = RecordingExecutor()
    await configure.inject_profile_env(executor, "WORKSPACE_PROFILE=personal\nFOO=bar")

    commands = [c[1]["command"] for c in executor.calls if c[0] == "exec_shell"]
    assert any(".env" in cmd for cmd in commands)


@pytest.mark.asyncio
async def test_inject_role_prompt():
    """inject_role_prompt should write role-prompt.md and update settings."""
    executor = RecordingExecutor()
    await configure.inject_role_prompt(executor, "worker", "You are a worker agent.")

    commands = [c[1]["command"] for c in executor.calls if c[0] == "exec_shell"]
    assert any("role-prompt.md" in cmd for cmd in commands)
    assert any("appendSystemPromptFiles" in cmd for cmd in commands)


@pytest.mark.asyncio
async def test_inject_role_prompt_windows_skipped():
    """inject_role_prompt should skip on Windows."""
    executor = RecordingExecutor(guest_os="windows")
    await configure.inject_role_prompt(executor, "worker", "content")
    assert len(executor.calls) == 0


@pytest.mark.asyncio
async def test_inject_task():
    """inject_task should write task.txt, hub-url.txt, and complete.sh."""
    executor = RecordingExecutor()
    await configure.inject_task(executor, "Fix the bug in main.py")

    commands = [c[1]["command"] for c in executor.calls if c[0] == "exec_shell"]
    assert any("task.txt" in cmd for cmd in commands)
    assert any("hub-url.txt" in cmd for cmd in commands)
    assert any("complete.sh" in cmd for cmd in commands)


@pytest.mark.asyncio
async def test_inject_config_bundle():
    """inject_config_bundle should extract tar and write settings.json."""
    import io
    import tarfile

    # Build a minimal bundle
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        settings_data = json.dumps({"bypassPermissions": True}).encode()
        import tarfile as _tf

        info = _tf.TarInfo(name=".claude/settings.json")
        info.size = len(settings_data)
        tf.addfile(info, io.BytesIO(settings_data))
    bundle_bytes = buf.getvalue()

    executor = RecordingExecutor()
    await configure.inject_config_bundle(executor, bundle_bytes)

    # Should have write_stdin for tar extraction
    stdin_calls = [c for c in executor.calls if c[0] == "write_stdin"]
    assert len(stdin_calls) >= 1
    assert "tar xz" in stdin_calls[0][1]["command"]

    # Should have exec_shell for settings.json
    commands = [c[1]["command"] for c in executor.calls if c[0] == "exec_shell"]
    assert any("settings.json" in cmd for cmd in commands)

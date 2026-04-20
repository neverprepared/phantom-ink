"""GuestExecutor protocol and transport implementations.

Provides a unified interface for executing commands and writing files inside
guest environments (Docker containers, SSH-reachable VMs, utmctl-exec VMs).
"""

from __future__ import annotations

import asyncio
import shlex
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class GuestExecutor(Protocol):
    """Protocol for executing commands inside a guest environment."""

    async def exec_shell(self, command: str, *, timeout: int = 30) -> tuple[int, str]:
        """Run a shell command and return (exit_code, combined_output).

        The command is interpreted by /bin/sh (or equivalent on Windows).
        """
        ...

    async def write_file(self, path: str, content: bytes, *, mode: int = 0o644) -> None:
        """Write content to a file, creating parent directories as needed."""
        ...

    async def append_file(self, path: str, content: bytes) -> None:
        """Append content to an existing file (creates if missing)."""
        ...

    async def write_stdin(
        self, command: str, stdin_data: bytes, *, timeout: int = 30
    ) -> tuple[int, str]:
        """Run a shell command with stdin data piped in.

        Returns (exit_code, combined_output).
        """
        ...

    @property
    def home_dir(self) -> str:
        """Absolute home directory path (e.g. /home/developer, /Users/developer)."""
        ...

    @property
    def guest_os(self) -> str:
        """Guest operating system: 'linux', 'macos', or 'windows'."""
        ...


class DockerExecExecutor:
    """Execute commands inside a Docker container via docker exec."""

    def __init__(self, container: Any, *, home_dir: str = "/home/developer") -> None:
        from ..backends.docker import _run

        self._container = container
        self._run = _run
        self._home_dir = home_dir

    @property
    def home_dir(self) -> str:
        return self._home_dir

    @property
    def guest_os(self) -> str:
        return "linux"

    async def exec_shell(self, command: str, *, timeout: int = 30) -> tuple[int, str]:
        result = await asyncio.wait_for(
            self._run(
                self._container.exec_run,
                ["sh", "-c", command],
            ),
            timeout=timeout,
        )
        exit_code = result.exit_code if hasattr(result, "exit_code") else 0
        output = (
            result.output.decode("utf-8", errors="replace")
            if hasattr(result, "output") and result.output
            else ""
        )
        return exit_code, output

    async def write_file(self, path: str, content: bytes, *, mode: int = 0o644) -> None:
        escaped = shlex.quote(content.decode("utf-8", errors="replace"))
        parent = str(Path(path).parent)
        mode_oct = oct(mode)[2:]
        await self.exec_shell(
            f"mkdir -p {shlex.quote(parent)}"
            f" && printf '%s' {escaped} > {shlex.quote(path)}"
            f" && chmod {mode_oct} {shlex.quote(path)}"
        )

    async def append_file(self, path: str, content: bytes) -> None:
        escaped = shlex.quote(content.decode("utf-8", errors="replace"))
        await self.exec_shell(f"printf '%s' {escaped} >> {shlex.quote(path)}")

    async def write_stdin(
        self, command: str, stdin_data: bytes, *, timeout: int = 30
    ) -> tuple[int, str]:
        import base64
        b64 = base64.b64encode(stdin_data).decode("ascii")
        return await self.exec_shell(
            f"printf '%s' {shlex.quote(b64)} | base64 -d | {command}",
            timeout=timeout,
        )


class SSHExecutor:
    """Execute commands inside a VM via SSH."""

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        key_path: Path,
        *,
        home_dir: str = "/Users/developer",
        guest_os: str = "macos",
    ) -> None:
        self._host = host
        self._port = port
        self._user = user
        self._key_path = key_path
        self._home_dir = home_dir
        self._guest_os = guest_os

    @property
    def home_dir(self) -> str:
        return self._home_dir

    @property
    def guest_os(self) -> str:
        return self._guest_os

    def _ssh_base_args(self) -> list[str]:
        return [
            "ssh",
            "-i",
            str(self._key_path),
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "LogLevel=ERROR",
            "-p",
            str(self._port),
            f"{self._user}@{self._host}",
        ]

    async def exec_shell(self, command: str, *, timeout: int = 30) -> tuple[int, str]:
        cmd = self._ssh_base_args() + [command]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            rc = proc.returncode or 0
            output = stdout_b.decode("utf-8", errors="replace") + stderr_b.decode(
                "utf-8", errors="replace"
            )
            return rc, output
        except asyncio.TimeoutError:
            proc.kill()
            raise TimeoutError(f"SSH command timed out after {timeout}s: {command[:80]}")

    async def write_file(self, path: str, content: bytes, *, mode: int = 0o644) -> None:
        escaped = shlex.quote(content.decode("utf-8", errors="replace"))
        parent = str(Path(path).parent)
        mode_oct = oct(mode)[2:]
        await self.exec_shell(
            f"mkdir -p {shlex.quote(parent)}"
            f" && printf '%s' {escaped} > {shlex.quote(path)}"
            f" && chmod {mode_oct} {shlex.quote(path)}"
        )

    async def append_file(self, path: str, content: bytes) -> None:
        escaped = shlex.quote(content.decode("utf-8", errors="replace"))
        await self.exec_shell(f"printf '%s' {escaped} >> {shlex.quote(path)}")

    async def write_stdin(
        self, command: str, stdin_data: bytes, *, timeout: int = 30
    ) -> tuple[int, str]:
        cmd = self._ssh_base_args() + [command]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(input=stdin_data), timeout=timeout
            )
            rc = proc.returncode or 0
            output = stdout_b.decode("utf-8", errors="replace") + stderr_b.decode(
                "utf-8", errors="replace"
            )
            return rc, output
        except asyncio.TimeoutError:
            proc.kill()
            raise TimeoutError(f"SSH stdin command timed out after {timeout}s")


class QEMUExecExecutor:
    """Execute commands inside a QEMU VM via utmctl exec (guest agent)."""

    def __init__(
        self,
        vm_name: str,
        utmctl_path: str,
        *,
        home_dir: str = "/home/developer",
        guest_os: str = "linux",
    ) -> None:
        self._vm_name = vm_name
        self._utmctl = utmctl_path
        self._home_dir = home_dir
        self._guest_os = guest_os

    @property
    def home_dir(self) -> str:
        return self._home_dir

    @property
    def guest_os(self) -> str:
        return self._guest_os

    async def exec_shell(self, command: str, *, timeout: int = 30) -> tuple[int, str]:
        args = [self._utmctl, "exec", self._vm_name, "--cmd", "/bin/sh", "-c", command]
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            rc = proc.returncode or 0
            output = stdout_b.decode("utf-8", errors="replace") + stderr_b.decode(
                "utf-8", errors="replace"
            )
            return rc, output
        except asyncio.TimeoutError:
            proc.kill()
            raise TimeoutError(f"utmctl exec timed out after {timeout}s")

    async def write_file(self, path: str, content: bytes, *, mode: int = 0o644) -> None:
        parent = str(Path(path).parent)
        mode_oct = oct(mode)[2:]
        await self.write_stdin(
            f"mkdir -p {shlex.quote(parent)}"
            f" && cat > {shlex.quote(path)}"
            f" && chmod {mode_oct} {shlex.quote(path)}",
            content,
        )

    async def append_file(self, path: str, content: bytes) -> None:
        await self.write_stdin(f"cat >> {shlex.quote(path)}", content)

    async def write_stdin(
        self, command: str, stdin_data: bytes, *, timeout: int = 30
    ) -> tuple[int, str]:
        args = [
            self._utmctl,
            "exec",
            self._vm_name,
            "--input",
            "--cmd",
            "/bin/sh",
            "-c",
            command,
        ]
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(input=stdin_data), timeout=timeout
            )
            rc = proc.returncode or 0
            output = stdout_b.decode("utf-8", errors="replace") + stderr_b.decode(
                "utf-8", errors="replace"
            )
            return rc, output
        except asyncio.TimeoutError:
            proc.kill()
            raise TimeoutError(f"utmctl exec stdin timed out after {timeout}s")

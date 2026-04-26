"""UTM backend implementation for brainbox.

Manages macOS VMs via UTM for iOS/macOS development workflows requiring Xcode,
Swift, and native macOS tooling.
"""

from __future__ import annotations

import asyncio
import os
import plistlib
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ...log import get_logger
from ...models import SessionContext, SessionState

log = get_logger()

# Default paths (configurable via environment)
DEFAULT_UTM_DOCS = Path.home() / "Library" / "Containers" / "com.utmapp.UTM" / "Data" / "Documents"
DEFAULT_UTMCTL = "/usr/local/bin/utmctl"
DEFAULT_SSH_KEY = Path.home() / ".ssh" / "id_ed25519"
DEFAULT_SSH_BASE_PORT = 2200


def _get_utmctl_path() -> str:
    """Get utmctl binary path.

    Resolution order:
    1. settings.utm.utmctl_path (non-empty string in config)
    2. CL_UTM__UTMCTL_PATH env var (legacy override)
    3. PATH lookup via shutil.which (augmented with Homebrew locations)
    4. Raise RuntimeError if not found
    """
    from ...config import settings

    configured = settings.utm.utmctl_path
    if configured:
        return configured
    custom = os.environ.get("CL_UTM__UTMCTL_PATH")
    if custom:
        return custom
    # Augment PATH with common Homebrew prefix locations so which() works
    # even when the daemon runs without a full interactive shell PATH.
    extra = "/opt/homebrew/bin:/usr/local/bin"
    search_path = extra + ":" + os.environ.get("PATH", "")
    found = shutil.which("utmctl", path=search_path)
    if found:
        return found
    raise RuntimeError(
        "utmctl not found. Install UTM command-line tools or set CL_UTM__UTMCTL_PATH. "
        "Searched PATH: " + search_path
    )


def _get_utm_docs_dir() -> Path:
    """Get UTM documents directory path."""
    custom = os.environ.get("CL_UTM__DOCS_DIR")
    if custom:
        return Path(custom)
    return DEFAULT_UTM_DOCS


def _get_ssh_key_path() -> Path:
    """Get SSH private key path.

    Resolution order:
    1. CL_UTM__SSH_KEY_PATH env var (explicit override)
    2. $WORKSPACE_HOME/.ssh/id_ed25519 (workspace profile key)
    3. ~/.ssh/id_ed25519 (fallback)
    """
    custom = os.environ.get("CL_UTM__SSH_KEY_PATH")
    if custom:
        return Path(custom)
    workspace_home = os.environ.get("WORKSPACE_HOME")
    if workspace_home:
        workspace_key = Path(workspace_home) / ".ssh" / "id_ed25519"
        if workspace_key.exists():
            return workspace_key
    return DEFAULT_SSH_KEY


def _get_ssh_base_port() -> int:
    """Get SSH base port for allocation."""
    return int(os.environ.get("CL_UTM__SSH_BASE_PORT", str(DEFAULT_SSH_BASE_PORT)))


async def _run_subprocess(
    cmd: list[str], *, timeout: int = 30, check: bool = True
) -> tuple[int, str, str]:
    """Run subprocess asynchronously.

    Returns:
        Tuple of (returncode, stdout, stderr)
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        returncode = proc.returncode or 0

        if check and returncode != 0:
            raise subprocess.CalledProcessError(returncode, cmd, stdout, stderr)

        return (returncode, stdout, stderr)
    except asyncio.TimeoutError:
        proc.kill()
        raise TimeoutError(f"Command timed out after {timeout}s: {' '.join(cmd)}")


def _generate_mac() -> str:
    """Generate a random locally administered unicast MAC address."""
    try:
        from mcp_utm.applescript import generate_mac
        return generate_mac()
    except ImportError:
        import random
        first = random.randint(0, 255) & 0xFE | 0x02
        rest = [random.randint(0, 255) for _ in range(5)]
        return ":".join(f"{b:02x}" for b in [first, *rest])


def _has_mcp_utm() -> bool:
    """Check if mcp-utm is available (macOS only)."""
    try:
        import mcp_utm.applescript  # noqa: F401
        return True
    except ImportError:
        return False


def _session_dir_for_mac(mac_address: str) -> Path | None:
    """Return the sessions/<MAC>/ directory in the brainbox share, or None if not configured."""
    from ...config import settings

    share_dir = settings.utm.share_dir
    if not share_dir:
        return None
    safe_mac = mac_address.lower().replace(":", "-")
    return Path(share_dir) / "sessions" / safe_mac


def _sessions_file_for_mac(mac_address: str) -> Path | None:
    """Return the sessions/<MAC>/ip file path, or None if share not configured."""
    session_dir = _session_dir_for_mac(mac_address)
    if session_dir is None:
        return None
    return session_dir / "ip"


def _write_session_dir(mac_address: str, workspace_home: str, slog) -> None:
    """Write per-session bootstrap files into brainbox/sessions/<MAC>/.

    Populates:
      authorized_keys  — public SSH key for the workspace profile
      env              — filtered .env (safe vars only, no secrets)
      gitconfig        — .gitconfig for the workspace profile

    The VM's init.sh LaunchDaemon reads these on boot to bootstrap the session.
    Files are scoped to the MAC so multiple concurrent sessions don't collide.
    """
    session_dir = _session_dir_for_mac(mac_address)
    if session_dir is None:
        slog.warning("utm.session_dir_skipped", metadata={"reason": "CL_UTM__SHARE_DIR not set"})
        return

    session_dir.mkdir(parents=True, exist_ok=True)
    ws = Path(workspace_home)

    # authorized_keys — public key only
    pub_key = ws / ".ssh" / "id_ed25519.pub"
    if pub_key.exists():
        (session_dir / "authorized_keys").write_bytes(pub_key.read_bytes())
        slog.info("utm.session_dir_wrote", metadata={"file": "authorized_keys"})
    else:
        slog.warning("utm.session_dir_missing", metadata={"file": str(pub_key)})

    # .env — exclude known secret vars; the SSH injection in configure() handles those
    env_src = ws / ".env"
    if env_src.exists():
        _EXCLUDED_ENV_KEYS = {
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
            "GITHUB_TOKEN",
            "NPM_TOKEN",
        }
        filtered_lines = []
        for line in env_src.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                filtered_lines.append(line)
                continue
            key = stripped.split("=", 1)[0].strip()
            # Strip "export " prefix before checking against exclusion set
            if key.startswith("export "):
                key = key[len("export "):].strip()
            if key not in _EXCLUDED_ENV_KEYS:
                filtered_lines.append(line)
        (session_dir / "env").write_text("\n".join(filtered_lines) + "\n")
        slog.info("utm.session_dir_wrote", metadata={"file": "env"})

    # .gitconfig
    gitconfig_src = ws / ".gitconfig"
    if gitconfig_src.exists():
        (session_dir / "gitconfig").write_bytes(gitconfig_src.read_bytes())
        slog.info("utm.session_dir_wrote", metadata={"file": "gitconfig"})

    # keychain_credentials — OAuth token for macOS Claude Code Keychain injection
    # The VM's session-bootstrap LaunchAgent reads this at login and adds the entry
    # to the login Keychain (where it can be read since the GUI session is active).
    keychain_json = _read_host_keychain_credentials()
    if keychain_json:
        (session_dir / "keychain_credentials").write_text(keychain_json)
        slog.info("utm.session_dir_wrote", metadata={"file": "keychain_credentials"})


def _remove_session_dir(mac_address: str, slog) -> None:
    """Remove the brainbox/sessions/<MAC>/ directory on session teardown."""
    session_dir = _session_dir_for_mac(mac_address)
    if session_dir is None or not session_dir.exists():
        return
    try:
        shutil.rmtree(session_dir)
        slog.info("utm.session_dir_removed", metadata={"mac": mac_address})
    except Exception as exc:
        slog.warning("utm.session_dir_remove_failed", metadata={"reason": str(exc)})


async def _discover_vm_ip(mac_address: str, timeout: int = 60) -> str:
    """Discover VM IP address.

    First checks the brainbox share sessions file written by the VM's init.sh
    LaunchDaemon (fast, no network sweep). Falls back to ARP table polling if
    the share is not configured or the file hasn't appeared yet.

    Args:
        mac_address: VM's MAC address (e.g., "a6:45:33:e5:e4:0d")
        timeout: How long to wait for the IP to appear

    Returns:
        VM's IP address

    Raises:
        TimeoutError: If VM IP not found within timeout
    """
    sessions_file = _sessions_file_for_mac(mac_address)

    start_time = asyncio.get_running_loop().time()

    # When the share is configured, the VM's init.sh LaunchDaemon writes its IP
    # to the sessions file on boot. Wait exclusively for that file — skip ARP,
    # which can hang when a VM's virtual NIC is initializing.
    if sessions_file is not None:
        while (asyncio.get_running_loop().time() - start_time) < timeout:
            if sessions_file.exists():
                ip = sessions_file.read_text().strip()
                if ip:
                    return ip
            await asyncio.sleep(2)
        raise TimeoutError(f"VM IP not found after {timeout}s (MAC: {mac_address})")

    # Fallback: ARP table polling (share not configured — no sessions file available)
    # Normalize MAC address for ARP matching (handles missing leading zeros)
    mac_parts = mac_address.lower().split(":")
    mac_pattern = ":".join(part.lstrip("0") or "0" for part in mac_parts)

    ping_done = False
    while (asyncio.get_running_loop().time() - start_time) < timeout:
        # Ping-sweep once to force ARP entries into the table
        if not ping_done:
            await _run_subprocess(
                ["ping", "-c", "1", "-W", "1", "-b", "192.168.64.255"],
                timeout=3,
                check=False,
            )
            ping_done = True

        returncode, stdout, stderr = await _run_subprocess(["arp", "-a"], timeout=10)
        if returncode == 0:
            # Parse ARP output: "? (192.168.64.12) at a6:45:33:e5:e4:d on bridge100"
            for line in stdout.split("\n"):
                line_lower = line.lower()
                if mac_pattern in line_lower or mac_address.lower() in line_lower:
                    import re

                    match = re.search(r"\(([0-9.]+)\)", line)
                    if match:
                        return match.group(1)

        await asyncio.sleep(2)

    raise TimeoutError(f"VM IP not found after {timeout}s (MAC: {mac_address})")


async def _vm_exec(
    vm_name: str,
    utmctl: str,
    cmd: list[str],
    *,
    stdin_data: bytes | None = None,
    env: dict[str, str] | None = None,
    timeout: int = 30,
) -> tuple[int, str, str]:
    """Execute a command inside the VM via utmctl exec (no SSH required).

    Args:
        vm_name: UTM VM name (e.g. "brainbox-myproject")
        utmctl: Path to utmctl binary
        cmd: Command + arguments to run inside the VM
        stdin_data: Optional bytes to pipe to the command's stdin
        env: Optional environment variables to set in the VM
        timeout: Command timeout in seconds

    Returns:
        Tuple of (returncode, stdout, stderr)
    """
    args = [utmctl, "exec", vm_name]
    if env:
        for k, v in env.items():
            args += ["--env", f"{k}={v}"]
    if stdin_data is not None:
        args.append("--input")
    args.append("--cmd")
    args.extend(cmd)

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE if stdin_data is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(input=stdin_data), timeout=timeout
        )
        rc = proc.returncode or 0
        return (
            rc,
            stdout_b.decode("utf-8", errors="replace"),
            stderr_b.decode("utf-8", errors="replace"),
        )
    except asyncio.TimeoutError:
        proc.kill()
        raise TimeoutError(f"utmctl exec timed out after {timeout}s: {cmd}")


async def _wait_for_guest_agent(
    vm_name: str, utmctl: str, timeout: int = 120, interval: int = 3
) -> bool:
    """Poll utmctl exec until the guest agent responds (replaces SSH wait).

    Returns True when the guest agent is ready, False on timeout.
    """
    elapsed = 0
    while elapsed < timeout:
        try:
            rc, _, _ = await _vm_exec(vm_name, utmctl, ["/bin/echo", "ok"], timeout=5)
            if rc == 0:
                return True
        except Exception:
            pass
        await asyncio.sleep(interval)
        elapsed += interval
    return False


async def _ssh_execute(
    host: str,
    port: int,
    user: str,
    ssh_key: Path,
    command: str,
    *,
    timeout: int = 30,
) -> tuple[int, str, str]:
    """Execute command via SSH (used for user-facing exec_command only)."""
    ssh_cmd = [
        "ssh",
        "-i",
        str(ssh_key),
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "LogLevel=ERROR",
        "-p",
        str(port),
        f"{user}@{host}",
        command,
    ]
    return await _run_subprocess(ssh_cmd, timeout=timeout, check=False)


async def _wait_for_ssh(host: str, port: int, timeout: int = 120, interval: int = 2) -> bool:
    """Wait for SSH port to become available.

    Args:
        host: SSH hostname
        port: SSH port
        timeout: Maximum wait time in seconds
        interval: Polling interval in seconds

    Returns:
        True if SSH is available, False if timeout
    """
    elapsed = 0
    while elapsed < timeout:
        try:
            # Try to connect to SSH port — TCP handshake success = SSH is listening.
            reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=5)
            writer.close()  # don't await wait_closed(); it can raise and spin the loop
            return True
        except (OSError, asyncio.TimeoutError):
            await asyncio.sleep(interval)
            elapsed += interval
    return False


def _find_available_ssh_port(start_port: int | None = None) -> int:
    """Find an available SSH port by scanning existing Docker and UTM usage.

    Args:
        start_port: Starting port number (defaults to CL_UTM__SSH_BASE_PORT)

    Returns:
        Available port number
    """
    if start_port is None:
        start_port = _get_ssh_base_port()

    used_ports: set[int] = set()

    # Scan Docker containers for used ports
    try:
        import docker

        client = docker.from_env()
        containers = client.containers.list()
        for c in containers:
            ports = c.attrs.get("NetworkSettings", {}).get("Ports") or {}
            for bindings in ports.values():
                if bindings:
                    for b in bindings:
                        if b.get("HostPort"):
                            used_ports.add(int(b["HostPort"]))
    except Exception as e:
        log.debug("utm.port_scan_docker_failed", metadata={"reason": str(e)})

    # Scan existing UTM VMs for used SSH ports (read from config.plist)
    try:
        utm_docs = _get_utm_docs_dir()
        if utm_docs.exists():
            for vm_dir in utm_docs.glob("brainbox-*.utm"):
                config_plist = vm_dir / "config.plist"
                if config_plist.exists():
                    with config_plist.open("rb") as f:
                        config = plistlib.load(f)
                    # Check for port forwarding rules
                    qemu = config.get("Qemu", {})
                    network = qemu.get("Network", {})
                    port_forward = network.get("PortForward", [])
                    for rule in port_forward:
                        if isinstance(rule, dict):
                            host_port = rule.get("HostPort")
                            if host_port:
                                used_ports.add(int(host_port))
    except Exception as e:
        log.debug("utm.port_scan_utm_config_failed", metadata={"reason": str(e)})

    # Find first available port
    port = start_port
    while port in used_ports:
        port += 1
    return port


def _plist_load(path: Path) -> dict:
    """Read and parse a plist file (sync — use asyncio.to_thread in async contexts)."""
    with path.open("rb") as f:
        return plistlib.load(f)


def _plist_dump(path: Path, config: dict) -> None:
    """Write a plist file (sync — use asyncio.to_thread in async contexts)."""
    with path.open("wb") as f:
        plistlib.dump(config, f)


async def _clone_vm_via_utmctl(utmctl: str, template_name: str, vm_name: str, slog) -> None:
    """Clone a UTM VM via utmctl, preserving registry (VirtioFS shares, bookmarks)."""
    slog.info("utm.cloning_template", metadata={"template": template_name, "clone": vm_name})
    returncode, stdout, stderr = await _run_subprocess(
        [utmctl, "clone", template_name, "--name", vm_name],
        timeout=120,
        check=False,
    )
    if returncode != 0:
        raise RuntimeError(f"utmctl clone failed: {stderr.strip() or stdout.strip()}")


SSH_SHARE_TAG = "brainbox-ssh"


def _get_ssh_dir() -> Path | None:
    """Return the workspace .ssh directory, falling back to ~/.ssh."""
    workspace_home = os.environ.get("WORKSPACE_HOME")
    if workspace_home:
        d = Path(workspace_home) / ".ssh"
        if d.exists():
            return d
    d = Path.home() / ".ssh"
    return d if d.exists() else None


def _add_ssh_share(config: dict, slog) -> None:
    """Append the workspace .ssh directory as a read-only SharedDirectory.

    Must be called AFTER _configure_shared_dirs (which clears the list).
    The VM startup script reads id_ed25519.pub from this share and writes
    it to ~/.ssh/authorized_keys before sshd begins accepting connections.
    """
    ssh_dir = _get_ssh_dir()
    if not ssh_dir:
        slog.warning("utm.ssh_share_skipped", metadata={"reason": "no .ssh directory found"})
        return
    shared_dirs = config.setdefault("SharedDirectories", [])
    shared_dirs.append(
        {
            "DirectoryURL": f"file://{ssh_dir}",
            "ReadOnly": True,
            "Name": SSH_SHARE_TAG,
        }
    )
    slog.info("utm.ssh_share_configured", metadata={"path": str(ssh_dir)})


READY_SHARE_TAG = "brainbox-ready"


def _get_utm_ready_dir() -> Path:
    """Return the host directory used for per-session ready signals."""
    workspace_home = os.environ.get("WORKSPACE_HOME")
    base = (
        Path(workspace_home) / ".config" / "developer"
        if workspace_home
        else Path.home() / ".config" / "developer"
    )
    return base / "utm-ready"


def _add_ready_share(config: dict, session_name: str, slog) -> Path:
    """Create a per-session ready-signal directory and add it as a read-write VirtioFS share.

    The VM's LaunchDaemon writes 'ready' into this directory once SSH is up,
    letting the host detect readiness via filesystem poll instead of ARP+SSH probing.

    Returns the ready directory path so callers can poll it.
    """
    ready_dir = _get_utm_ready_dir() / session_name
    ready_dir.mkdir(parents=True, exist_ok=True)
    # Remove stale ready file from any previous run
    (ready_dir / "ready").unlink(missing_ok=True)
    shared_dirs = config.setdefault("SharedDirectories", [])
    shared_dirs.append(
        {
            "DirectoryURL": f"file://{ready_dir}",
            "ReadOnly": False,
            "Name": READY_SHARE_TAG,
        }
    )
    slog.info("utm.ready_share_configured", metadata={"path": str(ready_dir)})
    return ready_dir


async def _wait_for_ready_signal(
    ready_dir: Path, timeout: int = 300, interval: float = 2.0
) -> str | None:
    """Poll the host-side ready directory until the VM writes the ready marker.

    The VM writes its IP address into the file (e.g. "192.168.64.12").
    Falls back gracefully if the template LaunchDaemon has not been updated —
    caller should fall back to ARP discovery.

    Returns the VM's IP address string on success, None on timeout.
    """
    ready_file = ready_dir / "ready"
    elapsed = 0.0
    while elapsed < timeout:
        if ready_file.exists():
            content = ready_file.read_text().strip()
            if content:
                return content
        await asyncio.sleep(interval)
        elapsed += interval
    return None


def _configure_shared_dirs(
    config: dict, volumes: dict[str, dict[str, str]]
) -> list[tuple[str, str]]:
    """Populate SharedDirectories in a VM config dict and return mount mappings.

    Modifies *config* in place.  Returns a list of ``(share_tag, container_path)``
    tuples consumed during the configure phase.
    """
    shared_dirs = config.setdefault("SharedDirectories", [])
    shared_dirs.clear()
    virtiofs_mounts: list[tuple[str, str]] = []

    for host_path, mount_spec in volumes.items():
        container_path = mount_spec["bind"]
        mode = mount_spec.get("mode", "rw")
        read_only = mode == "ro"
        share_tag = f"share-{len(shared_dirs)}"
        shared_dirs.append(
            {
                "DirectoryURL": f"file://{host_path}",
                "ReadOnly": read_only,
                "Name": share_tag,
            }
        )
        virtiofs_mounts.append((share_tag, container_path))

    return virtiofs_mounts


async def _start_vm_and_wait(
    vm_name: str, utmctl: str, ctx: SessionContext, slog
) -> tuple[str, int]:
    """Start a UTM VM and wait for SSH to become available.

    Returns ``(ssh_host, ssh_port)`` for subsequent SSH operations.
    Raises TimeoutError if SSH is not reachable within 300 s (macOS VMs can
    take 3-4 minutes to fully boot).
    """
    slog.info("utm.booting_for_config")
    await _run_subprocess([utmctl, "start", vm_name], timeout=60)

    if ctx.mac_address:
        # Bridged networking - discover IP via ARP
        slog.info("utm.discovering_ip_for_config", metadata={"mac": ctx.mac_address})
        vm_ip = await _discover_vm_ip(ctx.mac_address, timeout=180)
        ctx.vm_ip = vm_ip
        ssh_host: str = vm_ip
        ssh_port: int = 22
        slog.info("utm.ip_discovered", metadata={"ip": vm_ip})
    else:
        # Port forwarding
        ssh_host = "localhost"
        ssh_port = ctx.ssh_port

    slog.info("utm.waiting_for_ssh", metadata={"host": ssh_host, "port": ssh_port})
    ssh_ready = await _wait_for_ssh(ssh_host, ssh_port, timeout=180)
    if not ssh_ready:
        slog.error("utm.ssh_timeout")
        raise TimeoutError(f"SSH not available at {ssh_host}:{ssh_port} after 180s")

    return ssh_host, ssh_port


def _read_host_keychain_credentials() -> str | None:
    """Read Claude Code OAuth credentials from the host macOS Keychain.

    Returns the raw JSON string stored under 'Claude Code-credentials', or None
    if the entry doesn't exist or the read fails.
    """
    import subprocess

    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return None


async def _launch_claude_session(
    ctx: SessionContext,
    ssh_host: str,
    ssh_port: int,
    ssh_key: Path,
    slog,
) -> None:
    """Start a detached tmux session running Claude on the VM.

    Mirrors Docker's ttyd-wrapper.sh: creates a 'main' tmux session, sources
    ~/.env, then launches claude --dangerously-skip-permissions. If a task is
    present (.brainbox/task.txt), sends it as the first prompt.
    """
    if ctx.guest_os == "windows":
        slog.info("utm.claude_session_skipped_windows")
        return

    try:
        # Write start-claude.sh — runs in the GUI session via the session-bootstrap
        # LaunchAgent (com.brainbox.session-bootstrap), which is triggered by the
        # .brainbox-ready file and runs with an unlocked Keychain.
        #
        # Kills any stale Claude from the template auto-login, sources ~/.env for
        # secrets injected by brainbox, then launches Claude.  Task text (if any)
        # is piped in so Claude runs it immediately without requiring tmux.
        wrapper = (
            "#!/bin/bash\n"
            '[ -x /opt/homebrew/bin/brew ] && eval "$(/opt/homebrew/bin/brew shellenv)"\n'
            "source ~/.env 2>/dev/null || true\n"
            "# Kill any stale Claude from the template auto-start\n"
            'pkill -f "claude --dangerously-skip-permissions" 2>/dev/null || true\n'
            "sleep 1\n"
            "# Use full path — Homebrew may not be in PATH when run via nohup/SSH\n"
            "TMUX=/opt/homebrew/bin/tmux\n"
            "# Create (or reuse) a detached tmux session named 'main'\n"
            "if ! $TMUX has-session -t main 2>/dev/null; then\n"
            "    $TMUX -f /dev/null new -d -s main\n"
            "    $TMUX set -t main status off\n"
            "    $TMUX set -t main mouse on\n"
            "fi\n"
            'CLAUDE_CMD="claude --dangerously-skip-permissions"\n'
            '[ -n "$CLAUDE_MODEL" ] && CLAUDE_CMD="$CLAUDE_CMD --model $CLAUDE_MODEL"\n'
            '$TMUX send-keys -t main "source ~/.env 2>/dev/null; $CLAUDE_CMD" Enter\n'
            "# Wait for the Claude prompt then send the task (if any)\n"
            "if [ -f ~/.brainbox/task.txt ]; then\n"
            "    READY=0\n"
            "    for i in $(seq 1 60); do\n"
            "        sleep 2\n"
            "        if $TMUX capture-pane -t main -p 2>/dev/null | grep -qE '^[>❯]'; then\n"
            "            READY=$((READY + 1))\n"
            '            if [ "$READY" -ge 2 ]; then\n'
            "                TASK=$(tr '\\n' ' ' < ~/.brainbox/task.txt | sed 's/  */ /g; s/^ //; s/ $//')\n"
            '                $TMUX send-keys -t main "$TASK" Enter\n'
            "                break\n"
            "            fi\n"
            "        else\n"
            "            READY=0\n"
            "        fi\n"
            "    done\n"
            "fi\n"
        )
        await _ssh_execute(
            ssh_host,
            ssh_port,
            ctx.ssh_user,
            ssh_key,
            f"mkdir -p ~/.brainbox && printf {shlex.quote(wrapper)} > ~/.brainbox/start-claude.sh"
            " && chmod +x ~/.brainbox/start-claude.sh",
        )
        # Run Claude via nohup (survives SSH session close).
        # start-claude.sh creates a tmux 'main' session so the user can attach and watch.
        await _ssh_execute(
            ssh_host,
            ssh_port,
            ctx.ssh_user,
            ssh_key,
            "nohup bash ~/.brainbox/start-claude.sh > ~/.brainbox/start-claude.log 2>&1 &",
        )
        # Also write the trigger file for the session-bootstrap LaunchAgent (if the
        # template has it pre-installed).  The agent will re-run start-claude.sh from
        # the GUI session, which will kill the nohup instance and restart with the
        # same env — no harm done if both paths fire.
        await _ssh_execute(
            ssh_host,
            ssh_port,
            ctx.ssh_user,
            ssh_key,
            "touch ~/.brainbox/.brainbox-ready",
        )
        slog.info("utm.claude_session_launched")
    except Exception as exc:
        slog.warning("utm.claude_session_launch_failed", metadata={"reason": str(exc)})


class UTMBackend:
    """UTM VM backend for brainbox."""

    async def _resolve_ssh_endpoint(
        self, vm_name: str, utmctl: str, ctx: SessionContext, slog
    ) -> tuple[str, int]:
        """Wait for SSH to become available and return (host, port).

        For Bridged VMs: ARP discovery then SSH port poll.
        For Shared/NAT VMs: SSH port poll on localhost:ctx.ssh_port.
        """
        if ctx.mac_address:
            # Bridged: discover IP, then wait for SSH
            slog.info("utm.discovering_ip", metadata={"mac": ctx.mac_address})
            vm_ip = await _discover_vm_ip(ctx.mac_address, timeout=180)
            ctx.vm_ip = vm_ip
            ssh_host, ssh_port = vm_ip, 22
        else:
            # Shared/NAT: localhost port forwarding
            ssh_host, ssh_port = "localhost", ctx.ssh_port

        slog.info("utm.waiting_for_ssh", metadata={"host": ssh_host, "port": ssh_port})
        ssh_ready = await _wait_for_ssh(ssh_host, ssh_port, timeout=180)
        if not ssh_ready:
            raise TimeoutError(f"SSH not available at {ssh_host}:{ssh_port} after 180s")
        return ssh_host, ssh_port

    async def provision(
        self,
        ctx: SessionContext,
        *,
        image_or_template: str,
        volumes: dict[str, dict[str, str]],
        hardening_kwargs: dict[str, Any],
    ) -> SessionContext:
        """Clone UTM VM template and configure shared directories.

        Args:
            ctx: Session context with vm_template field set
            image_or_template: UTM template name (e.g., "brainbox-macos-template")
            volumes: Volume mounts in Docker format {host_path: {"bind": container_path, "mode": "rw"}}
            hardening_kwargs: Ignored for UTM (no container hardening)

        Returns:
            Updated SessionContext with vm_path and ssh_port
        """
        slog = get_logger(session_name=ctx.session_name, container_name=ctx.container_name)
        utm_docs = _get_utm_docs_dir()

        # Validate utmctl exists (raises RuntimeError if not found)
        utmctl = _get_utmctl_path()

        # Validate template exists
        template_path = utm_docs / f"{image_or_template}.utm"
        if not template_path.exists():
            raise FileNotFoundError(
                f"UTM template not found: {template_path}. "
                f"Create a golden image VM named '{image_or_template}' in UTM. "
                "See brainbox/docs/utm-setup.md for setup instructions."
            )

        # Clone VM
        vm_name = f"brainbox-{ctx.session_name}"
        vm_path = utm_docs / f"{vm_name}.utm"

        # Remove existing VM if present (stop + delete via utmctl to clean up registry)
        if vm_path.exists():
            slog.info("utm.removing_existing_vm", metadata={"vm": vm_name})
            try:
                await _run_subprocess([utmctl, "stop", vm_name], check=False)
                await asyncio.sleep(2)
                await _run_subprocess([utmctl, "delete", vm_name], check=False)
            except Exception as exc:
                slog.warning("utm.remove_failed", metadata={"reason": str(exc)})

        # Detect UTM backend type from template config to choose provision path.
        config_plist = utm_docs / f"{image_or_template}.utm" / "config.plist"
        template_backend = "QEMU"
        if config_plist.exists():
            try:
                tpl_config = await asyncio.to_thread(_plist_load, config_plist)
                template_backend = tpl_config.get("Backend", "QEMU")
            except Exception:
                pass
        ctx._utm_backend_type = template_backend  # type: ignore[attr-defined]

        # Allocate SSH port (I/O-bound; run off the event loop)
        ssh_port = await asyncio.to_thread(_find_available_ssh_port)

        try:
            await self._provision_clone(
                ctx, template_backend, image_or_template, vm_name, vm_path,
                utmctl, ssh_port, volumes, slog,
            )
        except Exception as exc:
            slog.error("utm.provision_failed", metadata={"reason": str(exc)})
            raise

        # Write per-session bootstrap files (authorized_keys, env, gitconfig)
        # into brainbox/sessions/<MAC>/ before the VM boots, scoped to this
        # workspace profile so multiple profiles don't cross-contaminate.
        # Also remove any stale ip file so _discover_vm_ip waits for the new
        # VM to write it rather than reusing an address from a previous session.
        if ctx.mac_address:
            workspace_home = os.environ.get("WORKSPACE_HOME", str(Path.home()))
            await asyncio.to_thread(_write_session_dir, ctx.mac_address, workspace_home, slog)
            stale_ip = _sessions_file_for_mac(ctx.mac_address)
            if stale_ip is not None and stale_ip.exists():
                stale_ip.unlink()
                slog.info("utm.stale_ip_cleared", metadata={"mac": ctx.mac_address})

        # Update context
        ctx.vm_path = str(vm_path)
        ctx.ssh_port = ssh_port
        ctx.state = SessionState.CONFIGURING

        slog.info(
            "utm.provisioned",
            metadata={
                "template": image_or_template,
                "vm_name": vm_name,
                "backend_type": template_backend,
                "mac": ctx.mac_address or "n/a",
                "ssh_port": ctx.ssh_port,
                "shared_dirs": len(volumes),
            },
        )

        return ctx

    async def _provision_clone(
        self,
        ctx: SessionContext,
        template_backend: str,
        image_or_template: str,
        vm_name: str,
        vm_path: Path,
        utmctl: str,
        ssh_port: int,
        volumes: dict[str, dict[str, str]],
        slog,
    ) -> None:
        """Clone and configure the VM. Dispatches to AppleScript or plist path."""

        # ── Apple VF path: use mcp-utm AppleScript API ──────────────────
        # AppleScript's `duplicate` + `update configuration` properly
        # updates UTM's in-memory state (random MAC, name). Raw plist
        # edits are ignored by Apple Virtualization.framework.
        if template_backend == "Apple" and _has_mcp_utm():
            from mcp_utm.applescript import clone_vm as as_clone

            slog.info("utm.apple_vf_clone_via_applescript", metadata={
                "template": image_or_template, "clone": vm_name,
            })

            # Clone with random MAC via AppleScript.
            # VirtioFS shares are inherited from the template (configured once
            # via UTM GUI). AppleScript's update registry requires sandboxing
            # entitlements that a background daemon doesn't have.
            clone_config = await asyncio.to_thread(
                as_clone, image_or_template, vm_name, True
            )
            ctx.mac_address = clone_config.mac_address
            ctx.ssh_port = 22
            slog.info("utm.mac_assigned", metadata={"mac": ctx.mac_address})

        # ── QEMU path: plist edits (unchanged) ──────────────────────────
        else:
            # Clone template via utmctl
            await _clone_vm_via_utmctl(utmctl, image_or_template, vm_name, slog)

            config_plist = vm_path / "config.plist"
            if not config_plist.exists():
                raise FileNotFoundError(f"config.plist not found in cloned VM: {config_plist}")

            config = await asyncio.to_thread(_plist_load, config_plist)

            # Update VM name
            config["Name"] = vm_name
            if "Information" not in config:
                config["Information"] = {}
            config["Information"]["Name"] = vm_name

            network_list = config.setdefault("Network", [{"Mode": "Shared"}])
            if not network_list:
                network_list.append({"Mode": "Shared"})
            iface = network_list[0]
            current_mode = iface.get("Mode", "Shared")

            if template_backend == "Apple":
                # Apple VF without mcp-utm: fall back to reading template MAC
                iface.pop("PortForward", None)
                mac_address = iface.get("MacAddress")
                if mac_address:
                    ctx.mac_address = mac_address
                ctx.ssh_port = 22
            elif current_mode == "Bridged":
                # QEMU Bridged: ARP discovery with fresh MAC
                iface.pop("PortForward", None)
                new_mac = _generate_mac()
                iface["MacAddress"] = new_mac
                ctx.mac_address = new_mac
                ctx.ssh_port = 22
                slog.info("utm.mac_assigned", metadata={"mac": new_mac})
            else:
                # QEMU Shared/NAT: port forwarding
                iface["Mode"] = "Shared"
                port_forward = iface.setdefault("PortForward", [])
                port_forward[:] = [
                    rule
                    for rule in port_forward
                    if isinstance(rule, dict) and rule.get("GuestPort") != 22
                ]
                port_forward.append(
                    {
                        "Protocol": "tcp",
                        "GuestAddress": "0.0.0.0",
                        "GuestPort": 22,
                        "HostAddress": "127.0.0.1",
                        "HostPort": ssh_port,
                    }
                )
                ctx.ssh_port = ssh_port

            ctx._virtiofs_mounts = _configure_shared_dirs(config, volumes)  # type: ignore
            if template_backend != "Apple":
                _add_ssh_share(config, slog)

            await asyncio.to_thread(_plist_dump, config_plist, config)

    async def configure(
        self,
        ctx: SessionContext,
        *,
        secrets: dict[str, str],
        env_content: str | None = None,
        oauth_account: dict[str, Any] | None = None,
        profile_env: str | None = None,
    ) -> SessionContext:
        """Boot VM and inject configuration.

        - Apple VF (macOS): waits for VirtioFS ready signal, then SSH batch-inject.
        - QEMU (Linux/Windows): uses utmctl exec (guest agent), no SSH needed.
        """
        slog = get_logger(session_name=ctx.session_name, container_name=ctx.container_name)
        utmctl = _get_utmctl_path()
        vm_name = f"brainbox-{ctx.session_name}"
        backend_type = getattr(ctx, "_utm_backend_type", "QEMU")

        slog.info("utm.booting_for_config", metadata={"vm_backend": backend_type})
        await _run_subprocess([utmctl, "start", vm_name], timeout=60)

        if backend_type == "Apple":
            await self._configure_apple_vf(
                vm_name, utmctl, ctx, secrets, oauth_account, profile_env, slog
            )
        else:
            await self._configure_qemu(
                vm_name, utmctl, ctx, secrets, oauth_account, profile_env, slog
            )

        ctx.state = SessionState.STARTING
        ctx._vm_ready_after_configure = True  # type: ignore[attr-defined]
        slog.info("utm.configured")
        return ctx

    async def _configure_apple_vf(
        self,
        vm_name: str,
        utmctl: str,
        ctx: SessionContext,
        secrets: dict[str, str],
        oauth_account: dict[str, Any] | None,
        profile_env: str | None,
        slog,
    ) -> None:
        """Configure an Apple VF (macOS) VM via SSH using shared functions."""
        from ..configure import (
            inject_claude_config,
            inject_claude_settings,
            inject_config_bundle,
            inject_env_file,
            inject_profile_env,
        )
        from ..executor import SSHExecutor

        ssh_key = _get_ssh_key_path()
        if not ssh_key.exists():
            raise FileNotFoundError(
                f"SSH key not found: {ssh_key}. "
                "Add the public key to the template VM's ~/.ssh/authorized_keys."
            )

        ssh_host, ssh_port = await self._resolve_ssh_endpoint(vm_name, utmctl, ctx, slog)

        executor = SSHExecutor(
            ssh_host,
            ssh_port,
            ctx.ssh_user,
            ssh_key,
            home_dir="/Users/developer",
            guest_os="macos",
        )

        # --- Inject configuration via shared functions ---
        await inject_env_file(executor, secrets, ctx.session_name, slog=slog)

        # Config bundle
        from ...bundle import build_config_bundle

        bundle_bytes = await asyncio.to_thread(build_config_bundle)
        await inject_config_bundle(executor, bundle_bytes, slog=slog)

        await inject_claude_config(executor, oauth_account, slog=slog)
        await inject_claude_settings(executor, slog=slog)
        if profile_env:
            await inject_profile_env(executor, profile_env, slog=slog)

        # Store connection details for start()
        ctx._ssh_host_after_configure = ssh_host  # type: ignore[attr-defined]
        ctx._ssh_port_after_configure = ssh_port  # type: ignore[attr-defined]

    async def _configure_qemu(
        self,
        vm_name: str,
        utmctl: str,
        ctx: SessionContext,
        secrets: dict[str, str],
        oauth_account: dict[str, Any] | None,
        profile_env: str | None,
        slog,
    ) -> None:
        """Configure a QEMU VM via shared functions using QEMUExecExecutor."""
        from ..configure import (
            inject_claude_config,
            inject_claude_settings,
            inject_config_bundle,
            inject_env_file,
            inject_profile_env,
        )
        from ..executor import QEMUExecExecutor

        slog.info("utm.waiting_for_guest_agent")
        ready = await _wait_for_guest_agent(vm_name, utmctl, timeout=300)
        if not ready:
            raise TimeoutError(f"Guest agent not ready in {vm_name} after 300s")
        slog.info("utm.guest_agent_ready")

        guest_os = ctx.guest_os or "linux"
        executor = QEMUExecExecutor(
            vm_name,
            utmctl,
            home_dir="/home/developer",
            guest_os=guest_os,
        )

        await inject_env_file(executor, secrets, ctx.session_name, slog=slog)

        # Config bundle
        from ...bundle import build_config_bundle

        bundle_bytes = await asyncio.to_thread(build_config_bundle)
        await inject_config_bundle(executor, bundle_bytes, slog=slog)

        await inject_claude_config(executor, oauth_account, slog=slog)
        await inject_claude_settings(executor, slog=slog)

        if profile_env:
            await inject_profile_env(executor, profile_env, slog=slog)

    async def start(self, ctx: SessionContext) -> SessionContext:
        """Inject final env, write task, launch Claude.

        VM is already running from configure() in the normal flow.
        Falls back to cold-start if called without prior configure().
        """
        slog = get_logger(session_name=ctx.session_name, container_name=ctx.container_name)
        utmctl = _get_utmctl_path()
        vm_name = f"brainbox-{ctx.session_name}"
        backend_type = getattr(ctx, "_utm_backend_type", "QEMU")

        if not getattr(ctx, "_vm_ready_after_configure", False):
            # Cold-start: VM not left running by configure().
            slog.info("utm.cold_starting_vm")
            await _run_subprocess([utmctl, "start", vm_name], timeout=60)
            if backend_type == "Apple":
                ssh_host, ssh_port = await self._resolve_ssh_endpoint(vm_name, utmctl, ctx, slog)
                ctx._ssh_host_after_configure = ssh_host  # type: ignore[attr-defined]
                ctx._ssh_port_after_configure = ssh_port  # type: ignore[attr-defined]
            else:
                ready = await _wait_for_guest_agent(vm_name, utmctl, timeout=300)
                if not ready:
                    raise TimeoutError(f"Guest agent not ready in {vm_name} after 300s")

        from ...lifecycle import _resolve_profile_env

        profile_env = _resolve_profile_env(
            workspace_profile=ctx.workspace_profile,
            workspace_home=ctx.workspace_home,
        )

        if backend_type == "Apple":
            await self._start_apple_vf(vm_name, utmctl, ctx, profile_env, slog)
        else:
            await self._start_qemu(vm_name, utmctl, ctx, profile_env, slog)

        ctx.state = SessionState.RUNNING
        return ctx

    async def _start_apple_vf(
        self,
        vm_name: str,
        utmctl: str,
        ctx: SessionContext,
        profile_env: str | None,
        slog,
    ) -> None:
        """Finalize Apple VF session: inject remaining env, role prompt, task, launch Claude via SSH."""
        from ..configure import inject_profile_env, inject_role_prompt, inject_task
        from ..executor import SSHExecutor

        ssh_key = _get_ssh_key_path()
        ssh_host = getattr(ctx, "_ssh_host_after_configure", ctx.vm_ip or "localhost")
        ssh_port = getattr(ctx, "_ssh_port_after_configure", ctx.ssh_port or 22)

        executor = SSHExecutor(
            ssh_host,
            ssh_port,
            ctx.ssh_user,
            ssh_key,
            home_dir="/Users/developer",
            guest_os="macos",
        )

        if profile_env:
            await inject_profile_env(executor, profile_env, slog=slog)

        # Role prompt injection (new for UTM — previously Docker-only)
        if ctx.role_prompt_file:
            from ...registry import get_role_prompt

            prompt_content = get_role_prompt(ctx.role)
            if prompt_content:
                await inject_role_prompt(executor, ctx.role, prompt_content, slog=slog)

        if ctx.task_description:
            await inject_task(executor, ctx.task_description, slog=slog)

        await _launch_claude_session(ctx, ssh_host, ssh_port, ssh_key, slog)

    async def _start_qemu(
        self,
        vm_name: str,
        utmctl: str,
        ctx: SessionContext,
        profile_env: str | None,
        slog,
    ) -> None:
        """Finalize QEMU session: inject remaining env, role prompt, task, launch Claude via utmctl exec."""
        from ..configure import inject_profile_env, inject_role_prompt, inject_task
        from ..executor import QEMUExecExecutor

        guest_os = ctx.guest_os or "linux"
        executor = QEMUExecExecutor(
            vm_name,
            utmctl,
            home_dir="/home/developer",
            guest_os=guest_os,
        )

        if profile_env:
            await inject_profile_env(executor, profile_env, slog=slog)

        # Role prompt injection (new for UTM — previously Docker-only)
        if ctx.role_prompt_file:
            from ...registry import get_role_prompt

            prompt_content = get_role_prompt(ctx.role)
            if prompt_content:
                await inject_role_prompt(executor, ctx.role, prompt_content, slog=slog)

        if ctx.task_description:
            await inject_task(executor, ctx.task_description, slog=slog)

        if ctx.guest_os != "windows":
            wrapper = (
                "#!/bin/bash\n"
                '[ -x /opt/homebrew/bin/brew ] && eval "$(/opt/homebrew/bin/brew shellenv)"\n'
                "[ -f ~/.zshrc ] && source ~/.zshrc 2>/dev/null || true\n"
                "if tmux has-session -t main 2>/dev/null; then\n"
                "    exit 0\n"
                "fi\n"
                "tmux -f /dev/null new -d -s main\n"
                "tmux set -t main status off\n"
                "tmux set -t main mouse on\n"
                'CLAUDE_CMD="claude --dangerously-skip-permissions"\n'
                '[ -n "$CLAUDE_MODEL" ] && CLAUDE_CMD="$CLAUDE_CMD --model $CLAUDE_MODEL"\n'
                'tmux send-keys -t main "source ~/.env 2>/dev/null; $CLAUDE_CMD" Enter\n'
                "READY=0\n"
                "for i in $(seq 1 60); do\n"
                "    sleep 2\n"
                "    if [ -f ~/.brainbox/task.txt ] && \\\n"
                "       tmux capture-pane -t main -p 2>/dev/null | grep -qE '^❯'; then\n"
                "        READY=$((READY + 1))\n"
                '        if [ "$READY" -ge 2 ]; then\n'
                "            TASK=$(tr '\\n' ' ' < ~/.brainbox/task.txt | sed 's/  */ /g; s/^ //; s/ $//')\n"
                '            tmux send-keys -t main "$TASK" Enter\n'
                "            break\n"
                "        fi\n"
                "    else\n"
                "        READY=0\n"
                "    fi\n"
                "done\n"
            )
            await _vm_exec(
                vm_name,
                utmctl,
                [
                    "/bin/sh",
                    "-c",
                    "mkdir -p ~/.brainbox && cat > ~/.brainbox/start-claude.sh && chmod +x ~/.brainbox/start-claude.sh",
                ],
                stdin_data=wrapper.encode(),
                timeout=15,
            )
            await _vm_exec(
                vm_name,
                utmctl,
                [
                    "/bin/sh",
                    "-c",
                    "nohup bash ~/.brainbox/start-claude.sh > ~/.brainbox/start-claude.log 2>&1 &",
                ],
                timeout=10,
            )
            slog.info("utm.claude_session_launched")

    async def stop(self, ctx: SessionContext) -> SessionContext:
        """Shut down UTM VM.

        Args:
            ctx: Session context

        Returns:
            Updated SessionContext
        """
        utmctl = _get_utmctl_path()
        vm_name = f"brainbox-{ctx.session_name}"

        try:
            await _run_subprocess([utmctl, "stop", vm_name], timeout=60)
        except Exception as e:
            log.debug("utm.stop_failed", metadata={"reason": str(e)})

        return ctx

    async def remove(self, ctx: SessionContext) -> SessionContext:
        """Stop UTM VM and delete .utm package.

        Args:
            ctx: Session context with vm_path

        Returns:
            Updated SessionContext
        """
        slog = get_logger(session_name=ctx.session_name, container_name=ctx.container_name)
        utmctl = _get_utmctl_path()
        vm_name = f"brainbox-{ctx.session_name}"

        # Stop VM first
        try:
            await _run_subprocess([utmctl, "stop", vm_name], timeout=60, check=False)
            await asyncio.sleep(2)  # Give VM time to fully stop
        except Exception as e:
            slog.debug("utm.stop_before_remove_failed", metadata={"reason": str(e)})

        # Delete via utmctl (cleans up registry entry) then fall back to rmtree
        try:
            await _run_subprocess([utmctl, "delete", vm_name], timeout=30, check=False)
            slog.info("utm.removed", metadata={"vm": vm_name})
        except Exception as exc:
            slog.warning("utm.utmctl_delete_failed", metadata={"reason": str(exc)})
            if ctx.vm_path:
                vm_path = Path(ctx.vm_path)
                if vm_path.exists():
                    shutil.rmtree(vm_path)
                    slog.info("utm.removed_via_rmtree", metadata={"path": str(vm_path)})

        # Clean up per-session directory from brainbox share
        if ctx.mac_address:
            _remove_session_dir(ctx.mac_address, slog)

        return ctx

    async def health_check(self, ctx: SessionContext) -> dict[str, Any]:
        """Check UTM VM state via utmctl status + guest agent ping."""
        utmctl = _get_utmctl_path()
        vm_name = f"brainbox-{ctx.session_name}"

        try:
            returncode, stdout, stderr = await _run_subprocess(
                [utmctl, "status", vm_name], timeout=10, check=False
            )
            if returncode != 0:
                return {
                    "backend": "utm",
                    "healthy": False,
                    "reason": f"utmctl status failed: {stderr}",
                }

            vm_state = stdout.strip().lower()
            # utmctl reports "started" for Apple VF VMs and "running" for QEMU VMs.
            if vm_state not in ("running", "started"):
                return {
                    "backend": "utm",
                    "healthy": False,
                    "reason": f"VM not running (state: {vm_state})",
                }

            # Ping guest agent
            try:
                rc, _, _ = await _vm_exec(vm_name, utmctl, ["/bin/echo", "ok"], timeout=5)
                agent_ok = rc == 0
            except Exception:
                agent_ok = False

            return {
                "backend": "utm",
                "healthy": agent_ok,
                "vm_state": vm_state,
                "guest_agent": agent_ok,
            }

        except Exception as exc:
            return {"backend": "utm", "healthy": False, "reason": str(exc)}

    async def exec_command(
        self, ctx: SessionContext, command: list[str], **kwargs: Any
    ) -> tuple[int, bytes]:
        """Execute command in UTM VM via utmctl exec."""
        utmctl = _get_utmctl_path()
        vm_name = f"brainbox-{ctx.session_name}"

        returncode, stdout, stderr = await _vm_exec(
            vm_name, utmctl, command, timeout=kwargs.get("timeout", 30)
        )
        return returncode, (stdout + stderr).encode("utf-8")

    def get_sessions_info(self) -> list[dict[str, Any]]:
        """List all managed UTM VMs (brainbox- prefix).

        Returns:
            List of session info dicts
        """
        sessions = []
        try:
            utm_docs = _get_utm_docs_dir()
            if not utm_docs.exists():
                return sessions

            utmctl = _get_utmctl_path()
            if not Path(utmctl).exists():
                return sessions

            # Find all brainbox VMs
            for vm_dir in utm_docs.glob("brainbox-*.utm"):
                vm_name = vm_dir.stem  # Remove .utm extension
                session_name = vm_name.replace("brainbox-", "")

                # Get VM state via utmctl
                try:
                    result = subprocess.run(
                        [utmctl, "status", vm_name],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    vm_state = (
                        result.stdout.strip().lower() if result.returncode == 0 else "unknown"
                    )
                    is_running = vm_state == "running"
                except Exception as e:
                    log.debug("utm.vm_status_check_failed", metadata={"reason": str(e)})
                    vm_state = "unknown"
                    is_running = False

                # Read config for SSH port
                ssh_port = None
                try:
                    config_plist = vm_dir / "config.plist"
                    if config_plist.exists():
                        with config_plist.open("rb") as f:
                            config = plistlib.load(f)
                        qemu = config.get("Qemu", {})
                        network = qemu.get("Network", {})
                        port_forward = network.get("PortForward", [])
                        for rule in port_forward:
                            if isinstance(rule, dict) and rule.get("GuestPort") == 22:
                                ssh_port = rule.get("HostPort")
                                break
                except Exception as e:
                    log.debug("utm.vm_config_read_failed", metadata={"reason": str(e)})

                sessions.append(
                    {
                        "backend": "utm",
                        "name": vm_name,
                        "session_name": session_name,
                        "port": ssh_port,
                        "url": None,  # No web terminal for UTM
                        "volume": "-",  # VirtioFS mounts not easily listed
                        "active": is_running,
                        "vm_state": vm_state,
                        "ssh_port": ssh_port,
                    }
                )

        except Exception as exc:
            log.error("utm.list_sessions_failed", metadata={"reason": str(exc)})

        return sessions

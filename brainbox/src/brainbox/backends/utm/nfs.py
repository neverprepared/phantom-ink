"""NFS export management for UTM VM volume mounts.

Manages macOS ``/etc/exports`` and ``nfsd`` to share host directories
with Apple VF VMs over NFS. Scoped to the UTM NAT subnet (192.168.64.0/24).
"""

from __future__ import annotations

import asyncio
import os
import re
import shlex
from pathlib import Path
from typing import Any

from ...log import get_logger

log = get_logger()

EXPORTS_FILE = Path("/etc/exports")
DEFAULT_NETWORK = "-network 192.168.64.0 -mask 255.255.255.0"


def _host_mapall() -> str:
    """Return mapall option using the current user's UID:GID."""
    uid = os.getuid()
    gid = os.getgid()
    return f"-mapall={uid}:{gid}"


# ---------------------------------------------------------------------------
# Parse /etc/exports
# ---------------------------------------------------------------------------

def list_nfs_exports() -> list[dict[str, str]]:
    """Parse /etc/exports and return structured entries.

    Returns list of {"path": "/abs/path", "options": "-network ... -mapall=..."}.
    """
    if not EXPORTS_FILE.exists():
        return []

    entries = []
    for line in EXPORTS_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Format: /path/to/dir -option1 -option2 ...
        # Path may be quoted if it contains spaces
        if line.startswith('"'):
            match = re.match(r'"([^"]+)"\s*(.*)', line)
            if match:
                entries.append({"path": match.group(1), "options": match.group(2).strip()})
        else:
            parts = line.split(None, 1)
            entries.append({
                "path": parts[0],
                "options": parts[1].strip() if len(parts) > 1 else "",
            })
    return entries


def _export_line(host_path: str, network: str = DEFAULT_NETWORK) -> str:
    """Build an /etc/exports line for a path."""
    mapall = _host_mapall()
    # Quote path if it contains spaces
    path_part = f'"{host_path}"' if " " in host_path else host_path
    return f"{path_part} {network} {mapall}"


def _path_is_exported(host_path: str) -> bool:
    """Check if a path is already in /etc/exports."""
    normalized = host_path.rstrip("/")
    for entry in list_nfs_exports():
        if entry["path"].rstrip("/") == normalized:
            return True
    return False


# ---------------------------------------------------------------------------
# Manage exports
# ---------------------------------------------------------------------------

async def ensure_nfs_export(
    host_path: str,
    network: str = DEFAULT_NETWORK,
    slog: Any = None,
) -> bool:
    """Add host_path to /etc/exports if not present, then reload nfsd.

    Returns True if the export was added (or already present).
    Raises RuntimeError on failure.
    """
    slog = slog or log
    normalized = Path(host_path).resolve()
    if not normalized.exists():
        raise FileNotFoundError(f"NFS export path does not exist: {normalized}")

    host_path_str = str(normalized)

    if _path_is_exported(host_path_str):
        slog.info("nfs.export_exists", metadata={"path": host_path_str})
        return True

    line = _export_line(host_path_str, network)

    # Append to /etc/exports via sudo tee -a
    proc = await asyncio.create_subprocess_exec(
        "sudo", "tee", "-a", str(EXPORTS_FILE),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate(input=f"{line}\n".encode())
    if proc.returncode != 0:
        raise RuntimeError(f"Failed to write /etc/exports: {stderr.decode().strip()}")

    slog.info("nfs.export_added", metadata={"path": host_path_str, "line": line})

    # Reload nfsd
    await _reload_nfsd(slog)
    return True


async def remove_nfs_export(host_path: str, slog: Any = None) -> bool:
    """Remove host_path from /etc/exports and reload nfsd.

    Returns True if the export was removed (or wasn't present).
    """
    slog = slog or log
    normalized = str(Path(host_path).resolve()).rstrip("/")

    if not EXPORTS_FILE.exists():
        return True

    lines = EXPORTS_FILE.read_text().splitlines()
    new_lines = []
    removed = False
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            new_lines.append(line)
            continue
        # Extract path from line
        if stripped.startswith('"'):
            match = re.match(r'"([^"]+)"', stripped)
            entry_path = match.group(1) if match else ""
        else:
            entry_path = stripped.split(None, 1)[0]

        if entry_path.rstrip("/") == normalized:
            removed = True
            slog.info("nfs.export_removed", metadata={"path": normalized})
            continue
        new_lines.append(line)

    if removed:
        content = "\n".join(new_lines)
        if content and not content.endswith("\n"):
            content += "\n"
        # Write via sudo tee
        proc = await asyncio.create_subprocess_exec(
            "sudo", "tee", str(EXPORTS_FILE),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate(input=content.encode())
        await _reload_nfsd(slog)

    return True


async def _reload_nfsd(slog: Any = None) -> None:
    """Check exports and reload nfsd."""
    slog = slog or log

    # Validate exports first
    proc = await asyncio.create_subprocess_exec(
        "sudo", "nfsd", "checkexports",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        err = stderr.decode().strip()
        slog.error("nfs.checkexports_failed", metadata={"output": err})
        raise RuntimeError(f"nfsd checkexports failed: {err}")

    # Start or restart nfsd
    proc = await asyncio.create_subprocess_exec(
        "sudo", "nfsd", "restart",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        # nfsd restart may fail if not running; try start
        proc = await asyncio.create_subprocess_exec(
            "sudo", "nfsd", "start",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

    slog.info("nfs.nfsd_reloaded")


# ---------------------------------------------------------------------------
# VM-side mount
# ---------------------------------------------------------------------------

async def mount_nfs_in_vm(
    executor: Any,
    host_ip: str,
    host_path: str,
    guest_path: str,
    slog: Any = None,
) -> bool:
    """Mount an NFS share inside the VM via SSH.

    Args:
        executor: SSHExecutor for the VM
        host_ip: Host IP reachable from VM (192.168.64.1 for Apple VF)
        host_path: Exported path on the host
        guest_path: Mount point inside the VM
        slog: Structured logger

    Returns True on success.
    """
    slog = slog or log

    # Create mount point
    rc, out = await executor.exec_shell(
        f"sudo mkdir -p {shlex.quote(guest_path)} && "
        f"sudo chown $(whoami) {shlex.quote(guest_path)}",
        timeout=15,
    )
    if rc != 0:
        slog.warning("nfs.mkdir_failed", metadata={
            "guest_path": guest_path, "output": out,
        })
        return False

    # Mount NFS
    mount_cmd = (
        f"sudo mount -t nfs "
        f"-o resvport,rw,nolock "
        f"{host_ip}:{shlex.quote(host_path)} {shlex.quote(guest_path)}"
    )
    rc, out = await executor.exec_shell(mount_cmd, timeout=30)
    if rc != 0:
        slog.warning("nfs.mount_failed", metadata={
            "host_path": host_path, "guest_path": guest_path, "output": out,
        })
        return False

    slog.info("nfs.mounted", metadata={
        "host_path": host_path, "guest_path": guest_path,
    })
    return True

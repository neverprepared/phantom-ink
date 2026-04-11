#!/usr/bin/env python3
"""Test SSH connectivity to UTM template VMs.

Usage:
    uv run python scripts/test-utm-ssh.py [--vm macos|windows|both]

Starts the template VM, discovers its IP (bridged) or uses port forwarding (shared),
then verifies SSH access with the workspace profile key.

The VM is stopped after testing unless --keep is passed.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


UTM_DOCS = Path.home() / "Library" / "Containers" / "com.utmapp.UTM" / "Data" / "Documents"
TEMPLATES = {
    "macos": "brainbox-macos-template",
    "windows": "brainbox-windows-template",
}
SSH_USERS = {
    "macos": "developer",
    "windows": "developer",
}


def get_utmctl() -> str:
    for p in ("/usr/local/bin/utmctl", "/opt/homebrew/bin/utmctl"):
        if Path(p).exists():
            return p
    found = shutil.which("utmctl")
    if found:
        return found
    sys.exit("utmctl not found — install UTM CLI tools")


def get_ssh_key() -> Path:
    workspace_home = os.environ.get("WORKSPACE_HOME")
    if workspace_home:
        k = Path(workspace_home) / ".ssh" / "id_ed25519"
        if k.exists():
            return k
    k = Path.home() / ".ssh" / "id_ed25519"
    if k.exists():
        return k
    sys.exit("No SSH key found at $WORKSPACE_HOME/.ssh/id_ed25519 or ~/.ssh/id_ed25519")


def get_mac_address(template_name: str) -> str | None:
    import plistlib
    plist = UTM_DOCS / f"{template_name}.utm" / "config.plist"
    with plist.open("rb") as f:
        config = plistlib.load(f)
    net = config.get("Network", [])
    if net:
        return net[0].get("MacAddress")
    return None


def get_network_mode(template_name: str) -> str:
    import plistlib
    plist = UTM_DOCS / f"{template_name}.utm" / "config.plist"
    with plist.open("rb") as f:
        config = plistlib.load(f)
    net = config.get("Network", [])
    if net:
        return net[0].get("Mode", "Shared")
    return "Shared"


async def discover_ip(mac: str, timeout: int = 90) -> str:
    """Discover VM IP via ARP table."""
    mac_lower = mac.lower()
    # Normalize for flexible matching (strip leading zeros from each octet)
    mac_parts = mac_lower.split(":")
    mac_norm = ":".join(p.lstrip("0") or "0" for p in mac_parts)

    print(f"  Waiting for VM to appear in ARP table (MAC: {mac_lower})...")
    start = asyncio.get_event_loop().time()
    while (asyncio.get_event_loop().time() - start) < timeout:
        proc = await asyncio.create_subprocess_exec(
            "arp", "-a",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        for line in stdout.decode().splitlines():
            line_lower = line.lower()
            if mac_norm in line_lower or mac_lower in line_lower:
                m = re.search(r"\(([0-9.]+)\)", line)
                if m:
                    return m.group(1)
        await asyncio.sleep(2)
    raise TimeoutError(f"VM IP not found after {timeout}s")


async def wait_for_ssh(host: str, port: int, timeout: int = 120) -> bool:
    """Poll SSH port using nc (more reliable than asyncio.open_connection on macOS)."""
    elapsed = 0
    while elapsed < timeout:
        proc = await asyncio.create_subprocess_exec(
            "nc", "-z", "-w", "3", host, str(port),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(proc.communicate(), timeout=5)
            if proc.returncode == 0:
                return True
        except asyncio.TimeoutError:
            proc.kill()
        await asyncio.sleep(2)
        elapsed += 2
    return False


async def test_ssh(host: str, port: int, user: str, key: Path, os_name: str) -> bool:
    """Run a simple SSH command to verify connectivity."""
    if os_name == "windows":
        cmd_str = "powershell -Command \"echo ssh-ok\""
    else:
        cmd_str = "echo ssh-ok"

    proc = await asyncio.create_subprocess_exec(
        "ssh",
        "-i", str(key),
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "LogLevel=ERROR",
        "-o", "ConnectTimeout=10",
        "-p", str(port),
        f"{user}@{host}",
        cmd_str,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
    if proc.returncode == 0 and b"ssh-ok" in stdout:
        return True
    print(f"    SSH stdout: {stdout.decode().strip()!r}")
    print(f"    SSH stderr: {stderr.decode().strip()!r}")
    return False


async def test_vm(os_name: str, utmctl: str, key: Path, keep: bool) -> bool:
    template = TEMPLATES[os_name]
    user = SSH_USERS[os_name]
    print(f"\n{'='*60}")
    print(f"Testing {os_name.upper()} — template: {template}")
    print(f"{'='*60}")

    # Check template exists
    template_path = UTM_DOCS / f"{template}.utm"
    if not template_path.exists():
        print(f"  ERROR: Template not found: {template_path}")
        return False

    mac = get_mac_address(template)
    mode = get_network_mode(template)
    print(f"  Network mode: {mode}  MAC: {mac}")

    # Start VM
    print(f"  Starting {template}...")
    result = subprocess.run([utmctl, "start", template], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ERROR starting VM: {result.stderr}")
        return False
    print("  VM started")

    try:
        ssh_host: str
        ssh_port: int

        if mac and mode in ("Bridged", "Shared"):
            # Both modes may use ARP for Apple VMs; Bridged QEMU VMs also use ARP
            if mode == "Bridged" or os_name == "macos":
                print(f"  Discovering IP via ARP...")
                try:
                    ip = await discover_ip(mac, timeout=90)
                    print(f"  IP discovered: {ip}")
                    ssh_host = ip
                    ssh_port = 22
                except TimeoutError as e:
                    print(f"  ERROR: {e}")
                    return False
            else:
                # Shared mode with port forwarding — not currently set up on templates
                print("  Shared mode without port forwarding — cannot test directly")
                return False
        else:
            print("  ERROR: No MAC address found in template config")
            return False

        # Wait for SSH port
        print(f"  Waiting for SSH at {ssh_host}:{ssh_port} (macOS VMs can take 3-4 min to boot)...")
        ready = await wait_for_ssh(ssh_host, ssh_port, timeout=300)
        if not ready:
            print("  ERROR: SSH port did not open within 300s")
            return False
        print("  SSH port is open")

        # Test SSH with profile key
        print(f"  Testing SSH as {user}@{ssh_host}:{ssh_port} with key {key}...")
        ok = await test_ssh(ssh_host, ssh_port, user, key, os_name)
        if ok:
            print("  SUCCESS: SSH authentication works with profile key")
        else:
            print("  FAILED: SSH authentication failed")
        return ok

    finally:
        if not keep:
            print(f"  Stopping {template}...")
            subprocess.run([utmctl, "stop", template], capture_output=True)
            print("  VM stopped")
        else:
            print(f"  --keep: leaving {template} running at {ssh_host}:{ssh_port}")


async def main():
    parser = argparse.ArgumentParser(description="Test SSH to UTM template VMs")
    parser.add_argument("--vm", choices=["macos", "windows", "both"], default="both")
    parser.add_argument("--keep", action="store_true", help="Leave VM running after test")
    args = parser.parse_args()

    utmctl = get_utmctl()
    key = get_ssh_key()

    print(f"utmctl: {utmctl}")
    print(f"SSH key: {key}")

    vms = ["macos", "windows"] if args.vm == "both" else [args.vm]
    results: dict[str, bool] = {}

    for vm in vms:
        results[vm] = await test_vm(vm, utmctl, key, args.keep)

    print(f"\n{'='*60}")
    print("RESULTS:")
    for vm, ok in results.items():
        status = "PASS" if ok else "FAIL"
        print(f"  {vm}: {status}")
    print(f"{'='*60}")

    if not all(results.values()):
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

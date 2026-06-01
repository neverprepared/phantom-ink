"""Shared guest configuration functions.

Each function takes a GuestExecutor and injects one piece of configuration
into the guest environment. Backend-specific code (Docker, UTM SSH, UTM QEMU)
constructs the appropriate executor and calls these functions in sequence.
"""

from __future__ import annotations

import io
import json
import shlex
import tarfile
from pathlib import Path
from typing import Any

from ..log import get_logger
from .executor import GuestExecutor
from .utils import _extract_from_bundle

log = get_logger()

# Container-optimized MCP server commands.
# These override the host-side commands (npx/uvx/uv run) written into
# .claude.json with pre-installed container binaries for zero-latency startup.
# Keys match the mcpServers key names in .claude.json.
_CONTAINER_MCP_OVERRIDES: dict[str, dict] = {
    "brainbox": {
        "command": "brainbox-mcp",
        "args": [],
        "env": {"BRAINBOX_URL": "${BRAINBOX_URL:-http://host.docker.internal:9999}"},
    },
    "obsidian-second-brain": {
        "command": "mcp-obsidian-second-brain",
        "args": [],
        "env": {
            "OBSIDIAN_VAULT_PATH": "${OBSIDIAN_VAULT_PATH}",
            "LOG_LEVEL": "${LOG_LEVEL:-info}",
        },
    },
    "google-workspace": {
        "command": "workspace-mcp",
        "args": ["--tool-tier", "core"],
    },
    "uptime-kuma": {
        "command": "mcp-uptime-kuma",
        "args": [],
    },
    "cloudflare-dns": {
        "command": "mcp-cloudflare",
        "args": [],
    },
    "markdown-to-confluence": {
        "command": "mcp-markdown-to-confluence",
        "args": [],
    },
}


def _escape_powershell_value(value: str) -> str:
    """Escape a string for use in a PowerShell single-quoted string context.

    Escapes backtick, dollar, braces (for double-quoted outer shell safety),
    and single quote (the only escape needed in PS single-quoted strings).
    """
    return (
        value
        .replace("`", "``")
        .replace("$", "`$")
        .replace("{", "`{")
        .replace("}", "`}")
        .replace("'", "''")
    )


async def inject_env_file(
    executor: GuestExecutor,
    secrets: dict[str, str],
    session_name: str,
    *,
    slog: Any | None = None,
) -> None:
    """Write secrets to ~/.env and ~/.agent-token.

    Also appends LANGFUSE_SESSION_ID to .env.

    For Windows guests, uses PowerShell; for Linux/macOS, uses POSIX shell.
    """
    slog = slog or log
    home = executor.home_dir

    try:
        if executor.guest_os == "windows":
            # Clear .env
            await executor.exec_shell(
                "powershell -Command \"Set-Content -Path $env:USERPROFILE\\.env -Value '' -Force\""
            )
            for key, value in secrets.items():
                escaped_value = _escape_powershell_value(value)
                if key == "agent-token":
                    await executor.exec_shell(
                        f'powershell -Command "Set-Content -Path $env:USERPROFILE\\.agent-token'
                        f" -Value '{escaped_value}' -Force\""
                    )
                else:
                    await executor.exec_shell(
                        f'powershell -Command "Add-Content -Path $env:USERPROFILE\\.env'
                        f" -Value '{key}={escaped_value}'\""
                    )
            # LANGFUSE_SESSION_ID
            await executor.exec_shell(
                f'powershell -Command "Add-Content -Path $env:USERPROFILE\\.env'
                f" -Value 'LANGFUSE_SESSION_ID={session_name}'\""
            )
        else:
            # Linux / macOS — create .env with secure permissions
            env_path = f"{home}/.env"
            await executor.exec_shell(f"rm -f {env_path} && umask 077 && touch {env_path}")

            for key, value in secrets.items():
                escaped_value = shlex.quote(value)
                if key == "agent-token":
                    token_path = f"{home}/.agent-token"
                    await executor.exec_shell(
                        f"umask 077 && echo {escaped_value} > {token_path}"
                        f" && chmod 400 {token_path}"
                    )
                    # Also export as BRAINBOX_TOKEN env var so Claude Code can use it directly
                    await executor.exec_shell(
                        f"echo {shlex.quote(f'export BRAINBOX_TOKEN={escaped_value}')} >> {env_path}"
                    )
                else:
                    await executor.exec_shell(
                        f"echo {shlex.quote(f'export {key}={escaped_value}')} >> {env_path}"
                    )

            # LANGFUSE_SESSION_ID
            langfuse_line = f"export LANGFUSE_SESSION_ID={session_name}"
            await executor.exec_shell(f"echo {shlex.quote(langfuse_line)} >> {env_path}")

        slog.info("configure.env_injected", metadata={"count": len(secrets)})

    except Exception as exc:
        slog.error("configure.env_inject_failed", metadata={"reason": str(exc)})
        raise


async def inject_claude_config(
    executor: GuestExecutor,
    oauth_account: dict[str, Any] | None,
    *,
    slog: Any | None = None,
) -> None:
    """Write Claude Code onboarding + auth state (.claude.json or .credentials.json).

    For Docker/Linux: patches ~/.claude.json via python3.
    For macOS: writes ~/.claude/.credentials.json (Keychain injection is handled
    separately by the session-bootstrap LaunchAgent).
    For Windows: patches via PowerShell.
    """
    slog = slog or log
    home = executor.home_dir

    if executor.guest_os == "windows":
        if not oauth_account:
            return
        try:
            patch_json = json.dumps(
                {
                    "hasCompletedOnboarding": True,
                    "bypassPermissionsModeAccepted": True,
                    "theme": "light",
                    "syntaxTheme": "monokai_extended",
                    "oauthAccount": oauth_account,
                }
            )
            escaped_patch = patch_json.replace('"', '\\"')
            await executor.exec_shell(
                f'powershell -Command "'
                f'$p = "$env:USERPROFILE\\.claude.json"; '
                f"$d = if (Test-Path $p) {{ Get-Content $p | ConvertFrom-Json }} else {{ {{}} }}; "
                f"$patch = '{escaped_patch}' | ConvertFrom-Json; "
                f"$patch.PSObject.Properties | ForEach-Object "
                f"{{ $d | Add-Member -Force -NotePropertyName $_.Name -NotePropertyValue $_.Value }}; "
                f'$d | ConvertTo-Json -Depth 10 | Set-Content $p"',
            )
            slog.info("configure.claude_config_patched")
        except Exception as exc:
            slog.warning("configure.claude_config_patch_failed", metadata={"reason": str(exc)})
        return

    if executor.guest_os == "macos":
        # macOS: write .credentials.json for OAuth (Keychain handled by LaunchAgent)
        if oauth_account:
            try:
                patch_json = json.dumps(
                    {
                        "hasCompletedOnboarding": True,
                        "bypassPermissionsModeAccepted": True,
                        "oauthAccount": oauth_account,
                    }
                )
                escaped_patch = shlex.quote(patch_json)
                await executor.exec_shell(
                    f"mkdir -p {home}/.claude"
                    f" && echo {escaped_patch} > {home}/.claude/.credentials.json"
                )
            except Exception as exc:
                slog.warning("configure.claude_config_patch_failed", metadata={"reason": str(exc)})

        # Also patch .claude.json with onboarding + bypass acceptance flags —
        # Claude Code CLI reads these from .claude.json, not .credentials.json.
        try:
            claude_json_patch: dict[str, Any] = {
                "hasCompletedOnboarding": True,
                "bypassPermissionsModeAccepted": True,
                "theme": "dark",
                "syntaxTheme": "monokai_extended",
            }
            patch_json = json.dumps(claude_json_patch)
            p_j = json.dumps(f"{home}/.claude.json").replace('"', '\\"')
            await executor.exec_shell(
                f'echo {shlex.quote(patch_json)} | python3 -c "'
                "import json, pathlib, sys; "
                f"p = pathlib.Path({p_j}); "
                "d = json.loads(p.read_text()) if p.exists() else {}; "
                "d.update(json.load(sys.stdin)); "
                "p.write_text(json.dumps(d, indent=2))"
                '"'
            )
        except Exception as exc:
            slog.warning("configure.claude_json_patch_failed", metadata={"reason": str(exc)})

        slog.info("configure.claude_config_patched")
        return

    # Linux (Docker): patch .claude.json via python3
    claude_json_patch: dict[str, Any] = {
        "hasCompletedOnboarding": True,
        "bypassPermissionsModeAccepted": True,
        "theme": "light",
        "syntaxTheme": "monokai_extended",
    }
    if oauth_account:
        claude_json_patch["oauthAccount"] = oauth_account

    try:
        patch_json = json.dumps(claude_json_patch)
        p_j = json.dumps(f"{home}/.claude.json").replace('"', '\\"')
        await executor.exec_shell(
            f'echo {shlex.quote(patch_json)} | python3 -c "'
            "import json, pathlib, sys; "
            f"p = pathlib.Path({p_j}); "
            "d = json.loads(p.read_text()) if p.exists() else {}; "
            "d.update(json.load(sys.stdin)); "
            "p.write_text(json.dumps(d, indent=2))"
            '"'
        )
        slog.info("configure.claude_config_patched")
    except Exception as exc:
        slog.warning("configure.claude_config_patch_failed", metadata={"reason": str(exc)})


async def inject_claude_settings(
    executor: GuestExecutor,
    *,
    slog: Any | None = None,
) -> None:
    """Write a workspace-level settings.local.json to override profile settings.

    The profile .claude directory is mounted read-only (CLAUDE_CONFIG_DIR), so
    we cannot write to its settings.json.  Instead we write a project-local
    settings.local.json in the container workspace that:
      - enables bypassPermissions / skipDangerousModePermissionPrompt
      - clears enabledPlugins so host-only LSP plugins (gopls, swift-lsp, …)
        don't hang at startup inside the Linux container

    For Windows: uses PowerShell.
    For Linux/macOS: uses python3.
    """
    slog = slog or log
    home = executor.home_dir

    try:
        if executor.guest_os == "windows":
            await executor.exec_shell(
                'powershell -Command "'
                '$p = "$env:USERPROFILE\\.claude\\settings.json"; '
                "$d = if (Test-Path $p) { Get-Content $p | ConvertFrom-Json } "
                "else { [PSCustomObject]@{} }; "
                "Add-Member -InputObject $d -Force -NotePropertyName bypassPermissions "
                "-NotePropertyValue $true; "
                "Add-Member -InputObject $d -Force -NotePropertyName skipDangerousModePermissionPrompt "
                "-NotePropertyValue $true; "
                "Add-Member -InputObject $d -Force -NotePropertyName bypassPermissionsModeAccepted "
                "-NotePropertyValue $true; "
                "Add-Member -InputObject $d -Force -NotePropertyName theme "
                "-NotePropertyValue dark; "
                "$a = [PSCustomObject]@{commit='';pr=''}; "
                "Add-Member -InputObject $d -Force -NotePropertyName attribution "
                "-NotePropertyValue $a; "
                '$d | ConvertTo-Json -Depth 10 | Set-Content $p"'
            )
        else:
            # Write user-level settings.json with theme so Claude Code doesn't prompt
            # on first launch. Project-local settings.local.json carries bypass flags.
            user_settings_json = (
                '{"theme":"light",'
                '"bypassPermissions":true,'
                '"skipDangerousModePermissionPrompt":true,'
                '"bypassPermissionsModeAccepted":true,'
                '"enabledPlugins":{},'
                '"attribution":{"commit":"","pr":""}}'
            )
            await executor.exec_shell(
                f"mkdir -p {home}/.claude && "
                f"echo {shlex.quote(user_settings_json)} > {home}/.claude/settings.json"
            )
            # Write settings.local.json to all common container working directories.
            # Project-local settings override user settings and don't conflict with
            # the read-only mounted CLAUDE_CONFIG_DIR.
            settings_json = (
                '{"bypassPermissions":true,'
                '"skipDangerousModePermissionPrompt":true,'
                '"bypassPermissionsModeAccepted":true,'
                '"enabledPlugins":{},'
                '"attribution":{"commit":"","pr":""}}'
            )
            for workspace in [
                f"{home}/workspace",
                f"{home}/task-repo",
                home,
            ]:
                await executor.exec_shell(
                    f"mkdir -p {workspace}/.claude && "
                    f"echo {shlex.quote(settings_json)} > {workspace}/.claude/settings.local.json"
                )
        slog.info("configure.claude_settings_applied")
    except Exception as exc:
        slog.warning("configure.claude_settings_failed", metadata={"reason": str(exc)})


async def inject_profile_env(
    executor: GuestExecutor,
    profile_env: str,
    *,
    slog: Any | None = None,
) -> None:
    """Append workspace profile environment variables to ~/.env.

    For Docker: writes to /run/profile/.env and sources from .bashrc and .env.
    For UTM: appends directly to ~/.env.
    For Windows: uses PowerShell Add-Content.
    """
    slog = slog or log
    home = executor.home_dir

    try:
        if executor.guest_os == "windows":
            for line in profile_env.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    escaped_line = line.replace("'", "''")
                    await executor.exec_shell(
                        f'powershell -Command "Add-Content -Path $env:USERPROFILE\\.env'
                        f" -Value '{escaped_line}'\""
                    )
        else:
            escaped = shlex.quote(profile_env)
            await executor.exec_shell(f"echo {escaped} >> {home}/.env")

        slog.info("configure.profile_env_injected")
    except Exception as exc:
        slog.warning("configure.profile_env_inject_failed", metadata={"reason": str(exc)})


async def inject_profile_env_docker(
    executor: GuestExecutor,
    profile_env: str,
    *,
    slog: Any | None = None,
) -> None:
    """Docker-specific profile env injection using /run/profile/.env.

    Creates /run/profile/.env and sources it from both .bashrc and .env.
    This is Docker-specific because it requires root access to create /run/profile.
    """
    slog = slog or log
    home = executor.home_dir

    try:
        # Create /run/profile as root — requires DockerExecExecutor with root user
        # The caller must handle root access; we just write the files.
        escaped = shlex.quote(profile_env)
        await executor.exec_shell(
            f"mkdir -p /run/profile && chmod 750 /run/profile"
            f" && echo {escaped} > /run/profile/.env"
            f" && chmod 644 /run/profile/.env"
        )

        # Source from .bashrc and .env
        source_line = "[ -f /run/profile/.env ] && set -a && . /run/profile/.env && set +a"
        for rc_file in (f"{home}/.bashrc", f"{home}/.env"):
            await executor.exec_shell(
                f"grep -q /run/profile/.env {rc_file} 2>/dev/null"
                f" || echo '{source_line}' >> {rc_file}"
            )

        slog.info("configure.profile_env_docker_injected")
    except Exception as exc:
        slog.warning("configure.profile_env_docker_inject_failed", metadata={"reason": str(exc)})


async def inject_config_bundle(
    executor: GuestExecutor,
    bundle_bytes: bytes,
    *,
    slog: Any | None = None,
) -> None:
    """Extract the config bundle tar.gz into $HOME and fix ownership.

    Explicitly writes settings.json after extraction to guard against silent
    drops on virtiofs/overlayfs mounts. Also merges mcpServers into .claude.json.
    """
    slog = slog or log
    home = executor.home_dir

    try:
        # Extract bundle into home
        await executor.write_stdin(
            f"tar xz -C {home}",
            bundle_bytes,
            timeout=60,
        )

        # Fix ownership
        await executor.exec_shell(
            f"chown -R $(whoami):$(id -gn) {home}/.claude 2>/dev/null || true",
            timeout=15,
        )

        # Explicitly write settings.json
        settings_json = _extract_from_bundle(bundle_bytes, ".claude/settings.json")
        if settings_json:
            await executor.exec_shell(
                f"mkdir -p {home}/.claude"
                f" && echo {shlex.quote(settings_json)} > {home}/.claude/settings.json",
                timeout=15,
            )

            # Merge user mcpServers into .claude.json workspace project
            user_mcps = json.loads(settings_json).get("mcpServers", {})
            if user_mcps:
                mcp_json = json.dumps(user_mcps)
                p_j = json.dumps(f"{home}/.claude.json").replace('"', '\\"')
                ws_j = json.dumps(f"{home}/workspace").replace('"', '\\"')
                await executor.exec_shell(
                    f'echo {shlex.quote(mcp_json)} | python3 -c "'
                    "import json, pathlib, sys; "
                    f"p = pathlib.Path({p_j}); "
                    "d = json.loads(p.read_text()) if p.exists() else {}; "
                    "u = json.load(sys.stdin); "
                    f"ws = {ws_j}; "
                    "d.setdefault('projects', {}).setdefault(ws, {}).setdefault('mcpServers', {}).update(u); "
                    "p.write_text(json.dumps(d, indent=2))"
                    '"',
                    timeout=15,
                )

        slog.info(
            "configure.config_bundle_injected",
            metadata={"bundle_bytes": len(bundle_bytes)},
        )
    except Exception as exc:
        slog.warning(
            "configure.config_bundle_inject_failed",
            metadata={"reason": str(exc)},
        )


async def inject_claude_config_copy(
    executor: GuestExecutor,
    claude_config_dir: Path,
    *,
    slog: Any | None = None,
) -> None:
    """Copy and patch profile .claude config into the container's ~/.claude.

    Reads .claude.json and settings.json from the host's CLAUDE_CONFIG_DIR,
    applies container-specific patches (MCP binary overrides, plugin stripping,
    trust for container paths), then streams the result into ~/.claude/ via tar.

    This replaces the read-only bind-mount approach: the container gets a
    writable, container-appropriate copy of the profile config.
    """
    slog = slog or log
    home = executor.home_dir

    # --- .credentials.json (OAuth tokens) ---
    credentials_path = claude_config_dir / ".credentials.json"
    try:
        credentials_bytes: bytes | None = credentials_path.read_bytes() if credentials_path.exists() else None
    except Exception:
        credentials_bytes = None

    # --- .claude.json ---
    claude_json_path = claude_config_dir / ".claude.json"
    try:
        claude_data: dict = json.loads(claude_json_path.read_text()) if claude_json_path.exists() else {}
    except Exception:
        claude_data = {}

    # Onboarding + bypass acceptance
    claude_data["hasCompletedOnboarding"] = True
    claude_data["bypassPermissionsModeAccepted"] = True

    # Trust container working directories
    for path in [f"{home}/workspace", f"{home}/task-repo", home]:
        existing = claude_data.setdefault("projects", {}).setdefault(path, {})
        existing.update(
            {
                "hasTrustDialogAccepted": True,
                "allowedTools": existing.get("allowedTools", []),
                "mcpContextUris": [],
                "projectOnboardingSeenCount": 0,
            }
        )

    # Patch mcpServers with container-optimized commands
    patched: list[str] = []
    if "mcpServers" in claude_data:
        for server_name, override in _CONTAINER_MCP_OVERRIDES.items():
            if server_name in claude_data["mcpServers"]:
                server = claude_data["mcpServers"][server_name]
                server["command"] = override["command"]
                server["args"] = override["args"]
                # Merge override env (don't wipe existing env — keep credential refs)
                if "env" in override:
                    server.setdefault("env", {}).update(override["env"])
                patched.append(server_name)

    # --- settings.json ---
    settings_json_path = claude_config_dir / "settings.json"
    try:
        settings_data: dict = json.loads(settings_json_path.read_text()) if settings_json_path.exists() else {}
    except Exception:
        settings_data = {}

    # Strip host-only LSP plugins; force bypass flags
    settings_data["enabledPlugins"] = {}
    settings_data["bypassPermissions"] = True
    settings_data["bypassPermissionsModeAccepted"] = True

    # --- stream both files into the container via tar ---
    claude_json_bytes = json.dumps(claude_data, indent=2).encode()
    settings_bytes = json.dumps(settings_data, indent=2).encode()

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        files: list[tuple[str, bytes]] = [
            (".claude.json", claude_json_bytes),        # ~/  .claude.json  (mcpServers, projects, etc.)
            (".claude/settings.json", settings_bytes),  # ~/.claude/settings.json (user prefs)
        ]
        if credentials_bytes is not None:
            files.append((".claude/.credentials.json", credentials_bytes))
        for arcname, content in files:
            info = tarfile.TarInfo(name=arcname)
            info.size = len(content)
            info.mode = 0o600
            tf.addfile(info, io.BytesIO(content))
    buf.seek(0)

    await executor.exec_shell(f"mkdir -p {home}/.claude")
    await executor.write_stdin(f"tar xz -C {home}", buf.read(), timeout=30)
    await executor.exec_shell(
        f"chown -R $(whoami):$(id -gn) {home}/.claude 2>/dev/null || true"
    )

    slog.info(
        "configure.claude_config_copied",
        metadata={"patched_servers": patched, "source": str(claude_config_dir)},
    )


async def inject_role_prompt(
    executor: GuestExecutor,
    role: str,
    prompt_content: str,
    *,
    slog: Any | None = None,
) -> None:
    """Write the role prompt file and configure Claude Code to use it.

    Creates ~/.brainbox/role-prompt.md and adds it to
    settings.json appendSystemPromptFiles.
    """
    slog = slog or log
    home = executor.home_dir

    if executor.guest_os == "windows":
        slog.info("configure.role_prompt_skipped_windows")
        return

    try:
        brainbox_dir = f"{home}/.brainbox"
        prompt_path = f"{brainbox_dir}/role-prompt.md"
        await executor.exec_shell(
            f"mkdir -p {brainbox_dir}"
            f" && echo {shlex.quote(prompt_content)} > {prompt_path}"
            f" && chmod 644 {prompt_path}"
        )

        # Configure Claude Code to use the role prompt via workspace settings.local.json.
        # We write to project-local files (not CLAUDE_CONFIG_DIR/settings.json which may
        # be read-only when the profile .claude dir is bind-mounted from the host).
        py_code = (
            "import json,os,pathlib; "
            'p = pathlib.Path(os.environ["BRAINBOX_WS"]) / ".claude/settings.local.json"; '
            "d = json.loads(p.read_text()) if p.exists() else {}; "
            'd["appendSystemPromptFiles"] = [os.environ["BRAINBOX_PP"]]; '
            "p.parent.mkdir(parents=True, exist_ok=True); "
            "p.write_text(json.dumps(d, indent=2))"
        )
        for workspace in [f"{home}/workspace", f"{home}/task-repo", home]:
            await executor.exec_shell(
                f"BRAINBOX_WS={shlex.quote(workspace)} BRAINBOX_PP={shlex.quote(prompt_path)}"
                f" python3 -c {shlex.quote(py_code)}"
            )

        slog.info("configure.role_prompt_injected", metadata={"role": role})
    except Exception as exc:
        slog.warning(
            "configure.role_prompt_inject_failed",
            metadata={"role": role, "reason": str(exc)},
        )


async def inject_task(
    executor: GuestExecutor,
    task_description: str,
    *,
    task_id: str = "",
    hub_url: str = "http://host.docker.internal:9999",
    slog: Any | None = None,
) -> None:
    """Write task description + completion helper script.

    Creates ~/.brainbox/task.txt, hub-url.txt, and complete.sh.
    """
    slog = slog or log
    home = executor.home_dir

    if executor.guest_os == "windows":
        slog.info("configure.task_skipped_windows")
        return

    try:
        brainbox_dir = f"{home}/.brainbox"
        complete_script = (
            "#!/bin/sh\n"
            "# Call this when your task is done to mark it complete in the hub.\n"
            f"TOKEN=$(cat {home}/.agent-token 2>/dev/null)\n"
            f"TASK_ID=$(cat {brainbox_dir}/task-id.txt 2>/dev/null || echo '')\n"
            f"HUB=$(cat {brainbox_dir}/hub-url.txt 2>/dev/null || echo '{hub_url}')\n"
            'RESULT="${1:-done}"\n'
            "# Build JSON body safely to avoid injection from result summary containing quotes\n"
            "BODY=$(python3 -c 'import json,sys; "
            'r=sys.argv[1]; print(json.dumps({"payload":{"event":"task.completed","result":r}}))'
            "' \"$RESULT\")\n"
            "# Try Bearer token auth first, fall back to API key with task ID\n"
            'curl -sf -X POST "${HUB}/api/hub/messages" \\\n'
            '  -H "Authorization: Bearer ${TOKEN}" \\\n'
            '  -H "Content-Type: application/json" \\\n'
            '  -d "$BODY" \\\n'
            "  && echo 'Task marked complete.' \\\n"
            "  || { \\\n"
            f"    APIKEY=$(cat {home}/.brainbox-api-key 2>/dev/null || echo '')\n"
            '    if [ -n "$APIKEY" ]; then \\\n'
            "      BODY2=$(python3 -c 'import json,sys; "
            'r,tid=sys.argv[1],sys.argv[2]; print(json.dumps({"payload":{"event":"task.completed","result":r,"task_id":tid}}))'
            "' \"$RESULT\" \"$TASK_ID\"); \\\n"
            '      curl -sf -X POST "${HUB}/api/hub/messages" \\\n'
            '        -H "X-API-Key: ${APIKEY}" \\\n'
            '        -H "Content-Type: application/json" \\\n'
            '        -d "$BODY2" \\\n'
            "        && echo 'Task marked complete (via API key).' \\\n"
            "        || echo 'Warning: could not reach hub.'; \\\n"
            "    else \\\n"
            "      echo 'Warning: could not reach hub (token expired, no API key fallback).'; \\\n"
            "    fi; \\\n"
            "  }\n"
        )
        task_with_footer = (
            task_description
            + "\n\nWhen your task is fully complete (PR opened or final output delivered), "
            'run this to notify the hub: ~/.brainbox/complete.sh "<brief result summary>"'
        )

        # Get API key for fallback auth in complete.sh
        from ..auth import get_api_key
        api_key = get_api_key()

        await executor.exec_shell(
            f"mkdir -p {brainbox_dir}"
            f" && echo {shlex.quote(task_with_footer)} > {brainbox_dir}/task.txt"
            f" && chmod 644 {brainbox_dir}/task.txt"
            f" && echo {shlex.quote(hub_url)} > {brainbox_dir}/hub-url.txt"
            + (f" && echo {shlex.quote(task_id)} > {brainbox_dir}/task-id.txt" if task_id else "")
            + f" && printf {shlex.quote(complete_script)} > {brainbox_dir}/complete.sh"
            f" && chmod 755 {brainbox_dir}/complete.sh"
            + (f" && echo {shlex.quote(api_key)} > {home}/.brainbox-api-key && chmod 600 {home}/.brainbox-api-key" if api_key else "")
        )

        slog.info("configure.task_injected", metadata={"task_len": len(task_description)})
    except Exception as exc:
        slog.warning("configure.task_inject_failed", metadata={"reason": str(exc)})

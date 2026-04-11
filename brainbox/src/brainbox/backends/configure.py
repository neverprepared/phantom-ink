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
from typing import Any

from ..log import get_logger
from .executor import GuestExecutor

log = get_logger()


def _extract_from_bundle(bundle_bytes: bytes, arcname: str) -> str | None:
    """Extract a single text file from a tar.gz bundle by archive name."""
    try:
        with tarfile.open(fileobj=io.BytesIO(bundle_bytes), mode="r:gz") as tf:
            member = tf.getmember(arcname)
            f = tf.extractfile(member)
            return f.read().decode("utf-8") if f else None
    except (KeyError, tarfile.TarError, OSError):
        return None


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
                escaped_value = value.replace("'", "''")
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
            }
            patch_json = json.dumps(claude_json_patch)
            await executor.exec_shell(
                f'echo {shlex.quote(patch_json)} | python3 -c "'
                "import json, pathlib, sys; "
                f"p = pathlib.Path('{home}/.claude.json'); "
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
    }
    if oauth_account:
        claude_json_patch["oauthAccount"] = oauth_account

    try:
        patch_json = json.dumps(claude_json_patch)
        await executor.exec_shell(
            f'echo {shlex.quote(patch_json)} | python3 -c "'
            "import json, pathlib, sys; "
            f"p = pathlib.Path('{home}/.claude.json'); "
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
    """Set bypassPermissions=true in settings.json.

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
                '$d | ConvertTo-Json -Depth 10 | Set-Content $p"'
            )
        else:
            # Patch settings.json with all bypass flags (including bypassPermissionsModeAccepted
            # which Claude Code CLI checks before suppressing the interactive permissions prompt)
            await executor.exec_shell(
                f"python3 -c '"
                "import json, pathlib; "
                f'p = pathlib.Path("{home}/.claude/settings.json"); '
                "p.parent.mkdir(parents=True, exist_ok=True); "
                "d = json.loads(p.read_text()) if p.exists() else {}; "
                'd["bypassPermissions"] = True; '
                'd["skipDangerousModePermissionPrompt"] = True; '
                'd["bypassPermissionsModeAccepted"] = True; '
                "p.write_text(json.dumps(d, indent=2))"
                "'"
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
            f"mkdir -p /run/profile && chmod 777 /run/profile"
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
                await executor.exec_shell(
                    f'echo {shlex.quote(mcp_json)} | python3 -c "'
                    "import json, pathlib, sys; "
                    f"p = pathlib.Path('{home}/.claude.json'); "
                    "d = json.loads(p.read_text()) if p.exists() else {}; "
                    "u = json.load(sys.stdin); "
                    f"ws = '{home}/workspace'; "
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

        # Configure Claude Code to use the role prompt
        await executor.exec_shell(
            f"python3 -c '"
            "import json, pathlib; "
            f'p = pathlib.Path("{home}/.claude/settings.json"); '
            "d = json.loads(p.read_text()) if p.exists() else {}; "
            f"d['appendSystemPromptFiles'] = ['{prompt_path}']; "
            "p.write_text(json.dumps(d, indent=2))"
            "'"
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
            f"HUB=$(cat {brainbox_dir}/hub-url.txt 2>/dev/null || echo '{hub_url}')\n"
            'RESULT="${1:-done}"\n'
            'curl -sf -X POST "${HUB}/api/hub/messages" \\\n'
            '  -H "Authorization: Bearer ${TOKEN}" \\\n'
            '  -H "Content-Type: application/json" \\\n'
            '  -d "{\\"payload\\": {\\"event\\": \\"task.completed\\", \\"result\\": \\"${RESULT}\\"}}" \\\n'
            "  && echo 'Task marked complete.' || echo 'Warning: could not reach hub.'\n"
        )
        task_with_footer = (
            task_description
            + "\n\nWhen your task is fully complete (PR opened or final output delivered), "
            'run this to notify the hub: ~/.brainbox/complete.sh "<brief result summary>"'
        )

        await executor.exec_shell(
            f"mkdir -p {brainbox_dir}"
            f" && echo {shlex.quote(task_with_footer)} > {brainbox_dir}/task.txt"
            f" && chmod 644 {brainbox_dir}/task.txt"
            f" && echo {shlex.quote(hub_url)} > {brainbox_dir}/hub-url.txt"
            f" && printf {shlex.quote(complete_script)} > {brainbox_dir}/complete.sh"
            f" && chmod 755 {brainbox_dir}/complete.sh"
        )

        slog.info("configure.task_injected", metadata={"task_len": len(task_description)})
    except Exception as exc:
        slog.warning("configure.task_inject_failed", metadata={"reason": str(exc)})

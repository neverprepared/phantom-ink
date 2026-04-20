"""Docker backend implementation for brainbox."""

from __future__ import annotations

import asyncio
import io
import json
import os
import re
import shlex
import tarfile
import textwrap
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import docker
from docker.errors import NotFound

from ...log import get_logger
from ...models import SessionContext, SessionState

# Docker client singletons
_client: docker.DockerClient | None = None
_remote_clients: dict[str, docker.DockerClient] = {}
_executor = ThreadPoolExecutor(max_workers=4)

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


def _docker(docker_host: str | None = None) -> docker.DockerClient:
    """Get or create Docker client, optionally targeting a remote host."""
    global _client
    if docker_host:
        if docker_host not in _remote_clients:
            _remote_clients[docker_host] = docker.DockerClient(base_url=docker_host)
        return _remote_clients[docker_host]
    if _client is None:
        macos_sock = Path.home() / ".docker" / "run" / "docker.sock"
        if macos_sock.is_socket():
            _client = docker.DockerClient(base_url=f"unix://{macos_sock}")
        else:
            _client = docker.from_env()
    return _client


async def _run(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Run a blocking Docker SDK function in the thread pool."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, lambda: fn(*args, **kwargs))


def _calc_cpu(stats: dict) -> float:
    """Calculate CPU percentage from docker stats."""
    cpu = stats.get("cpu_stats", {})
    precpu = stats.get("precpu_stats", {})

    cpu_delta = cpu.get("cpu_usage", {}).get("total_usage", 0) - precpu.get("cpu_usage", {}).get(
        "total_usage", 0
    )
    sys_delta = cpu.get("system_cpu_usage", 0) - precpu.get("system_cpu_usage", 0)
    n_cpus = cpu.get("online_cpus", 1)

    if sys_delta > 0 and cpu_delta >= 0:
        return (cpu_delta / sys_delta) * n_cpus * 100.0
    return 0.0


def _human_bytes(b: int) -> str:
    """Format bytes as human-readable string."""
    for unit in ("B", "KiB", "MiB", "GiB"):
        if abs(b) < 1024:
            return f"{b:.1f}{unit}"
        b /= 1024  # type: ignore[assignment]
    return f"{b:.1f}TiB"


def _build_container_env(ctx: "SessionContext") -> dict[str, str]:
    """Build the Docker container environment dict.

    Non-sensitive path vars are passed as Docker env vars so every process in
    the container (Claude, MCP servers, scripts) sees them without needing to
    source ~/.env or read from /run/secrets/.

    CLAUDE_CONFIG_DIR is intentionally omitted — the container uses its own
    ~/.claude (populated by inject_claude_config_copy during configure), so
    Claude's default config path resolution works without any override.
    """
    env: dict[str, str] = {"BRAINBOX_ROLE": ctx.role}

    # OBSIDIAN_VAULT_PATH: vault is bind-mounted at the same host path inside
    # the container, so this value is valid for both sides.
    if ctx.workspace_home:
        vault_path = os.environ.get("OBSIDIAN_VAULT_PATH", "")
        if vault_path:
            env["OBSIDIAN_VAULT_PATH"] = vault_path

    return env


class DockerBackend:
    """Docker container backend for brainbox."""

    async def provision(
        self,
        ctx: SessionContext,
        *,
        image_or_template: str,
        volumes: dict[str, dict[str, str]],
        hardening_kwargs: dict[str, Any],
    ) -> SessionContext:
        """Create Docker container with specified image and volumes."""
        slog = get_logger(session_name=ctx.session_name, container_name=ctx.container_name)
        client = _docker(ctx.docker_host)

        # Always pull latest image before provision (ensures up-to-date; falls back to cache)
        try:
            await _run(client.images.pull, image_or_template)
            slog.info("container.image_pulled", metadata={"image": image_or_template})
        except Exception as pull_exc:
            slog.warning(
                "container.image_pull_failed",
                metadata={"image": image_or_template, "reason": str(pull_exc)},
            )
            # Fall back to locally cached image
            try:
                await _run(client.images.get, image_or_template)
            except Exception as exc:
                slog.error("container.provision_failed", metadata={"reason": str(exc)})
                raise

        # Remove existing container if present
        try:
            old = await _run(client.containers.get, ctx.container_name)
            await _run(old.remove, force=True)
        except NotFound:
            pass

        # Build create kwargs
        port_bindings: dict[str, tuple[str, int]] = {"7681/tcp": ("127.0.0.1", ctx.port)}

        # Add custom port mappings if specified
        if ctx.ports:
            for container_port, host_port in ctx.ports.items():
                port_bindings[f"{container_port}/tcp"] = ("127.0.0.1", host_port)

        kwargs: dict[str, Any] = {
            "image": image_or_template,
            "name": ctx.container_name,
            "command": ["sleep", "infinity"],
            "ports": port_bindings,
            "labels": {
                "brainbox.managed": "true",
                "brainbox.session_name": ctx.session_name,
                "brainbox.role": ctx.role,
                "brainbox.llm_provider": ctx.llm_provider,
                "brainbox.llm_model": ctx.llm_model or "",
                "brainbox.workspace_profile": (ctx.workspace_profile or "").upper(),
                "brainbox.task_id": ctx.task_id or "",
                "brainbox.job_id": ctx.job_id or ctx.task_id or "",
            },
            "environment": _build_container_env(ctx),
            "detach": True,
            "volumes": volumes,
        }

        # Apply hardening or legacy settings
        kwargs.update(hardening_kwargs)

        try:
            await _run(client.containers.create, **kwargs)
        except Exception as exc:
            slog.error("container.provision_failed", metadata={"reason": str(exc)})
            raise

        ctx.state = SessionState.CONFIGURING
        slog.info(
            "container.provisioned",
            metadata={
                "image": image_or_template,
                "role": ctx.role,
                "port": ctx.port,
                "hardened": ctx.hardened,
            },
        )
        return ctx

    async def configure(
        self,
        ctx: SessionContext,
        *,
        secrets: dict[str, str],
        env_content: str | None = None,
        oauth_account: dict[str, Any] | None = None,
        profile_env: str | None = None,
    ) -> SessionContext:
        """Write secrets and configuration to Docker container."""
        from ..configure import (
            inject_claude_config,
            inject_claude_settings,
            inject_env_file,
            inject_role_prompt,
            inject_task,
        )
        from ..executor import DockerExecExecutor

        slog = get_logger(session_name=ctx.session_name, container_name=ctx.container_name)
        client = _docker(ctx.docker_host)
        container = await _run(client.containers.get, ctx.container_name)

        # Start container temporarily if not running (needed for exec)
        if container.status != "running":
            await _run(container.start)

        executor = DockerExecExecutor(container)

        # Write workspace settings.local.json — disables host-only plugins,
        # sets bypass flags. Runs in all modes.
        await inject_claude_settings(executor, slog=slog)

        if ctx.hardened:
            # Write each secret to /run/secrets (Docker-specific, no UTM equivalent)
            for name, value in secrets.items():
                if not re.match(r"^[A-Za-z0-9_.-]+$", name):
                    slog.warning(
                        "container.secret_name_rejected",
                        metadata={"secret": name, "reason": "invalid characters"},
                    )
                    continue
                safe_name = shlex.quote(name)
                try:
                    await _run(
                        container.exec_run,
                        [
                            "sh",
                            "-c",
                            f"echo {shlex.quote(value)} > /run/secrets/{safe_name} && chmod 400 /run/secrets/{safe_name}",
                        ],
                    )
                except Exception as exc:
                    slog.warning(
                        "container.secret_write_failed",
                        metadata={"secret": name, "reason": str(exc)},
                    )
        else:
            await inject_env_file(executor, secrets, ctx.session_name, slog=slog)
            await inject_claude_config(executor, oauth_account, slog=slog)

        # Inject role prompt file for --append-system-prompt-file
        if ctx.role_prompt_file:
            from ...registry import get_role_prompt

            prompt_content = get_role_prompt(ctx.role)
            if prompt_content:
                executor = DockerExecExecutor(container)
                await inject_role_prompt(executor, ctx.role, prompt_content, slog=slog)

        # Inject task description + completion helper for hub-spawned workers
        if ctx.task_description:
            executor = DockerExecExecutor(container)
            await inject_task(executor, ctx.task_description, task_id=ctx.task_id or "", slog=slog)

        # Claude config is delivered via inject_config_bundle() before configure() runs —
        # no staging copy needed here. bypassPermissions is already forced in the bundle.

        ctx.state = SessionState.STARTING
        slog.info("container.configured", metadata={"hardened": ctx.hardened})
        return ctx

    async def start(self, ctx: SessionContext) -> SessionContext:
        """Start Docker container and launch ttyd terminal."""
        from ...lifecycle import _resolve_profile_env

        slog = get_logger(session_name=ctx.session_name, container_name=ctx.container_name)
        client = _docker(ctx.docker_host)

        container = await _run(client.containers.get, ctx.container_name)

        # Start if not already running
        if container.status != "running":
            await _run(container.start)

        # Launch tmux + claude, then ttyd for web terminal access.
        # For task containers: run the wrapper first to create the tmux session
        # with the task injected, THEN start ttyd which attaches to it.
        # For interactive: ttyd starts the wrapper on first browser connection.
        if not ctx.hardened:
            title = f"{ctx.role.capitalize()} - {ctx.session_name}"

            if ctx.task_description:
                # Start wrapper first — creates tmux session + launches claude with task
                try:
                    await _run(
                        container.exec_run,
                        ["/home/developer/ttyd-wrapper.sh"],
                        detach=True,
                        user="developer",
                    )
                    slog.info("container.wrapper_autostarted")
                except Exception as exc:
                    slog.warning(
                        "container.wrapper_autostart_failed", metadata={"reason": str(exc)}
                    )

                # Brief pause to let tmux session establish before ttyd attaches
                import asyncio
                await asyncio.sleep(1)

            # Start ttyd — attaches to existing tmux session (task) or starts new one (interactive)
            try:
                await _run(
                    container.exec_run,
                    [
                        "ttyd",
                        "-W",
                        "-t",
                        f"titleFixed={title}",
                        "-p",
                        "7681",
                        "/home/developer/ttyd-wrapper.sh",
                    ],
                    detach=True,
                )
            except Exception as exc:
                slog.warning("container.ttyd_start_failed", metadata={"reason": str(exc)})

        ctx.state = SessionState.RUNNING
        slog.info("container.started", metadata={"port": ctx.port})
        return ctx

    async def stop(self, ctx: SessionContext) -> SessionContext:
        """Stop Docker container."""
        client = _docker(ctx.docker_host)
        try:
            container = await _run(client.containers.get, ctx.container_name)
            await _run(container.stop, timeout=5)
        except Exception:
            pass
        return ctx

    async def remove(self, ctx: SessionContext) -> SessionContext:
        """Remove Docker container."""
        slog = get_logger(session_name=ctx.session_name, container_name=ctx.container_name)
        client = _docker(ctx.docker_host)

        try:
            container = await _run(client.containers.get, ctx.container_name)
            await _run(container.remove)
            slog.info("container.removed")
        except Exception:
            pass

        return ctx

    async def health_check(self, ctx: SessionContext) -> dict[str, Any]:
        """Check Docker container health and collect CPU/memory metrics."""
        client = _docker(ctx.docker_host)

        try:
            container = await _run(client.containers.get, ctx.container_name)
            await _run(container.reload)

            is_running = container.attrs["State"]["Running"]

            if not is_running:
                return {
                    "backend": "docker",
                    "healthy": False,
                    "reason": "container not running",
                }

            # Get stats (non-streaming)
            stats = await _run(container.stats, stream=False)
            cpu_pct = _calc_cpu(stats)
            mem = stats.get("memory_stats", {})
            mem_usage = mem.get("usage", 0)
            mem_limit = mem.get("limit", 1)

            return {
                "backend": "docker",
                "healthy": True,
                "cpu_percent": round(cpu_pct, 2),
                "memory_usage": mem_usage,
                "memory_limit": mem_limit,
                "memory_usage_human": _human_bytes(mem_usage),
                "memory_limit_human": _human_bytes(mem_limit),
            }

        except NotFound:
            return {
                "backend": "docker",
                "healthy": False,
                "reason": "container not found",
            }
        except Exception as exc:
            return {
                "backend": "docker",
                "healthy": False,
                "reason": str(exc),
            }

    async def exec_command(
        self, ctx: SessionContext, command: list[str], **kwargs: Any
    ) -> tuple[int, bytes]:
        """Execute command in Docker container via docker exec."""
        client = _docker(ctx.docker_host)
        container = await _run(client.containers.get, ctx.container_name)

        # Run exec_run with kwargs (detach, user, etc.)
        result = await _run(container.exec_run, command, **kwargs)

        # exec_run returns ExecResult with exit_code and output
        # Handle both detached (returns None) and attached modes
        if kwargs.get("detach"):
            return (0, b"")
        else:
            exit_code = result.exit_code if hasattr(result, "exit_code") else 0
            output = result.output if hasattr(result, "output") else b""
            return (exit_code, output)

    async def inject_config_bundle(self, ctx: SessionContext, bundle_bytes: bytes) -> None:
        """Inject translated ~/.claude config bundle into the container via put_archive.

        Uses Docker's put_archive as a fast path for tar extraction, then
        delegates settings.json writing and mcpServers merging to the shared
        inject_config_bundle function.
        """
        from ..executor import DockerExecExecutor

        slog = get_logger(session_name=ctx.session_name, container_name=ctx.container_name)
        client = _docker(ctx.docker_host)
        try:
            container = await _run(client.containers.get, ctx.container_name)
            # put_archive works on stopped containers — Docker-specific fast path.
            await _run(container.put_archive, "/home/developer", bundle_bytes)
            # exec_run requires a running container; start it now if needed.
            await _run(container.reload)
            if container.status != "running":
                await _run(container.start)

            # Fix ownership + write settings.json + merge mcpServers via shared function.
            # The shared function re-extracts from the bundle, which is fine — the
            # put_archive above handles the bulk tar; the shared function handles the
            # settings.json workaround and mcpServers merge.
            executor = DockerExecExecutor(container)
            # Only do the post-extraction fixups (ownership + settings.json), not the
            # tar extraction itself (already done via put_archive above).
            await _run(
                container.exec_run,
                [
                    "sh",
                    "-c",
                    "chown -R developer:developer /home/developer/.claude 2>/dev/null || true",
                ],
                user="root",
            )

            settings_json = _extract_from_bundle(bundle_bytes, ".claude/settings.json")
            if settings_json:
                await executor.exec_shell(
                    f"echo {shlex.quote(settings_json)} > /home/developer/.claude/settings.json"
                )

                user_mcps = json.loads(settings_json).get("mcpServers", {})
                if user_mcps:
                    mcp_json = json.dumps(user_mcps)
                    await executor.exec_shell(
                        f'echo {shlex.quote(mcp_json)} | python3 -c "'
                        "import json, pathlib, sys; "
                        "p = pathlib.Path('/home/developer/.claude.json'); "
                        "d = json.loads(p.read_text()) if p.exists() else {}; "
                        "u = json.load(sys.stdin); "
                        "ws = '/home/developer/workspace'; "
                        "d.setdefault('projects', {}).setdefault(ws, {}).setdefault('mcpServers', {}).update(u); "
                        "p.write_text(json.dumps(d, indent=2))"
                        '"'
                    )

            slog.info("container.config_bundle_injected")
        except Exception as exc:
            slog.warning("container.config_bundle_inject_failed", metadata={"reason": str(exc)})

    async def inject_remote_credentials(self, ctx: SessionContext) -> None:
        """Set up credential proxies for remote Docker mode.

        - AWS: credential_process pointing to /api/credentials/aws-token
        - SSH: websocat relay connecting unix socket to WebSocket endpoint
        """
        from ...config import settings

        slog = get_logger(session_name=ctx.session_name, container_name=ctx.container_name)
        client = _docker(ctx.docker_host)
        try:
            container = await _run(client.containers.get, ctx.container_name)
        except Exception as exc:
            slog.warning(
                "container.remote_credentials_failed",
                metadata={"reason": str(exc)},
            )
            return

        hub_url = f"http://host.docker.internal:{settings.api_port}"

        # AWS credential_process — SDK calls this on token expiry for always-fresh creds
        aws_config = textwrap.dedent(f"""\
            [default]
            credential_process = sh -c 'curl -sf \\
              -H "Authorization: Bearer $(cat /home/developer/.agent-token 2>/dev/null)" \\
              {hub_url}/api/credentials/aws-token'
        """)
        try:
            await _run(
                container.exec_run,
                [
                    "sh",
                    "-c",
                    f"mkdir -p /home/developer/.aws"
                    f" && printf '%s' {shlex.quote(aws_config)} > /home/developer/.aws/config",
                ],
                user="developer",
            )
        except Exception as exc:
            slog.warning("container.aws_credential_process_failed", metadata={"reason": str(exc)})

        # SSH agent WebSocket relay — websocat proxies unix socket to brainbox API
        # The relay runs in background; SSH_AUTH_SOCK points to the local unix socket
        hub_host = "host.docker.internal"
        hub_port = settings.api_port
        ssh_setup = textwrap.dedent(f"""\
            TOKEN=$(cat /home/developer/.agent-token 2>/dev/null || echo "")
            nohup websocat -b unix-l:/tmp/ssh-agent.sock \\
              "ws://{hub_host}:{hub_port}/api/credentials/ssh-agent" \\
              --header "Authorization: Bearer $TOKEN" \\
              >/dev/null 2>&1 &
            echo 'export SSH_AUTH_SOCK=/tmp/ssh-agent.sock' >> /home/developer/.env
        """)
        try:
            await _run(
                container.exec_run,
                ["sh", "-c", ssh_setup],
                user="developer",
            )
        except Exception as exc:
            slog.warning("container.ssh_relay_failed", metadata={"reason": str(exc)})

        slog.info("container.remote_credentials_injected")

    async def fix_git_credential_paths(self, ctx: SessionContext) -> None:
        """Rewrite git credential helper to use container-local brew path."""
        slog = get_logger(session_name=ctx.session_name, container_name=ctx.container_name)
        client = _docker(ctx.docker_host)
        container = await _run(client.containers.get, ctx.container_name)
        git_cred_fix = textwrap.dedent("""\
            BREW_GH="$(brew --prefix 2>/dev/null)/bin/gh"
            cp /home/developer/.gitconfig /home/developer/.gitconfig.local 2>/dev/null || true
            if [ -x "$BREW_GH" ]; then
                sed -i "s|!/opt/homebrew/bin/gh|!$BREW_GH|g" /home/developer/.gitconfig.local 2>/dev/null
            fi
            # GPG signing keys are in the macOS keychain — not available in containers.
            # Disable signing so commits work; identity is still verified by the gh token.
            git config --file /home/developer/.gitconfig.local commit.gpgsign false
            git config --file /home/developer/.gitconfig.local tag.gpgsign false
            echo 'export GIT_CONFIG_GLOBAL=/home/developer/.gitconfig.local' >> /home/developer/.env
            export GIT_CONFIG_GLOBAL=/home/developer/.gitconfig.local
        """)
        try:
            await _run(
                container.exec_run,
                ["sh", "-c", git_cred_fix],
                user="developer",
            )
        except Exception as exc:
            slog.warning("container.git_credential_fix_failed", metadata={"reason": str(exc)})

    def get_sessions_info(self) -> list[dict[str, Any]]:
        """List all managed Docker containers."""
        sessions = []
        try:
            client = _docker()
            containers = client.containers.list(
                all=True, filters={"label": "brainbox.managed=true"}
            )

            for c in containers:
                name = c.name
                is_running = c.status == "running"
                port = None
                volume = "-"

                if is_running:
                    ports = c.attrs.get("NetworkSettings", {}).get("Ports") or {}
                    for bindings in ports.values():
                        if bindings:
                            for b in bindings:
                                hp = b.get("HostPort")
                                if hp:
                                    port = hp
                                    break

                # Get volume mounts
                mounts = c.attrs.get("Mounts", [])
                bind_mounts = [
                    f"{m['Source']}:{m['Destination']}"
                    for m in mounts
                    if m.get("Type") == "bind"
                    and not m["Destination"].endswith("/.claude/projects")
                ]
                if bind_mounts:
                    volume = ", ".join(bind_mounts)

                labels = c.labels or {}
                llm_provider = labels.get("brainbox.llm_provider", "claude")
                llm_model = labels.get("brainbox.llm_model", "")
                workspace_profile = labels.get("brainbox.workspace_profile", "")

                sessions.append(
                    {
                        "backend": "docker",
                        "name": name,
                        "port": port,
                        "url": f"http://localhost:{port}" if port else None,
                        "volume": volume,
                        "active": is_running,
                        "llm_provider": llm_provider,
                        "llm_model": llm_model,
                        "workspace_profile": workspace_profile,
                    }
                )

        except Exception as exc:
            log.error("docker.list_sessions_failed", metadata={"reason": str(exc)})

        return sessions

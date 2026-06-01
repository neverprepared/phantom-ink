"""Brainbox lifecycle: provision → configure → start → monitor → recycle.

All Docker operations use the Docker SDK and are wrapped with run_in_executor
so they never block the async event loop.
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .backends.docker import _docker, _client  # noqa: F401 — re-exported for test monkeypatching
from .backends.docker.cosign import CosignVerificationError, verify_image, verify_image_keyless
from .backends.docker.hardening import get_hardening_kwargs, get_legacy_kwargs
from .config import settings
from .log import get_logger
from .models import SessionContext, SessionState, Token
from .utils import now_ms as _now_ms, iso_now as _iso_now

# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------

_sessions: dict[str, SessionContext] = {}
_executor = ThreadPoolExecutor(max_workers=4)
_port_lock: asyncio.Lock | None = None

log = get_logger()


async def _run(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Run a blocking function in the thread pool."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, lambda: fn(*args, **kwargs))


def _load_cache_env_text(cache_env: Path) -> str:
    """Read cache env file text synchronously."""
    return cache_env.read_text()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_dir(
    env_vars: list[str],
    fallback: Path,
    *,
    use_parent: bool = False,
    env_override: dict[str, str] | None = None,
) -> Path | None:
    """Find a host directory from env vars or a fallback path.

    When *use_parent* is True the env var value is treated as a file path and
    its parent directory is returned instead.

    When *env_override* is provided, look up variables there instead of
    ``os.environ`` (used for cross-profile cache lookups).
    """
    env_source = env_override if env_override is not None else os.environ
    for var in env_vars:
        val = env_source.get(var)
        if val:
            candidate = Path(val).parent if use_parent else Path(val)
            if candidate.is_dir():
                return candidate
    if fallback.is_dir():
        return fallback
    return None


def _parse_env_text(text: str, workspace_home: str) -> dict[str, str]:
    """Parse .env file content into a dict, expanding $WORKSPACE_HOME."""
    result: dict[str, str] = {}
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:]
        name, _, value = stripped.partition("=")
        name = name.strip()
        value = value.strip()
        if not name or not value:
            continue
        # Strip surrounding quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        # Expand $WORKSPACE_HOME / ${WORKSPACE_HOME} to actual host path
        value = value.replace("${WORKSPACE_HOME}", workspace_home)
        value = value.replace("$WORKSPACE_HOME", workspace_home)
        result[name] = value
    return result


def _read_profile_vars(
    workspace_profile: str,
    workspace_home: str,
) -> dict[str, str]:
    """Read env vars from a profile directory (.env + .env.secrets).

    Falls back to the legacy volatile cache at $TMPDIR/sp-profiles/{profile}/.env
    if the profile directory files are not found.
    """
    ws = Path(workspace_home)
    result: dict[str, str] = {}

    # Read tool paths from .env
    env_file = ws / ".env"
    if env_file.is_file():
        result.update(_parse_env_text(env_file.read_text(), workspace_home))

    # Read secrets from .env.secrets (1Password FIFO mount or plaintext)
    secrets_file = ws / ".env.secrets"
    if secrets_file.exists():  # .exists() works for FIFOs too
        try:
            result.update(_parse_env_text(secrets_file.read_text(), workspace_home))
        except OSError:
            pass  # FIFO may not be readable if 1Password is locked

    # Fallback: legacy volatile cache (for profiles not yet migrated)
    if not result:
        tmpdir = os.environ.get("TMPDIR", "/tmp")
        cache_env = Path(tmpdir) / "sp-profiles" / workspace_profile / ".env"
        if cache_env.is_file():
            result.update(
                _parse_env_text(_load_cache_env_text(cache_env), workspace_home)
            )

    return result


def _compute_mount_context(
    workspace_profile: str | None,
    workspace_home: str | None,
) -> dict:
    """Compute the path and env-override context needed for mount resolution.

    Returns a dict with keys: ``home``, ``ws_path``, ``env_override``,
    ``workspace_home``, and ``use_env_vars`` (bool — whether env-var names
    should be consulted when locating credential directories).
    """
    if workspace_home:
        ws_path = Path(workspace_home)
        home = ws_path
        if workspace_profile:
            cache_vars = _read_profile_vars(workspace_profile, workspace_home)
        else:
            cache_vars = {}
        use_env = bool(cache_vars)
    else:
        home = Path.home()
        ws = os.environ.get("WORKSPACE_HOME", "")
        ws_path = Path(ws) if ws else home
        cache_vars = {}
        use_env = True

    env_override = cache_vars if cache_vars else None

    return {
        "home": home,
        "ws_path": ws_path,
        "env_override": env_override,
        "workspace_home": workspace_home,
        "workspace_profile": workspace_profile,
        "use_env_vars": use_env,
    }


def _generate_container_mcp_json(
    claude_config_dir: Path,
    dest_path: Path,
) -> bool:
    """Generate a workspace .mcp.json with container-optimised MCP commands.

    Reads mcpServers from the profile's .claude.json, applies container binary
    overrides (replacing npx/uvx/uv-run commands with pre-installed binaries),
    and writes the result to *dest_path* so it can be bind-mounted read-only
    into ~/workspace/.mcp.json inside the container.

    Returns True if the file was written, False if there was nothing to do.
    """
    from .backends.configure import _CONTAINER_MCP_OVERRIDES

    claude_json_path = claude_config_dir / ".claude.json"
    if not claude_json_path.exists():
        return False

    try:
        data = json.loads(claude_json_path.read_text())
    except Exception:
        return False

    user_servers: dict = data.get("mcpServers", {})
    if not user_servers:
        return False

    container_servers: dict = {}
    for name, server in user_servers.items():
        override = _CONTAINER_MCP_OVERRIDES.get(name)
        if override:
            patched = dict(server)
            patched["command"] = override["command"]
            patched["args"] = override["args"]
            if "env" in override:
                patched.setdefault("env", {}).update(override["env"])
            container_servers[name] = patched
        else:
            container_servers[name] = server

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_text(json.dumps({"mcpServers": container_servers}, indent=2))
    return True


def _build_volume_map(env_vars: dict) -> dict[str, dict[str, str]]:
    """Translate the env context into a host-path → volume-spec mount map."""
    home: Path = env_vars["home"]
    ws_path: Path = env_vars["ws_path"]
    env_override: dict[str, str] | None = env_vars["env_override"]
    workspace_home: str | None = env_vars["workspace_home"]
    workspace_profile: str | None = env_vars.get("workspace_profile")
    use_env_vars: bool = env_vars["use_env_vars"]

    p = settings.profile

    mount_specs: list[tuple[bool, str, list[str], Path, bool]] = [
        # (enabled, name, mount_env_vars, fallback, use_parent)
        (
            p.mount_aws,
            "aws",
            ["AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE"] if use_env_vars else [],
            home / ".aws",
            True,
        ),
        (
            p.mount_azure,
            "azure",
            ["AZURE_CONFIG_DIR"] if use_env_vars else [],
            home / ".azure",
            False,
        ),
        (
            p.mount_kube,
            "kube",
            ["KUBECONFIG"] if use_env_vars else [],
            home / ".kube",
            True,
        ),
        (
            p.mount_ssh,
            "ssh",
            [],
            ws_path / ".ssh" if (ws_path / ".ssh").is_dir() else Path.home() / ".ssh",
            False,
        ),
        (
            p.mount_gitconfig,
            "gitconfig",
            ["GIT_CONFIG_GLOBAL"] if use_env_vars else [],
            ws_path / ".gitconfig",
            False,
        ),
        (
            p.mount_gcloud,
            "gcloud",
            ["CLOUDSDK_CONFIG"] if use_env_vars else [],
            home / ".gcloud",
            False,
        ),
        (
            p.mount_terraform,
            "terraform",
            ["TF_CLI_CONFIG_FILE"] if use_env_vars else [],
            home / ".terraform.d",
            True,
        ),
        (
            p.mount_codex,
            "codex",
            ["CODEX_HOME"] if use_env_vars else [],
            ws_path / ".codex",
            False,
        ),
    ]

    # Ensure codex dir exists so _resolve_dir can find it — codex won't
    # create it itself until first run, but Docker requires the source to exist.
    if p.mount_codex:
        _env = env_override if env_override is not None else os.environ
        codex_dir = Path(_env["CODEX_HOME"]) if _env.get("CODEX_HOME") else ws_path / ".codex"
        codex_dir.mkdir(parents=True, exist_ok=True)

    container_targets = {
        "aws": "/home/developer/.aws",
        "azure": "/home/developer/.azure",
        "kube": "/home/developer/.kube",
        "ssh": "/home/developer/.ssh",
        "gitconfig": "/home/developer/.gitconfig",
        "gcloud": "/home/developer/.gcloud",
        "terraform": "/home/developer/.terraform.d",
        "codex": "/home/developer/.codex",
    }

    # Credential mounts default to read-only to prevent containers
    # from modifying host credentials.  Gitconfig stays rw so git can
    # write commit metadata.
    _RW_MOUNTS = {"gitconfig", "codex"}

    mounts: dict[str, dict[str, str]] = {}

    for enabled, name, mount_env_vars, fallback, use_parent in mount_specs:
        if not enabled:
            continue
        mode = "rw" if name in _RW_MOUNTS else "ro"
        # gitconfig is a file mount, not a directory
        if name == "gitconfig":
            found = None
            env_source = env_override if env_override is not None else os.environ
            for var in mount_env_vars:
                val = env_source.get(var)
                if val and Path(val).is_file():
                    found = Path(val)
                    break
            if found is None and fallback.is_file():
                found = fallback
            if found is not None:
                mounts[str(found)] = {"bind": container_targets[name], "mode": mode}
        else:
            host_dir = _resolve_dir(
                mount_env_vars, fallback, use_parent=use_parent, env_override=env_override
            )
            if host_dir is not None:
                mounts[str(host_dir)] = {"bind": container_targets[name], "mode": mode}

    # GPG agent socket forwarding: mount the pubring (key IDs) and the agent
    # extra socket so containers can sign commits via the host's gpg-agent.
    if p.mount_gpg:
        import subprocess

        gnupg_dir = Path.home() / ".gnupg"
        if gnupg_dir.is_dir():
            # Mount pubring + trustdb read-only so gpg knows the key IDs
            for f in ("pubring.kbx", "trustdb.gpg"):
                fpath = gnupg_dir / f
                if fpath.is_file():
                    mounts[str(fpath)] = {
                        "bind": f"/home/developer/.gnupg/{f}",
                        "mode": "ro",
                    }

            # Mount the agent extra socket (designed for forwarding)
            try:
                extra_sock = subprocess.check_output(
                    ["gpgconf", "--list-dirs", "agent-extra-socket"],
                    text=True,
                ).strip()
            except Exception:
                extra_sock = str(gnupg_dir / "S.gpg-agent.extra")

            if Path(extra_sock).exists():
                mounts[extra_sock] = {
                    "bind": "/home/developer/.gnupg/S.gpg-agent",
                    "mode": "rw",
                }

    # Note: profile .claude config is NOT bind-mounted. Instead, configure()
    # copies and patches it into the container's ~/.claude via inject_claude_config_copy.

    # Reflex share dir: mount so hooks/skills inside the container can invoke
    # the same reflex runtime that the host uses.
    if p.mount_reflex:
        reflex_path = Path(p.reflex_share_path)
        if reflex_path.is_dir():
            mounts[str(reflex_path)] = {"bind": str(reflex_path), "mode": "ro"}

    # Obsidian vault: mount the profile memory vault read-write so the
    # obsidian-second-brain MCP server can read and write notes from inside
    # the container using the same OBSIDIAN_VAULT_PATH the host has configured.
    if p.mount_obsidian_vault:
        _env = env_override if env_override is not None else os.environ
        vault_path_str = _env.get("OBSIDIAN_VAULT_PATH")
        if not vault_path_str and workspace_home:
            # Derive conventional path: <workspace_home>/obsidian/vaults/<profile>-memory
            # Prefer the explicitly passed profile name over env lookup (env_override may
            # not contain WORKSPACE_PROFILE when the profile cache is sparse).
            profile_name = workspace_profile or _env.get("WORKSPACE_PROFILE", "")
            if profile_name:
                vault_path_str = str(ws_path / "obsidian" / "vaults" / f"{profile_name}-memory")
        if vault_path_str:
            vault_path = Path(vault_path_str)
            if vault_path.is_dir():
                mounts[str(vault_path)] = {"bind": str(vault_path), "mode": "rw"}

    # When workspace_home differs from the real home, AWS SSO tokens live in
    # the real $HOME/.aws/sso/cache/ (aws sso login always writes there).
    # Add a nested bind mount so the container sees live tokens.
    if workspace_home and p.mount_aws:
        real_sso_cache = Path.home() / ".aws" / "sso" / "cache"
        if real_sso_cache.is_dir():
            mounts[str(real_sso_cache)] = {
                "bind": "/home/developer/.aws/sso/cache",
                "mode": "rw",
            }

    return mounts


# Container bind paths that brainbox-init delivers via sealed bundle when
# ctx.delivery == "bundle". Anything else in the profile mount set (live
# sockets, reflex share, obsidian vault) stays as a bind mount.
_BUNDLE_DELIVERED_BINDS: frozenset[str] = frozenset(
    {
        "/home/developer/.aws",
        "/home/developer/.aws/sso/cache",
        "/home/developer/.azure",
        "/home/developer/.kube",
        "/home/developer/.ssh",
        "/home/developer/.gnupg/pubring.kbx",
        "/home/developer/.gnupg/trustdb.gpg",
        "/home/developer/.gitconfig",
        "/home/developer/.gcloud",
        "/home/developer/.terraform.d",
        "/home/developer/.codex",
    }
)


def _is_credential_bind(bind: str) -> bool:
    return bind in _BUNDLE_DELIVERED_BINDS


def _resolve_credential_sources(
    workspace_profile: str | None,
    workspace_home: str | None,
) -> list[tuple[Path, str, int | None]]:
    """Return (host_path, target_relative_to_home, mode_override) for every
    credential bind that would have been mounted under bind delivery.

    Used by the bundle delivery path: same source-of-truth as the bind mounts,
    different mechanism for getting the bytes into the container.
    """
    mounts = _resolve_profile_mounts(
        workspace_profile=workspace_profile, workspace_home=workspace_home
    )
    sources: list[tuple[Path, str, int | None]] = []
    for host_path, spec in mounts.items():
        if not _is_credential_bind(spec["bind"]):
            continue
        target = spec["bind"].removeprefix("/home/developer/").lstrip("/")
        if not target:
            continue
        sources.append((Path(host_path), target, None))
    return sources


def _resolve_profile_mounts(
    workspace_profile: str | None = None,
    workspace_home: str | None = None,
) -> dict[str, dict[str, str]]:
    """Resolve profile credential / config directories to Docker volume mounts.

    When *workspace_home* is provided with a *workspace_profile*, the volatile
    cache at ``$TMPDIR/sp-profiles/{profile}/.env`` is read to resolve env vars
    from the target profile (expanding ``$WORKSPACE_HOME`` references).  When
    only *workspace_home* is provided (no profile), falls back to directory-based
    resolution.  When neither is provided, uses the current process environment.

    Returns a dict of host_path → {"bind": container_path, "mode": "rw"}.
    """
    env_vars = _compute_mount_context(workspace_profile, workspace_home)
    return _build_volume_map(env_vars)


# Vars that are host-specific and should not be forwarded into containers
_HOST_ONLY_VARS = frozenset(
    {
        "SSH_AUTH_SOCK",
        "GIT_SSH_COMMAND",
        "TMPDIR",
        "SHELL",
        "TERM_PROGRAM",
        "TERM_SESSION_ID",
        "HOME",
        "USER",
        "LOGNAME",
        "PATH",
        "PWD",
        "OLDPWD",
        "SHLVL",
        "XDG_CONFIG_HOME",
        # Container has its own config dirs; the host paths would conflict
        # with build-time defaults and cause Claude to miss settings.
        "CLAUDE_CONFIG_DIR",
        "GEMINI_CONFIG_DIR",
    }
)


def _resolve_profile_env(
    workspace_profile: str | None = None,
    workspace_home: str | None = None,
) -> str | None:
    """Read profile env files and return content suitable for a container.

    Resolution order (reads all that exist, merges):
    1. workspace_home/.env — tool paths and non-secret config
    2. workspace_home/.env.secrets — secrets (1Password FIFO or plaintext)
    3. Fallback: legacy volatile cache at $TMPDIR/sp-profiles/{profile}/.env

    Returns the file content with host-only vars stripped and workspace identity
    vars prepended, or None if no sources are found.
    """
    profile = workspace_profile or os.environ.get("WORKSPACE_PROFILE", "")
    if not profile:
        return None

    raw_lines: list[str] = []

    # Primary: read from profile directory
    if workspace_home:
        ws = Path(workspace_home)
        for env_file in [ws / ".env", ws / ".env.secrets"]:
            if env_file.exists():
                try:
                    raw_lines.extend(env_file.read_text().splitlines())
                except OSError:
                    pass  # .env.secrets FIFO may not be readable if 1Password is locked

    # Fallback: legacy volatile cache (for unmigrated profiles or Docker API)
    if not raw_lines:
        tmpdir = os.environ.get("TMPDIR", "/tmp")
        for candidate in [
            Path(tmpdir) / "sp-profiles" / profile / ".env",
            Path("/host-sp-profiles") / profile / ".env",
        ]:
            if candidate.is_file():
                try:
                    raw_lines.extend(_load_cache_env_text(candidate).splitlines())
                except OSError:
                    pass
                break

    if not raw_lines:
        return None

    lines: list[str] = []
    # Prepend workspace identity
    lines.append(f"WORKSPACE_PROFILE={profile}")
    lines.append("WORKSPACE_HOME=/home/developer")

    for raw_line in raw_lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        bare = stripped
        if bare.startswith("export "):
            bare = bare[7:]
        var_name = bare.split("=", 1)[0].strip()
        if var_name in _HOST_ONLY_VARS:
            continue
        lines.append(bare)

    return "\n".join(lines)


def _resolve_oauth_account() -> dict[str, str] | None:
    """Read oauthAccount from the host's .claude.json for container auth."""
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))
    claude_json = Path(config_dir) / ".claude.json"
    if not claude_json.is_file():
        return None
    try:
        data = json.loads(claude_json.read_text())
        acct = data.get("oauthAccount")
        if isinstance(acct, dict) and "accountUuid" in acct:
            return acct
    except (OSError, json.JSONDecodeError):
        pass
    return None


def _find_available_port(start: int = 7681) -> int:
    """Scan running containers to find a free host port."""
    client = _docker()
    try:
        containers = client.containers.list()
        used: set[int] = set()
        for c in containers:
            ports = c.attrs.get("NetworkSettings", {}).get("Ports") or {}
            for bindings in ports.values():
                if bindings:
                    for b in bindings:
                        if b.get("HostPort"):
                            used.add(int(b["HostPort"]))
        port = start
        while port in used:
            port += 1
        return port
    except Exception as exc:
        log.warning("lifecycle.port_scan_failed", metadata={"reason": str(exc)})
        return start


# ---------------------------------------------------------------------------
# Local vs remote Docker detection
# ---------------------------------------------------------------------------


def _docker_is_local(docker_host: str | None = None) -> bool:
    """Return True when the Docker daemon is local (unix socket or no host set)."""
    host = docker_host or settings.docker.host or ""
    return not host or host.startswith("unix://") or host.startswith("/")


# ---------------------------------------------------------------------------
# Cosign verification
# ---------------------------------------------------------------------------


async def _verify_cosign(image: Any, image_name: str, slog: Any) -> None:
    """Run cosign signature verification according to configured mode.

    Supports two verification strategies:
    - **Keyless** (preferred): uses ``certificate_identity`` + ``oidc_issuer``
      to verify against Sigstore Fulcio/Rekor transparency log.
    - **Key-based** (fallback): uses a local PEM public key file.
    """
    mode = settings.cosign.mode
    key_path = settings.cosign.key
    cert_identity = settings.cosign.certificate_identity
    oidc_issuer = settings.cosign.oidc_issuer

    if mode == "off":
        slog.info("container.cosign_skipped", metadata={"reason": "mode is off"})
        return

    # Determine verification strategy
    use_keyless = bool(cert_identity and oidc_issuer)
    use_key = bool(key_path)

    if not use_keyless and not use_key:
        if mode == "enforce":
            raise ValueError(
                "Cosign enforce mode requires either keyless config "
                "(CL_COSIGN__CERTIFICATE_IDENTITY + CL_COSIGN__OIDC_ISSUER) "
                "or a key (CL_COSIGN__KEY)"
            )
        slog.warning("container.cosign_skipped", metadata={"reason": "no verification configured"})
        return

    # Key-based: verify key file exists on disk
    if not use_keyless and use_key:
        if not os.path.isfile(key_path):
            if mode == "enforce":
                raise FileNotFoundError(f"Cosign public key not found: {key_path}")
            slog.warning(
                "container.cosign_skipped",
                metadata={"reason": f"key file not found: {key_path}"},
            )
            return

    # Resolve repo digests from the pulled image
    repo_digests: list[str] = image.attrs.get("RepoDigests", [])

    if not repo_digests:
        if mode == "enforce":
            raise ValueError(
                f"Image '{image_name}' has no repo digests — "
                "cannot verify a local-only image in enforce mode"
            )
        slog.info(
            "container.cosign_skipped",
            metadata={"reason": "local-only image (no repo digests)"},
        )
        return

    # Run cosign verify
    if use_keyless:
        result = await _run(
            verify_image_keyless, image_name, cert_identity, oidc_issuer, repo_digests
        )
    else:
        result = await _run(verify_image, image_name, key_path, repo_digests)

    if result.verified:
        slog.info(
            "container.cosign_verified",
            metadata={"image_ref": result.image_ref, "method": "keyless" if use_keyless else "key"},
        )
        return

    if mode == "enforce":
        raise CosignVerificationError(result)

    slog.warning(
        "container.cosign_failed",
        metadata={"image_ref": result.image_ref, "stderr": result.stderr},
    )


# ---------------------------------------------------------------------------
# Runner dispatch — when a session names a remote runner, we hand the whole
# provision/configure/start cycle off to it and just await the result.
# ---------------------------------------------------------------------------


async def _provision_via_runner(*, runner: str, **payload: Any) -> SessionContext:
    """Enqueue a session.create work item for a runner and await the
    SessionContext it builds. Caller is expected to have already validated
    that the runner exists."""
    from .runners import get_registry

    reg = get_registry()
    info = await reg.get(runner)
    if info is None:
        raise RuntimeError(
            f"runner {runner!r} is not registered — start it with `brainbox runner`"
        )

    # Capability gate: the requested backend must be supported.
    requested_backend = payload.get("backend") or "docker"
    if not info.capabilities.get(requested_backend):
        raise RuntimeError(
            f"runner {runner!r} does not advertise capability {requested_backend!r}"
        )

    # Backpressure: refuse if runner is at capacity.
    if info.in_flight >= info.max_concurrent:
        raise RuntimeError(
            f"runner {runner!r} is saturated ({info.in_flight}/{info.max_concurrent} in flight) — retry later"
        )

    # Remote runners can't bind-mount from this host's filesystem, so credential
    # delivery must always use the bundle path (keygen → seal → inject).
    payload["delivery"] = "bundle"

    serializable = {
        k: (v.model_dump() if hasattr(v, "model_dump") else v)
        for k, v in payload.items()
        if v is not None
    }
    item = await reg.enqueue(
        runner=runner,
        kind="session.create",
        payload=serializable,
    )
    timeout = float(os.environ.get("BRAINBOX_RUNNER_TIMEOUT", "300"))
    try:
        result = await asyncio.wait_for(item.fut, timeout=timeout)
    except asyncio.TimeoutError as exc:
        await reg.cancel(item.id, "runner timed out")
        raise RuntimeError(
            f"runner {runner!r} did not return a result within {timeout}s"
        ) from exc
    if not result.get("ok"):
        raise RuntimeError(
            f"runner {runner!r} failed session.create: {result.get('error', 'unknown error')}"
        )
    ctx_data = result.get("data") or {}
    ctx = SessionContext(**ctx_data)
    if not ctx.runner_name:
        ctx.runner_name = runner
    # Backfill runner_host from the registry so URL construction works even if
    # the runner didn't include it in the session.create response data.
    if not ctx.runner_host and info is not None:
        ctx.runner_host = info.host
    _sessions[ctx.session_name] = ctx
    return ctx


# ---------------------------------------------------------------------------
# Phase 1: Provision
# ---------------------------------------------------------------------------


async def provision(
    *,
    session_name: str = "default",
    role: str | None = None,
    port: int | None = None,
    hardened: bool = True,
    ttl: int | None = None,
    volume_mounts: list[str] | None = None,
    token: Token | None = None,
    llm_provider: str = "claude",
    llm_model: str | None = None,
    llm_effort: str | None = None,
    ollama_host: str | None = None,
    codex_api_key: str | None = None,
    workspace_profile: str | None = None,
    workspace_home: str | None = None,
    backend: str = "docker",
    vm_template: str | None = None,
    guest_os: str = "linux",
    ports: dict[str, int] | None = None,
    repo_url: str | None = None,
    task_description: str | None = None,
    task_id: str | None = None,
    job_id: str | None = None,
    docker_host: str | None = None,
    delivery: str | None = None,
    runner: str | None = None,
) -> SessionContext:
    from .backends import create_backend

    # Runner routing — if a remote runner is named, dispatch the whole
    # provision request to it and return the SessionContext it builds.
    if runner and runner != "local":
        return await _provision_via_runner(
            runner=runner,
            session_name=session_name,
            role=role,
            port=port,
            hardened=hardened,
            ttl=ttl,
            volume_mounts=volume_mounts,
            token=token,
            llm_provider=llm_provider,
            llm_model=llm_model,
            llm_effort=llm_effort,
            ollama_host=ollama_host,
            codex_api_key=codex_api_key,
            workspace_profile=workspace_profile,
            workspace_home=workspace_home,
            backend=backend,
            vm_template=vm_template,
            guest_os=guest_os,
            ports=ports,
            repo_url=repo_url,
            task_description=task_description,
            task_id=task_id,
            job_id=job_id,
            docker_host=docker_host,
            delivery=delivery,
        )

    resolved_role = role or settings.role
    resolved_prefix = settings.container_prefix or f"{resolved_role}-"
    container_name = f"{resolved_prefix}{session_name}"
    resolved_ttl = ttl if ttl is not None else settings.ttl
    resolved_workspace_profile = workspace_profile or os.environ.get("WORKSPACE_PROFILE")
    resolved_workspace_home = workspace_home or os.environ.get("WORKSPACE_HOME")

    # Determine image/template based on backend
    if backend == "utm":
        image_or_template = vm_template or settings.utm.default_template
        # UTM uses SSH port, not web terminal port
        resolved_port = port or 0  # Will be assigned by backend
    else:
        # Single unified image — role is injected as BRAINBOX_ROLE env var
        image_or_template = settings.image or "brainbox"
        global _port_lock
        if _port_lock is None:
            _port_lock = asyncio.Lock()
        async with _port_lock:
            resolved_port = port or await _run(_find_available_port)

    # Resolve role prompt and teams configuration
    from .registry import get_agent

    teams_enabled = settings.hub.enable_teams
    role_prompt_file = None
    agent_def = get_agent(resolved_role)
    if agent_def and agent_def.role_prompt:
        role_prompt_file = str(settings.agents_dir / agent_def.role_prompt)

    # Apply agent-level model/effort defaults when not explicitly set in request
    if agent_def and llm_model is None:
        if llm_provider == "claude" and agent_def.claude_model:
            llm_model = agent_def.claude_model
        elif llm_provider == "codex" and agent_def.codex_model:
            llm_model = agent_def.codex_model
        elif llm_provider == "ollama" and agent_def.ollama_model:
            llm_model = agent_def.ollama_model
    if agent_def and llm_effort is None and llm_provider == "claude" and agent_def.claude_effort:
        llm_effort = agent_def.claude_effort

    ctx = SessionContext(
        session_name=session_name,
        container_name=container_name,
        port=resolved_port,
        role=resolved_role,
        state=SessionState.PROVISIONING,
        created_at=_now_ms(),
        ttl=resolved_ttl,
        hardened=hardened,
        volume_mounts=volume_mounts or [],
        token=token,
        llm_provider=llm_provider,
        llm_model=llm_model,
        llm_effort=llm_effort,
        ollama_host=ollama_host,
        codex_api_key=codex_api_key,
        workspace_profile=resolved_workspace_profile,
        workspace_home=resolved_workspace_home,
        backend=backend,
        vm_template=vm_template,
        guest_os=guest_os,
        ports=ports,
        teams_enabled=teams_enabled,
        role_prompt_file=role_prompt_file,
        repo_url=repo_url,
        task_description=task_description,
        task_id=task_id,
        job_id=job_id,
        docker_host=docker_host,
        delivery=delivery if delivery in ("bind", "bundle") else "bind",
    )

    slog = get_logger(session_name=session_name, container_name=container_name)

    # Docker-only: cosign verification
    if backend == "docker":
        client = _docker()
        try:
            image = await _run(client.images.get, image_or_template)
        except Exception as exc:
            slog.error("container.provision_failed", metadata={"reason": str(exc)})
            raise

        # Cosign image signature verification
        await _verify_cosign(image, image_or_template, slog)

    # Session data volume (Docker and UTM)
    session_data_dir = settings.sessions_dir / session_name
    session_data_dir.mkdir(parents=True, exist_ok=True)
    volumes = {str(session_data_dir): {"bind": "/home/developer/.claude/projects", "mode": "rw"}}

    # Generate container-optimised .mcp.json from the profile's enabled MCP servers
    # and mount it into ~/workspace so Claude picks it up as the project MCP config.
    # Also bind-mount the profile's global CLAUDE.md so container Claude gets the
    # same working-memory protocol and global instructions as the host.
    if backend == "docker" and resolved_workspace_home:
        _claude_config_dir = Path(
            os.environ.get("CLAUDE_CONFIG_DIR", str(Path(resolved_workspace_home) / ".claude"))
        )
        _mcp_json_path = session_data_dir / "workspace-mcp.json"
        if _generate_container_mcp_json(_claude_config_dir, _mcp_json_path):
            volumes[str(_mcp_json_path)] = {
                "bind": "/home/developer/workspace/.mcp.json",
                "mode": "ro",
            }

        _claude_md = _claude_config_dir / "CLAUDE.md"
        if _claude_md.is_file():
            volumes[str(_claude_md)] = {
                "bind": "/home/developer/.claude/CLAUDE.md",
                "mode": "ro",
            }

    # User-specified volume mounts
    for vol in ctx.volume_mounts:
        parts = vol.split(":")
        if len(parts) >= 2:
            host_path = parts[0]
            container_path = parts[1]
            mode = parts[2] if len(parts) > 2 else "rw"
            volumes[host_path] = {"bind": container_path, "mode": mode}

    # Profile credential / config mounts (Docker only for now)
    if backend == "docker":
        profile_mounts = _resolve_profile_mounts(
            workspace_profile=resolved_workspace_profile,
            workspace_home=resolved_workspace_home,
        )
        if ctx.delivery == "bundle":
            # Cred bind-mounts are dropped; brainbox-init will lay them down inside
            # the container from a sealed bundle. Non-cred mounts (sockets, runtime
            # code, vaults) stay as bind mounts because they can't ride in a tar.
            profile_mounts = {
                host: spec
                for host, spec in profile_mounts.items()
                if not _is_credential_bind(spec["bind"])
            }
        volumes.update(profile_mounts)
        # Track which mounts were actually resolved
        _bind_to_name = {
            "/home/developer/.aws": "aws",
            "/home/developer/.azure": "azure",
            "/home/developer/.kube": "kube",
            "/home/developer/.ssh": "ssh",
            "/home/developer/.gnupg/S.gpg-agent": "gpg",
            "/home/developer/.gitconfig": "gitconfig",
            "/home/developer/.gcloud": "gcloud",
            "/home/developer/.terraform.d": "terraform",
        }
        for mount in profile_mounts.values():
            name = _bind_to_name.get(mount["bind"])
            if name:
                ctx.profile_mounts.add(name)

    # Hardening kwargs (Docker only)
    if backend == "docker":
        if hardened:
            hardening_kwargs = get_hardening_kwargs()
        else:
            hardening_kwargs = get_legacy_kwargs()
    else:
        hardening_kwargs = {}

    # Create backend and provision
    backend_impl = create_backend(backend)
    ctx = await backend_impl.provision(
        ctx,
        image_or_template=image_or_template,
        volumes=volumes,
        hardening_kwargs=hardening_kwargs,
    )

    _sessions[session_name] = ctx
    return ctx


# ---------------------------------------------------------------------------
# Phase 2: Configure
# ---------------------------------------------------------------------------


async def configure(ctx_or_name: SessionContext | str) -> SessionContext:
    from .backends import create_backend

    ctx = _resolve(ctx_or_name)
    ctx.state = SessionState.CONFIGURING

    # Resolve secrets (1Password when configured, plaintext files otherwise)
    from .secrets import resolve_secrets, has_op_integration

    resolved = resolve_secrets()

    # Inject provider-specific env vars
    if ctx.llm_provider == "ollama":
        resolved["ANTHROPIC_AUTH_TOKEN"] = "ollama"
        resolved["ANTHROPIC_API_KEY"] = ""
        resolved["ANTHROPIC_BASE_URL"] = ctx.ollama_host or settings.ollama.host
        resolved["CLAUDE_MODEL"] = ctx.llm_model or settings.ollama.model

    elif ctx.llm_provider == "codex":
        api_key = ctx.codex_api_key or settings.codex.api_key.get_secret_value()
        if api_key:
            # Only set if we have a key — otherwise rely on OPENAI_API_KEY
            # already present in resolved from workspace secrets.
            resolved["OPENAI_API_KEY"] = api_key
        resolved["CODEX_MODEL"] = ctx.llm_model or settings.codex.model

    else:
        # Claude: set model if explicitly requested (agent default or session override)
        if ctx.llm_model:
            resolved["CLAUDE_MODEL"] = ctx.llm_model

        # Forward OAuth token from host env if present — takes precedence over
        # .claude.json oauthAccount for hosts that manage auth via env var.
        oauth_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
        if oauth_token:
            resolved["CLAUDE_CODE_OAUTH_TOKEN"] = oauth_token

    # Always expose provider name so ttyd-wrapper.sh can detect which CLI to launch
    resolved["LLM_PROVIDER"] = ctx.llm_provider

    # Inject effort for Claude (low | medium | high)
    if ctx.llm_provider == "claude" and ctx.llm_effort:
        resolved["CLAUDE_EFFORT"] = ctx.llm_effort

    # Phase 1: Enable Claude Code Teams experimental feature
    if ctx.teams_enabled:
        resolved["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"] = "1"

    # Inject hub URL for agent communication (shell scripts use BRAINBOX_HUB_URL;
    # the brainbox MCP server uses BRAINBOX_URL — keep both in sync)
    _hub_url = f"http://host.docker.internal:{settings.api_port}"
    resolved["BRAINBOX_HUB_URL"] = _hub_url
    resolved["BRAINBOX_URL"] = _hub_url

    # Inject task/job IDs so workers can build unique branch names and identify themselves
    if ctx.task_id:
        resolved["BRAINBOX_TASK_ID"] = ctx.task_id
    if ctx.job_id:
        resolved["BRAINBOX_JOB_ID"] = ctx.job_id
    # Inject token ID so the agent can authenticate API calls with its own identity
    if ctx.token:
        resolved["BRAINBOX_TOKEN_ID"] = ctx.token.token_id

    # Inject repo URL if associated
    if ctx.repo_url:
        resolved["BRAINBOX_REPO_URL"] = ctx.repo_url

    # Read profile .env.secrets (1Password FIFO or plaintext) and merge
    if ctx.workspace_home:
        secrets_path = Path(ctx.workspace_home) / ".env.secrets"
        if secrets_path.exists():
            try:
                for line in secrets_path.read_text().splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("export "):
                        line = line[7:]
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip()
                    if key and key not in _HOST_ONLY_VARS and key not in resolved:
                        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                            value = value[1:-1]
                        resolved[key] = value
            except OSError:
                pass  # FIFO may not be readable if 1Password is locked

    ctx.secrets.update(resolved)
    if not ctx.hardened:
        ctx.env_content = "\n".join(f"export {k}={shlex.quote(v)}" for k, v in resolved.items())

    # Agent token — store only the UUID so `Authorization: Bearer <content>` works
    if ctx.token:
        ctx.secrets["agent-token"] = ctx.token.token_id
    else:
        ctx.secrets["agent-token"] = json.dumps(
            {
                "stub": True,
                "issued": _iso_now(),
                "note": "Use hub API to get a real token",
            }
        )

    # Resolve OAuth account
    oauth_account = _resolve_oauth_account()

    # Delegate to backend (profile_env is handled in start())
    backend_impl = create_backend(ctx.backend)
    ctx = await backend_impl.configure(
        ctx,
        secrets=ctx.secrets,
        env_content=ctx.env_content,
        oauth_account=oauth_account,
        profile_env=None,  # Handled in start()
    )

    slog = get_logger(session_name=ctx.session_name, container_name=ctx.container_name)
    slog.info(
        "container.configured",
        metadata={
            "secretCount": len(ctx.secrets),
            "hardened": ctx.hardened,
            "source": "1password" if has_op_integration() else "files",
        },
    )
    return ctx


# ---------------------------------------------------------------------------
# Phase 3: Start
# ---------------------------------------------------------------------------


async def start(ctx_or_name: SessionContext | str) -> SessionContext:
    from .backends import create_backend

    ctx = _resolve(ctx_or_name)
    ctx.state = SessionState.STARTING
    slog = get_logger(session_name=ctx.session_name, container_name=ctx.container_name)

    # Delegate to backend
    backend_impl = create_backend(ctx.backend)
    ctx = await backend_impl.start(ctx)

    slog.info("container.started", metadata={"port": ctx.port, "backend": ctx.backend})
    return ctx


# ---------------------------------------------------------------------------
# Phase 4: Monitor (delegates to monitor module)
# ---------------------------------------------------------------------------


async def monitor(ctx_or_name: SessionContext | str) -> SessionContext:
    from .monitor import start_monitoring

    ctx = _resolve(ctx_or_name)
    start_monitoring(ctx)
    ctx.state = SessionState.MONITORING
    slog = get_logger(session_name=ctx.session_name, container_name=ctx.container_name)
    slog.info("container.monitoring", metadata={"ttl": ctx.ttl})
    return ctx


# ---------------------------------------------------------------------------
# Phase 5: Recycle
# ---------------------------------------------------------------------------


async def recycle(ctx_or_name: SessionContext | str, reason: str = "manual") -> SessionContext:
    from .backends import create_backend
    from .monitor import stop_monitoring

    ctx = _resolve(ctx_or_name)
    ctx.state = SessionState.RECYCLING
    slog = get_logger(session_name=ctx.session_name, container_name=ctx.container_name)

    stop_monitoring(ctx.session_name)

    # Delegate to backend
    backend_impl = create_backend(ctx.backend)
    await backend_impl.stop(ctx)
    await backend_impl.remove(ctx)

    ctx.state = SessionState.RECYCLED
    _sessions.pop(ctx.session_name, None)
    slog.info("container.recycled", metadata={"reason": reason, "backend": ctx.backend})

    # Clean up host worktree if one was created for this session
    if ctx.worktree_path:
        await _remove_host_worktree(ctx.worktree_path)

    return ctx


# ---------------------------------------------------------------------------
# Repo helpers
# ---------------------------------------------------------------------------


async def _create_host_worktree(repo_path: str, branch: str) -> str:
    """Create a git worktree on the host and return its path."""
    wt_id = uuid.uuid4().hex[:8]
    wt_path = f"/tmp/brainbox-wt-{wt_id}"
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", repo_path, "worktree", "add", "-B", branch, wt_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, "git worktree add", stderr=stderr)
    log.info("worktree.created", metadata={"path": wt_path, "branch": branch})
    return wt_path


async def _remove_host_worktree(wt_path: str) -> None:
    """Remove a host git worktree, ignoring errors."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "worktree", "remove", "--force", wt_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, "git worktree remove")
        log.info("worktree.removed", metadata={"path": wt_path})
    except Exception as exc:
        log.warning("worktree.remove_failed", metadata={"path": wt_path, "error": str(exc)})


async def _inject_repo_clone(container: Any, repo: Any) -> None:
    """Clone the repo inside the container, then optionally create an inner worktree."""
    clone_dest = repo.container_path
    parent_dir = clone_dest.rsplit("/", 1)[0] if "/" in clone_dest else "/home/developer"

    # Ensure parent directory exists
    await _run(
        container.exec_run,
        ["sh", "-c", f"mkdir -p {parent_dir}"],
        user="developer",
    )

    if repo.mode == "ci-ratchet":
        # ci-ratchet uses HTTPS + GH_TOKEN (no SSH agent inside the container).
        # Normalise SSH remote URLs to HTTPS so GH_TOKEN auth works.
        clone_url = repo.url
        if clone_url.startswith("git@github.com:"):
            clone_url = "https://github.com/" + clone_url[len("git@github.com:") :]
        elif clone_url.startswith("git@gitlab.com:"):
            clone_url = "https://gitlab.com/" + clone_url[len("git@gitlab.com:") :]

        if clone_url.startswith("https://"):
            host_path = clone_url[len("https://") :]
            clone_cmd = (
                ". /home/developer/.env 2>/dev/null || true"
                f" && git clone https://x-access-token:${{GH_TOKEN}}@{shlex.quote(host_path)} {shlex.quote(clone_dest)}"
            )
        else:
            clone_cmd = f"git clone {clone_url} {shlex.quote(clone_dest)}"

        result = await _run(
            container.exec_run,
            ["sh", "-c", clone_cmd],
            user="developer",
        )
        if result.exit_code and result.exit_code != 0:
            output = result.output.decode() if result.output else ""
            raise RuntimeError(f"git clone failed (exit {result.exit_code}): {output}")

        # Create the work branch locally
        result = await _run(
            container.exec_run,
            ["git", "-C", clone_dest, "checkout", "-b", repo.branch],
            user="developer",
        )
        if result.exit_code and result.exit_code != 0:
            output = result.output.decode() if result.output else ""
            raise RuntimeError(f"git checkout -b failed (exit {result.exit_code}): {output}")
        return

    # Clone into the container (clone / clone-worktree)
    result = await _run(
        container.exec_run,
        ["git", "clone", "--branch", repo.branch, "--single-branch", repo.url, clone_dest],
        user="developer",
    )
    if result.exit_code and result.exit_code != 0:
        output = result.output.decode() if result.output else ""
        raise RuntimeError(f"git clone failed (exit {result.exit_code}): {output}")

    if repo.mode == "clone-worktree":
        wt_path = clone_dest + "-wt"
        result = await _run(
            container.exec_run,
            ["git", "-C", clone_dest, "worktree", "add", "-B", repo.branch, wt_path],
            user="developer",
        )
        if result.exit_code and result.exit_code != 0:
            output = result.output.decode() if result.output else ""
            raise RuntimeError(f"git worktree add failed (exit {result.exit_code}): {output}")


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------


async def run_pipeline(
    *,
    session_name: str = "default",
    role: str | None = None,
    port: int | None = None,
    hardened: bool = True,
    ttl: int | None = None,
    volume_mounts: list[str] | None = None,
    token: Token | None = None,
    llm_provider: str = "claude",
    llm_model: str | None = None,
    llm_effort: str | None = None,
    ollama_host: str | None = None,
    codex_api_key: str | None = None,
    workspace_profile: str | None = None,
    workspace_home: str | None = None,
    backend: str = "docker",
    vm_template: str | None = None,
    guest_os: str = "linux",
    ports: dict[str, int] | None = None,
    repo_url: str | None = None,
    task_description: str | None = None,
    task_id: str | None = None,
    job_id: str | None = None,
    docker_host: str | None = None,
    repo: Any = None,  # RepoConfig | None — avoid circular import
    delivery: str | None = None,
    runner: str | None = None,
) -> SessionContext:
    # Pre-provision: ci-ratchet sets defaults (branch, role, task_description).
    # "Brownian ratchet" concept from multiclaude by Dan Lorenc et al.:
    # https://github.com/dlorenc/multiclaude
    if repo is not None and repo.mode == "ci-ratchet":
        if not repo.branch:
            repo = repo.model_copy(update={"branch": f"work/{session_name}"})
        if role is None or role == "developer":
            role = "worker"
        if task_description is None:
            task_description = repo.task

    # Pre-provision: worktree-mount creates a host worktree and mounts it
    worktree_path: str | None = None
    if repo is not None and repo.mode == "worktree-mount":
        worktree_path = await _create_host_worktree(repo.url, repo.branch)
        volume_mounts = list(volume_mounts or [])
        volume_mounts.append(f"{worktree_path}:{repo.container_path}:rw")

    ctx = await provision(
        session_name=session_name,
        role=role,
        port=port,
        hardened=hardened,
        ttl=ttl,
        volume_mounts=volume_mounts,
        token=token,
        llm_provider=llm_provider,
        llm_model=llm_model,
        llm_effort=llm_effort,
        ollama_host=ollama_host,
        codex_api_key=codex_api_key,
        workspace_profile=workspace_profile,
        workspace_home=workspace_home,
        backend=backend,
        vm_template=vm_template,
        guest_os=guest_os,
        ports=ports,
        repo_url=repo_url,
        task_description=task_description,
        task_id=task_id,
        job_id=job_id,
        docker_host=docker_host,
        delivery=delivery,
        runner=runner,
    )

    # If the session was dispatched to a remote runner, the runner has already
    # driven provision → configure → start. Skip the local config bundle and
    # remaining pipeline steps; the runner owns the lifecycle from here.
    if ctx.runner_name and ctx.runner_name != "local":
        return ctx

    # Profile .claude is mounted read-only at provision time (see _build_volume_map).
    # Config bundle injection is no longer needed — MCP servers, settings, and
    # plugins are delivered via the bind mount.
    if backend == "docker":
        from .backends import create_backend
        docker_backend = create_backend("docker")

        # Remote Docker: inject live credential proxies instead of bind mounts
        if not _docker_is_local(docker_host):
            await docker_backend.inject_remote_credentials(ctx)

        # Fix git credential helper paths for all containers (host brew path → container brew path)
        await docker_backend.fix_git_credential_paths(ctx)

    # Store worktree path in context so delete can clean it up
    if worktree_path:
        ctx.worktree_path = worktree_path

    await configure(ctx)
    await start(ctx)

    # Post-start: inject repo clone for clone / clone-worktree / ci-ratchet modes
    if (
        repo is not None
        and repo.mode in ("clone", "clone-worktree", "ci-ratchet")
        and backend == "docker"
    ):
        try:
            client = _docker(docker_host)
            container = await _run(client.containers.get, ctx.container_name)
            await _inject_repo_clone(container, repo)
            log.info("repo.cloned", metadata={"mode": repo.mode, "branch": repo.branch})
        except Exception as exc:
            log.warning("repo.clone_failed", metadata={"error": str(exc)})

    # Post-start: auto-start merge-queue container for ci-ratchet mode
    if (
        repo is not None
        and repo.mode == "ci-ratchet"
        and repo.start_merge_queue
        and backend == "docker"
    ):
        try:
            from .router import submit_task

            await submit_task(
                f"Merge queue for {repo.url}",
                "merge-queue",
                repo_url=repo.url,
            )
            log.info("ci_ratchet.merge_queue_started", metadata={"repo": repo.url})
        except Exception as exc:
            log.warning("ci_ratchet.merge_queue_failed", metadata={"error": str(exc)})

    await monitor(ctx)
    return ctx


# ---------------------------------------------------------------------------
# Session lookup
# ---------------------------------------------------------------------------


def _resolve(ctx_or_name: SessionContext | str) -> SessionContext:
    if isinstance(ctx_or_name, str):
        ctx = _sessions.get(ctx_or_name)
        if not ctx:
            raise ValueError(f"Session '{ctx_or_name}' not found")
        return ctx
    return ctx_or_name


def get_session(session_name: str) -> SessionContext | None:
    return _sessions.get(session_name)


def list_sessions() -> list[SessionContext]:
    return list(_sessions.values())


def register_runner_session(ctx: SessionContext) -> None:
    """Register a session built by a remote runner. Used for late result
    delivery — when the runner couldn't reach the API during execution,
    it queues the result and retries; this call reconciles the session
    into _sessions once the result arrives."""
    if ctx.session_name not in _sessions:
        _sessions[ctx.session_name] = ctx


async def _dispatch_runner_op(
    session_name: str,
    kind: str,
    extra_payload: dict[str, Any] | None = None,
    *,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Dispatch a runner operation (stop/exec/query) and await its result.

    Raises RuntimeError on timeout or if the runner reports failure."""
    from .runners import get_registry

    ctx = _sessions.get(session_name)
    if ctx is None or not ctx.runner_name:
        raise RuntimeError(f"no runner session for {session_name!r}")

    reg = get_registry()
    payload: dict[str, Any] = {"session_name": session_name, "container_name": ctx.container_name}
    if extra_payload:
        payload.update(extra_payload)

    item = await reg.enqueue(runner=ctx.runner_name, kind=kind, payload=payload)
    try:
        result = await asyncio.wait_for(item.fut, timeout=timeout)
    except asyncio.TimeoutError as exc:
        await reg.cancel(item.id, f"{kind} timed out")
        raise RuntimeError(f"runner {ctx.runner_name!r} did not complete {kind} within {timeout}s") from exc

    if not result.get("ok"):
        raise RuntimeError(f"runner {ctx.runner_name!r} failed {kind}: {result.get('error', 'unknown')}")
    return result.get("data") or {}


def recover_sessions_from_docker() -> int:
    """Scan Docker for managed containers not in _sessions and re-register them.

    Called on API startup so that containers started before a restart are not
    orphaned — check_running_tasks() can then find them and recycle properly.
    Returns the number of sessions recovered.
    """
    try:
        from .models import SessionContext, SessionState

        client = _docker()
        containers = client.containers.list(
            filters={"label": "brainbox.managed=true", "status": "running"}
        )
    except Exception as exc:
        log.warning("lifecycle.recover_sessions_failed", metadata={"reason": str(exc)})
        return 0

    recovered = 0
    for container in containers:
        labels = container.labels or {}
        session_name = labels.get("brainbox.session_name", "")
        if not session_name:
            # Fallback: derive from container name using known role prefixes
            name = container.name or ""
            _PREFIXES = (
                "developer-", "researcher-", "performer-",
                "supervisor-", "worker-", "merge-queue-", "pr-shepherd-", "reviewer-",
            )
            for pfx in _PREFIXES:
                if name.startswith(pfx):
                    session_name = name[len(pfx):]
                    break
            if not session_name:
                session_name = name

        if session_name in _sessions:
            continue  # already registered

        # Reconstruct a minimal SessionContext so recycle() can find the session
        port_bindings = container.ports or {}
        port = 7681
        for binding in port_bindings.values():
            if binding:
                try:
                    port = int(binding[0]["HostPort"])
                    break
                except (KeyError, IndexError, ValueError):
                    pass

        ctx = SessionContext(
            session_name=session_name,
            container_name=container.name,
            port=port,
            role=labels.get("brainbox.role", "developer"),
            backend="docker",
            state=SessionState.RUNNING,
            created_at=_now_ms(),
            ttl=0,  # no TTL enforcement for recovered sessions
            hardened=False,
        )
        _sessions[session_name] = ctx
        recovered += 1
        log.info(
            "lifecycle.session_recovered",
            metadata={"session": session_name, "container": container.name},
        )

    if recovered:
        log.info("lifecycle.sessions_recovered", metadata={"count": recovered})
    return recovered



"""Application settings from environment variables and defaults.

Configuration is read from environment variables with the ``CL_`` prefix
(nested via ``CL__`` double-underscore delimiter).  For example:

    CL_API_PORT=9999            # top-level field
    CL_LANGFUSE__BASE_URL=...   # nested LangfuseSettings.base_url

Container-injected environment variables
-----------------------------------------
The following variables are injected by brainbox into worker/task containers
at session-creation time:

``BRAINBOX_TASK_ID``
    UUID of the hub task assigned to this container.  Workers use this to
    report completion back to the hub via ``POST /api/hub/messages``.

``BRAINBOX_JOB_ID``
    UUID of the parent supervisor job that spawned this worker.  Set when a
    supervisor passes its own task ID as ``job_id`` in ``submit_task()``.
    Workers can include this in task-completion payloads so the supervisor
    can correlate results.

``BRAINBOX_REPO_URL``
    GitHub repository URL associated with the task (e.g.
    ``https://github.com/org/repo``).  Set when the task was submitted with a
    ``repo_url`` argument.  Workers use this to clone the repo and open PRs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_config_dir() -> Path:
    import os

    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        # New path: phantom-ink/brainbox; fallback to legacy developer/
        new = Path(xdg) / "phantom-ink" / "brainbox"
        legacy = Path(xdg) / "developer"
        return new if new.is_dir() else (legacy if legacy.is_dir() else new)
    ws = os.environ.get("WORKSPACE_HOME")
    if ws:
        new = Path(ws) / ".config" / "phantom-ink" / "brainbox"
        legacy = Path(ws) / ".config" / "developer"
        return new if new.is_dir() else (legacy if legacy.is_dir() else new)
    new = Path.home() / ".config" / "phantom-ink" / "brainbox"
    legacy = Path.home() / ".config" / "developer"
    return new if new.is_dir() else (legacy if legacy.is_dir() else new)


def migrate_config_dir() -> None:
    """Migrate legacy ~/.config/developer/ → ~/.config/phantom-ink/brainbox/.

    Moves the directory contents if the legacy path exists and the new path
    does not. Safe to call multiple times — no-op if already migrated.
    """
    import os
    import shutil

    bases: list[tuple[Path, Path]] = []

    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        bases.append((Path(xdg) / "developer", Path(xdg) / "phantom-ink" / "brainbox"))
    ws = os.environ.get("WORKSPACE_HOME")
    if ws:
        bases.append(
            (Path(ws) / ".config" / "developer", Path(ws) / ".config" / "phantom-ink" / "brainbox")
        )
    bases.append(
        (Path.home() / ".config" / "developer", Path.home() / ".config" / "phantom-ink" / "brainbox")
    )

    for legacy, new in bases:
        if legacy.is_dir() and not new.is_dir():
            new.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(legacy), str(new))
            # Leave a symlink so anything still referencing the old path works
            try:
                legacy.symlink_to(new)
            except OSError:
                pass
            break


class ResourceSettings(BaseSettings):
    memory: str = "2g"
    cpus: str = "2"
    tmpfs_workspace: str = "500M"
    tmpfs_tmp: str = "100M"
    tmpfs_secrets: str = "10M"


class HardeningSettings(BaseSettings):
    read_only_rootfs: bool = True
    no_new_privileges: bool = True
    drop_caps: list[str] = Field(
        default_factory=lambda: ["NET_RAW", "SYS_ADMIN", "MKNOD", "SYS_CHROOT", "NET_ADMIN"]
    )
    seccomp_profile: str = "default"


class CosignSettings(BaseSettings):
    mode: Literal["off", "warn", "enforce"] = "warn"
    key: str = ""  # path to PEM public key file
    certificate_identity: str = ""  # signer identity pattern (regexp) for keyless verification
    oidc_issuer: str = ""  # OIDC issuer URL for keyless verification


def _langfuse_env_fallback(*env_names: str) -> str:
    """Return the first set env var from the list, or empty string."""
    import os

    for name in env_names:
        val = os.environ.get(name)
        if val:
            return val
    return ""


def _langfuse_base_url() -> str:
    return _langfuse_env_fallback("LANGFUSE_BASE_URL") or "http://localhost:3000"


def _langfuse_public_key() -> str:
    return _langfuse_env_fallback("LANGFUSE_PUBLIC_KEY", "LANGFUSE_API_PUBLIC_KEY")


def _langfuse_secret_key() -> str:
    return _langfuse_env_fallback("LANGFUSE_SECRET_KEY", "LANGFUSE_API_SECRET_KEY")


class LangfuseSettings(BaseSettings):
    mode: Literal["off", "warn", "enforce"] = "warn"
    base_url: str = Field(default_factory=_langfuse_base_url)
    public_key: str = Field(default_factory=_langfuse_public_key)
    secret_key: SecretStr = Field(default_factory=_langfuse_secret_key)


class MinioSettings(BaseSettings):
    """MinIO / S3-compatible object store.

    Two buckets, profile-prefixed within each. The credential pair
    here is a per-profile IAM key minted by ``docker/minio/bootstrap-
    buckets.sh``; cross-profile reads return 403 by policy.

    ``enabled=False`` (the default) means the artifact store is off —
    no client created, no bucket calls made, the app hides the Files
    panel. Operator opts in by setting CL_MINIO__ENABLED=true alongside
    the endpoint + creds.
    """

    enabled: bool = False
    endpoint: str = "http://localhost:9090"
    access_key: SecretStr = SecretStr("")
    secret_key: SecretStr = SecretStr("")
    region: str = "us-east-1"
    # Two buckets, separate access patterns.
    bucket_artifacts: str = "phantom-artifacts"
    bucket_vault: str = "phantom-vault"
    # Profile prefix root inside each bucket. Operator's brainbox.env
    # sets this to the active profile so the daemon's reads/writes
    # land under the right namespace.
    profile_prefix: str = ""


class GatewaySettings(BaseSettings):
    """MCP gateway (ADR-002) — per-profile encrypted env store.

    ``secret_key`` is the single operator-held key used to encrypt/decrypt
    each profile's env at rest — an **age passphrase** (any strong string;
    encryption uses age via pyrage, passphrase mode). Set
    ``CL_GATEWAY__SECRET_KEY`` on the host where the gateway runs; it lives
    only in the process env/memory. Empty = the store is locked (no
    encrypt/decrypt).

    ``secrets_dir`` holds the encrypted per-profile blobs; empty =
    ``<config_dir>/gateway/secrets``.
    """

    secret_key: SecretStr = SecretStr("")
    secrets_dir: str = ""
    # Path to the curated MCP server catalog (reflex's mcp-catalog.json). Empty
    # = no downstream servers. (DB-backed/app-editable registry is issue #152.)
    catalog_path: str = ""
    # Allowlist of catalog server names the gateway exposes. Empty = none;
    # set CL_GATEWAY__SERVERS='["phantom-brain","slack"]'. Keeps tools/list from
    # spawning every catalog server. Per-profile enablement is issue #152.
    servers: list[str] = Field(default_factory=list)
    # Session wiring (ADR-002 phase 3): when servers are exposed, spawned
    # sessions get a `phantom-gateway` HTTP MCP entry injected into their
    # container .mcp.json, with a per-session Tier-0 token for the session's
    # profile. ``inject_sessions`` is the kill-switch; ``container_url`` is how
    # the container reaches the gateway (host.docker.internal on Docker
    # Desktop); ``session_token_ttl`` bounds the minted token's lifetime.
    inject_sessions: bool = True
    container_url: str = "http://host.docker.internal:9999/gateway/mcp"
    session_token_ttl: int = 86400  # seconds (24h)


def _qdrant_url() -> str:
    import os

    return os.environ.get("QDRANT_URL") or "http://localhost:6333"


def _qdrant_api_key() -> str:
    import os

    return os.environ.get("QDRANT_API_KEY") or ""


class QdrantSettings(BaseSettings):
    enabled: bool = True
    url: str = Field(default_factory=_qdrant_url)
    api_key: SecretStr = Field(default_factory=_qdrant_api_key)
    collection: str = "brainbox"


class ProfileSettings(BaseSettings):
    mount_env: bool = True  # mount the profile .env from volatile cache
    mount_aws: bool = True
    mount_azure: bool = True
    mount_kube: bool = True
    mount_ssh: bool = True  # .ssh directory
    mount_gpg: bool = True  # .gnupg directory + agent socket forwarding for commit signing
    mount_gitconfig: bool = True  # .gitconfig file
    mount_gcloud: bool = False  # opt-in
    mount_terraform: bool = False  # opt-in
    mount_codex: bool = True  # ~/.codex config directory
    mount_reflex: bool = True  # Reflex share dir (hooks/skills runtime)
    reflex_share_path: str = "/opt/homebrew/opt/reflex/share/reflex"
    mount_obsidian_vault: bool = True  # Obsidian <profile>-memory vault (rw)


class OllamaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OLLAMA_")

    host: str = "http://localhost:11434"
    model: str = "qwen3:8b"


class CodexSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CODEX_")

    api_key: SecretStr = SecretStr("")  # Falls back to OPENAI_API_KEY in env
    model: str = "gpt-4.5"


class UTMSettings(BaseSettings):
    default_template: str = "brainbox-macos-template"
    ssh_base_port: int = 2200
    utmctl_path: str = (
        ""  # Empty = auto-detect via PATH (searches /opt/homebrew/bin, /usr/local/bin)
    )
    ssh_key_path: str = ""  # Empty = use ~/.ssh/id_ed25519
    docs_dir: str = ""  # Empty = use ~/Library/Containers/com.utmapp.UTM/Data/Documents
    share_dir: str = ""  # Host path of the VirtioFS 'brainbox' share; empty = ARP-only
    ssh_user: str = ""  # VM SSH username; empty = use OS username ($USER)


class HubSettings(BaseSettings):
    flush_interval: int = 30  # seconds
    prune_completed_after: int = 3600  # seconds
    message_retention: int = 100
    token_ttl: int = 3600  # seconds — default token time-to-live
    enable_teams: bool = True  # Enable Claude Code Teams in containers
    persistent_token_ttl: int = 86400  # 24h TTL for persistent agent tokens


class PipelineSettings(BaseSettings):
    builtin_dir: str = ""  # Empty = auto-detect brainbox/pipelines/
    config_dir: str = ""  # Empty = ~/.config/phantom-ink/brainbox/pipelines/
    workspace_dir: str = ""  # Empty = $BRAINBOX_PIPELINES_DIR env var
    default_timeout: int = 600  # seconds per step
    max_concurrent_steps: int = 4


class DockerSettings(BaseSettings):
    host: str | None = None  # None = auto-detect local socket
    tls_verify: bool = True
    cert_path: str | None = None


class Settings(BaseSettings):
    role: str = "assistant"
    image: str = ""
    container_prefix: str = ""
    user: str = "65534:65534"
    config_dir: Path = Field(default_factory=_default_config_dir)
    cors_origins: list[str] = Field(default_factory=list)
    ttl: int = 3600
    health_check_interval: int = 30  # seconds
    health_check_timeout: int = 5  # seconds
    health_check_retries: int = 3

    api_port: int = Field(default=9999, ge=1, le=65535)
    public_host: str = "localhost"  # Advertised hostname/IP for the API; set CL_PUBLIC_HOST on remote hosts
    public_url: str = ""      # Full HTTPS base URL for the API (e.g. https://phantom-api.neverprepared.com)
    sessions_url: str = ""    # Base URL for terminal sessions (e.g. https://sessions.neverprepared.com)
    bind_host: str = ""       # Override for container port bind IP; auto-derived when unset
    nginx_config_dir: str = ""        # Dir for per-session nginx .conf fragments
    nginx_reload_cmd: str = "nginx -s reload"

    @property
    def container_bind_ip(self) -> str:
        """IP to bind container ports to.

        When a reverse proxy fronts the terminals (sessions_url or
        nginx_config_dir set), bind to 127.0.0.1 — the proxy handles
        external access. Otherwise bind to 0.0.0.0 for direct remote access.
        """
        if self.bind_host:
            return self.bind_host
        if self.sessions_url or self.nginx_config_dir or self.public_url:
            return "127.0.0.1"
        if self.public_host in ("localhost", "127.0.0.1", ""):
            return "127.0.0.1"
        return "0.0.0.0"

    @property
    def session_base_url(self) -> str:
        """Base URL used to construct terminal session URLs."""
        if self.sessions_url:
            return self.sessions_url.rstrip("/")
        if self.public_url:
            return self.public_url.rstrip("/")
        return f"http://{self.public_host}"

    op_vault: str = ""

    # Private Docker registry for pre-built profile images.
    # Set CL_REGISTRY_URL to enable (e.g. registry.internal:5000).
    # Credentials via CL_REGISTRY_USERNAME / CL_REGISTRY_PASSWORD.
    registry_url: str = ""
    registry_username: str = ""
    registry_password: SecretStr = SecretStr("")

    @property
    def profile_image_tag(self) -> str | None:
        """Return None if registry is not configured."""
        return self.registry_url.rstrip("/") if self.registry_url else None

    resources: ResourceSettings = Field(default_factory=ResourceSettings)
    hardening: HardeningSettings = Field(default_factory=HardeningSettings)
    cosign: CosignSettings = Field(default_factory=CosignSettings)
    langfuse: LangfuseSettings = Field(default_factory=LangfuseSettings)
    minio: MinioSettings = Field(default_factory=MinioSettings)
    gateway: GatewaySettings = Field(default_factory=GatewaySettings)
    qdrant: QdrantSettings = Field(default_factory=QdrantSettings)
    profile: ProfileSettings = Field(default_factory=ProfileSettings)
    hub: HubSettings = Field(default_factory=HubSettings)
    ollama: OllamaSettings = Field(default_factory=OllamaSettings)
    codex: CodexSettings = Field(default_factory=CodexSettings)
    utm: UTMSettings = Field(default_factory=UTMSettings)
    pipeline: PipelineSettings = Field(default_factory=PipelineSettings)
    docker: DockerSettings = Field(default_factory=DockerSettings)
    path_map: dict[str, str] = Field(
        default_factory=dict
    )  # host path → container path substitutions

    # GitHub webhook trigger for Loop runs. When github_webhook_secret is
    # set, /api/webhooks/github accepts signed payloads, verifies them via
    # X-Hub-Signature-256, and fires start_loop on the pr-review-loop
    # template. github_loop_repos optionally restricts which repos can
    # trigger: empty list means accept any signed payload (require an
    # external allowlist via the webhook secret rotation), non-empty means
    # the payload's repository.full_name must be in the list.
    github_webhook_secret: str = ""  # CL_GITHUB_WEBHOOK_SECRET
    github_loop_repos: list[str] = Field(default_factory=list)  # CL_GITHUB_LOOP_REPOS

    # AI Assist for Loop templates dispatches to an ephemeral brainbox
    # session — no API keys. See top-level CLAUDE.md "No API Keys for
    # Agents" and the loop_assist module docstring.

    model_config = {"env_prefix": "CL_", "env_nested_delimiter": "__"}

    @property
    def resolved_image(self) -> str:
        return self.image or "brainbox"

    @property
    def resolved_prefix(self) -> str:
        return self.container_prefix or f"{self.role}-"

    @property
    def secrets_dir(self) -> Path:
        return self.config_dir / ".secrets"

    @property
    def op_sa_token_file(self) -> Path:
        return self.config_dir / ".op-sa-token"

    @property
    def sessions_dir(self) -> Path:
        return self.config_dir / "sessions"

    @property
    def state_file(self) -> Path:
        return self.config_dir / "hub-state.json"

    @property
    def db_file(self) -> Path:
        return self.config_dir / "brainbox.db"

    @property
    def api_key_file(self) -> Path:
        return self.config_dir / ".api-key"

    @property
    def agents_dir(self) -> Path:
        return Path(__file__).resolve().parent.parent.parent / "agents"

    @property
    def roles_dir(self) -> Path:
        return self.agents_dir / "roles"


settings = Settings()

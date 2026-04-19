"""Application settings from environment variables and defaults."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
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


class ArtifactSettings(BaseSettings):
    mode: Literal["off", "warn", "enforce"] = "warn"
    endpoint: str = "http://localhost:9090"
    access_key: str = ""
    secret_key: str = ""
    bucket: str = "artifacts"
    region: str = "us-east-1"


def _langfuse_env_fallback(field: str, *env_names: str) -> str:
    """Return the first set env var from the list, or empty string."""
    import os

    for name in env_names:
        val = os.environ.get(name)
        if val:
            return val
    return ""


def _langfuse_base_url() -> str:
    return _langfuse_env_fallback("base_url", "LANGFUSE_BASE_URL") or "http://localhost:3000"


def _langfuse_public_key() -> str:
    return _langfuse_env_fallback("public_key", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_API_PUBLIC_KEY")


def _langfuse_secret_key() -> str:
    return _langfuse_env_fallback("secret_key", "LANGFUSE_SECRET_KEY", "LANGFUSE_API_SECRET_KEY")


class LangfuseSettings(BaseSettings):
    mode: Literal["off", "warn", "enforce"] = "warn"
    base_url: str = Field(default_factory=_langfuse_base_url)
    public_key: str = Field(default_factory=_langfuse_public_key)
    secret_key: str = Field(default_factory=_langfuse_secret_key)


def _qdrant_url() -> str:
    import os

    return os.environ.get("QDRANT_URL") or "http://localhost:6333"


def _qdrant_api_key() -> str:
    import os

    return os.environ.get("QDRANT_API_KEY") or ""


class QdrantSettings(BaseSettings):
    enabled: bool = True
    url: str = Field(default_factory=_qdrant_url)
    api_key: str = Field(default_factory=_qdrant_api_key)
    collection: str = "brainbox"


class ProfileSettings(BaseSettings):
    mount_env: bool = True  # mount the profile .env from volatile cache
    mount_aws: bool = True
    mount_azure: bool = True
    mount_kube: bool = True
    mount_ssh: bool = True  # .ssh directory
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

    api_key: str = ""  # Falls back to OPENAI_API_KEY in env
    model: str = "gpt-5.4"


class UTMSettings(BaseSettings):
    default_template: str = "brainbox-macos-template"
    ssh_base_port: int = 2200
    utmctl_path: str = (
        ""  # Empty = auto-detect via PATH (searches /opt/homebrew/bin, /usr/local/bin)
    )
    ssh_key_path: str = ""  # Empty = use ~/.ssh/id_ed25519
    docs_dir: str = ""  # Empty = use ~/Library/Containers/com.utmapp.UTM/Data/Documents
    share_dir: str = ""  # Host path of the VirtioFS 'brainbox' share; empty = ARP-only


class HubSettings(BaseSettings):
    flush_interval: int = 30  # seconds
    prune_completed_after: int = 3600  # seconds
    message_retention: int = 100
    token_ttl: int = 3600  # seconds — default token time-to-live
    enable_teams: bool = True  # Enable Claude Code Teams in containers
    persistent_token_ttl: int = 86400  # 24h TTL for persistent agent tokens


class PipelineSettings(BaseSettings):
    builtin_dir: str = ""  # Empty = auto-detect brainbox/pipelines/
    config_dir: str = ""  # Empty = ~/.config/developer/pipelines/
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
    artifact_max_size: int = 100 * 1024 * 1024  # 100MB default

    ttl: int = 3600
    health_check_interval: int = 30  # seconds
    health_check_timeout: int = 5  # seconds
    health_check_retries: int = 3

    api_port: int = 9999
    op_vault: str = ""

    resources: ResourceSettings = Field(default_factory=ResourceSettings)
    hardening: HardeningSettings = Field(default_factory=HardeningSettings)
    cosign: CosignSettings = Field(default_factory=CosignSettings)
    artifact: ArtifactSettings = Field(default_factory=ArtifactSettings)
    langfuse: LangfuseSettings = Field(default_factory=LangfuseSettings)
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
    def api_key_file(self) -> Path:
        return self.config_dir / ".api-key"

    @property
    def agents_dir(self) -> Path:
        return Path(__file__).resolve().parent.parent.parent / "agents"

    @property
    def roles_dir(self) -> Path:
        return self.agents_dir / "roles"


settings = Settings()

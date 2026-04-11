"""Pydantic models for API request validation."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .validation import (
    ValidationError,
    validate_session_name,
    validate_role,
    validate_volume_mount,
)


class RepoConfig(BaseModel):
    """Repo access configuration for container sessions.

    The ``ci-ratchet`` mode implements the "Brownian ratchet" philosophy from
    multiclaude (https://github.com/dlorenc/multiclaude) by Dan Lorenc et al.:
    workers clone a remote repo, complete a task, and open a PR; CI is the
    ratchet that only lets passing work merge — forward progress is permanent.
    """

    url: str  # local path (worktree-mount) or git remote URL (clone/clone-worktree/ci-ratchet)
    mode: Literal["worktree-mount", "clone", "clone-worktree", "ci-ratchet"]
    branch: str = ""  # branch to create or checkout; defaults to work/<session-name> for ci-ratchet
    container_path: str = "/home/developer/workspace/repo"  # where to mount/clone inside container
    task: str | None = None  # worker task description (required for ci-ratchet)
    start_merge_queue: bool = True  # auto-start merge-queue container for this repo

    @model_validator(mode="after")
    def validate_ci_ratchet(self) -> "RepoConfig":
        if self.mode == "ci-ratchet" and not self.task:
            raise ValueError("task is required for ci-ratchet mode")
        if self.mode != "ci-ratchet" and not self.branch:
            raise ValueError("branch is required for non-ci-ratchet modes")
        return self


class CreateSessionRequest(BaseModel):
    """Request model for POST /api/create endpoint."""

    name: str | None = None
    role: str | None = None
    volume: str | None = None  # Legacy single volume (backward compatibility)
    volumes: list[str] | None = None  # New multi-volume support
    llm_provider: str = "claude"
    llm_model: str | None = None
    ollama_host: str | None = None
    workspace_profile: str | None = None
    workspace_home: str | None = None
    backend: str = "docker"  # "docker" or "utm"
    vm_template: str | None = None  # UTM only: template VM name
    guest_os: str = "linux"  # UTM only: guest OS — "linux", "macos", or "windows"
    task: str | None = None  # Initial task to send to Claude on first launch
    ports: dict[str, int] | None = None  # Additional port mappings (container_port: host_port)
    docker_host: str | None = None  # Docker daemon host (None = local socket)
    repo: RepoConfig | None = None  # Repo access mode (worktree-mount, clone, clone-worktree)

    @field_validator("name")
    @classmethod
    def validate_name_field(cls, v: str | None) -> str:
        """Validate session name using existing validation function."""
        if v is None:
            return "default"
        try:
            return validate_session_name(v)
        except ValidationError as e:
            raise ValueError(str(e)) from e

    @field_validator("role")
    @classmethod
    def validate_role_field(cls, v: str | None) -> str:
        """Validate role using existing validation function."""
        if v is None:
            return "developer"
        try:
            return validate_role(v)
        except ValidationError as e:
            raise ValueError(str(e)) from e

    @model_validator(mode="after")
    def validate_volumes_and_normalize(self) -> CreateSessionRequest:
        """Normalize volumes field and validate each volume mount."""
        # Support both new "volumes" (list) and legacy "volume" (string)
        if self.volumes is None:
            # Fall back to legacy single volume parameter
            if self.volume:
                self.volumes = [self.volume]
            else:
                self.volumes = []
        elif not isinstance(self.volumes, list):
            # Normalize single string to list
            self.volumes = [self.volumes] if self.volumes else []

        # Validate each volume mount
        validated_volumes = []
        for vol in self.volumes:
            if vol and vol != "-":  # Skip empty or placeholder volumes
                try:
                    host, container, mode = validate_volume_mount(vol)
                    validated_volumes.append(f"{host}:{container}:{mode}")
                except ValidationError as e:
                    raise ValueError(str(e)) from e

        self.volumes = validated_volumes
        return self


class StopSessionRequest(BaseModel):
    """Request model for POST /api/stop endpoint."""

    name: str = Field(..., description="Container name to stop")


class DeleteSessionRequest(BaseModel):
    """Request model for POST /api/delete endpoint."""

    name: str = Field(..., description="Container name to delete")


class StartSessionRequest(BaseModel):
    """Request model for POST /api/start endpoint."""

    name: str = Field(..., description="Container name to start")


class ExecSessionRequest(BaseModel):
    """Request model for POST /api/sessions/{name}/exec endpoint."""

    command: str = Field(..., description="Command to execute in the container")

    @field_validator("command")
    @classmethod
    def validate_command_not_empty(cls, v: str) -> str:
        """Ensure command is not empty after stripping."""
        stripped = v.strip()
        if not stripped:
            raise ValueError("command is required")
        return stripped


class QuerySessionRequest(BaseModel):
    """Request model for POST /api/sessions/{name}/query endpoint."""

    prompt: str = Field(..., description="Prompt to send to Claude Code in the container")
    working_dir: str | None = Field(None, description="Working directory for Claude Code execution")
    timeout: int = Field(300, description="Timeout in seconds for query execution", ge=10, le=3600)
    fork_session: bool = Field(
        False, description="Fork a new conversation thread instead of using main session"
    )

    @field_validator("prompt")
    @classmethod
    def validate_prompt_not_empty(cls, v: str) -> str:
        """Ensure prompt is not empty after stripping."""
        stripped = v.strip()
        if not stripped:
            raise ValueError("prompt is required")
        return stripped


class CreateRepoRequest(BaseModel):
    """Request model for POST /api/hub/repos endpoint."""

    url: str = Field(..., description="GitHub repo URL")
    name: str | None = Field(None, description="Short name (derived from URL if omitted)")
    merge_queue: bool = Field(False, description="Enable merge-queue agent")
    pr_shepherd: bool = Field(False, description="Enable PR shepherd agent")
    target_branch: str = Field("main", description="Target branch for merges")
    is_fork: bool = Field(False, description="Whether this is a fork repo")
    upstream_url: str | None = Field(None, description="Upstream repo URL (for forks)")
    workspace_home: str | None = Field(
        None, description="Workspace home path for credential mounts (SSH, git, cloud)"
    )
    workspace_profile: str | None = Field(None, description="Workspace profile name")

    @field_validator("url")
    @classmethod
    def validate_repo_url(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Repository URL is required")
        if not v.startswith("https://github.com/"):
            raise ValueError("Only GitHub URLs are supported (https://github.com/owner/repo)")
        return v


class UpdateRepoRequest(BaseModel):
    """Request model for PATCH /api/hub/repos/{name} endpoint."""

    merge_queue: bool | None = None
    pr_shepherd: bool | None = None
    target_branch: str | None = None


class OllamaChatRequest(BaseModel):
    """Request model for POST /api/ollama/chat endpoint."""

    model: str | None = Field(None, description="Model name (default: server config)")
    messages: list[dict] = Field(..., description="Chat messages [{role, content}]")


class OllamaPullRequest(BaseModel):
    """Request model for POST /api/ollama/pull endpoint."""

    name: str = Field(..., description="Model name to pull (e.g. 'llama3.2')")


class StartPipelineRunRequest(BaseModel):
    """Request model for POST /api/pipelines/{name}/run endpoint."""

    params: dict = Field(default_factory=dict, description="Parameters passed to pipeline steps")

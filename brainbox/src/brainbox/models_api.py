"""Pydantic models for API request validation."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator, model_validator

from .validation import (
    ValidationError,
    validate_session_name,
    validate_role,
    validate_volume_mount,
)


class CreateSessionRequest(BaseModel):
    """Request model for POST /api/create endpoint."""

    name: str | None = None
    role: str | None = None
    volume: str | None = None  # Legacy single volume (backward compatibility)
    volumes: list[str] | None = None  # New multi-volume support
    llm_provider: str = "claude"
    llm_model: str | None = None
    llm_effort: str | None = None  # claude only: "low" | "medium" | "high"
    ollama_host: str | None = None
    codex_api_key: str | None = None
    workspace_profile: str | None = None
    workspace_home: str | None = None
    backend: str = "docker"  # "docker" or "utm"
    vm_template: str | None = None  # UTM only: template VM name
    guest_os: str = "linux"  # UTM only: guest OS — "linux", "macos", or "windows"
    task: str | None = None  # Initial task to send to Claude on first launch
    continue_from: str | None = None  # Prior session whose handoff.md seeds this task
    ports: dict[str, int] | None = None  # Additional port mappings (container_port: host_port)
    docker_host: str | None = None  # Docker daemon host (None = local socket)
    delivery: str | None = None  # passed through to runner
    runner: str | None = None  # Runner name to dispatch this session to (None = local execution)
    env: dict[str, str] | None = None  # Caller-supplied env vars from originating host profile

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

    @field_validator("continue_from")
    @classmethod
    def validate_continue_from_field(cls, v: str | None) -> str | None:
        """continue_from names a prior session — same charset rules as name."""
        if v is None or v == "":
            return None
        try:
            return validate_session_name(v)
        except ValidationError as e:
            raise ValueError(str(e)) from e

    @field_validator("role")
    @classmethod
    def validate_role_field(cls, v: str | None) -> str:
        """Validate role using existing validation function."""
        if v is None:
            return "assistant"
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

    @field_validator("prompt")
    @classmethod
    def validate_prompt_not_empty(cls, v: str) -> str:
        """Ensure prompt is not empty after stripping."""
        stripped = v.strip()
        if not stripped:
            raise ValueError("prompt is required")
        return stripped


class OllamaChatRequest(BaseModel):
    """Request model for POST /api/ollama/chat endpoint."""

    model: str | None = Field(None, description="Model name (default: server config)")
    messages: list[dict] = Field(..., description="Chat messages [{role, content}]")


class OllamaPullRequest(BaseModel):
    """Request model for POST /api/ollama/pull endpoint."""

    name: str = Field(..., description="Model name to pull (e.g. 'llama3.2')")


# ---------------------------------------------------------------------------
# Channel request models
# ---------------------------------------------------------------------------


class ChannelParticipantRequest(BaseModel):
    """One participant in a CreateChannelRequest."""

    name: str = Field(..., description="Display name in channel")
    type: str = Field(..., description="'session', 'ollama', or 'user'")
    session_name: str | None = Field(None, description="Brainbox session name (type=session)")
    ollama_model: str | None = Field(None, description="Ollama model name (type=ollama)")
    system_prompt: str | None = Field(None, description="Role instructions for this participant")


class CreateChannelRequest(BaseModel):
    """Request model for POST /api/hub/channels."""

    name: str = Field(..., min_length=1, max_length=128, description="Channel name")
    participants: list[ChannelParticipantRequest] = Field(
        ..., min_length=1, description="Participants to add at creation"
    )
    parent_task_id: str | None = None
    workspace_profile: str | None = None


class PostChannelMessageRequest(BaseModel):
    """Request model for POST /api/hub/channels/{id}/messages."""

    from_participant: str = Field(..., description="Sender's participant name")
    content: str = Field(..., min_length=1, description="Message content")
    summary: str | None = Field(None, description="Brief for other agents' context management")
    addressed_to: str | None = Field(None, description="Recipient name, or None for broadcast")


class CompleteChannelRequest(BaseModel):
    """Request model for POST /api/hub/channels/{id}/complete."""

    by: str = Field(..., description="Name of participant signalling completion")
    reason: str | None = Field(None, description="Optional reason / summary")


class CreatePlaybookRequest(BaseModel):
    """Request model for POST /api/hub/playbooks."""

    name: str = Field(..., min_length=1, max_length=128, description="Playbook name")
    markdown: str = Field(..., min_length=1, description="Markdown with - [ ] checklist items")
    workspace_profile: str = Field("global", description="Profile scope, or 'global' for all profiles")
    runner: str | None = Field(None, description="Runner to dispatch all tasks to (None = local)")


class UpdatePlaybookRequest(BaseModel):
    """Request model for PATCH /api/hub/playbooks/{id}."""

    name: str | None = Field(None, min_length=1, max_length=128)
    markdown: str | None = Field(None, min_length=1)
    runner: str | None = Field(None, description="Runner to dispatch all tasks to (None = local, omit = no change)")


class CreateAgentRequest(BaseModel):
    """Request model for POST /api/hub/agents."""

    name: str = Field(..., description="Agent name slug (a-z, 0-9, hyphens)")
    image: str = Field("brainbox", description="Docker image name")
    description: str = Field("", description="Human-readable description")
    category: str = Field("general", description="Agent category (e.g. general, development, orchestration)")
    spawn_mode: str = Field("container", description="Execution mode: 'container' (full brainbox session) or 'subagent' (spawned by Claude Code/Codex)")
    capabilities: list[str] = Field(default_factory=list, description="Agent capabilities")
    hardened: bool = Field(False, description="Enable security hardening")
    persistent: bool = Field(False, description="Auto-restart on exit")
    role_prompt_content: str | None = Field(None, description="Markdown role prompt content")
    claude_model: str | None = Field(None, description="Default Claude model (e.g. claude-opus-4-7)")
    claude_effort: str | None = Field(None, description="Claude reasoning effort: low | medium | high")
    codex_model: str | None = Field(None, description="Default Codex model (e.g. codex-mini-latest)")
    ollama_model: str | None = Field(None, description="Default Ollama model (e.g. qwen3:8b)")

    @field_validator("name")
    @classmethod
    def validate_agent_name(cls, v: str) -> str:
        import re
        v = v.strip()
        if not v:
            raise ValueError("Agent name is required")
        if not re.match(r"^[a-z0-9][a-z0-9-]*$", v):
            raise ValueError("Agent name must be lowercase letters, numbers, and hyphens only")
        return v


class UpdateAgentRequest(BaseModel):
    """Request model for PATCH /api/hub/agents/{name}."""

    image: str | None = None
    description: str | None = None
    category: str | None = None
    spawn_mode: str | None = None
    capabilities: list[str] | None = None
    hardened: bool | None = None
    persistent: bool | None = None
    role_prompt_content: str | None = None  # empty string = clear prompt
    claude_model: str | None = None  # empty string = clear
    claude_effort: str | None = None  # empty string = clear
    codex_model: str | None = None   # empty string = clear
    ollama_model: str | None = None  # empty string = clear


class MintProfileTokenRequest(BaseModel):
    """Request model for POST /api/tokens (T11 profile token minting).

    ``workspace_profile`` is free-text (brainbox has no authoritative profiles
    registry — the dashboard offers the gateway-secrets profile list as a
    convenience dropdown but the operator may name any profile), so it is
    validated to a safe charset rather than checked against an allow-list.
    ``capabilities`` is validated against the catalog server-side.
    """

    workspace_profile: str = Field(..., description="Profile this token is bound to")
    capabilities: list[str] = Field(default_factory=list)
    label: str = Field("", max_length=200, description="Human-readable note")

    @field_validator("workspace_profile")
    @classmethod
    def validate_profile(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("workspace_profile is required")
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$", stripped):
            raise ValueError(
                "workspace_profile must start alphanumeric and contain only "
                "letters, numbers, '.', '_', or '-'"
            )
        return stripped

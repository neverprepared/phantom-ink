"""Pydantic models for API request validation."""

from __future__ import annotations

import re
from typing import Literal

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
    exec_mode: str = "interactive"  # session run mode: "interactive" (tmux REPL) | "print" (claude -p headless)

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
    def validate_exec_mode_field(self) -> CreateSessionRequest:
        """Print mode runs `claude -p` headless — it needs something to execute
        and is claude-only. Reject nonsensical combinations early so a bad
        request fails at the API, not silently in the container wrapper (which
        also defensively falls back to interactive)."""
        if self.exec_mode not in ("interactive", "print"):
            raise ValueError(
                f"exec_mode must be 'interactive' or 'print', got {self.exec_mode!r}"
            )
        if self.exec_mode == "print":
            has_work = bool(self.task and self.task.strip()) or bool(self.continue_from)
            if not has_work:
                raise ValueError(
                    "exec_mode='print' requires a task (or continue_from) to execute"
                )
            if self.llm_provider != "claude":
                raise ValueError(
                    "exec_mode='print' is only supported for llm_provider='claude'"
                )
        return self

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


# ---------------------------------------------------------------------------
# Conversations (multi-agent Chat) — the new engine that supersedes channels.
# ---------------------------------------------------------------------------


class ConversationParticipantRequest(BaseModel):
    """A participant spec in CreateConversationRequest.

    ``kind='persona'`` is the lightweight LLM participant PR1 drives through the
    complete() seam: ``model_target`` selects the provider/model and
    ``role_prompt`` is its system prompt. ``kind='session'`` is a promoted
    container session (see ``conversation_session``): it is driven by its own
    container through the ``channel_*`` tools, not by the turn orchestrator.
    """

    name: str = Field(..., min_length=1, max_length=128)
    kind: Literal["human", "persona", "session"] = "persona"
    model_target: dict | None = Field(
        None, description="ModelTarget shape: {provider, model, effort}"
    )
    role_prompt: str | None = Field(None, description="System prompt for a persona")
    cooldown_s: float | None = Field(
        None,
        description=(
            "Minimum seconds between this persona's turns; None uses "
            "settings.conversations.default_cooldown_s"
        ),
    )


class CreateConversationRequest(BaseModel):
    """Request model for POST /api/conversations."""

    title: str = Field(..., min_length=1, max_length=200)
    profile: str = Field(..., min_length=1, description="Workspace profile that owns the room")
    participants: list[ConversationParticipantRequest] = Field(default_factory=list)


class PostConversationMessageRequest(BaseModel):
    """Request model for POST /api/conversations/{id}/messages."""

    author: str = Field(..., min_length=1, max_length=128, description="Human sender's name")
    content: str = Field(..., min_length=1)
    addressed_to: str | None = Field(None, description="Participant name, or None for the room")


class ArchiveConversationRequest(BaseModel):
    """Optional body for POST /api/conversations/{id}/archive.

    A closing note is part of the record: ``reason`` is appended as a final
    ``kind='session'`` message before the room is archived, so the log says why
    it ended. Both fields are optional — an archive with no body just closes the
    room, which is what the desktop app's archive button does.
    """

    by: str | None = Field(None, max_length=128, description="Who is closing the room")
    reason: str | None = Field(None, description="Closing note appended before archiving")


class PromoteMessageRequest(BaseModel):
    """Request model for POST /api/conversations/{id}/messages/{mid}/promote.

    ``target`` picks the platform surface the message becomes: ``memory`` and
    ``todo`` write a record into that phantom-brain vault for the caller's
    profile; ``task`` submits a hub task; ``session`` (PR4) submits that same
    hub task AND wires the resulting container session into the room as a
    ``kind='session'`` participant that reports back. Everything else is
    optional — the message supplies the content, and the profile comes from the
    caller's auth context (never from the body).
    """

    target: Literal["memory", "todo", "task", "session"]
    title: str | None = Field(
        None, max_length=200, description="Override the derived record/task title"
    )
    note: str | None = Field(
        None, description="Extra context appended to the promoted body"
    )
    tags: list[str] = Field(default_factory=list)
    agent_name: str = Field(
        "worker", description="target='task'/'session' only: which hub agent runs it"
    )
    repo_url: str | None = Field(
        None, description="target='task'/'session' only: repo to clone"
    )

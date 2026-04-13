"""Pydantic models for all domain objects."""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


def _now_ms() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------


class AgentRole(str, Enum):
    """Agent roles absorbed from multiclaude's agent type system.

    Attribution: Role system originated from Dan Lorenc's multiclaude project
    (github.com/dlorenc/multiclaude).
    """

    DEVELOPER = "developer"  # Interactive session (existing default)
    SUPERVISOR = "supervisor"  # Orchestrates agents, persistent
    WORKER = "worker"  # Task executor, transient
    MERGE_QUEUE = "merge-queue"  # PR automation, persistent
    PR_SHEPHERD = "pr-shepherd"  # Fork PR coordination, persistent
    REVIEWER = "reviewer"  # Code review, transient


class AgentDefinition(BaseModel):
    name: str
    image: str
    description: str = ""
    capabilities: list[str] = Field(default_factory=list)
    hardened: bool = False
    role_prompt: str | None = None  # Path to role prompt markdown (relative to agents dir)
    persistent: bool = False  # Persistent roles auto-restart; transient roles clean up
    repo_url: str | None = None  # GitHub repo URL for repo-specific agents


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------


class Token(BaseModel):
    token_id: str
    agent_name: str
    task_id: str
    capabilities: list[str] = Field(default_factory=list)
    issued: int  # epoch ms
    expiry: int  # epoch ms


# ---------------------------------------------------------------------------
# Session context
# ---------------------------------------------------------------------------


class SessionState(str, Enum):
    PROVISIONING = "provisioning"
    CONFIGURING = "configuring"
    STARTING = "starting"
    RUNNING = "running"
    MONITORING = "monitoring"
    RECYCLING = "recycling"
    RECYCLED = "recycled"


class SessionContext(BaseModel):
    session_name: str
    container_name: str
    port: int
    role: str = "developer"
    teams_enabled: bool = False  # Claude Code Teams experimental feature
    role_prompt_file: str | None = None  # Path to role prompt injected into container
    repo_url: str | None = None  # Associated repository URL
    task_description: str | None = None  # Task description for hub-spawned workers
    task_id: str | None = None  # Hub task ID for completion callbacks
    state: SessionState = SessionState.PROVISIONING
    created_at: int  # epoch ms
    ttl: int  # seconds
    hardened: bool = True
    volume_mounts: list[str] = Field(default_factory=list)
    secrets: dict[str, str] = Field(default_factory=dict)
    health_failures: int = 0
    token: Token | None = None
    env_content: str | None = None  # legacy mode .env body
    llm_provider: str = "claude"  # "claude", "ollama", or "codex"
    llm_model: str | None = None  # e.g. "qwen3-coder"
    ollama_host: str | None = None  # per-session override
    codex_api_key: str | None = None  # per-session override
    profile_mounts: set[str] = Field(default_factory=set)  # {"aws", "azure", "kube", "ssh", ...}
    workspace_profile: str | None = None  # Caller's profile name
    workspace_home: str | None = None  # Caller's workspace home path
    # Backend-specific fields
    backend: str = "docker"  # "docker" or "utm"
    docker_host: str | None = None  # Docker daemon host (None = local socket)
    ports: dict[str, int] | None = None  # Additional port mappings (container_port: host_port)
    ssh_port: int | None = None  # UTM only: SSH port for VM access (deprecated - use vm_ip)
    ssh_user: str = "developer"  # UTM SSH username
    vm_template: str | None = None  # UTM only: Template VM name used for cloning
    vm_path: str | None = None  # UTM only: Full path to .utm package
    vm_ip: str | None = None  # UTM only: VM's IP address (bridged networking)
    mac_address: str | None = None  # UTM only: VM's MAC address for IP discovery
    guest_os: str = "linux"  # UTM only: guest OS type — "linux", "macos", or "windows"
    worktree_path: str | None = (
        None  # Host worktree path created for this session (worktree-mount mode)
    )


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskCreate(BaseModel):
    description: str
    agent_name: str
    repo_url: str | None = None
    workspace_profile: str | None = None
    workspace_home: str | None = None


class Task(BaseModel):
    id: str
    description: str
    agent_name: str
    status: TaskStatus = TaskStatus.PENDING
    created_at: int  # epoch ms
    updated_at: int  # epoch ms
    token_id: str | None = None
    session_name: str | None = None
    result: Any = None
    error: str | None = None
    repo_url: str | None = None  # Associated repository


# ---------------------------------------------------------------------------
# Repositories
# ---------------------------------------------------------------------------


class Repository(BaseModel):
    """A tracked repository with associated agent containers.

    Attribution: Multi-repo awareness originated from Dan Lorenc's multiclaude project.
    """

    url: str  # GitHub repo URL (e.g., "https://github.com/owner/repo")
    name: str  # Short name derived from URL (e.g., "repo")
    containers: dict[str, str] = Field(default_factory=dict)  # role -> session_name
    merge_queue_enabled: bool = False
    pr_shepherd_enabled: bool = False
    target_branch: str = "main"
    is_fork: bool = False
    upstream_url: str | None = None
    workspace_home: str | None = None  # Caller's workspace home (for credential mounts)
    workspace_profile: str | None = None  # Caller's workspace profile name
    local_path_override: str | None = None  # Override for local checkout path; default: {workspace_home}/code/{name}/


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


class MessageEnvelope(BaseModel):
    """Inbound message from an agent."""

    recipient: str = "hub"
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    """Fully resolved message stored internally."""

    id: str
    timestamp: int  # epoch ms
    sender: str
    sender_token_id: str
    task_id: str | None = None
    recipient: str = "hub"
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class MessageLogEntry(BaseModel):
    """Audit log entry for a routed or rejected message."""

    id: str
    timestamp: int  # epoch ms
    sender: str | None = None
    sender_token_id: str | None = None
    recipient: str | None = None
    type: str | None = None
    status: str  # "delivered" | "rejected"
    reason: str | None = None


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


class PolicyResult(BaseModel):
    allowed: bool
    reason: str | None = None


# ---------------------------------------------------------------------------
# Hub state persistence
# ---------------------------------------------------------------------------


class RegistryState(BaseModel):
    tokens: list[tuple[str, dict[str, Any]]] = Field(default_factory=list)


class RouterState(BaseModel):
    tasks: list[tuple[str, dict[str, Any]]] = Field(default_factory=list)


class MessagesState(BaseModel):
    pending: list[tuple[str, list[dict[str, Any]]]] = Field(default_factory=list)
    log: list[dict[str, Any]] = Field(default_factory=list)


class HubState(BaseModel):
    flushed_at: int  # epoch ms
    registry: RegistryState = Field(default_factory=RegistryState)
    router: RouterState = Field(default_factory=RouterState)
    messages: MessagesState = Field(default_factory=MessagesState)


# ---------------------------------------------------------------------------
# Channels (group chat)
# ---------------------------------------------------------------------------


class ChannelParticipant(BaseModel):
    name: str  # display name in channel
    type: Literal["session", "ollama", "user"]
    session_name: str | None = None  # for type="session"
    ollama_model: str | None = None  # for type="ollama"
    system_prompt: str | None = None  # role instructions for Ollama
    joined_at: int = Field(default_factory=_now_ms)


class ChannelMessage(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    channel_id: str
    from_participant: str
    content: str
    summary: str | None = None  # sender-authored brief for context management
    addressed_to: str | None = None  # None = broadcast, name = directed
    type: Literal["message", "join", "completion"] = "message"
    timestamp: int = Field(default_factory=_now_ms)


class Channel(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str
    participants: list[ChannelParticipant] = Field(default_factory=list)
    status: Literal["active", "completed"] = "active"
    created_at: int = Field(default_factory=_now_ms)
    completed_at: int | None = None
    completed_by: str | None = None


class PlaybookTask(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    index: int
    content: str  # the raw checklist item text — sent as the agent prompt
    status: Literal["pending", "running", "completed", "failed"] = "pending"
    session_name: str | None = None  # ephemeral session that ran this task
    output: str | None = None
    error: str | None = None
    started_at: int | None = None
    finished_at: int | None = None


class Worktree(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    repo_name: str  # references Repository.name
    branch: str  # git branch name
    worktree_path: str  # absolute host path
    session_name: str | None = None  # associated brainbox session (None = available)
    status: Literal["ready", "in_use", "error"] = "ready"
    created_at: int = Field(default_factory=_now_ms)
    error: str | None = None


class Playbook(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str
    markdown: str  # raw user-supplied markdown
    tasks: list[PlaybookTask] = Field(default_factory=list)
    status: Literal["idle", "running", "completed", "failed", "cancelled"] = "idle"
    workspace_profile: str = "global"  # profile name or "global" for all profiles
    created_at: int = Field(default_factory=_now_ms)
    started_at: int | None = None
    finished_at: int | None = None

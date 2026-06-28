"""Pydantic models for all domain objects."""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from .utils import now_ms as _now_ms


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------


class AgentDefinition(BaseModel):
    name: str
    image: str
    description: str = ""
    category: str = "general"  # e.g. "general", "development", "orchestration"
    spawn_mode: Literal["container", "subagent"] = "container"
    capabilities: list[str] = Field(default_factory=list)
    hardened: bool = False
    role_prompt: str | None = None  # Path to role prompt markdown (relative to agents dir)
    persistent: bool = False  # Persistent roles auto-restart; transient roles clean up
    repo_url: str | None = None  # GitHub repo URL for repo-specific agents
    # Per-provider model/effort defaults (override global config when set)
    claude_model: str | None = None
    claude_effort: Literal["low", "medium", "high"] | None = None
    codex_model: str | None = None
    ollama_model: str | None = None


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
    job_id: str | None = None  # Root supervisor task ID (own task_id if this is the root)
    state: SessionState = SessionState.PROVISIONING
    created_at: int  # epoch ms
    ttl: int  # seconds
    hardened: bool = True
    volume_mounts: list[str] = Field(default_factory=list)
    secrets: dict[str, str] = Field(default_factory=dict)
    health_failures: int = 0
    token: Token | None = None
    env_content: str | None = None  # legacy mode .env body
    llm_provider: Literal["claude", "ollama", "codex"] = "claude"
    llm_model: str | None = None  # e.g. "qwen3-coder"
    llm_effort: str | None = None  # claude only: "low" | "medium" | "high"
    ollama_host: str | None = None  # per-session override
    codex_api_key: str | None = None  # per-session override
    profile_mounts: set[str] = Field(default_factory=set)  # {"aws", "azure", "kube", "ssh", ...}
    workspace_profile: str | None = None  # Caller's profile name
    workspace_home: str | None = None  # Caller's workspace home path
    # image: credentials are baked into the per-profile Docker image at build time.
    delivery: str = "image"
    # Runner that owns this session. None or "local" = executed in-process by
    # the API host; any other value routes through the runner work queue.
    runner_name: str | None = None
    # Advertised host/IP of the runner machine. Used to construct the ttyd URL
    # for remote sessions (e.g. "192.168.1.42" → "http://192.168.1.42:{port}").
    runner_host: str | None = None
    # Caller-supplied env vars forwarded from the originating host's profile.
    # Merged into resolved secrets during configure(); caller values win.
    extra_env: dict[str, str] = Field(default_factory=dict)
    # Backend-specific fields
    backend: Literal["docker", "utm"] = "docker"
    docker_host: str | None = None  # Docker daemon host (None = local socket)
    ports: dict[str, int] | None = None  # Additional port mappings (container_port: host_port)
    ssh_port: int | None = None  # UTM only: SSH port for VM access (deprecated - use vm_ip)
    ssh_user: str = "phantomink"  # UTM SSH username
    vm_template: str | None = None  # UTM only: Template VM name used for cloning
    vm_path: str | None = None  # UTM only: Full path to .utm package
    vm_ip: str | None = None  # UTM only: VM's IP address (bridged networking)
    mac_address: str | None = None  # UTM only: VM's MAC address for IP discovery
    guest_os: Literal["linux", "macos", "windows"] = "linux"  # UTM only
    worktree_path: str | None = (
        None  # Host worktree path created for this session (worktree-mount mode)
    )
    profile_image: bool = False  # True when session uses a pre-built profile image from the registry


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"            # waiting on dependency / external resource (attention-eligible)
    NEEDS_ACTION = "needs_action"  # waiting on human input (attention-eligible)


class SuspensionKind(str, Enum):
    """Why a task is currently suspended.

    Set in tandem with TaskStatus.BLOCKED (JOIN/SCHEDULE/CHILD — auto-resumed
    by the scheduler) or TaskStatus.NEEDS_ACTION (HUMAN — only resumed by an
    explicit resume_task() call).

    Suspended tasks do not consume queue slots — they're invisible to
    _select_next() until they transition back to PENDING.
    """

    HUMAN = "human"        # awaiting UI button / webhook → resume_task(id, payload)
    JOIN = "join"          # awaiting N upstream children to COMPLETED
    SCHEDULE = "schedule"  # awaiting wall-clock resume_at_ms
    CHILD = "child"        # variant of JOIN with N=1 (single specific child)


class ModelTarget(BaseModel):
    """Per-task LLM selection.

    Carried on a Task; the scheduler unpacks it into the provider/model/effort
    the session is provisioned with. When absent (None), the task uses the
    agent/global defaults exactly as before — see scheduler dispatch.

    Provider is a 3-value Literal today; widen it (and add an env branch in
    lifecycle) to support gemini/aider/opencode later.
    """

    provider: Literal["claude", "ollama", "codex"] | None = None
    model: str | None = None
    effort: Literal["low", "medium", "high"] | None = None


class TaskCreate(BaseModel):
    description: str
    agent_name: str
    repo_url: str | None = None
    workspace_profile: str | None = None
    workspace_home: str | None = None
    job_id: str | None = None  # Parent supervisor task ID; None means this task IS the job root
    runner: str | None = None                              # explicit runner name; None = auto-select
    runner_tags: list[str] = Field(default_factory=list)  # preferred tags for auto-selection
    backend: str = "docker"                                # backend capability to match
    priority: int = 0                                      # higher = dispatched sooner
    max_attempts: int = 1                                  # permanent failure after N dispatch failures
    deadline_ms: int | None = None                         # epoch ms; fail if not RUNNING by then
    model_target: ModelTarget | None = None                # per-task LLM provider/model/effort


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
    workspace_profile: str | None = None  # Profile that submitted this task
    job_id: str | None = None       # Parent supervisor task ID (own id if this is the root)
    spawned_by: str | None = None   # Task ID that directly spawned this task (None for roots)
    child_task_ids: list[str] = Field(default_factory=list)  # Tasks spawned by this one
    channel_ids: list[str] = Field(default_factory=list)    # Channels spawned by this task
    runner_name: str | None = None  # Runner that handled this task; None = executed in-process
    workspace_home: str | None = None    # Stored for scheduler retry on backoff
    backend: str = "docker"              # Backend capability required for this task
    runner_tags: list[str] = Field(default_factory=list)  # Preferred runner tags
    priority: int = 0                    # Higher = dispatched sooner
    max_attempts: int = 1                # Permanent failure after N dispatch failures
    attempts: int = 0                    # Dispatch attempts so far
    deadline_ms: int | None = None       # Epoch ms; fail if not RUNNING by this time
    next_attempt_at: int | None = None   # Epoch ms; backoff: don't retry before this
    last_error: str | None = None        # Error from the most recent failed dispatch attempt

    # Suspension primitive — populated only while status is BLOCKED or NEEDS_ACTION.
    # See SuspensionKind docstring for the four shapes and resume mechanics.
    suspension_kind: SuspensionKind | None = None
    resume_at_ms: int | None = None                              # SCHEDULE: wake when wall-clock passes
    resume_on_children: list[str] = Field(default_factory=list)  # JOIN/CHILD: wake when all are COMPLETED
    resume_payload: dict[str, Any] = Field(default_factory=dict) # carried into the next iteration on resume

    # Loop iteration context — populated when this task IS an iteration child
    # of a Loop. The dispatch path reads these to inject BRAINBOX_LOOP_ID and
    # BRAINBOX_ITERATION env vars into the session (so reviewer Mode C
    # detects loop context reliably) and to apply the Loop's permission tier
    # to the session's env merge. All optional — non-loop tasks default to
    # backward-compatible "no loop context."
    loop_id: str | None = None
    loop_iteration: int = 0
    permission_tier: str | None = None  # "inherit" | "default" | "strict"
    node_requires: list[str] = Field(default_factory=list)

    # Per-task LLM selection (Phase 2). None → agent/global defaults (unchanged).
    model_target: ModelTarget | None = None


# ---------------------------------------------------------------------------
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
    repos: list[tuple[str, dict[str, Any]]] = Field(default_factory=list)


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
    parent_task_id: str | None = None  # task that spawned this channel
    workspace_profile: str | None = None


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


class Playbook(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str
    markdown: str  # raw user-supplied markdown
    tasks: list[PlaybookTask] = Field(default_factory=list)
    status: Literal["idle", "running", "completed", "failed", "cancelled"] = "idle"
    workspace_profile: str = "global"  # profile name or "global" for all profiles
    runner: str | None = None  # runner to dispatch all tasks to; None = local execution
    created_at: int = Field(default_factory=_now_ms)
    started_at: int | None = None
    finished_at: int | None = None

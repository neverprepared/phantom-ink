"""MCP server exposing brainbox API as tools.

Stateless protocol adapter — each tool is an HTTP call to the
brainbox FastAPI backend.

Usage:
    brainbox mcp                    # stdio transport (default)
    brainbox mcp --url http://host:9999  # custom API URL
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("brainbox")


def _api_url() -> str:
    return os.environ.get("BRAINBOX_URL", "http://127.0.0.1:9999")


def _api_key() -> str:
    """Load API key from CL_API_KEY env, key file on disk, or loopback /api/auth/key."""
    key = os.environ.get("CL_API_KEY", "")
    if key:
        return key
    # Try common key file locations: new canonical path then legacy fallback.
    # New path: {base}/phantom-ink/brainbox/.api-key
    # Legacy path: {base}/developer/.api-key  (pre-rename; kept for compat)
    for base in [
        os.environ.get("XDG_CONFIG_HOME", ""),
        os.path.join(os.environ.get("WORKSPACE_HOME", ""), ".config"),
        os.path.join(str(Path.home()), ".config"),
    ]:
        if not base:
            continue
        for subdir in ("phantom-ink/brainbox", "developer"):
            key_file = Path(base) / subdir / ".api-key"
            if key_file.exists():
                return key_file.read_text().strip()
    # Fall back to loopback endpoint (works regardless of which profile started brainbox)
    try:
        req = urllib.request.Request(f"{_api_url()}/api/auth/key")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode())
            return data.get("key", "")
    except Exception:
        return ""


def _request_raw(
    method: str, path: str, data: bytes, content_type: str = "text/plain", timeout: int = 30
) -> Any:
    """Make an HTTP request with raw bytes body."""
    url = f"{_api_url()}{path}"
    headers = {"Content-Type": content_type}
    key = _api_key()
    if key:
        headers["X-API-Key"] = key
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode() if exc.fp else str(exc)
        try:
            detail = json.loads(detail).get("detail", detail)
        except (json.JSONDecodeError, AttributeError):
            pass
        return {"error": detail, "status": exc.code}
    except urllib.error.URLError as exc:
        return {"error": f"Cannot reach API at {url}: {exc.reason}"}


def _request(method: str, path: str, body: dict[str, Any] | None = None, timeout: int = 30) -> Any:
    """Make an HTTP request to the brainbox API."""
    url = f"{_api_url()}{path}"
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"} if data else {}
    key = _api_key()
    if key:
        headers["X-API-Key"] = key
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode() if exc.fp else str(exc)
        try:
            detail = json.loads(detail).get("detail", detail)
        except (json.JSONDecodeError, AttributeError):
            pass
        return {"error": detail, "status": exc.code}
    except urllib.error.URLError as exc:
        return {"error": f"Cannot reach API at {url}: {exc.reason}"}


def _request_as_session(
    method: str, path: str, body: dict[str, Any] | None = None, timeout: int = 30
) -> Any:
    """Make an HTTP request authenticated with the session bearer token.

    Uses BRAINBOX_TOKEN_ID (injected at container boot) as Authorization: Bearer.
    Falls back to API key if no token is available.
    """
    token_id = os.environ.get("BRAINBOX_TOKEN_ID", "")
    if not token_id:
        return _request(method, path, body, timeout)

    url = f"{_api_url()}{path}"
    data = json.dumps(body).encode() if body is not None else b"{}"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token_id}",
    }
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode() if exc.fp else str(exc)
        try:
            detail = json.loads(detail).get("detail", detail)
        except (json.JSONDecodeError, AttributeError):
            pass
        return {"error": detail, "status": exc.code}
    except urllib.error.URLError as exc:
        return {"error": f"Cannot reach API at {url}: {exc.reason}"}


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def list_sessions() -> list[dict[str, Any]]:
    """List all container sessions with their ports, volumes, and status."""
    return _request("GET", "/api/sessions")


@mcp.tool()
def create_session(
    name: str = "default",
    volume: str | None = None,
    role: str = "developer",
    docker_host: str | None = None,
) -> dict[str, Any]:
    """Create and start a new container session running Claude Code.

    Provisions a Docker container for the specified role, injects credentials
    and workspace configuration, then starts and monitors the session.

    Available roles:
      developer   — interactive Claude Code session (default)
      supervisor  — orchestrates the overall workflow, spawns worker tasks
      worker      — executes a specific task and opens a PR (transient)
      reviewer    — reviews an open PR and posts comments (transient)
      merge-queue — watches PRs and merges when CI passes (persistent)
      pr-shepherd — coordinates PRs for fork repos (persistent)

    Persistent roles (supervisor, merge-queue, pr-shepherd) auto-restart on failure.
    Transient roles (worker, reviewer) remove their containers on completion.

    Returns a dict with:
      success (bool): Whether provisioning succeeded.
      backend (str): Always "docker" for this tool.
      url (str): Web terminal URL, e.g. "http://localhost:7681".

    Args:
        name: Session name; container will be named ``{role}-{name}``
              (e.g. ``developer-default``). Defaults to ``"default"``.
        volume: Optional host-to-container volume mount in Docker format:
                ``/host/path:/container/path`` or
                ``/host/path:/container/path:ro``.
                Use this to expose a local repo or workspace to the container.
        role: Agent role — controls the system prompt injected into the
              container. See role list above.
        docker_host: Docker daemon URL (e.g. ``tcp://remote:2376``).
                     ``None`` uses the local socket.
    """
    body: dict[str, Any] = {"name": name, "role": role}
    if volume:
        body["volume"] = volume
    if docker_host:
        body["docker_host"] = docker_host
    return _request("POST", "/api/create", body)


@mcp.tool()
def start_session(name: str) -> dict[str, Any]:
    """Start an existing stopped container session.

    Args:
        name: Container name (e.g. developer-default)
    """
    return _request("POST", "/api/start", {"name": name})


@mcp.tool()
def stop_session(name: str) -> dict[str, Any]:
    """Stop a running container session.

    Args:
        name: Container name (e.g. developer-default)
    """
    return _request("POST", "/api/stop", {"name": name})


@mcp.tool()
def delete_session(name: str) -> dict[str, Any]:
    """Delete a container session (stops and removes the container).

    Args:
        name: Container name (e.g. developer-default)
    """
    return _request("POST", "/api/delete", {"name": name})


@mcp.tool()
def push_config(name: str) -> dict[str, Any]:
    """Re-inject translated ~/.claude config bundle into a running container.

    Use this after updating plugins, skills, hooks, or settings in ~/.claude
    to propagate changes without reprovisioning the container.

    Args:
        name: Session name (e.g. default) or container name (e.g. developer-default)
    """
    return _request("POST", f"/api/sessions/{name}/push-config")


@mcp.tool()
def get_metrics() -> list[dict[str, Any]]:
    """Get resource metrics for all running brainbox-managed container sessions.

    Each element describes one container and includes:
      name (str): Full container name (e.g. "worker-my-task").
      session_name (str): Session name with role prefix stripped.
      role (str): Agent role (e.g. "developer", "worker").
      llm_provider (str): LLM backend the session was started with.
      workspace_profile (str): Active workspace profile, or empty string.
      cpu_percent (float): CPU usage as a percentage of one core.
      mem_usage (int): Memory used by the container in bytes.
      mem_usage_human (str): Human-readable memory usage (e.g. "1.2 GB").
      mem_limit (int): Container memory limit in bytes.
      mem_limit_human (str): Human-readable memory limit.
      uptime_seconds (int): Seconds since the container was started.
      trace_count (int): LangFuse traces recorded for this session (cached 60 s).
      error_count (int): Error-level LangFuse traces for this session.
    """
    return _request("GET", "/api/metrics/containers")


@mcp.tool()
def submit_task(
    description: str,
    agent_name: str = "worker",
    repo_url: str | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    """Submit a task to the hub — spawns an isolated container running the specified agent.

    Multiclaude workflow: register a repo with add_repo(), then submit a supervisor task
    to coordinate workers. The supervisor spawns workers autonomously; use list_tasks()
    and get_message_log() to monitor progress.

    Available agent names:
      supervisor   — orchestrates the overall workflow, spawns workers
      worker       — executes a specific task and creates a PR (transient)
      reviewer     — reviews an open PR and posts comments (transient)
      merge-queue  — watches PRs and merges when CI passes (persistent, prefer add_repo)
      pr-shepherd  — coordinates PRs for fork repos (persistent, prefer add_repo)
      developer    — interactive Claude Code session (default for create_session)

    Args:
        description: Task description / instructions for the agent
        agent_name: Agent role to run (default: worker)
        repo_url: Optional GitHub repo URL to associate the task with
        job_id: Parent supervisor task ID — links this worker to a job for tracking.
                Supervisors should pass their own task ID here when spawning workers.
    """
    body: dict[str, Any] = {
        "description": description,
        "agent_name": agent_name,
    }
    if repo_url:
        body["repo_url"] = repo_url
    if job_id:
        body["job_id"] = job_id
    return _request("POST", "/api/hub/tasks", body)


@mcp.tool()
def get_task(task_id: str) -> dict[str, Any]:
    """Get the status and result of a submitted task.

    Args:
        task_id: The task ID returned by submit_task
    """
    return _request("GET", f"/api/hub/tasks/{task_id}")


@mcp.tool()
def list_tasks(status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """List hub tasks, optionally filtered by status.

    Args:
        status: Filter by status (pending, running, completed, failed, cancelled)
        limit: Max number of tasks to return, most recent first (default 50)
    """
    params: list[str] = []
    if status:
        params.append(f"status={status}")
    params.append(f"limit={limit}")
    path = "/api/hub/tasks?" + "&".join(params)
    return _request("GET", path)


@mcp.tool()
def get_hub_state() -> dict[str, Any]:
    """Get full hub state: agents, tasks, tokens, and message log."""
    return _request("GET", "/api/hub/state")


@mcp.tool()
def get_session(name: str) -> dict[str, Any]:
    """Get info for a single session by name.

    Args:
        name: Session name (e.g. test-1)
    """
    return _request("GET", f"/api/sessions/{name}")


@mcp.tool()
def exec_session(name: str, command: str) -> dict[str, Any]:
    """Execute a shell command inside a running container session.

    Args:
        name: Session name (e.g. test-1)
        command: Shell command to run (e.g. "pytest tests/", "git status")
    """
    return _request("POST", f"/api/sessions/{name}/exec", {"command": command})


@mcp.tool()
def query_session(
    name: str,
    prompt: str,
    timeout: int = 300,
) -> dict[str, Any]:
    """Send a prompt to Claude Code running in a container session.

    Args:
        name: Session name (e.g. test-1)
        prompt: The prompt/task to execute in the container
        timeout: Maximum seconds to wait for response (default: 300)
    """
    body: dict[str, Any] = {"prompt": prompt, "timeout": timeout}
    return _request("POST", f"/api/sessions/{name}/query", body, timeout=timeout + 10)


@mcp.tool()
def cancel_task(task_id: str) -> dict[str, Any]:
    """Cancel a pending or running task.

    Args:
        task_id: The task ID to cancel
    """
    return _request("DELETE", f"/api/hub/tasks/{task_id}")


@mcp.tool()
def get_langfuse_health() -> dict[str, Any]:
    """Check LangFuse observability service health and connectivity.

    Pings the LangFuse ``/api/public/health`` endpoint using the configured
    base URL and credentials (``LANGFUSE_BASE_URL``, ``LANGFUSE_PUBLIC_KEY``,
    ``LANGFUSE_SECRET_KEY``).

    Returns a dict with:
      healthy (bool): True if LangFuse responded with HTTP 200.
      url (str): The LangFuse base URL that was probed.
      error (str, optional): Error message if the probe failed.
    """
    return _request("GET", "/api/langfuse/health")


@mcp.tool()
def get_qdrant_health() -> dict[str, Any]:
    """Check Qdrant vector database health and connectivity."""
    return _request("GET", "/api/qdrant/health")


@mcp.tool()
def list_agents() -> list[dict[str, Any]]:
    """List all registered agents in the hub."""
    return _request("GET", "/api/hub/agents")


@mcp.tool()
def get_agent(name: str) -> dict[str, Any]:
    """Get info for a single registered hub agent.

    Args:
        name: Agent name (e.g. developer)
    """
    return _request("GET", f"/api/hub/agents/{name}")


@mcp.tool()
def list_tokens() -> list[dict[str, Any]]:
    """List all registered hub tokens (agent identities)."""
    return _request("GET", "/api/hub/tokens")


@mcp.tool()
def refresh_secrets(name: str) -> dict[str, Any]:
    """Re-inject secrets into a running container session from the host environment.

    Args:
        name: Session name (e.g. test-1)
    """
    return _request("POST", f"/api/sessions/{name}/refresh-secrets")


@mcp.tool()
def api_info() -> dict[str, Any]:
    """Get API version and basic health status."""
    return _request("GET", "/api/info")


# ---------------------------------------------------------------------------
# LangFuse trace tools
# ---------------------------------------------------------------------------


@mcp.tool()
def get_langfuse_session_traces(session_name: str, limit: int = 50) -> list[dict[str, Any]]:
    """List LangFuse traces recorded for a container session.

    Queries LangFuse for all traces whose ``sessionId`` matches the brainbox
    session name.  Traces represent individual Claude Code invocations or
    tool-use sequences.

    Each element in the returned list includes:
      id (str): LangFuse trace ID.
      name (str): Trace name (typically the Claude Code command).
      session_id (str): LangFuse session ID (matches session_name).
      timestamp (str): ISO 8601 creation timestamp.
      status (str): "ok" or "error" (derived from the trace level field).
      input (str): Truncated trace input (prompt text).
      output (str): Truncated trace output (assistant response).

    Args:
        session_name: Brainbox session name (e.g. "test-1"). Used as the
                      LangFuse sessionId filter.
        limit: Maximum number of traces to return, most recent first
               (default: 50).
    """
    return _request("GET", f"/api/langfuse/sessions/{session_name}/traces?limit={limit}")


@mcp.tool()
def get_langfuse_session_summary(session_name: str) -> dict[str, Any]:
    """Get aggregated observability statistics for a session from LangFuse.

    Batch-fetches all observations for the session and computes counts in a
    single API round-trip.  Use this for a quick health snapshot before
    drilling into individual traces with get_langfuse_session_traces.

    Returns a dict with:
      session_id (str): The session name used as the LangFuse session ID.
      total_traces (int): Total number of traces recorded for this session.
      total_observations (int): Total number of observations (spans, generations,
          events) across all traces.
      error_count (int): Number of observations at ERROR level.
      tool_counts (dict[str, int]): Map of tool/observation name → call count,
          showing which Claude tools were used most.

    Args:
        session_name: Brainbox session name (e.g. "test-1").
    """
    return _request("GET", f"/api/langfuse/sessions/{session_name}/summary")


@mcp.tool()
def get_langfuse_trace_detail(trace_id: str) -> dict[str, Any]:
    """Get full detail for a single LangFuse trace, including all observations.

    Fetches the trace record and all its child observations (spans, generations,
    events) in two API calls, then returns them together.  Use this to inspect
    what Claude did inside a specific invocation — which tools it called, in
    what order, and whether any errored.

    Returns a dict with:
      trace (dict): The trace record:
        id (str): LangFuse trace ID.
        name (str): Trace name.
        session_id (str): LangFuse session ID.
        timestamp (str): ISO 8601 creation timestamp.
        status (str): "ok" or "error".
        input (str): Truncated prompt input.
        output (str): Truncated assistant output.
      observations (list[dict]): Child observations, each with:
        id (str): Observation ID.
        trace_id (str): Parent trace ID.
        name (str): Tool or span name.
        type (str): "SPAN", "GENERATION", or "EVENT".
        start_time (str): ISO 8601 start timestamp.
        end_time (str): ISO 8601 end timestamp (empty if still running).
        status (str): "ok" or "error".
        level (str): Severity — "DEFAULT", "DEBUG", "WARNING", or "ERROR".

    Args:
        trace_id: LangFuse trace ID, as returned by get_langfuse_session_traces.
    """
    return _request("GET", f"/api/langfuse/traces/{trace_id}")


# ---------------------------------------------------------------------------
@mcp.tool()
def get_message_log(limit: int = 50) -> list[dict[str, Any]]:
    """Return the hub inter-agent message audit log.

    Shows messages exchanged between agents (supervisor → worker, worker → hub lifecycle
    events, merge-queue → supervisor status updates, etc.). Useful for monitoring
    multiclaude workflow progress without pulling the full hub state.

    Args:
        limit: Maximum number of recent messages to return (default: 50)
    """
    log = _request("GET", "/api/hub/message-log")
    if isinstance(log, list):
        return log[-limit:]
    return log


@mcp.tool()
def multiclaude_status() -> dict[str, Any]:
    """Summarise the current multiclaude workflow state in one call.

    Returns a structured snapshot of:
      - repos: tracked repositories and their persistent agent containers
      - tasks: active (pending/running) tasks grouped by agent role
      - recent_messages: last 20 inter-agent messages
      - agents: available agent roles

    Use this as the primary monitoring tool during a multiclaude session.
    """
    state = _request("GET", "/api/hub/state")
    if "error" in state:
        return state

    active_tasks = [t for t in state.get("tasks", []) if t.get("status") in ("pending", "running")]
    recent_messages = state.get("messages", [])[-20:]

    by_role: dict[str, list[dict]] = {}
    for task in active_tasks:
        role = task.get("agent_name", "unknown")
        by_role.setdefault(role, []).append(
            {
                "id": task.get("id"),
                "status": task.get("status"),
                "description": (task.get("description") or "")[:120],
                "repo_url": task.get("repo_url"),
                "created_at": task.get("created_at"),
            }
        )

    return {
        "active_tasks": by_role,
        "active_task_count": len(active_tasks),
        "recent_messages": recent_messages,
        "available_agents": [a.get("name") for a in state.get("agents", [])],
    }


# ---------------------------------------------------------------------------
# Group chat channel tools
# ---------------------------------------------------------------------------


@mcp.tool()
def channel_read(channel_id: str, since_id: str | None = None) -> list[dict[str, Any]]:
    """Read new messages from a group channel. Poll every few seconds.

    Returns messages since since_id (or all messages if omitted).
    Each message has: id, from_participant, content, summary, addressed_to, type, timestamp.

    Args:
        channel_id: The channel ID to read from
        since_id: Return only messages after this message ID (use the last id you received)
    """
    path = f"/api/hub/channels/{channel_id}/messages"
    if since_id:
        path += f"?since_id={since_id}"
    return _request("GET", path)


@mcp.tool()
def channel_send(
    channel_id: str,
    from_participant: str,
    content: str,
    summary: str | None = None,
    addressed_to: str | None = None,
) -> dict[str, Any]:
    """Send a message to a group channel.

    Args:
        channel_id: The channel ID to post to
        from_participant: Your participant name in this channel
        content: The message content
        summary: Brief 1-2 sentence summary of your key point (used for context management)
        addressed_to: Participant name for a directed message, omit for broadcast
    """
    body: dict[str, Any] = {
        "from_participant": from_participant,
        "content": content,
    }
    if summary:
        body["summary"] = summary
    if addressed_to:
        body["addressed_to"] = addressed_to
    return _request("POST", f"/api/hub/channels/{channel_id}/messages", body)


@mcp.tool()
def channel_complete(channel_id: str, by: str, reason: str | None = None) -> dict[str, Any]:
    """Signal that a group channel discussion is complete.

    Call this when you believe the conversation has reached a conclusion.

    Args:
        channel_id: The channel ID to complete
        by: Your participant name
        reason: Optional reason or summary of the conclusion
    """
    body: dict[str, Any] = {"by": by}
    if reason:
        body["reason"] = reason
    return _request("POST", f"/api/hub/channels/{channel_id}/complete", body)


@mcp.tool()
def channel_join(channel_id: str) -> dict[str, Any]:
    """Join a group channel as a participant using this session's identity.

    Idempotent — safe to call if already a member. Your identity is derived
    automatically from BRAINBOX_TOKEN_ID; no participant name is needed.
    Requires the session to have the 'hub_messaging' capability.

    Args:
        channel_id: The channel ID to join
    """
    return _request_as_session("POST", f"/api/hub/channels/{channel_id}/join")


# ---------------------------------------------------------------------------
# Playbook tools
# ---------------------------------------------------------------------------


@mcp.tool()
def list_playbooks(workspace_profile: str = "global") -> list[dict[str, Any]]:
    """List playbooks, optionally filtered by workspace profile.

    Workspace profiles are named credential+environment bundles managed via the
    Profiles panel in the brainbox desktop app.  Each profile maps to a
    workspace directory and injects its own ``.env`` file, AWS/GCP/Azure
    credentials, kubeconfig, and SSH keys into container sessions.  Playbooks
    can be scoped to a specific profile so that the correct credentials are
    used when the steps execute.  Pass ``"global"`` to list playbooks that
    apply to all profiles.

    Args:
        workspace_profile: Profile name to filter by (e.g. ``"work"``), or
                           ``"global"`` to list cross-profile playbooks
                           (default: ``"global"``).
    """
    path = "/api/hub/playbooks"
    if workspace_profile:
        path += f"?profile={workspace_profile}"
    return _request("GET", path)


@mcp.tool()
def get_playbook(playbook_id: str) -> dict[str, Any]:
    """Get a single playbook by ID, including its task list and status.

    Args:
        playbook_id: The playbook ID
    """
    return _request("GET", f"/api/hub/playbooks/{playbook_id}")


@mcp.tool()
def create_playbook(name: str, markdown: str, workspace_profile: str = "global") -> dict[str, Any]:
    """Create a new playbook from a markdown checklist.

    Each ``- [ ] task description`` line in the markdown becomes a sequential
    step dispatched to a fresh ephemeral worker session when the playbook is
    run.  Steps execute one at a time in order; each runs in its own isolated
    container with the specified workspace profile's credentials.

    Workspace profiles are named credential+environment bundles.  Specifying a
    profile here ensures every worker step launched by this playbook has the
    correct ``.env``, cloud credentials, and kubeconfig injected.  Use
    ``"global"`` for playbooks that do not require profile-specific credentials.

    Args:
        name: Display name for the playbook.
        markdown: Markdown content containing ``- [ ]`` checklist items.
                  Non-checklist lines are stored but ignored during execution.
        workspace_profile: Profile scope for worker sessions spawned by this
                           playbook (e.g. ``"work"``), or ``"global"`` for
                           all-profile playbooks (default: ``"global"``).
    """
    return _request("POST", "/api/hub/playbooks", {
        "name": name,
        "markdown": markdown,
        "workspace_profile": workspace_profile,
    })


@mcp.tool()
def run_playbook(playbook_id: str) -> dict[str, Any]:
    """Start sequential execution of a playbook's checklist steps.

    Each step runs in a fresh ephemeral worker session. Progress can be
    tracked by polling get_playbook().

    Args:
        playbook_id: The playbook ID to run
    """
    return _request("POST", f"/api/hub/playbooks/{playbook_id}/run", {})


@mcp.tool()
def cancel_playbook(playbook_id: str) -> dict[str, Any]:
    """Cancel a running playbook, stopping after the current step completes.

    Args:
        playbook_id: The playbook ID to cancel
    """
    return _request("POST", f"/api/hub/playbooks/{playbook_id}/cancel", {})


# ---------------------------------------------------------------------------
# Event contract discovery
#
# Agents shouldn't have to be told where the timeline-entry schema lives. Expose
# the LIVE schema straight off the pydantic model (never a cached copy), so it
# can never lag the model — the same source `/openapi.json` and the ingest
# validator derive from. Offered as both a resource (for clients that read
# resources) and a tool (for clients that only call tools).
# ---------------------------------------------------------------------------


def _timeline_entry_schema() -> dict[str, Any]:
    """Return the live JSON Schema for the timeline-entry (AgentEnvelope) contract.

    Imported lazily so the MCP adapter — which is otherwise a pure HTTP client —
    doesn't pull the FastAPI/DB stack in at import time. `model_json_schema()` is
    computed fresh on every call, so the schema tracks the model with no cache to
    invalidate.
    """
    from .agent_store import AgentEnvelope

    return AgentEnvelope.model_json_schema()


@mcp.resource("contract://events/timeline-entry")
def timeline_entry_schema() -> dict[str, Any]:
    """Live JSON Schema for a timeline-entry envelope (the agent event contract).

    This is the canonical v2.1 `AgentEnvelope` shape — the same schema
    `/openapi.json` publishes at `components.schemas.AgentEnvelope` and the
    ingest route (`POST /api/agent_events`) validates against. Generated fresh
    from the pydantic model on every read, so it never lags the model.
    """
    return _timeline_entry_schema()


@mcp.tool()
def get_event_schema() -> dict[str, Any]:
    """Get the live JSON Schema for a timeline-entry event envelope.

    Returns the canonical v2.1 `AgentEnvelope` contract — the shape every
    envelope POSTed to `/api/agent_events` must conform to. Identical to the
    `contract://events/timeline-entry` MCP resource and to
    `/openapi.json#/components/schemas/AgentEnvelope`. Computed from the live
    pydantic model, so it always reflects the current contract.
    """
    return _timeline_entry_schema()


def run() -> None:
    """Run the MCP server on stdio transport."""
    mcp.run(transport="stdio")

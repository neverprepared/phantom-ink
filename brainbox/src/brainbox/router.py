"""Task router: dispatch tasks to agents, manage lifecycle coordination.

Enhanced with multi-repo awareness and role-aware dispatch, absorbing patterns
from multiclaude (Dan Lorenc, github.com/dlorenc/multiclaude).
"""

from __future__ import annotations

import uuid
from typing import Any, Callable

from .config import settings
from .log import get_logger
from .models import SessionState, SuspensionKind, Task, TaskStatus
from .policy import evaluate_task_assignment
from .registry import get_agent, issue_token, revoke_token
from .utils import now_ms as _now_ms

log = get_logger()

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

_tasks: dict[str, Task] = {}
_listeners: list[Callable[[str, Task], None]] = []


def _emit(event: str, task: Task) -> None:
    for fn in _listeners:
        try:
            fn(event, task)
        except Exception as exc:
            log.warning("router.event_listener_error", metadata={"event": event, "reason": str(exc)})


def on_event(fn: Callable[[str, Task], None]) -> None:
    """Register an event listener (for SSE bridge)."""
    _listeners.append(fn)


# ---------------------------------------------------------------------------
# Task management
# ---------------------------------------------------------------------------


async def submit_task(
    description: str,
    agent_name: str,
    *,
    repo_url: str | None = None,
    workspace_profile: str | None = None,
    workspace_home: str | None = None,
    job_id: str | None = None,
    runner: str | None = None,
    runner_tags: list[str] | None = None,
    backend: str = "docker",
    priority: int = 0,
    max_attempts: int = 1,
    deadline_ms: int | None = None,
    loop_id: str | None = None,
    loop_iteration: int = 0,
    permission_tier: str | None = None,
    node_requires: list[str] | None = None,
) -> Task:
    """Enqueue a task for the given agent.

    Returns immediately with the task in PENDING state. The scheduler loop
    dispatches it to a runner (or in-process) within seconds.

    Loop context kwargs (loop_id, loop_iteration, permission_tier,
    node_requires) are populated only when the task is an iteration child
    of a Loop. lifecycle.run_pipeline reads them to inject BRAINBOX_LOOP_ID
    and BRAINBOX_ITERATION into the session env and to apply the Loop's
    permission tier to the env merge.
    """
    from . import scheduler

    if not description:
        raise ValueError("Task description is required")
    if not agent_name:
        raise ValueError("Agent name is required")

    agent_def = get_agent(agent_name)
    if not agent_def:
        raise ValueError(f"Agent '{agent_name}' not found")

    task_id = str(uuid.uuid4())
    now = _now_ms()
    # Supervisors own their job; workers inherit the supervisor's task_id as job_id
    resolved_job_id = job_id or task_id
    # spawned_by is set when a parent task explicitly spawned this one
    spawned_by = job_id if (job_id and job_id != task_id) else None

    task = Task(
        id=task_id,
        description=description,
        agent_name=agent_name,
        status=TaskStatus.PENDING,
        created_at=now,
        updated_at=now,
        repo_url=repo_url,
        workspace_profile=workspace_profile,
        workspace_home=workspace_home,
        job_id=resolved_job_id,
        spawned_by=spawned_by,
        runner_name=runner,
        runner_tags=runner_tags or [],
        backend=backend,
        priority=priority,
        max_attempts=max_attempts,
        deadline_ms=deadline_ms,
        loop_id=loop_id,
        loop_iteration=loop_iteration,
        permission_tier=permission_tier,
        node_requires=node_requires or [],
    )

    # Register as child of parent task
    if spawned_by and spawned_by in _tasks:
        parent = _tasks[spawned_by]
        if task_id not in parent.child_task_ids:
            parent.child_task_ids.append(task_id)

    # Policy check
    check = evaluate_task_assignment(agent_def, task)
    if not check.allowed:
        raise ValueError(f"Policy denied: {check.reason}")

    _tasks[task_id] = task
    log.info("router.task_queued", metadata={"task_id": task_id, "agent": agent_name})
    _emit("task.queued", task)
    scheduler.notify()
    return task


def register_ci_ratchet_task(
    description: str,
    repo_url: str,
    session_name: str,
) -> tuple[Task, Any]:
    """Register a ci-ratchet worker as a hub task and issue its auth token.

    Wires complete.sh → hub message → complete_task() → recycle() so the
    container is automatically cleaned up when the worker signals completion.
    Unlike submit_task(), this does NOT launch a container — the caller
    (api_create_session) passes the returned token to run_pipeline() directly.
    """
    task_id = str(uuid.uuid4())
    now = _now_ms()
    token = issue_token("worker", task_id, ttl=settings.hub.token_ttl)
    task = Task(
        id=task_id,
        description=description,
        agent_name="worker",
        status=TaskStatus.RUNNING,
        created_at=now,
        updated_at=now,
        repo_url=repo_url,
        token_id=token.token_id,
        session_name=session_name,
    )
    _tasks[task_id] = task
    log.info(
        "router.ci_ratchet_task_registered",
        metadata={"task_id": task_id, "session": session_name, "repo": repo_url},
    )
    return task, token


def get_task(task_id: str) -> Task | None:
    return _tasks.get(task_id)


def _add_channel_to_task(task_id: str, channel_id: str) -> None:
    task = _tasks.get(task_id)
    if task and channel_id not in task.channel_ids:
        task.channel_ids.append(channel_id)


def on_channel_completed(task_id: str, channel_id: str, summary: str) -> None:
    """Called by channels.complete_channel when a task-linked channel finishes."""
    task = _tasks.get(task_id)
    if not task:
        return
    log.info(
        "router.channel_completed",
        metadata={"task_id": task_id, "channel_id": channel_id},
    )
    _emit("task.signal", task)


def list_tasks(
    *,
    status: str | None = None,
    agent_name: str | None = None,
    job_id: str | None = None,
    workspace_profile: str | None = None,
    limit: int | None = 50,
) -> list[Task]:
    result = list(_tasks.values())
    if status:
        try:
            status_enum = TaskStatus(status)
        except ValueError:
            raise ValueError(f"Invalid status '{status}'")
        result = [t for t in result if t.status == status_enum]
    if agent_name:
        result = [t for t in result if t.agent_name == agent_name]
    if job_id:
        result = [t for t in result if t.job_id == job_id]
    if workspace_profile is not None:
        result = [t for t in result if t.workspace_profile == workspace_profile]
    result.sort(key=lambda t: t.created_at, reverse=True)
    if limit is not None:
        result = result[:limit]
    return result


async def _finalize_task(task: Task, reason: str) -> None:
    """Recycle the task's container and revoke its token."""
    from . import lifecycle

    if task.session_name:
        try:
            await lifecycle.recycle(task.session_name, reason=reason)
        except Exception as exc:
            log.warning("router.recycle_failed", metadata={"task_id": task.id, "reason": str(exc)})

    if task.token_id:
        revoke_token(task.token_id)


async def complete_task(task_id: str, result: Any = None) -> Task:
    """Mark a task as completed and recycle its container."""
    task = _tasks.get(task_id)
    if not task:
        raise ValueError(f"Task '{task_id}' not found")
    if task.status != TaskStatus.RUNNING:
        raise ValueError(f"Task '{task_id}' is not running (status: {task.status})")

    task.status = TaskStatus.COMPLETED
    task.result = result
    task.updated_at = _now_ms()

    await _finalize_task(task, reason="task_completed")

    log.info("router.task_completed", metadata={"task_id": task_id})
    _emit("task.completed", task)
    return task


async def fail_task(task_id: str, error: str | None = None) -> Task:
    task = _tasks.get(task_id)
    if not task:
        raise ValueError(f"Task '{task_id}' not found")
    if task.status not in (TaskStatus.RUNNING, TaskStatus.PENDING):
        raise ValueError(
            f"Task '{task_id}' cannot be failed from status '{task.status}'"
        )

    task.status = TaskStatus.FAILED
    task.error = error or "Unknown error"
    task.updated_at = _now_ms()

    await _finalize_task(task, reason="task_failed")

    log.info("router.task_failed", metadata={"task_id": task_id, "error": error})
    _emit("task.failed", task)
    return task


# ---------------------------------------------------------------------------
# Suspension primitive — the WAITING_* substrate for Loop iteration handoff,
# human-in-the-loop pauses, scheduled wakes, and join barriers. See
# SuspensionKind in models.py for the four shapes.
#
# Suspended tasks drop the queue slot (they're invisible to _select_next),
# but remain in router._tasks so the scheduler can observe and resume them.
# ---------------------------------------------------------------------------


def suspend_task(
    task_id: str,
    kind: SuspensionKind,
    *,
    resume_at_ms: int | None = None,
    resume_on_children: list[str] | None = None,
    resume_payload: dict | None = None,
) -> Task:
    """Move a task into a suspended state.

    HUMAN → NEEDS_ACTION (only an explicit resume_task() call wakes it).
    JOIN / SCHEDULE / CHILD → BLOCKED (the scheduler auto-resumes when the
    condition fires).

    Argument requirements per kind:
      - SCHEDULE      requires resume_at_ms
      - JOIN, CHILD   require resume_on_children (CHILD = list of length 1)
      - HUMAN         neither required
    """
    task = _tasks.get(task_id)
    if not task:
        raise ValueError(f"Task '{task_id}' not found")
    if task.status not in (TaskStatus.PENDING, TaskStatus.RUNNING):
        raise ValueError(
            f"Task '{task_id}' cannot be suspended from status '{task.status}'"
        )

    if kind == SuspensionKind.SCHEDULE and resume_at_ms is None:
        raise ValueError("SCHEDULE suspension requires resume_at_ms")
    if kind in (SuspensionKind.JOIN, SuspensionKind.CHILD) and not resume_on_children:
        raise ValueError(f"{kind.value} suspension requires resume_on_children")
    if kind == SuspensionKind.CHILD and len(resume_on_children) != 1:
        raise ValueError("CHILD suspension takes exactly one child id")

    task.status = (
        TaskStatus.NEEDS_ACTION if kind == SuspensionKind.HUMAN else TaskStatus.BLOCKED
    )
    task.suspension_kind = kind
    task.resume_at_ms = resume_at_ms
    task.resume_on_children = list(resume_on_children or [])
    if resume_payload:
        task.resume_payload = {**task.resume_payload, **resume_payload}
    task.updated_at = _now_ms()

    log.info(
        "router.task_suspended",
        metadata={"task_id": task_id, "kind": kind.value},
    )
    _emit("task.suspended", task)
    return task


def resume_task(task_id: str, payload: dict | None = None) -> Task:
    """Wake a suspended task: BLOCKED/NEEDS_ACTION → PENDING.

    Payload (if any) is merged into the task's resume_payload so the next
    dispatch can read whatever the human/upstream supplied. Clears the
    suspension fields and pokes the scheduler.
    """
    from . import scheduler

    task = _tasks.get(task_id)
    if not task:
        raise ValueError(f"Task '{task_id}' not found")
    if task.status not in (TaskStatus.BLOCKED, TaskStatus.NEEDS_ACTION):
        raise ValueError(
            f"Task '{task_id}' is not suspended (status: {task.status})"
        )

    if payload:
        task.resume_payload = {**task.resume_payload, **payload}

    prior_kind = task.suspension_kind
    task.status = TaskStatus.PENDING
    task.suspension_kind = None
    task.resume_at_ms = None
    task.resume_on_children = []
    task.updated_at = _now_ms()

    log.info(
        "router.task_resumed",
        metadata={"task_id": task_id, "kind": prior_kind.value if prior_kind else None},
    )
    _emit("task.resumed", task)
    scheduler.notify()
    return task


async def cancel_task(task_id: str) -> Task:
    task = _tasks.get(task_id)
    if not task:
        raise ValueError(f"Task '{task_id}' not found")
    if task.status not in (
        TaskStatus.RUNNING,
        TaskStatus.PENDING,
        TaskStatus.BLOCKED,
        TaskStatus.NEEDS_ACTION,
    ):
        raise ValueError(f"Task '{task_id}' cannot be cancelled (status: {task.status})")

    task.status = TaskStatus.CANCELLED
    task.updated_at = _now_ms()

    await _finalize_task(task, reason="task_cancelled")

    log.info("router.task_cancelled", metadata={"task_id": task_id})
    _emit("task.cancelled", task)
    return task


async def check_running_tasks() -> None:
    """Check running tasks for missing or recycled containers.

    Implements role-aware recovery: persistent agents (merge-queue, PR shepherd,
    supervisor) auto-restart on failure; transient agents (worker, reviewer)
    clean up.
    """
    from . import lifecycle

    for task in list(_tasks.values()):
        if task.status != TaskStatus.RUNNING:
            continue

        session = lifecycle.get_session(task.session_name)
        if not session:
            agent_def = get_agent(task.agent_name)
            if agent_def and agent_def.persistent:
                log.info(
                    "router.persistent_agent_restart",
                    metadata={"task_id": task.id, "agent": task.agent_name},
                )
                try:
                    await _restart_persistent_task(task)
                except Exception as exc:
                    log.error(
                        "router.restart_failed",
                        metadata={"task_id": task.id, "reason": str(exc)},
                    )
                    await fail_task(task.id, f"Restart failed: {exc}")
            else:
                await fail_task(task.id, "Container no longer exists")
            continue

        if session.state == SessionState.RECYCLED:
            await fail_task(task.id, "Container was recycled externally")
        elif task.deadline_ms and _now_ms() > task.deadline_ms:
            log.info("router.task_deadline_exceeded", metadata={"task_id": task.id})
            await cancel_task(task.id)


async def _restart_persistent_task(task: Task) -> None:
    """Restart a persistent agent's container after failure."""
    from . import lifecycle

    agent_def = get_agent(task.agent_name)
    if not agent_def:
        raise ValueError(f"Agent '{task.agent_name}' not found for restart")

    # Reuse session name for continuity
    ttl = settings.hub.persistent_token_ttl
    old_token_id = task.token_id
    token = issue_token(task.agent_name, task.id, ttl=ttl)
    task.token_id = token.token_id
    task.updated_at = _now_ms()

    try:
        await lifecycle.run_pipeline(
            session_name=task.session_name,
            role=task.agent_name,
            hardened=agent_def.hardened,
            token=token,
            repo_url=task.repo_url,
        )
    except Exception:
        revoke_token(token.token_id)
        task.token_id = old_token_id
        raise

    if old_token_id:
        revoke_token(old_token_id)

    log.info(
        "router.persistent_agent_restarted",
        metadata={"task_id": task.id, "session": task.session_name},
    )
    _emit("task.restarted", task)


# ---------------------------------------------------------------------------
# State serialization
# ---------------------------------------------------------------------------


def get_state() -> dict:
    return {
        "tasks": [(tid, t.model_dump()) for tid, t in _tasks.items()],
    }


def restore_state(state: dict | None) -> None:
    if not state:
        return
    if "tasks" in state:
        for tid, data in state["tasks"]:
            task = Task(**data)
            _tasks[tid] = task



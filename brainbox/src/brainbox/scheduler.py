"""Task scheduler: background dispatch loop for PENDING tasks.

Reads from router._tasks, dispatches eligible PENDING tasks to runners or
in-process, handles retry backoff and deadline enforcement.

The scheduler loop wakes on a short timer or immediately when notify() is
called after a new task is enqueued. It processes all ready PENDING tasks
each iteration in priority order.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from .log import get_logger
from .models import TaskStatus
from .utils import now_ms as _now_ms

if TYPE_CHECKING:
    from .models import Task

log = get_logger()

_dispatch_task: asyncio.Task[None] | None = None
_wakeup: asyncio.Event = asyncio.Event()

_RETRY_BASE_MS = 30_000    # 30 s per attempt
_RETRY_CAP_MS = 300_000    # cap at 5 min
_LOOP_TIMEOUT_S = 5.0      # wake at least every 5 s


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def notify() -> None:
    """Wake the scheduler loop immediately (call after enqueuing a task)."""
    _wakeup.set()


def reset_for_tests() -> None:
    """Clear wakeup state between tests."""
    _wakeup.clear()


def start() -> None:
    """Start the scheduler background loop (called from hub.init)."""
    global _dispatch_task
    loop = asyncio.get_running_loop()
    _dispatch_task = loop.create_task(_scheduler_loop())
    log.info("scheduler.started")


def stop() -> None:
    """Stop the scheduler background loop (called from hub.shutdown)."""
    global _dispatch_task
    if _dispatch_task and not _dispatch_task.done():
        _dispatch_task.cancel()
    _dispatch_task = None
    log.info("scheduler.stopped")


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def _select_next() -> Task | None:
    """Return the highest-priority ready PENDING task, or None."""
    from . import router

    now = _now_ms()
    pending = [
        t for t in router._tasks.values()
        if t.status == TaskStatus.PENDING
        and (t.next_attempt_at is None or t.next_attempt_at <= now)
        and not (t.deadline_ms and now > t.deadline_ms)
    ]
    if not pending:
        return None
    return min(pending, key=lambda t: (-t.priority, t.next_attempt_at or 0, t.created_at))


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


async def _dispatch_pending(task: Task) -> None:
    """Attempt to dispatch a single PENDING task.

    On success: task transitions to RUNNING.
    On retryable failure: task stays PENDING with a backoff delay.
    On permanent failure or attempts exhausted: task transitions to FAILED.
    """
    from . import lifecycle, router
    from .config import settings
    from .registry import get_agent, issue_token, revoke_token
    from .runners import get_registry

    now = _now_ms()

    # Deadline guard — re-check at dispatch time in case the loop was slow
    if task.deadline_ms and now > task.deadline_ms:
        task.status = TaskStatus.FAILED
        task.error = "deadline exceeded before dispatch"
        task.updated_at = now
        router._emit("task.failed", task)
        log.info("scheduler.task_deadline_exceeded", metadata={"task_id": task.id})
        return

    agent_def = get_agent(task.agent_name)
    if not agent_def:
        task.status = TaskStatus.FAILED
        task.error = f"Agent '{task.agent_name}' not found at dispatch time"
        task.updated_at = _now_ms()
        router._emit("task.failed", task)
        return

    # Issue a fresh token for this attempt
    ttl = settings.hub.persistent_token_ttl if agent_def.persistent else settings.hub.token_ttl
    token = issue_token(task.agent_name, task.id, ttl=ttl)
    task.token_id = token.token_id

    # Build session name on first attempt only (persistent agents reuse it)
    if not task.session_name:
        task.session_name = f"task-{task.id[:8]}"

    task.status = TaskStatus.RUNNING
    task.updated_at = _now_ms()

    # Auto-select runner (explicit runner_name from submit overrides)
    resolved_runner = task.runner_name if task.runner_name else None
    if resolved_runner is None:
        resolved_runner = await get_registry().select_runner(
            backend=task.backend,
            preferred_tags=task.runner_tags,
        )
        if resolved_runner:
            task.runner_name = resolved_runner
            log.info(
                "scheduler.runner_selected",
                metadata={"task_id": task.id, "runner": resolved_runner},
            )

    # Resolve workspace context from repo if not set on task
    workspace_home = task.workspace_home
    workspace_profile = task.workspace_profile
    if not workspace_home and task.repo_url:
        repo = router._repos.get(router._repo_name(task.repo_url))
        if repo:
            workspace_home = workspace_home or repo.workspace_home
            workspace_profile = workspace_profile or repo.workspace_profile

    try:
        await lifecycle.run_pipeline(
            session_name=task.session_name,
            role=task.agent_name,
            hardened=agent_def.hardened,
            token=token,
            repo_url=task.repo_url,
            task_description=task.description,
            task_id=task.id,
            job_id=task.job_id,
            workspace_home=workspace_home,
            workspace_profile=workspace_profile,
            runner=resolved_runner,
            backend=task.backend,
        )
    except Exception as exc:
        task.attempts += 1
        err = str(exc)
        task.last_error = err
        revoke_token(token.token_id)
        task.token_id = None

        retryable = (
            "saturated" in err.lower()
            or isinstance(exc, (asyncio.TimeoutError, TimeoutError))
        )
        if retryable and task.attempts < task.max_attempts:
            backoff = min(task.attempts * _RETRY_BASE_MS, _RETRY_CAP_MS)
            task.next_attempt_at = _now_ms() + backoff
            task.status = TaskStatus.PENDING
            task.updated_at = _now_ms()
            log.info(
                "scheduler.task_retry_scheduled",
                metadata={
                    "task_id": task.id,
                    "attempts": task.attempts,
                    "backoff_ms": backoff,
                },
            )
            router._emit("task.retrying", task)
        else:
            task.status = TaskStatus.FAILED
            task.error = err
            task.updated_at = _now_ms()
            log.error(
                "scheduler.task_dispatch_failed",
                metadata={"task_id": task.id, "reason": err},
            )
            router._emit("task.failed", task)
        return

    # Track container in repo if applicable
    if task.repo_url:
        repo = router._repos.get(router._repo_name(task.repo_url))
        if repo:
            repo.containers[task.agent_name] = task.session_name

    log.info(
        "scheduler.task_dispatched",
        metadata={
            "task_id": task.id,
            "session": task.session_name,
            "agent": task.agent_name,
            "runner": resolved_runner,
        },
    )
    router._emit("task.started", task)


# ---------------------------------------------------------------------------
# Scheduler loop
# ---------------------------------------------------------------------------


async def _scheduler_loop() -> None:
    try:
        while True:
            # Wait for wakeup or periodic tick
            try:
                await asyncio.wait_for(asyncio.shield(_wakeup.wait()), timeout=_LOOP_TIMEOUT_S)
            except asyncio.TimeoutError:
                pass
            _wakeup.clear()

            # Expire deadline-missed PENDING tasks
            from . import router
            now = _now_ms()
            for task in list(router._tasks.values()):
                if (
                    task.status == TaskStatus.PENDING
                    and task.deadline_ms
                    and now > task.deadline_ms
                ):
                    task.status = TaskStatus.FAILED
                    task.error = "deadline exceeded before dispatch"
                    task.updated_at = now
                    router._emit("task.failed", task)
                    log.info("scheduler.deadline_expired", metadata={"task_id": task.id})

            # Dispatch all ready PENDING tasks in priority order
            while (task := _select_next()) is not None:
                await _dispatch_pending(task)

    except asyncio.CancelledError:
        pass

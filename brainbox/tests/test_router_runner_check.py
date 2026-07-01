"""check_running_tasks must not fail remote-runner tasks whose session lives on
the runner (regression: A2A worker dispatched to the 'Local' runner failed with
'Container no longer exists')."""

from __future__ import annotations

import time
import uuid

import pytest

import brainbox.registry as reg_module
import brainbox.router as router_module
from brainbox.models import AgentDefinition, Task, TaskStatus


def _running_task(*, runner_name: str | None) -> Task:
    reg_module._agents["worker"] = AgentDefinition(
        name="worker", image="t", capabilities=["task_submit"]
    )  # transient (not persistent)
    tid = str(uuid.uuid4())
    now = int(time.time() * 1000)
    task = Task(
        id=tid,
        description="do work",
        agent_name="worker",
        status=TaskStatus.RUNNING,
        created_at=now,
        updated_at=now,
        session_name=f"task-{tid[:8]}",
        runner_name=runner_name,
    )
    router_module._tasks[tid] = task
    return task


@pytest.mark.asyncio
async def test_remote_runner_task_not_failed_when_session_absent():
    # Session lives on the runner, so it's absent from the daemon's _sessions.
    task = _running_task(runner_name="Local")
    await router_module.check_running_tasks()
    assert router_module.get_task(task.id).status == TaskStatus.RUNNING  # not failed


@pytest.mark.asyncio
async def test_local_task_still_failed_when_container_gone():
    # Regression guard: a non-runner (in-process) task with no session is still
    # correctly failed — the fix must not disable that path.
    task = _running_task(runner_name=None)
    await router_module.check_running_tasks()
    failed = router_module.get_task(task.id)
    assert failed.status == TaskStatus.FAILED
    assert failed.error == "Container no longer exists"

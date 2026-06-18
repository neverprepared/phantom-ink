"""Tests for the WAITING_* suspension primitive used by the Loop runner.

Covers suspend/resume mechanics in router.py and scheduler auto-resume of
SCHEDULE / JOIN / CHILD waiters. HUMAN suspension is also exercised — the
scheduler must NOT auto-resume it; only an explicit resume_task() call wakes
HUMAN-suspended tasks.
"""

from __future__ import annotations

import time
import uuid

import pytest

import brainbox.router as router_module
from brainbox.models import SuspensionKind, Task, TaskStatus
from brainbox.scheduler import _select_next, _wake_resumable


def _make_task(
    *,
    status: TaskStatus = TaskStatus.RUNNING,
    agent_name: str = "worker",
) -> Task:
    now = int(time.time() * 1000)
    task_id = str(uuid.uuid4())
    task = Task(
        id=task_id,
        description="test task",
        agent_name=agent_name,
        status=status,
        created_at=now,
        updated_at=now,
        job_id=task_id,
    )
    router_module._tasks[task_id] = task
    return task


# ---------------------------------------------------------------------------
# suspend_task — input validation
# ---------------------------------------------------------------------------


class TestSuspendValidation:
    def test_unknown_task_raises(self):
        with pytest.raises(ValueError, match="not found"):
            router_module.suspend_task("nope", SuspensionKind.HUMAN)

    def test_cannot_suspend_completed_task(self):
        task = _make_task(status=TaskStatus.COMPLETED)
        with pytest.raises(ValueError, match="cannot be suspended"):
            router_module.suspend_task(task.id, SuspensionKind.HUMAN)

    def test_schedule_requires_resume_at_ms(self):
        task = _make_task()
        with pytest.raises(ValueError, match="resume_at_ms"):
            router_module.suspend_task(task.id, SuspensionKind.SCHEDULE)

    def test_join_requires_children(self):
        task = _make_task()
        with pytest.raises(ValueError, match="resume_on_children"):
            router_module.suspend_task(task.id, SuspensionKind.JOIN)

    def test_child_requires_exactly_one_child(self):
        task = _make_task()
        with pytest.raises(ValueError, match="exactly one"):
            router_module.suspend_task(
                task.id,
                SuspensionKind.CHILD,
                resume_on_children=["a", "b"],
            )


# ---------------------------------------------------------------------------
# HUMAN — never auto-wakes; only resume_task() unblocks
# ---------------------------------------------------------------------------


class TestHumanSuspension:
    def test_human_moves_to_needs_action(self):
        task = _make_task()
        router_module.suspend_task(task.id, SuspensionKind.HUMAN)
        assert task.status == TaskStatus.NEEDS_ACTION
        assert task.suspension_kind == SuspensionKind.HUMAN

    def test_wake_resumable_leaves_human_suspended(self):
        task = _make_task()
        router_module.suspend_task(task.id, SuspensionKind.HUMAN)
        _wake_resumable()
        assert task.status == TaskStatus.NEEDS_ACTION

    def test_resume_merges_payload_and_returns_to_pending(self):
        task = _make_task()
        router_module.suspend_task(
            task.id,
            SuspensionKind.HUMAN,
            resume_payload={"a": 1},
        )
        router_module.resume_task(task.id, payload={"b": 2})
        assert task.status == TaskStatus.PENDING
        assert task.suspension_kind is None
        assert task.resume_payload == {"a": 1, "b": 2}

    def test_resume_unknown_task_raises(self):
        with pytest.raises(ValueError, match="not found"):
            router_module.resume_task("nope")

    def test_resume_non_suspended_raises(self):
        task = _make_task(status=TaskStatus.RUNNING)
        with pytest.raises(ValueError, match="not suspended"):
            router_module.resume_task(task.id)


# ---------------------------------------------------------------------------
# SCHEDULE — wakes when wall-clock passes resume_at_ms
# ---------------------------------------------------------------------------


class TestScheduleSuspension:
    def test_schedule_moves_to_blocked(self):
        task = _make_task()
        future = int(time.time() * 1000) + 60_000
        router_module.suspend_task(
            task.id,
            SuspensionKind.SCHEDULE,
            resume_at_ms=future,
        )
        assert task.status == TaskStatus.BLOCKED
        assert task.suspension_kind == SuspensionKind.SCHEDULE

    def test_does_not_wake_before_resume_at(self):
        task = _make_task()
        future = int(time.time() * 1000) + 60_000
        router_module.suspend_task(
            task.id,
            SuspensionKind.SCHEDULE,
            resume_at_ms=future,
        )
        _wake_resumable()
        assert task.status == TaskStatus.BLOCKED

    def test_wakes_when_resume_at_has_passed(self):
        task = _make_task()
        past = int(time.time() * 1000) - 1000
        router_module.suspend_task(
            task.id,
            SuspensionKind.SCHEDULE,
            resume_at_ms=past,
        )
        _wake_resumable()
        assert task.status == TaskStatus.PENDING
        assert task.suspension_kind is None


# ---------------------------------------------------------------------------
# JOIN / CHILD — wake when children COMPLETED; fail when any FAILED
# ---------------------------------------------------------------------------


class TestJoinSuspension:
    def test_does_not_wake_while_children_running(self):
        parent = _make_task()
        child_a = _make_task(status=TaskStatus.RUNNING)
        child_b = _make_task(status=TaskStatus.COMPLETED)
        router_module.suspend_task(
            parent.id,
            SuspensionKind.JOIN,
            resume_on_children=[child_a.id, child_b.id],
        )
        _wake_resumable()
        assert parent.status == TaskStatus.BLOCKED

    def test_wakes_when_all_children_completed(self):
        parent = _make_task()
        child_a = _make_task(status=TaskStatus.COMPLETED)
        child_b = _make_task(status=TaskStatus.COMPLETED)
        router_module.suspend_task(
            parent.id,
            SuspensionKind.JOIN,
            resume_on_children=[child_a.id, child_b.id],
        )
        _wake_resumable()
        assert parent.status == TaskStatus.PENDING

    def test_fails_when_any_child_failed(self):
        parent = _make_task()
        ok = _make_task(status=TaskStatus.COMPLETED)
        bad = _make_task(status=TaskStatus.FAILED)
        router_module.suspend_task(
            parent.id,
            SuspensionKind.JOIN,
            resume_on_children=[ok.id, bad.id],
        )
        _wake_resumable()
        assert parent.status == TaskStatus.FAILED
        assert "child failed" in (parent.error or "")

    def test_fails_when_child_missing(self):
        parent = _make_task()
        router_module.suspend_task(
            parent.id,
            SuspensionKind.JOIN,
            resume_on_children=["ghost"],
        )
        _wake_resumable()
        assert parent.status == TaskStatus.FAILED

    def test_child_kind_wakes_on_single_completion(self):
        parent = _make_task()
        child = _make_task(status=TaskStatus.COMPLETED)
        router_module.suspend_task(
            parent.id,
            SuspensionKind.CHILD,
            resume_on_children=[child.id],
        )
        _wake_resumable()
        assert parent.status == TaskStatus.PENDING


# ---------------------------------------------------------------------------
# Slot-free invariant — suspended tasks must not be picked by _select_next
# ---------------------------------------------------------------------------


class TestSuspendedTasksReleaseQueueSlot:
    def test_blocked_task_invisible_to_select_next(self):
        task = _make_task()
        future = int(time.time() * 1000) + 60_000
        router_module.suspend_task(
            task.id,
            SuspensionKind.SCHEDULE,
            resume_at_ms=future,
        )
        assert _select_next() is None

    def test_needs_action_task_invisible_to_select_next(self):
        task = _make_task()
        router_module.suspend_task(task.id, SuspensionKind.HUMAN)
        assert _select_next() is None

    def test_resumed_task_becomes_selectable(self):
        task = _make_task()
        router_module.suspend_task(task.id, SuspensionKind.HUMAN)
        assert _select_next() is None
        router_module.resume_task(task.id)
        picked = _select_next()
        assert picked is not None
        assert picked.id == task.id


# ---------------------------------------------------------------------------
# cancel_task now reaches suspended tasks too
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_reaches_suspended_task():
    task = _make_task()
    router_module.suspend_task(task.id, SuspensionKind.HUMAN)
    # cancel_task is async and calls into lifecycle — patch the finalize step
    # so we don't need a real session to exist.
    from unittest.mock import AsyncMock, patch

    with patch.object(router_module, "_finalize_task", AsyncMock()):
        await router_module.cancel_task(task.id)
    assert task.status == TaskStatus.CANCELLED

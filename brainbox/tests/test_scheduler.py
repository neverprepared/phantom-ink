"""Tests for the task scheduler: selection, backoff, deadline, dispatch."""

from __future__ import annotations

import time
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import brainbox.router as router_module
from brainbox.models import AgentDefinition, Task, TaskStatus
import brainbox.registry as reg_module
from brainbox.scheduler import (
    _dispatch_pending,
    _select_next,
    _wakeup,
    notify,
    reset_for_tests,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(
    *,
    priority: int = 0,
    status: TaskStatus = TaskStatus.PENDING,
    next_attempt_at: int | None = None,
    deadline_ms: int | None = None,
    max_attempts: int = 1,
    attempts: int = 0,
    agent_name: str = "worker",
    backend: str = "docker",
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
        priority=priority,
        next_attempt_at=next_attempt_at,
        deadline_ms=deadline_ms,
        max_attempts=max_attempts,
        attempts=attempts,
        backend=backend,
        job_id=task_id,
    )
    router_module._tasks[task_id] = task
    return task


def _make_agent(name: str = "worker") -> AgentDefinition:
    agent = AgentDefinition(name=name, image="test-image", capabilities=["hub_messaging"])
    reg_module._agents[name] = agent
    return agent


# ---------------------------------------------------------------------------
# TestSelectNext
# ---------------------------------------------------------------------------


class TestSelectNext:
    def test_no_tasks_returns_none(self):
        assert _select_next() is None

    def test_no_pending_tasks_returns_none(self):
        _make_task(status=TaskStatus.RUNNING)
        assert _select_next() is None

    def test_returns_pending_task(self):
        task = _make_task()
        result = _select_next()
        assert result is not None
        assert result.id == task.id

    def test_higher_priority_wins(self):
        low = _make_task(priority=0)
        high = _make_task(priority=10)
        result = _select_next()
        assert result.id == high.id

    def test_earliest_created_wins_on_tie(self):
        now = int(time.time() * 1000)
        task_a = _make_task(priority=0)
        task_a.created_at = now - 1000
        task_b = _make_task(priority=0)
        task_b.created_at = now
        result = _select_next()
        assert result.id == task_a.id

    def test_skips_task_in_backoff(self):
        future = int(time.time() * 1000) + 60_000
        _make_task(next_attempt_at=future)
        assert _select_next() is None

    def test_skips_deadline_exceeded_task(self):
        past = int(time.time() * 1000) - 1
        _make_task(deadline_ms=past)
        assert _select_next() is None

    def test_ready_after_backoff_elapses(self):
        past = int(time.time() * 1000) - 1000
        task = _make_task(next_attempt_at=past)
        result = _select_next()
        assert result is not None
        assert result.id == task.id


# ---------------------------------------------------------------------------
# TestDispatchPending — success path
# ---------------------------------------------------------------------------


class TestDispatchPendingSuccess:
    async def test_task_transitions_to_running(self):
        _make_agent()
        task = _make_task()

        with patch("brainbox.lifecycle.run_pipeline", AsyncMock()), \
             patch("brainbox.registry.issue_token", return_value=MagicMock(token_id="tok-1")):
            await _dispatch_pending(task)

        assert task.status == TaskStatus.RUNNING

    async def test_session_name_set_on_first_dispatch(self):
        _make_agent()
        task = _make_task()
        assert task.session_name is None

        with patch("brainbox.lifecycle.run_pipeline", AsyncMock()), \
             patch("brainbox.registry.issue_token", return_value=MagicMock(token_id="tok-1")):
            await _dispatch_pending(task)

        assert task.session_name == f"task-{task.id[:8]}"

    async def test_task_started_event_emitted(self):
        _make_agent()
        task = _make_task()
        events = []
        router_module._listeners.append(lambda e, t: events.append(e))

        with patch("brainbox.lifecycle.run_pipeline", AsyncMock()), \
             patch("brainbox.registry.issue_token", return_value=MagicMock(token_id="tok-1")):
            await _dispatch_pending(task)

        assert "task.started" in events


# ---------------------------------------------------------------------------
# TestDispatchPending — failure paths
# ---------------------------------------------------------------------------


class TestDispatchPendingFailure:
    async def test_permanent_error_transitions_to_failed(self):
        _make_agent()
        task = _make_task(max_attempts=1)

        with patch("brainbox.lifecycle.run_pipeline", AsyncMock(side_effect=RuntimeError("docker failed"))), \
             patch("brainbox.registry.issue_token", return_value=MagicMock(token_id="tok-1")), \
             patch("brainbox.registry.revoke_token"):
            await _dispatch_pending(task)

        assert task.status == TaskStatus.FAILED
        assert "docker failed" in task.error

    async def test_saturated_error_stays_pending_with_backoff(self):
        _make_agent()
        task = _make_task(max_attempts=3)

        with patch("brainbox.lifecycle.run_pipeline", AsyncMock(side_effect=RuntimeError("runner alpha is saturated"))), \
             patch("brainbox.registry.issue_token", return_value=MagicMock(token_id="tok-1")), \
             patch("brainbox.registry.revoke_token"):
            await _dispatch_pending(task)

        assert task.status == TaskStatus.PENDING
        assert task.attempts == 1
        assert task.next_attempt_at is not None

    async def test_backoff_increases_with_attempts(self):
        _make_agent()
        task = _make_task(max_attempts=5)

        before = int(time.time() * 1000)
        with patch("brainbox.lifecycle.run_pipeline", AsyncMock(side_effect=RuntimeError("runner is saturated"))), \
             patch("brainbox.registry.issue_token", return_value=MagicMock(token_id="tok-1")), \
             patch("brainbox.registry.revoke_token"):
            await _dispatch_pending(task)
            first_backoff = task.next_attempt_at - before

            task.next_attempt_at = None
            task.status = TaskStatus.PENDING
            await _dispatch_pending(task)
            second_backoff = task.next_attempt_at - before

        assert second_backoff > first_backoff

    async def test_max_attempts_exhausted_transitions_to_failed(self):
        _make_agent()
        task = _make_task(max_attempts=1)
        task.attempts = 1  # already used up the one attempt

        with patch("brainbox.lifecycle.run_pipeline", AsyncMock(side_effect=RuntimeError("runner is saturated"))), \
             patch("brainbox.registry.issue_token", return_value=MagicMock(token_id="tok-1")), \
             patch("brainbox.registry.revoke_token"):
            await _dispatch_pending(task)

        assert task.status == TaskStatus.FAILED

    async def test_missing_agent_transitions_to_failed(self):
        task = _make_task(agent_name="nonexistent-agent")
        await _dispatch_pending(task)
        assert task.status == TaskStatus.FAILED
        assert "not found" in task.error

    async def test_deadline_exceeded_at_dispatch_transitions_to_failed(self):
        _make_agent()
        past = int(time.time() * 1000) - 1
        task = _make_task(deadline_ms=past)

        await _dispatch_pending(task)

        assert task.status == TaskStatus.FAILED
        assert "deadline" in task.error

    async def test_failed_event_emitted_on_permanent_failure(self):
        _make_agent()
        task = _make_task(max_attempts=1)
        events = []
        router_module._listeners.append(lambda e, t: events.append(e))

        with patch("brainbox.lifecycle.run_pipeline", AsyncMock(side_effect=RuntimeError("boom"))), \
             patch("brainbox.registry.issue_token", return_value=MagicMock(token_id="tok-1")), \
             patch("brainbox.registry.revoke_token"):
            await _dispatch_pending(task)

        assert "task.failed" in events

    async def test_retrying_event_emitted_on_backoff(self):
        _make_agent()
        task = _make_task(max_attempts=3)
        events = []
        router_module._listeners.append(lambda e, t: events.append(e))

        with patch("brainbox.lifecycle.run_pipeline", AsyncMock(side_effect=RuntimeError("saturated (0/4)"))), \
             patch("brainbox.registry.issue_token", return_value=MagicMock(token_id="tok-1")), \
             patch("brainbox.registry.revoke_token"):
            await _dispatch_pending(task)

        assert "task.retrying" in events


# ---------------------------------------------------------------------------
# TestDeadlineEnforcement (via scheduler loop state)
# ---------------------------------------------------------------------------


class TestDeadlineEnforcement:
    def test_deadline_expired_pending_task_fails_via_select(self):
        """_select_next skips deadline-expired tasks; they're cleaned by the loop."""
        past = int(time.time() * 1000) - 1
        task = _make_task(deadline_ms=past)
        # _select_next should skip it
        result = _select_next()
        assert result is None
        # Task is still PENDING until the loop cleans it up
        assert task.status == TaskStatus.PENDING


# ---------------------------------------------------------------------------
# TestNotify
# ---------------------------------------------------------------------------


class TestNotify:
    def test_notify_sets_wakeup(self):
        assert not _wakeup.is_set()
        notify()
        assert _wakeup.is_set()

    def test_reset_clears_wakeup(self):
        notify()
        reset_for_tests()
        assert not _wakeup.is_set()


# ---------------------------------------------------------------------------
# TestSubmitTask (router integration)
# ---------------------------------------------------------------------------


class TestSubmitTask:
    async def test_submit_task_returns_pending(self):
        _make_agent()
        from brainbox.router import submit_task
        task = await submit_task("do something", "worker")
        assert task.status == TaskStatus.PENDING

    async def test_submit_task_stores_in_tasks(self):
        _make_agent()
        from brainbox.router import submit_task
        task = await submit_task("do something", "worker")
        assert task.id in router_module._tasks

    async def test_submit_task_emits_queued_event(self):
        _make_agent()
        events = []
        router_module._listeners.append(lambda e, t: events.append(e))
        from brainbox.router import submit_task
        await submit_task("do something", "worker")
        assert "task.queued" in events

    async def test_submit_task_notifies_scheduler(self):
        _make_agent()
        from brainbox.router import submit_task
        await submit_task("do something", "worker")
        assert _wakeup.is_set()

    async def test_submit_task_stores_priority(self):
        _make_agent()
        from brainbox.router import submit_task
        task = await submit_task("do something", "worker", priority=5)
        assert task.priority == 5

    async def test_submit_task_stores_deadline(self):
        _make_agent()
        future_ms = int(time.time() * 1000) + 60_000
        from brainbox.router import submit_task
        task = await submit_task("do something", "worker", deadline_ms=future_ms)
        assert task.deadline_ms == future_ms

    async def test_submit_task_unknown_agent_raises(self):
        from brainbox.router import submit_task
        with pytest.raises(ValueError, match="not found"):
            await submit_task("do something", "nonexistent-agent")

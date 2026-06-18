"""Tests for the Phase A3c plumbing: SQLite persistence + router event bridge.

Two surfaces:
  1. Persistence — start_loop writes the instance row; advance_loop writes
     the iteration metric row and updates the instance row; terminate writes
     the final status. rehydrate_from_store loads RUNNING/PENDING instances
     and rebuilds the child_to_loop reverse index.
  2. Event bridge — start() registers a router event listener. When a
     task.completed event fires for a known iteration child, advance_loop
     runs and the loop transitions. task.failed fires on_iteration_failed.
     Non-loop tasks are ignored.

The bridge tests use a real asyncio event loop (the asyncio fixture from
pytest-asyncio in auto mode) so router._emit → asyncio.create_task →
advance_loop actually wires end-to-end.
"""

from __future__ import annotations

import asyncio

import pytest

import brainbox.loop_runner as runner
import brainbox.router as router_module
import brainbox.store as store
from brainbox.loop_runner import (
    advance_loop,
    rehydrate_from_store,
    start,
    start_loop,
)
from brainbox.loops import (
    Body,
    HandoffEnvelope,
    Intent,
    LoopSpec,
    LoopStatus,
    Node,
)
from brainbox.models import AgentDefinition, TaskStatus
import brainbox.registry as reg_module


@pytest.fixture
def reviewer_agent():
    agent = AgentDefinition(name="reviewer", image="test-image", capabilities=["hub_messaging"])
    reg_module._agents["reviewer"] = agent
    return agent


def _spec(*, max_iterations: int = 5) -> LoopSpec:
    return LoopSpec(
        name="test-loop",
        intent=Intent(outcome="x", convergence="length(findings.blockers) == `0`"),
        body=Body(nodes=[Node(id="reviewer", role="reviewer", prompt="review")]),
        convergence_metric="length(findings.blockers)",
        max_iterations=max_iterations,
    )


# ---------------------------------------------------------------------------
# Persistence — instance round-trip
# ---------------------------------------------------------------------------


class TestInstancePersistence:
    @pytest.mark.asyncio
    async def test_start_loop_writes_instance_row(self, reviewer_agent):
        inst = await start_loop(_spec(), HandoffEnvelope())
        persisted = store.get_loop_instance(inst.id)
        assert persisted is not None
        assert persisted.id == inst.id
        assert persisted.status == LoopStatus.RUNNING
        assert persisted.iteration == 1
        assert persisted.current_child_id == inst.current_child_id

    @pytest.mark.asyncio
    async def test_advance_updates_instance_row(self, reviewer_agent):
        inst = await start_loop(_spec(max_iterations=10), HandoffEnvelope())
        await advance_loop(inst.id, HandoffEnvelope(findings={"blockers": [1, 2]}))
        persisted = store.get_loop_instance(inst.id)
        assert persisted is not None
        assert persisted.iteration == 2
        assert persisted.metric_history == [2.0]

    @pytest.mark.asyncio
    async def test_terminate_writes_final_status(self, reviewer_agent):
        inst = await start_loop(_spec(), HandoffEnvelope())
        await advance_loop(inst.id, HandoffEnvelope(findings={"blockers": []}))
        persisted = store.get_loop_instance(inst.id)
        assert persisted is not None
        assert persisted.status == LoopStatus.CONVERGED
        assert persisted.current_child_id is None


# ---------------------------------------------------------------------------
# Persistence — iteration metric rows
# ---------------------------------------------------------------------------


class TestIterationMetricRows:
    @pytest.mark.asyncio
    async def test_each_advance_writes_one_row(self, reviewer_agent):
        inst = await start_loop(_spec(max_iterations=10), HandoffEnvelope())
        await advance_loop(inst.id, HandoffEnvelope(findings={"blockers": [1, 2, 3]}))
        await advance_loop(inst.id, HandoffEnvelope(findings={"blockers": [1, 2]}))
        await advance_loop(inst.id, HandoffEnvelope(findings={"blockers": [1]}))

        rows = store.query_loop_iteration_metrics(inst.id)
        assert [r["iteration"] for r in rows] == [1, 2, 3]
        assert [r["convergence_metric_value"] for r in rows] == [3.0, 2.0, 1.0]

    @pytest.mark.asyncio
    async def test_metric_row_records_state_at_end(self, reviewer_agent):
        inst = await start_loop(_spec(), HandoffEnvelope())
        await advance_loop(inst.id, HandoffEnvelope(findings={"blockers": []}))
        rows = store.query_loop_iteration_metrics(inst.id)
        assert rows[-1]["state_at_end"] == LoopStatus.CONVERGED.value

    @pytest.mark.asyncio
    async def test_metric_upserts_on_replay(self, reviewer_agent):
        # A replay shouldn't duplicate rows — UNIQUE(loop_id, iteration) +
        # ON CONFLICT DO UPDATE preserves the latest write.
        inst = await start_loop(_spec(max_iterations=10), HandoffEnvelope())
        await advance_loop(inst.id, HandoffEnvelope(findings={"blockers": [1, 2]}))
        # Manually re-insert iteration 1 with a different metric value
        await store.async_insert_loop_iteration_metric(
            loop_id=inst.id,
            iteration=1,
            convergence_metric_value=999.0,
            timestamp_ms=1,
        )
        rows = store.query_loop_iteration_metrics(inst.id)
        # Still exactly one row for iteration 1, with the overwritten value.
        iter1 = [r for r in rows if r["iteration"] == 1]
        assert len(iter1) == 1
        assert iter1[0]["convergence_metric_value"] == 999.0


# ---------------------------------------------------------------------------
# Rehydration — daemon restart recovery
# ---------------------------------------------------------------------------


class TestRehydrate:
    @pytest.mark.asyncio
    async def test_rehydrate_loads_running_instances(self, reviewer_agent):
        inst = await start_loop(_spec(max_iterations=10), HandoffEnvelope())
        await advance_loop(inst.id, HandoffEnvelope(findings={"blockers": [1, 2]}))

        # Simulate daemon restart: drop in-memory state but keep DB
        runner._instances.clear()
        runner._child_to_loop.clear()

        count = await rehydrate_from_store()
        assert count == 1
        rehydrated = runner.get_instance(inst.id)
        assert rehydrated is not None
        assert rehydrated.status == LoopStatus.RUNNING
        assert rehydrated.iteration == 2
        # Reverse index restored so a completing child still finds its loop
        assert runner.loop_id_for_child(rehydrated.current_child_id) == inst.id

    @pytest.mark.asyncio
    async def test_rehydrate_skips_terminal_instances(self, reviewer_agent):
        inst = await start_loop(_spec(), HandoffEnvelope())
        await advance_loop(inst.id, HandoffEnvelope(findings={"blockers": []}))
        assert runner.get_instance(inst.id).status == LoopStatus.CONVERGED  # type: ignore[union-attr]

        runner._instances.clear()
        runner._child_to_loop.clear()

        count = await rehydrate_from_store()
        # CONVERGED is terminal — should NOT be reloaded into the active map
        assert count == 0
        assert runner.get_instance(inst.id) is None


# ---------------------------------------------------------------------------
# Router event bridge
# ---------------------------------------------------------------------------


class TestEventBridge:
    @pytest.mark.asyncio
    async def test_start_is_idempotent(self, reviewer_agent):
        start()
        start()
        # Only one registration on the router
        assert router_module._listeners.count(runner._on_router_event) == 1

    @pytest.mark.asyncio
    async def test_completed_event_fires_advance_loop(self, reviewer_agent):
        start()
        inst = await start_loop(_spec(max_iterations=10), HandoffEnvelope())
        child_id = inst.current_child_id

        # Mark the child task COMPLETED with an envelope as result, then emit
        # the router event. The bridge schedules advance_loop on the loop;
        # await one event-loop turn for it to run.
        child = router_module._tasks[child_id]
        child.status = TaskStatus.COMPLETED
        child.result = HandoffEnvelope(findings={"blockers": [1, 2]}).model_dump()
        router_module._emit("task.completed", child)

        # Yield until advance_loop has run. A single await is enough because
        # create_task schedules the coroutine on the current loop.
        await runner.wait_for_bridges()

        result = runner.get_instance(inst.id)
        assert result is not None
        assert result.iteration == 2
        assert result.status == LoopStatus.RUNNING

    @pytest.mark.asyncio
    async def test_failed_event_fires_on_iteration_failed(self, reviewer_agent):
        start()
        inst = await start_loop(_spec(), HandoffEnvelope())
        child_id = inst.current_child_id

        child = router_module._tasks[child_id]
        child.status = TaskStatus.FAILED
        child.error = "container crashed"
        router_module._emit("task.failed", child)

        await runner.wait_for_bridges()

        result = runner.get_instance(inst.id)
        assert result is not None
        assert result.status == LoopStatus.FAILED
        assert "container crashed" in (result.error or "")

    @pytest.mark.asyncio
    async def test_unknown_task_event_is_ignored(self, reviewer_agent):
        start()
        # Build a task that no loop is waiting on
        from brainbox.models import Task

        ghost = Task(
            id="ghost",
            description="x",
            agent_name="reviewer",
            status=TaskStatus.COMPLETED,
            created_at=1,
            updated_at=1,
        )
        router_module._tasks["ghost"] = ghost
        # Should not raise; should not register a loop
        router_module._emit("task.completed", ghost)
        await runner.wait_for_bridges()
        # No regression on the empty registry
        assert runner.list_instances() == []

    @pytest.mark.asyncio
    async def test_envelope_extraction_from_dict_result(self, reviewer_agent):
        # The bridge accepts a plain dict as task.result and parses it into a
        # HandoffEnvelope. This is the intermediate path while agents still
        # emit JSON instead of typed envelopes.
        start()
        inst = await start_loop(_spec(max_iterations=10), HandoffEnvelope())
        child_id = inst.current_child_id

        child = router_module._tasks[child_id]
        child.status = TaskStatus.COMPLETED
        child.result = {"findings": {"blockers": []}}  # raw dict
        router_module._emit("task.completed", child)

        await runner.wait_for_bridges()

        result = runner.get_instance(inst.id)
        assert result is not None
        assert result.status == LoopStatus.CONVERGED

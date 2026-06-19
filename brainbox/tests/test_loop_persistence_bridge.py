"""Tests for SQLite persistence + router event bridge (markdown edition).

Two surfaces:
  1. Persistence — start_loop writes the instance row; advance_loop writes
     the iteration row and updates the instance row; terminate writes the
     final status. rehydrate_from_store loads RUNNING/PENDING instances
     and rebuilds the child_to_loop reverse index.
  2. Event bridge — start() registers a router event listener. When a
     task.completed event fires for a known iteration child, advance_loop
     runs and the loop transitions. task.failed fires on_iteration_failed.
     Non-loop tasks are ignored.

The judge functions ``evaluate_stop`` / ``evaluate_escalation`` are
patched with async fakes that return a ``JudgeVerdict`` — the runner
itself is what we are testing, not the judge LLM.
"""

from __future__ import annotations

import pytest

import brainbox.loop_runner as runner
import brainbox.registry as reg_module
import brainbox.router as router_module
import brainbox.store as store
from brainbox.loop_judge import JudgeVerdict
from brainbox.loop_md import parse
from brainbox.loop_runner import (
    advance_loop,
    rehydrate_from_store,
    start,
    start_loop,
)
from brainbox.loops import HandoffEnvelope, LoopStatus
from brainbox.models import AgentDefinition, TaskStatus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_TEMPLATE = """\
---
name: test-loop
trigger: manual
max_iterations: 3
---

# Role

You do a thing.

# When to stop

- The thing is done.

# When to escalate

- The thing breaks.
"""


def _template(*, max_iterations: int = 3, name: str = "test-loop") -> str:
    return f"""\
---
name: {name}
trigger: manual
max_iterations: {max_iterations}
---

# Role

You do a thing.

# When to stop

- The thing is done.

# When to escalate

- The thing breaks.
"""


@pytest.fixture
def reviewer_agent():
    # Template name is the default agent name; register it so router can dispatch.
    agent = AgentDefinition(name="test-loop", image="test-image", capabilities=["hub_messaging"])
    reg_module._agents["test-loop"] = agent
    return agent


@pytest.fixture
def never_stop(monkeypatch):
    """Judge: never fires stop or escalation. Loop continues until max_iter
    guard or explicit override per-test."""

    async def _stop(**_kw):
        return JudgeVerdict(fired=False, reason="not done", via="judge")

    async def _esc(*, iteration, max_iterations, **_kw):
        if iteration >= max_iterations:
            return JudgeVerdict(fired=True, reason="iteration cap hit", via="objective")
        return JudgeVerdict(fired=False, reason="not breaking", via="judge")

    monkeypatch.setattr(runner, "evaluate_stop", _stop)
    monkeypatch.setattr(runner, "evaluate_escalation", _esc)


@pytest.fixture
def always_stop(monkeypatch):
    """Judge: stop fires immediately on advance — CONVERGED."""

    async def _stop(**_kw):
        return JudgeVerdict(fired=True, reason="done", via="judge")

    async def _esc(**_kw):
        return JudgeVerdict(fired=False, reason="n/a", via="judge")

    monkeypatch.setattr(runner, "evaluate_stop", _stop)
    monkeypatch.setattr(runner, "evaluate_escalation", _esc)


# ---------------------------------------------------------------------------
# Persistence — instance round-trip
# ---------------------------------------------------------------------------


class TestInstancePersistence:
    @pytest.mark.asyncio
    async def test_start_loop_writes_instance_row(self, reviewer_agent, never_stop):
        loop = parse(_template())
        inst = await start_loop(loop, HandoffEnvelope())

        persisted = store.get_loop_instance(inst.id)
        assert persisted is not None
        assert persisted.id == inst.id
        assert persisted.status == LoopStatus.RUNNING
        assert persisted.iteration == 1
        assert persisted.current_child_id == inst.current_child_id
        assert persisted.template_name == "test-loop"
        assert persisted.template_text == loop.raw
        assert persisted.template_hash  # non-empty
        assert persisted.mermaid  # rendered

    @pytest.mark.asyncio
    async def test_advance_updates_instance_row(self, reviewer_agent, never_stop):
        loop = parse(_template(max_iterations=10))
        inst = await start_loop(loop, HandoffEnvelope())
        await advance_loop(inst.id, HandoffEnvelope(findings={"blockers": [1, 2]}))

        persisted = store.get_loop_instance(inst.id)
        assert persisted is not None
        assert persisted.iteration == 2
        # cost_history grows by one float per advance (currently 0.0)
        assert persisted.cost_history == [0.0]
        assert persisted.cost_usd == 0.0
        assert persisted.status == LoopStatus.RUNNING

    @pytest.mark.asyncio
    async def test_terminate_writes_final_status(self, reviewer_agent, always_stop):
        loop = parse(_template())
        inst = await start_loop(loop, HandoffEnvelope())
        await advance_loop(inst.id, HandoffEnvelope(findings={"blockers": []}))

        persisted = store.get_loop_instance(inst.id)
        assert persisted is not None
        assert persisted.status == LoopStatus.CONVERGED
        assert persisted.current_child_id is None
        assert persisted.stop_reason == "done"


# ---------------------------------------------------------------------------
# Persistence — iteration rows
# ---------------------------------------------------------------------------


class TestIterationRows:
    @pytest.mark.asyncio
    async def test_each_advance_writes_one_row(self, reviewer_agent, never_stop):
        loop = parse(_template(max_iterations=10))
        inst = await start_loop(loop, HandoffEnvelope())
        await advance_loop(inst.id, HandoffEnvelope(findings={"blockers": [3]}))
        await advance_loop(inst.id, HandoffEnvelope(findings={"blockers": [2]}))
        await advance_loop(inst.id, HandoffEnvelope(findings={"blockers": [1]}))

        rows = store.query_loop_iteration_metrics(inst.id)
        assert [r["iteration"] for r in rows] == [1, 2, 3]
        # cost is the value carried in the convergence_metric_value column
        assert [r["convergence_metric_value"] for r in rows] == [0.0, 0.0, 0.0]

    @pytest.mark.asyncio
    async def test_row_records_state_at_end_on_converge(
        self, reviewer_agent, always_stop
    ):
        loop = parse(_template())
        inst = await start_loop(loop, HandoffEnvelope())
        await advance_loop(inst.id, HandoffEnvelope(findings={"blockers": []}))

        rows = store.query_loop_iteration_metrics(inst.id)
        assert rows[-1]["state_at_end"] == LoopStatus.CONVERGED.value

    @pytest.mark.asyncio
    async def test_row_records_state_at_end_on_running(
        self, reviewer_agent, never_stop
    ):
        loop = parse(_template(max_iterations=10))
        inst = await start_loop(loop, HandoffEnvelope())
        await advance_loop(inst.id, HandoffEnvelope())

        rows = store.query_loop_iteration_metrics(inst.id)
        assert rows[-1]["state_at_end"] == LoopStatus.RUNNING.value

    @pytest.mark.asyncio
    async def test_row_upserts_on_replay(self, reviewer_agent, never_stop):
        # A replay shouldn't duplicate rows — UNIQUE(loop_id, iteration) +
        # ON CONFLICT DO UPDATE preserves the latest write.
        loop = parse(_template(max_iterations=10))
        inst = await start_loop(loop, HandoffEnvelope())
        await advance_loop(inst.id, HandoffEnvelope())

        await store.async_insert_loop_iteration_metric(
            loop_id=inst.id,
            iteration=1,
            convergence_metric_value=999.0,
            timestamp_ms=1,
        )
        rows = store.query_loop_iteration_metrics(inst.id)
        iter1 = [r for r in rows if r["iteration"] == 1]
        assert len(iter1) == 1
        assert iter1[0]["convergence_metric_value"] == 999.0


# ---------------------------------------------------------------------------
# Rehydration — daemon restart recovery
# ---------------------------------------------------------------------------


class TestRehydrate:
    @pytest.mark.asyncio
    async def test_rehydrate_loads_running_instances(
        self, reviewer_agent, never_stop
    ):
        loop = parse(_template(max_iterations=10))
        inst = await start_loop(loop, HandoffEnvelope())
        await advance_loop(inst.id, HandoffEnvelope(findings={"blockers": [1, 2]}))

        # Simulate daemon restart: drop in-memory state but keep DB
        runner._instances.clear()
        runner._child_to_loop.clear()
        runner._parsed.clear()

        count = await rehydrate_from_store()
        assert count == 1
        rehydrated = runner.get_instance(inst.id)
        assert rehydrated is not None
        assert rehydrated.status == LoopStatus.RUNNING
        assert rehydrated.iteration == 2
        assert rehydrated.template_name == "test-loop"
        assert rehydrated.template_text == loop.raw
        # Reverse index restored so a completing child still finds its loop
        assert runner.loop_id_for_child(rehydrated.current_child_id) == inst.id

    @pytest.mark.asyncio
    async def test_rehydrate_skips_terminal_instances(
        self, reviewer_agent, always_stop
    ):
        loop = parse(_template())
        inst = await start_loop(loop, HandoffEnvelope())
        await advance_loop(inst.id, HandoffEnvelope(findings={"blockers": []}))
        assert runner.get_instance(inst.id).status == LoopStatus.CONVERGED  # type: ignore[union-attr]

        runner._instances.clear()
        runner._child_to_loop.clear()
        runner._parsed.clear()

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
    async def test_completed_event_fires_advance_loop(
        self, reviewer_agent, never_stop
    ):
        start()
        loop = parse(_template(max_iterations=10))
        inst = await start_loop(loop, HandoffEnvelope())
        child_id = inst.current_child_id

        child = router_module._tasks[child_id]
        child.status = TaskStatus.COMPLETED
        child.result = HandoffEnvelope(findings={"blockers": [1, 2]}).model_dump()
        router_module._emit("task.completed", child)

        await runner.wait_for_bridges()

        result = runner.get_instance(inst.id)
        assert result is not None
        assert result.iteration == 2
        assert result.status == LoopStatus.RUNNING

    @pytest.mark.asyncio
    async def test_failed_event_fires_on_iteration_failed(
        self, reviewer_agent, never_stop
    ):
        start()
        loop = parse(_template())
        inst = await start_loop(loop, HandoffEnvelope())
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
        from brainbox.models import Task

        ghost = Task(
            id="ghost",
            description="x",
            agent_name="test-loop",
            status=TaskStatus.COMPLETED,
            created_at=1,
            updated_at=1,
        )
        router_module._tasks["ghost"] = ghost
        # Should not raise; should not register a loop
        router_module._emit("task.completed", ghost)
        await runner.wait_for_bridges()
        assert runner.list_instances() == []

    @pytest.mark.asyncio
    async def test_envelope_extraction_from_dict_result(
        self, reviewer_agent, always_stop
    ):
        # The bridge accepts a plain dict as task.result and parses it into a
        # HandoffEnvelope. Stop judge will fire immediately on the next advance
        # so we can observe the envelope flowed through.
        start()
        loop = parse(_template(max_iterations=10))
        inst = await start_loop(loop, HandoffEnvelope())
        child_id = inst.current_child_id

        child = router_module._tasks[child_id]
        child.status = TaskStatus.COMPLETED
        child.result = {"findings": {"blockers": []}}  # raw dict
        router_module._emit("task.completed", child)

        await runner.wait_for_bridges()

        result = runner.get_instance(inst.id)
        assert result is not None
        assert result.status == LoopStatus.CONVERGED

"""Tests for the Loop runner core (Phase A3b).

Covers the five terminal paths of advance_loop and the start_loop wiring:
  - CONVERGED (convergence predicate fires) → parent task COMPLETED
  - STOPPED_BY_CONDITION (stop predicate matches) → parent FAILED, reason recorded
  - MAX_ITER (iteration cap hit) → parent FAILED
  - THRASHING (non-decreasing metric twice) → parent FAILED
  - FAILED (iteration child task failed) → parent FAILED
  - Normal continue → next iteration child enqueued, metric_history grows

Tests skip the router event listener entirely and call advance_loop
directly with synthetic envelopes. The listener wiring lands with A3c so
each PR can be reviewed for one concern.
"""

from __future__ import annotations

import pytest

import brainbox.loop_runner as runner
import brainbox.router as router_module
from brainbox.loop_runner import (
    advance_loop,
    get_instance,
    loop_id_for_child,
    on_iteration_failed,
    start_loop,
)
from brainbox.loops import (
    Body,
    HandoffEnvelope,
    Intent,
    LoopSpec,
    LoopStatus,
    Node,
    StopCondition,
)
from brainbox.models import AgentDefinition, TaskStatus
import brainbox.registry as reg_module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def reviewer_agent():
    agent = AgentDefinition(name="reviewer", image="test-image", capabilities=["hub_messaging"])
    reg_module._agents["reviewer"] = agent
    return agent


def _spec(
    *,
    convergence: str = "length(findings.blockers) == `0`",
    metric: str = "length(findings.blockers)",
    max_iterations: int = 5,
    stop_conditions: list[StopCondition] | None = None,
) -> LoopSpec:
    return LoopSpec(
        name="test-loop",
        intent=Intent(outcome="x", convergence=convergence),
        body=Body(nodes=[Node(id="reviewer", role="reviewer", prompt="review the diff")]),
        convergence_metric=metric,
        max_iterations=max_iterations,
        stop_conditions=stop_conditions or [],
    )


# ---------------------------------------------------------------------------
# start_loop
# ---------------------------------------------------------------------------


class TestStartLoop:
    @pytest.mark.asyncio
    async def test_creates_instance_parent_and_first_child(self, reviewer_agent):
        spec = _spec()
        inst = await start_loop(spec, HandoffEnvelope())

        assert inst.status == LoopStatus.RUNNING
        assert inst.iteration == 1
        assert inst.parent_task_id in router_module._tasks
        assert inst.current_child_id in router_module._tasks

        parent = router_module._tasks[inst.parent_task_id]
        child = router_module._tasks[inst.current_child_id]
        assert parent.status == TaskStatus.RUNNING
        assert child.status == TaskStatus.PENDING
        assert child.agent_name == "reviewer"
        assert child.job_id == parent.id  # child is parented to the loop task

    @pytest.mark.asyncio
    async def test_stamps_loop_id_and_iteration_on_envelope(self, reviewer_agent):
        spec = _spec()
        inst = await start_loop(spec, HandoffEnvelope())
        assert inst.envelope.loop_id == inst.id
        assert inst.envelope.iteration == 0

    @pytest.mark.asyncio
    async def test_pins_template_snapshot(self, reviewer_agent):
        spec = _spec()
        inst = await start_loop(spec, HandoffEnvelope())
        snap = inst.spec_snapshot.template_snapshot
        assert snap is not None
        assert snap.name == "test-loop"
        assert snap.hash  # non-empty

    @pytest.mark.asyncio
    async def test_registers_child_to_loop_mapping(self, reviewer_agent):
        spec = _spec()
        inst = await start_loop(spec, HandoffEnvelope())
        assert loop_id_for_child(inst.current_child_id) == inst.id

    @pytest.mark.asyncio
    async def test_rejects_empty_body(self, reviewer_agent):
        spec = LoopSpec(
            name="empty",
            intent=Intent(outcome="x", convergence="`true`"),
            body=Body(nodes=[]),
        )
        with pytest.raises(ValueError, match="at least one node"):
            await start_loop(spec, HandoffEnvelope())

    @pytest.mark.asyncio
    async def test_rejects_first_node_without_agent_or_role(self, reviewer_agent):
        spec = LoopSpec(
            name="no-role",
            intent=Intent(outcome="x", convergence="`true`"),
            body=Body(nodes=[Node(id="x")]),
        )
        with pytest.raises(ValueError, match="agent_id or role"):
            await start_loop(spec, HandoffEnvelope())


# ---------------------------------------------------------------------------
# advance_loop — CONVERGED happy path
# ---------------------------------------------------------------------------


class TestAdvanceConverged:
    @pytest.mark.asyncio
    async def test_zero_blockers_converges(self, reviewer_agent):
        spec = _spec()
        inst = await start_loop(spec, HandoffEnvelope())

        # Reviewer reports zero blockers and CI green — should converge
        env = HandoffEnvelope(
            findings={"blockers": []},
            observations={"ci_status": "green"},
        )
        # Loop has just convergence=length(findings.blockers)==0, so CI status irrelevant
        spec_simple = inst.spec_snapshot  # already has convergence_predicate set
        result = await advance_loop(inst.id, env)
        assert result.status == LoopStatus.CONVERGED
        assert result.current_child_id is None
        # Parent task transitions to COMPLETED
        parent = router_module._tasks[result.parent_task_id]
        assert parent.status == TaskStatus.COMPLETED
        # Metric recorded
        assert result.metric_history == [0.0]

    @pytest.mark.asyncio
    async def test_convergence_clears_child_mapping(self, reviewer_agent):
        spec = _spec()
        inst = await start_loop(spec, HandoffEnvelope())
        old_child = inst.current_child_id

        env = HandoffEnvelope(findings={"blockers": []})
        await advance_loop(inst.id, env)
        assert loop_id_for_child(old_child) is None


# ---------------------------------------------------------------------------
# advance_loop — normal continue path
# ---------------------------------------------------------------------------


class TestAdvanceContinue:
    @pytest.mark.asyncio
    async def test_blockers_present_enqueues_next_iteration(self, reviewer_agent):
        spec = _spec(max_iterations=10)
        inst = await start_loop(spec, HandoffEnvelope())
        original_child = inst.current_child_id

        env = HandoffEnvelope(findings={"blockers": [{"file": "a.go"}, {"file": "b.go"}]})
        result = await advance_loop(inst.id, env)

        assert result.status == LoopStatus.RUNNING
        assert result.iteration == 2
        assert result.current_child_id != original_child
        assert result.current_child_id in router_module._tasks
        new_child = router_module._tasks[result.current_child_id]
        assert new_child.status == TaskStatus.PENDING
        assert new_child.job_id == result.parent_task_id
        # Old child mapping cleared; new one registered
        assert loop_id_for_child(original_child) is None
        assert loop_id_for_child(result.current_child_id) == inst.id

    @pytest.mark.asyncio
    async def test_metric_history_accumulates_across_iterations(self, reviewer_agent):
        spec = _spec(max_iterations=10)
        inst = await start_loop(spec, HandoffEnvelope())

        await advance_loop(inst.id, HandoffEnvelope(findings={"blockers": [1, 2, 3]}))
        await advance_loop(inst.id, HandoffEnvelope(findings={"blockers": [1, 2]}))
        result = await advance_loop(inst.id, HandoffEnvelope(findings={"blockers": [1]}))

        assert result.metric_history == [3.0, 2.0, 1.0]
        assert result.status == LoopStatus.RUNNING


# ---------------------------------------------------------------------------
# advance_loop — MAX_ITER
# ---------------------------------------------------------------------------


class TestAdvanceMaxIter:
    @pytest.mark.asyncio
    async def test_hits_cap_marks_max_iter(self, reviewer_agent):
        spec = _spec(max_iterations=2)
        inst = await start_loop(spec, HandoffEnvelope())  # iteration=1

        # iteration 1 → enqueue 2 (still under cap)
        await advance_loop(inst.id, HandoffEnvelope(findings={"blockers": [1]}))
        # iteration 2 → at cap, terminal
        result = await advance_loop(inst.id, HandoffEnvelope(findings={"blockers": [1]}))

        assert result.status == LoopStatus.MAX_ITER
        assert result.current_child_id is None
        parent = router_module._tasks[result.parent_task_id]
        assert parent.status == TaskStatus.FAILED


# ---------------------------------------------------------------------------
# advance_loop — STOPPED_BY_CONDITION
# ---------------------------------------------------------------------------


class TestAdvanceStopCondition:
    @pytest.mark.asyncio
    async def test_diff_size_cap_stops_loop(self, reviewer_agent):
        spec = _spec(
            max_iterations=10,
            stop_conditions=[
                StopCondition(
                    predicate="observations.diff_lines > `100`",
                    reason="diff_too_large",
                ),
            ],
        )
        inst = await start_loop(spec, HandoffEnvelope())

        env = HandoffEnvelope(
            findings={"blockers": [1]},
            observations={"diff_lines": 500},
        )
        result = await advance_loop(inst.id, env)

        assert result.status == LoopStatus.STOPPED_BY_CONDITION
        assert result.stop_reason == "diff_too_large"
        parent = router_module._tasks[result.parent_task_id]
        assert parent.status == TaskStatus.FAILED

    @pytest.mark.asyncio
    async def test_first_matching_condition_wins(self, reviewer_agent):
        # Multiple conditions; the runner picks the first match for the reason tag.
        spec = _spec(
            max_iterations=10,
            stop_conditions=[
                StopCondition(predicate="observations.diff_lines > `100`", reason="diff_too_large"),
                StopCondition(predicate="observations.cost_usd > `1.0`", reason="too_expensive"),
            ],
        )
        inst = await start_loop(spec, HandoffEnvelope())

        env = HandoffEnvelope(
            findings={"blockers": [1]},
            observations={"diff_lines": 500, "cost_usd": 2.5},
        )
        result = await advance_loop(inst.id, env)
        assert result.stop_reason == "diff_too_large"


# ---------------------------------------------------------------------------
# advance_loop — THRASHING
# ---------------------------------------------------------------------------


class TestAdvanceThrashing:
    @pytest.mark.asyncio
    async def test_non_decreasing_twice_marks_thrashing(self, reviewer_agent):
        spec = _spec(max_iterations=10)
        inst = await start_loop(spec, HandoffEnvelope())

        # iter 1: 3 blockers (history [3])
        await advance_loop(inst.id, HandoffEnvelope(findings={"blockers": [1, 2, 3]}))
        # iter 2: 3 blockers (history [3, 3]) — first non-decrease, not yet thrashing
        inst2 = await advance_loop(inst.id, HandoffEnvelope(findings={"blockers": [1, 2, 3]}))
        assert inst2.status == LoopStatus.RUNNING
        # iter 3: 4 blockers (history [3, 3, 4]) — second non-decrease → thrashing
        inst3 = await advance_loop(inst.id, HandoffEnvelope(findings={"blockers": [1, 2, 3, 4]}))
        assert inst3.status == LoopStatus.THRASHING
        assert inst3.metric_history == [3.0, 3.0, 4.0]
        parent = router_module._tasks[inst3.parent_task_id]
        assert parent.status == TaskStatus.FAILED

    @pytest.mark.asyncio
    async def test_steady_decrease_does_not_thrash(self, reviewer_agent):
        spec = _spec(max_iterations=10)
        inst = await start_loop(spec, HandoffEnvelope())

        await advance_loop(inst.id, HandoffEnvelope(findings={"blockers": [1, 2, 3, 4]}))
        await advance_loop(inst.id, HandoffEnvelope(findings={"blockers": [1, 2, 3]}))
        result = await advance_loop(inst.id, HandoffEnvelope(findings={"blockers": [1, 2]}))
        assert result.status == LoopStatus.RUNNING


# ---------------------------------------------------------------------------
# Iteration child failure → loop FAILED
# ---------------------------------------------------------------------------


class TestIterationChildLoopContext:
    """Loop iteration children carry loop_id / loop_iteration / permission_tier
    / node_requires through router.submit_task → Task so the dispatch path
    can inject env vars and (later) filter by permission tier."""

    @pytest.mark.asyncio
    async def test_first_child_has_loop_context_fields(self, reviewer_agent):
        spec = _spec()
        inst = await start_loop(spec, HandoffEnvelope())
        child = router_module._tasks[inst.current_child_id]
        assert child.loop_id == inst.id
        assert child.loop_iteration == 1
        # pr-review template uses default tier
        assert child.permission_tier == "default"

    @pytest.mark.asyncio
    async def test_node_requires_propagate_to_child(self, reviewer_agent):
        spec = LoopSpec(
            name="strict-test",
            intent=Intent(outcome="x", convergence="`true`"),
            body=Body(nodes=[
                Node(id="reviewer", role="reviewer", prompt="x",
                     requires=["repo:read", "memory:write"]),
            ]),
            convergence_metric="`0`",
        )
        inst = await start_loop(spec, HandoffEnvelope())
        child = router_module._tasks[inst.current_child_id]
        assert child.node_requires == ["repo:read", "memory:write"]

    @pytest.mark.asyncio
    async def test_subsequent_iteration_has_correct_counter(self, reviewer_agent):
        spec = _spec(max_iterations=10)
        inst = await start_loop(spec, HandoffEnvelope())
        await advance_loop(inst.id, HandoffEnvelope(findings={"blockers": [1]}))
        # iter 2's child should carry loop_iteration == 2
        child = router_module._tasks[inst.current_child_id]
        assert child.loop_iteration == 2


class TestCancelLoop:
    @pytest.mark.asyncio
    async def test_cancel_running_loop(self, reviewer_agent):
        from brainbox.loop_runner import cancel_loop

        spec = _spec()
        inst = await start_loop(spec, HandoffEnvelope())
        child_id = inst.current_child_id

        result = await cancel_loop(inst.id, reason="test")
        assert result.status == LoopStatus.CANCELLED
        assert result.error == "test"
        parent = router_module._tasks[result.parent_task_id]
        assert parent.status == TaskStatus.CANCELLED
        # The child was cancelled too (or was already terminal)
        child = router_module._tasks[child_id]
        assert child.status == TaskStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_cancel_terminal_loop_is_noop(self, reviewer_agent):
        from brainbox.loop_runner import cancel_loop

        spec = _spec()
        inst = await start_loop(spec, HandoffEnvelope())
        await advance_loop(inst.id, HandoffEnvelope(findings={"blockers": []}))
        # Already CONVERGED — cancel should return unchanged
        result = await cancel_loop(inst.id)
        assert result.status == LoopStatus.CONVERGED

    @pytest.mark.asyncio
    async def test_cancel_unknown_loop_raises(self):
        from brainbox.loop_runner import cancel_loop

        with pytest.raises(ValueError, match="not found"):
            await cancel_loop("ghost")


class TestIterationFailed:
    @pytest.mark.asyncio
    async def test_child_failure_fails_loop(self, reviewer_agent):
        spec = _spec()
        inst = await start_loop(spec, HandoffEnvelope())

        result = await on_iteration_failed(inst.current_child_id, "subprocess crashed")
        assert result is not None
        assert result.status == LoopStatus.FAILED
        assert "subprocess crashed" in (result.error or "")
        parent = router_module._tasks[result.parent_task_id]
        assert parent.status == TaskStatus.FAILED

    @pytest.mark.asyncio
    async def test_unknown_child_is_noop(self):
        # No registered loop for this id — should silently return None
        assert await on_iteration_failed("ghost-child", "x") is None


# ---------------------------------------------------------------------------
# advance_loop validation
# ---------------------------------------------------------------------------


class TestAdvanceValidation:
    @pytest.mark.asyncio
    async def test_unknown_loop_raises(self):
        with pytest.raises(ValueError, match="not found"):
            await advance_loop("ghost-loop", HandoffEnvelope())

    @pytest.mark.asyncio
    async def test_already_converged_cannot_advance(self, reviewer_agent):
        spec = _spec()
        inst = await start_loop(spec, HandoffEnvelope())
        await advance_loop(inst.id, HandoffEnvelope(findings={"blockers": []}))
        with pytest.raises(ValueError, match="not active"):
            await advance_loop(inst.id, HandoffEnvelope())

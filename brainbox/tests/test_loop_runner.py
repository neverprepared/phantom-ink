"""Tests for the Loop runner core (markdown edition).

The runner now drives a parsed ``LoopMarkdown`` template. Stop and
escalation decisions are delegated to ``loop_judge.evaluate_stop`` and
``loop_judge.evaluate_escalation``, which dispatch real brainbox
sessions in production. Tests monkeypatch those two functions in
``brainbox.loop_runner`` with async fakes that return ``JudgeVerdict``s
so we never spin up a session.

Covers:
  - start_loop happy path: instance, parent task, first child enqueued
  - start_loop wiring: envelope stamping, template snapshot, mermaid,
    child→loop mapping
  - missing required_refs raises before any task is created
  - advance_loop CONVERGED (stop verdict fires)
  - advance_loop STOPPED_BY_CONDITION (escalation verdict fires)
  - advance_loop MAX_ITER (iteration cap reached, escalation fires
    with "iteration cap" reason)
  - advance_loop continue → next iteration enqueued, cost_history grows
  - advance_loop validation: unknown id, terminal id
  - cancel_loop: running → CANCELLED, terminal → noop, unknown raises
  - on_iteration_failed: child failure → loop FAILED, unknown child noop
  - child task carries loop_id / loop_iteration / permission_tier
"""

from __future__ import annotations

import pytest

import brainbox.loop_runner as runner
import brainbox.registry as reg_module
import brainbox.router as router_module
from brainbox.loop_judge import JudgeVerdict
from brainbox.loop_md import parse
from brainbox.loop_runner import (
    advance_loop,
    cancel_loop,
    get_instance,
    loop_id_for_child,
    on_iteration_failed,
    start_loop,
)
from brainbox.loops import HandoffEnvelope, LoopStatus
from brainbox.models import AgentDefinition, TaskStatus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def reviewer_agent():
    """Register the agent names the runner looks up.

    The runner now defaults to ``agent: worker`` when the template
    omits one, and start_loop validates that the agent exists in the
    registry. We register both ``worker`` (the default) and the
    legacy ``test-loop`` (used by a couple of tests that build a
    template with an explicit agent override) so every test path
    finds an agent."""
    worker = AgentDefinition(name="worker", image="test-image", capabilities=["hub_messaging"])
    reg_module._agents["worker"] = worker
    legacy = AgentDefinition(name="test-loop", image="test-image", capabilities=["hub_messaging"])
    reg_module._agents["test-loop"] = legacy
    return worker


def _template(
    *,
    name: str = "test-loop",
    max_iterations: int = 5,
    permissions: str | None = None,
    required_refs: list[dict] | None = None,
    objective: dict | None = None,
    budget_usd: float | None = None,
    agent: str | None = None,
) -> str:
    """Build a minimal valid markdown template, varying the fields the
    runner actually reads."""
    fm_lines = [
        f"name: {name}",
        "trigger: manual",
        f"max_iterations: {max_iterations}",
    ]
    if permissions is not None:
        fm_lines.append(f"permissions: {permissions}")
    if budget_usd is not None:
        fm_lines.append(f"budget_usd: {budget_usd}")
    if agent is not None:
        fm_lines.append(f"agent: {agent}")
    if required_refs:
        fm_lines.append("required_refs:")
        for ref in required_refs:
            fm_lines.append(f"  - name: {ref['name']}")
            if "required" in ref:
                fm_lines.append(f"    required: {str(ref['required']).lower()}")
    if objective:
        fm_lines.append("objective:")
        for k, v in objective.items():
            fm_lines.append(f"  {k}: {v}")

    fm = "\n".join(fm_lines)
    return (
        f"---\n{fm}\n---\n\n"
        "# Role\n"
        "You do a thing.\n\n"
        "# When to stop\n"
        "- The thing is done.\n\n"
        "# When to escalate\n"
        "- The thing breaks.\n"
    )


def _loop(**kwargs):
    return parse(_template(**kwargs))


@pytest.fixture
def patch_judge(monkeypatch):
    """Default: both judges return fired=False so the loop keeps iterating.
    Tests override individual calls by patching again after this fixture."""
    async def _no_stop(**_kwargs):
        return JudgeVerdict(fired=False, reason="not done", via="judge")

    async def _no_escalate(**_kwargs):
        return JudgeVerdict(fired=False, reason="ok", via="judge")

    monkeypatch.setattr(runner, "evaluate_stop", _no_stop)
    monkeypatch.setattr(runner, "evaluate_escalation", _no_escalate)
    return monkeypatch


def _set_stop(monkeypatch, *, fired: bool, reason: str = "done", via: str = "judge"):
    async def _stop(**_kwargs):
        return JudgeVerdict(fired=fired, reason=reason, via=via)

    monkeypatch.setattr(runner, "evaluate_stop", _stop)


def _set_escalation(monkeypatch, *, fired: bool, reason: str = "escalate", via: str = "judge"):
    async def _esc(**_kwargs):
        return JudgeVerdict(fired=fired, reason=reason, via=via)

    monkeypatch.setattr(runner, "evaluate_escalation", _esc)


# ---------------------------------------------------------------------------
# start_loop
# ---------------------------------------------------------------------------


class TestStartLoop:
    @pytest.mark.asyncio
    async def test_creates_instance_parent_and_first_child(self, reviewer_agent, patch_judge):
        inst = await start_loop(_loop(), HandoffEnvelope())

        assert inst.status == LoopStatus.RUNNING
        assert inst.iteration == 1
        assert inst.parent_task_id in router_module._tasks
        assert inst.current_child_id in router_module._tasks

        parent = router_module._tasks[inst.parent_task_id]
        child = router_module._tasks[inst.current_child_id]
        assert parent.status == TaskStatus.RUNNING
        assert child.status == TaskStatus.PENDING
        assert child.agent_name == "worker"  # default after agent-from-name → worker
        assert child.job_id == parent.id

    @pytest.mark.asyncio
    async def test_stamps_loop_id_and_iteration_on_envelope(self, reviewer_agent, patch_judge):
        inst = await start_loop(_loop(), HandoffEnvelope())
        assert inst.envelope.loop_id == inst.id
        assert inst.envelope.iteration == 0

    @pytest.mark.asyncio
    async def test_pins_template_snapshot(self, reviewer_agent, patch_judge):
        inst = await start_loop(_loop(), HandoffEnvelope())
        assert inst.template_name == "test-loop"
        assert inst.template_text  # non-empty raw markdown
        assert inst.template_hash  # non-empty content hash
        assert inst.mermaid  # rendered

    @pytest.mark.asyncio
    async def test_registers_child_to_loop_mapping(self, reviewer_agent, patch_judge):
        inst = await start_loop(_loop(), HandoffEnvelope())
        assert loop_id_for_child(inst.current_child_id) == inst.id

    @pytest.mark.asyncio
    async def test_rejects_missing_required_refs(self, reviewer_agent, patch_judge):
        loop = _loop(required_refs=[{"name": "pr_number"}, {"name": "repo"}])
        with pytest.raises(ValueError, match="pr_number|repo"):
            await start_loop(loop, HandoffEnvelope())

    @pytest.mark.asyncio
    async def test_accepts_when_all_required_refs_present(self, reviewer_agent, patch_judge):
        loop = _loop(required_refs=[{"name": "pr_number"}, {"name": "repo"}])
        env = HandoffEnvelope(artifact_refs={"pr_number": 117, "repo": "owner/name"})
        inst = await start_loop(loop, env)
        assert inst.status == LoopStatus.RUNNING

    @pytest.mark.asyncio
    async def test_optional_ref_can_be_missing(self, reviewer_agent, patch_judge):
        loop = _loop(required_refs=[
            {"name": "pr_number"},
            {"name": "head_sha", "required": False},
        ])
        env = HandoffEnvelope(artifact_refs={"pr_number": 117})
        inst = await start_loop(loop, env)
        assert inst.status == LoopStatus.RUNNING


# ---------------------------------------------------------------------------
# advance_loop — CONVERGED
# ---------------------------------------------------------------------------


class TestAdvanceConverged:
    @pytest.mark.asyncio
    async def test_stop_verdict_converges(self, reviewer_agent, patch_judge, monkeypatch):
        inst = await start_loop(_loop(), HandoffEnvelope())

        # After the iteration runs, judge says "done".
        _set_stop(monkeypatch, fired=True, reason="objective satisfied")

        result = await advance_loop(inst.id, HandoffEnvelope(findings={"blockers": []}))
        assert result.status == LoopStatus.CONVERGED
        assert result.stop_reason == "objective satisfied"
        assert result.current_child_id is None

        parent = router_module._tasks[result.parent_task_id]
        assert parent.status == TaskStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_convergence_clears_child_mapping(self, reviewer_agent, patch_judge, monkeypatch):
        inst = await start_loop(_loop(), HandoffEnvelope())
        old_child = inst.current_child_id

        _set_stop(monkeypatch, fired=True)
        await advance_loop(inst.id, HandoffEnvelope())
        assert loop_id_for_child(old_child) is None


# ---------------------------------------------------------------------------
# advance_loop — continue
# ---------------------------------------------------------------------------


class TestAdvanceContinue:
    @pytest.mark.asyncio
    async def test_neither_judge_fires_enqueues_next_iteration(
        self, reviewer_agent, patch_judge
    ):
        inst = await start_loop(_loop(max_iterations=10), HandoffEnvelope())
        original_child = inst.current_child_id

        result = await advance_loop(inst.id, HandoffEnvelope())

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
    async def test_cost_history_grows_across_iterations(self, reviewer_agent, patch_judge):
        inst = await start_loop(_loop(max_iterations=10), HandoffEnvelope())

        await advance_loop(inst.id, HandoffEnvelope())
        await advance_loop(inst.id, HandoffEnvelope())
        result = await advance_loop(inst.id, HandoffEnvelope())

        # iter cost is 0.0 each (session-based execution doesn't surface
        # tokens yet); shape check only.
        assert len(result.cost_history) == 3
        assert all(c == 0.0 for c in result.cost_history)
        assert result.cost_usd == 0.0
        assert result.status == LoopStatus.RUNNING


# ---------------------------------------------------------------------------
# advance_loop — MAX_ITER (iteration cap via escalation)
# ---------------------------------------------------------------------------


class TestAdvanceMaxIter:
    @pytest.mark.asyncio
    async def test_iteration_cap_marks_max_iter(self, reviewer_agent, patch_judge, monkeypatch):
        inst = await start_loop(_loop(max_iterations=2), HandoffEnvelope())  # iteration=1

        # iteration 1 → escalation says "iteration cap exceeded" (the
        # runner uses the substring "iteration cap" in the reason to
        # pick MAX_ITER over STOPPED_BY_CONDITION).
        _set_escalation(monkeypatch, fired=True, reason="iteration cap exceeded")

        result = await advance_loop(inst.id, HandoffEnvelope())
        assert result.status == LoopStatus.MAX_ITER
        assert result.current_child_id is None
        parent = router_module._tasks[result.parent_task_id]
        assert parent.status == TaskStatus.FAILED


# ---------------------------------------------------------------------------
# advance_loop — STOPPED_BY_CONDITION (escalation fires, not a cap)
# ---------------------------------------------------------------------------


class TestAdvanceEscalation:
    @pytest.mark.asyncio
    async def test_escalation_marks_stopped_by_condition(
        self, reviewer_agent, patch_judge, monkeypatch
    ):
        inst = await start_loop(_loop(max_iterations=10), HandoffEnvelope())

        _set_escalation(monkeypatch, fired=True, reason="diff too large")

        result = await advance_loop(inst.id, HandoffEnvelope())
        assert result.status == LoopStatus.STOPPED_BY_CONDITION
        assert result.stop_reason == "diff too large"
        parent = router_module._tasks[result.parent_task_id]
        assert parent.status == TaskStatus.FAILED

    @pytest.mark.asyncio
    async def test_stop_takes_precedence_over_escalation(
        self, reviewer_agent, patch_judge, monkeypatch
    ):
        """Per the runner's ordering, stop is checked before escalation —
        if both would fire, CONVERGED wins."""
        inst = await start_loop(_loop(), HandoffEnvelope())

        _set_stop(monkeypatch, fired=True, reason="done")
        _set_escalation(monkeypatch, fired=True, reason="also escalate")

        result = await advance_loop(inst.id, HandoffEnvelope())
        assert result.status == LoopStatus.CONVERGED
        assert result.stop_reason == "done"


# ---------------------------------------------------------------------------
# Child task loop context
# ---------------------------------------------------------------------------


class TestIterationChildLoopContext:
    @pytest.mark.asyncio
    async def test_first_child_has_loop_context_fields(
        self, reviewer_agent, patch_judge
    ):
        inst = await start_loop(_loop(), HandoffEnvelope())
        child = router_module._tasks[inst.current_child_id]
        assert child.loop_id == inst.id
        assert child.loop_iteration == 1
        # default permission tier
        assert child.permission_tier == "default"

    @pytest.mark.asyncio
    async def test_strict_permission_tier_propagates(
        self, reviewer_agent, patch_judge
    ):
        inst = await start_loop(_loop(permissions="strict"), HandoffEnvelope())
        child = router_module._tasks[inst.current_child_id]
        assert child.permission_tier == "strict"

    @pytest.mark.asyncio
    async def test_subsequent_iteration_has_correct_counter(
        self, reviewer_agent, patch_judge
    ):
        inst = await start_loop(_loop(max_iterations=10), HandoffEnvelope())
        await advance_loop(inst.id, HandoffEnvelope())
        # iter 2's child should carry loop_iteration == 2
        child = router_module._tasks[inst.current_child_id]
        assert child.loop_iteration == 2


# ---------------------------------------------------------------------------
# cancel_loop
# ---------------------------------------------------------------------------


class TestCancelLoop:
    @pytest.mark.asyncio
    async def test_cancel_running_loop(self, reviewer_agent, patch_judge):
        inst = await start_loop(_loop(), HandoffEnvelope())
        child_id = inst.current_child_id

        result = await cancel_loop(inst.id, reason="test")
        assert result.status == LoopStatus.CANCELLED
        assert result.error == "test"
        parent = router_module._tasks[result.parent_task_id]
        assert parent.status == TaskStatus.CANCELLED
        child = router_module._tasks[child_id]
        assert child.status == TaskStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_cancel_terminal_loop_is_noop(self, reviewer_agent, patch_judge, monkeypatch):
        inst = await start_loop(_loop(), HandoffEnvelope())
        _set_stop(monkeypatch, fired=True)
        await advance_loop(inst.id, HandoffEnvelope())
        # Already CONVERGED — cancel should return unchanged
        result = await cancel_loop(inst.id)
        assert result.status == LoopStatus.CONVERGED

    @pytest.mark.asyncio
    async def test_cancel_unknown_loop_raises(self):
        with pytest.raises(ValueError, match="not found"):
            await cancel_loop("ghost")


# ---------------------------------------------------------------------------
# on_iteration_failed
# ---------------------------------------------------------------------------


class TestIterationFailed:
    @pytest.mark.asyncio
    async def test_child_failure_fails_loop(self, reviewer_agent, patch_judge):
        inst = await start_loop(_loop(), HandoffEnvelope())

        result = await on_iteration_failed(inst.current_child_id, "subprocess crashed")
        assert result is not None
        assert result.status == LoopStatus.FAILED
        assert "subprocess crashed" in (result.error or "")
        parent = router_module._tasks[result.parent_task_id]
        assert parent.status == TaskStatus.FAILED

    @pytest.mark.asyncio
    async def test_unknown_child_is_noop(self):
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
    async def test_already_converged_cannot_advance(
        self, reviewer_agent, patch_judge, monkeypatch
    ):
        inst = await start_loop(_loop(), HandoffEnvelope())
        _set_stop(monkeypatch, fired=True)
        await advance_loop(inst.id, HandoffEnvelope())
        with pytest.raises(ValueError, match="not active"):
            await advance_loop(inst.id, HandoffEnvelope())

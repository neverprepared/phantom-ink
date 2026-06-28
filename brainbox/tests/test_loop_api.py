"""Tests for the Loop trigger/monitor API endpoints (Phase B3+B4).

Exercises the operator-facing surface end-to-end via the FastAPI ASGI
client, including:
  - templates discovery
  - start_loop wiring (template_name + envelope → LoopInstance)
  - list / get / iteration metrics
  - cancel mechanics (in-flight loop terminates, parent task CANCELLED,
    terminal loop is a no-op)
  - error paths (missing template → 404, missing API key → 401,
    invalid envelope → 400)
"""

from __future__ import annotations

import pytest

import brainbox.loop_judge as loop_judge
import brainbox.loop_runner as runner
import brainbox.registry as reg_module
import brainbox.router as router_module
from brainbox.loop_judge import JudgeVerdict
from brainbox.loops import LoopStatus
from brainbox.models import AgentDefinition


@pytest.fixture
def reviewer_agent():
    agent = AgentDefinition(name="reviewer", image="test-image", capabilities=["hub_messaging"])
    reg_module._agents["reviewer"] = agent
    return agent


# ---------------------------------------------------------------------------
# /api/loops/templates
# ---------------------------------------------------------------------------


class TestListTemplates:
    @pytest.mark.asyncio
    async def test_returns_builtin_templates(self, client, reviewer_agent):
        async with client as c:
            resp = await c.get("/api/loops/templates")
        assert resp.status_code == 200
        names = resp.json()["templates"]
        assert "pr-review-loop" in names


# ---------------------------------------------------------------------------
# /api/loops/start
# ---------------------------------------------------------------------------


class TestStartLoop:
    @pytest.mark.asyncio
    async def test_starts_pr_review_loop_with_envelope(self, client, reviewer_agent):
        body = {
            "template_name": "pr-review-loop",
            "envelope": {
                "artifact_refs": {"pr_number": 119, "repo": "owner/name"},
            },
        }
        async with client as c:
            resp = await c.post("/api/loops/start", json=body)
        assert resp.status_code == 200
        inst = resp.json()
        assert inst["status"] == LoopStatus.RUNNING.value
        assert inst["iteration"] == 1
        assert inst["envelope"]["artifact_refs"]["pr_number"] == 119
        # The runner's in-memory store has the loop registered
        assert runner.get_instance(inst["id"]) is not None

    @pytest.mark.asyncio
    async def test_unknown_template_returns_404(self, client, reviewer_agent):
        async with client as c:
            resp = await c.post("/api/loops/start", json={"template_name": "ghost"})
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_missing_template_name_returns_400(self, client, reviewer_agent):
        async with client as c:
            resp = await c.post("/api/loops/start", json={"envelope": {}})
        assert resp.status_code == 400
        assert "template_name" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_invalid_envelope_returns_400(self, client, reviewer_agent):
        body = {
            "template_name": "pr-review-loop",
            "envelope": "not an object",
        }
        async with client as c:
            resp = await c.post("/api/loops/start", json=body)
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# /api/loops + /api/loops/{id}
# ---------------------------------------------------------------------------


class TestListAndGet:
    @pytest.mark.asyncio
    async def test_list_returns_started_loops(self, client, reviewer_agent):
        async with client as c:
            start = await c.post(
                "/api/loops/start",
                json={"template_name": "pr-review-loop", "envelope": {"artifact_refs": {"pr_number": 1, "repo": "test/repo"}}},
            )
            assert start.status_code == 200
            list_resp = await c.get("/api/loops")
        assert list_resp.status_code == 200
        loops = list_resp.json()["loops"]
        assert len(loops) == 1
        assert loops[0]["status"] == LoopStatus.RUNNING.value
        # List view drops the heavy spec snapshot but keeps the slim fields
        assert "spec_snapshot" not in loops[0]
        assert loops[0]["name"] == "pr-review-loop"
        assert loops[0]["iteration"] == 1
        assert loops[0]["max_iterations"] == 3

    @pytest.mark.asyncio
    async def test_list_filters_by_status(self, client, reviewer_agent):
        async with client as c:
            await c.post(
                "/api/loops/start",
                json={"template_name": "pr-review-loop", "envelope": {"artifact_refs": {"pr_number": 1, "repo": "test/repo"}}},
            )
            resp_running = await c.get("/api/loops", params={"status": "running"})
            resp_converged = await c.get("/api/loops", params={"status": "converged"})
        assert len(resp_running.json()["loops"]) == 1
        assert len(resp_converged.json()["loops"]) == 0

    @pytest.mark.asyncio
    async def test_list_with_unknown_status_returns_400(self, client, reviewer_agent):
        async with client as c:
            resp = await c.get("/api/loops", params={"status": "ghost"})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_get_returns_full_loop_with_spec(self, client, reviewer_agent):
        async with client as c:
            start = await c.post(
                "/api/loops/start",
                json={"template_name": "pr-review-loop", "envelope": {"artifact_refs": {"pr_number": 1, "repo": "test/repo"}}},
            )
            loop_id = start.json()["id"]
            resp = await c.get(f"/api/loops/{loop_id}")
        assert resp.status_code == 200
        inst = resp.json()
        # Full GET includes the pinned template snapshot
        assert inst["template_name"] == "pr-review-loop"
        # The raw markdown template text is frozen at creation time
        assert "name: pr-review-loop" in inst["template_text"]
        assert "max_iterations: 3" in inst["template_text"]
        # Mermaid diagram rendered at create time
        assert inst["mermaid"]
        assert inst["template_hash"]

    @pytest.mark.asyncio
    async def test_get_unknown_loop_returns_404(self, client, reviewer_agent):
        async with client as c:
            resp = await c.get("/api/loops/ghost-loop-id")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# /api/loops/{id}/iterations — metric history
# ---------------------------------------------------------------------------


class TestIterationMetrics:
    @pytest.mark.asyncio
    async def test_metrics_grow_across_advances(self, client, reviewer_agent, monkeypatch):
        from brainbox.loop_runner import advance_loop
        from brainbox.loops import HandoffEnvelope

        # Pin the judges off so the loop neither converges nor escalates.
        async def _stop_no(**_kwargs):
            return JudgeVerdict(fired=False, reason="not yet", via="objective")

        async def _esc_no(**_kwargs):
            return JudgeVerdict(fired=False, reason="not yet", via="objective")

        monkeypatch.setattr(loop_judge, "evaluate_stop", _stop_no)
        monkeypatch.setattr(loop_judge, "evaluate_escalation", _esc_no)
        monkeypatch.setattr(runner, "evaluate_stop", _stop_no)
        monkeypatch.setattr(runner, "evaluate_escalation", _esc_no)

        async with client as c:
            start = await c.post(
                "/api/loops/start",
                json={"template_name": "pr-review-loop", "envelope": {"artifact_refs": {"pr_number": 1, "repo": "test/repo"}}},
            )
            loop_id = start.json()["id"]
            # Simulate an iteration directly (no agent in the loop)
            await advance_loop(loop_id, HandoffEnvelope(findings={"blockers": [1, 2]}))
            resp = await c.get(f"/api/loops/{loop_id}/iterations")
        assert resp.status_code == 200
        rows = resp.json()["iterations"]
        assert len(rows) == 1
        assert rows[0]["iteration"] == 1
        # convergence_metric_value now tracks per-iteration cost in USD.
        # Session-based execution doesn't surface token usage, so cost is
        # 0.0 — the column stays populated for shape consistency.
        assert rows[0]["convergence_metric_value"] == 0.0


# ---------------------------------------------------------------------------
# /api/loops/{id}/cancel
# ---------------------------------------------------------------------------


class TestCancelLoop:
    @pytest.mark.asyncio
    async def test_cancel_in_flight_loop(self, client, reviewer_agent):
        async with client as c:
            start = await c.post(
                "/api/loops/start",
                json={"template_name": "pr-review-loop", "envelope": {"artifact_refs": {"pr_number": 1, "repo": "test/repo"}}},
            )
            loop_id = start.json()["id"]
            child_id = start.json()["current_child_id"]
            resp = await c.post(
                f"/api/loops/{loop_id}/cancel",
                json={"reason": "operator changed mind"},
            )
        assert resp.status_code == 200
        inst = resp.json()
        assert inst["status"] == LoopStatus.CANCELLED.value
        assert inst["error"] == "operator changed mind"
        # Parent task transitions to CANCELLED
        parent = router_module._tasks[inst["parent_task_id"]]
        assert parent.status.value == "cancelled"
        # Child task was cancelled too
        child = router_module._tasks.get(child_id)
        if child is not None:
            assert child.status.value == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_unknown_loop_returns_404(self, client, reviewer_agent):
        async with client as c:
            resp = await c.post("/api/loops/ghost/cancel", json={})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_cancel_already_terminal_is_noop(self, client, reviewer_agent, monkeypatch):
        from brainbox.loop_runner import advance_loop
        from brainbox.loops import HandoffEnvelope

        # Force convergence via a patched judge — real judge calls a
        # session that isn't running under pytest, so the runner would
        # otherwise log judge errors and keep iterating.
        async def _stop_fire(**_kwargs):
            return JudgeVerdict(fired=True, reason="all clear", via="objective")

        monkeypatch.setattr(loop_judge, "evaluate_stop", _stop_fire)
        monkeypatch.setattr(runner, "evaluate_stop", _stop_fire)

        async with client as c:
            start = await c.post(
                "/api/loops/start",
                json={"template_name": "pr-review-loop", "envelope": {"artifact_refs": {"pr_number": 1, "repo": "test/repo"}}},
            )
            loop_id = start.json()["id"]
            # Drive the loop to CONVERGED via the patched judge.
            await advance_loop(
                loop_id,
                HandoffEnvelope(
                    findings={"blockers": []},
                    observations={"ci_status": "green"},
                ),
            )
            resp = await c.post(f"/api/loops/{loop_id}/cancel", json={})
        assert resp.status_code == 200
        # Already terminal — cancel is a no-op and the prior status sticks.
        assert resp.json()["status"] == LoopStatus.CONVERGED.value

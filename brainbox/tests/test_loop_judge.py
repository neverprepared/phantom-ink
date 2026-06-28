"""Tests for the loop judge — both the objective fast path and the
prose-via-session slow path.

Layers:

  - eval_objective: pure-Python deterministic checks against the
    envelope. No I/O, no LLM, no session — these are the cheap
    short-circuit before the judge dispatches.
  - evaluate_stop / evaluate_escalation: the public async entrypoints.
    Session dispatch is mocked (no real brainbox needed) so we can
    exercise objective short-circuiting, judge-error handling, and JSON
    extraction without standing up a container.
"""

from __future__ import annotations

import pytest

import brainbox.loop_judge as loop_judge
from brainbox.loop_judge import (
    JudgeError,
    JudgeVerdict,
    eval_objective,
    evaluate_escalation,
    evaluate_stop,
)


# ---------------------------------------------------------------------------
# Objective fast path
# ---------------------------------------------------------------------------


class TestEvalObjective:
    def test_no_checks_does_not_fire(self):
        v = eval_objective({"a": 1}, {})
        assert v.fired is False
        assert v.via == "missing"

    def test_equality_passes(self):
        v = eval_objective({"observations": {"ci_status": "green"}}, {"observations.ci_status": "green"})
        assert v.fired is True
        assert v.via == "objective"

    def test_equality_fails(self):
        v = eval_objective({"observations": {"ci_status": "red"}}, {"observations.ci_status": "green"})
        assert v.fired is False
        assert "observations.ci_status" in v.reason

    def test_truthy_expected_true(self):
        v = eval_objective({"findings": {"approved": True}}, {"findings.approved": True})
        assert v.fired is True

    def test_truthy_expected_true_for_falsy_value(self):
        v = eval_objective({"findings": {"approved": False}}, {"findings.approved": True})
        assert v.fired is False

    def test_ge_comparison(self):
        v = eval_objective({"n": 5}, {"n": {">=": 5}})
        assert v.fired is True
        v = eval_objective({"n": 4}, {"n": {">=": 5}})
        assert v.fired is False

    def test_le_comparison(self):
        v = eval_objective({"diff_lines": 200}, {"diff_lines": {"<=": 500}})
        assert v.fired is True
        v = eval_objective({"diff_lines": 600}, {"diff_lines": {"<=": 500}})
        assert v.fired is False

    def test_in_comparison(self):
        v = eval_objective({"status": "green"}, {"status": {"in": ["green", "yellow"]}})
        assert v.fired is True
        v = eval_objective({"status": "red"}, {"status": {"in": ["green", "yellow"]}})
        assert v.fired is False

    def test_not_empty(self):
        v = eval_objective({"items": [1, 2]}, {"items": {"not_empty": True}})
        assert v.fired is True
        v = eval_objective({"items": []}, {"items": {"not_empty": True}})
        assert v.fired is False

    def test_missing_path_is_falsy(self):
        v = eval_objective({}, {"a.b.c": "expected"})
        assert v.fired is False

    def test_all_checks_must_pass(self):
        # Two checks; one fails — the whole verdict does not fire.
        env = {"observations": {"ci_status": "green"}, "findings": {"approved": False}}
        v = eval_objective(env, {
            "observations.ci_status": "green",
            "findings.approved": True,
        })
        assert v.fired is False
        assert "findings.approved" in v.reason

    def test_dotted_walk_into_dicts(self):
        env = {"a": {"b": {"c": 42}}}
        v = eval_objective(env, {"a.b.c": 42})
        assert v.fired is True


# ---------------------------------------------------------------------------
# Prose judge — session path is mocked
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_judge(monkeypatch):
    """Short-circuit _with_judge_session + _query_session so we exercise
    the judge logic without standing up a real brainbox session.

    Returns a list the test populates with replies; each await consumes
    one entry."""
    replies: list[str] = []

    async def _fake_with_session(fn):
        return await fn(object(), "fake-judge-session")

    async def _fake_query(client, name, *, system, user):
        if not replies:
            raise AssertionError("test ran out of fake judge replies")
        return replies.pop(0)

    monkeypatch.setattr(loop_judge, "_with_judge_session", _fake_with_session)
    monkeypatch.setattr(loop_judge, "_query_session", _fake_query)
    return replies


class TestEvaluateStop:
    async def test_objective_short_circuits_no_judge_call(self, fake_judge):
        # If objective passes, the judge is never consulted — empty
        # replies list confirms.
        v = await evaluate_stop(
            envelope={"observations": {"ci_status": "green"}, "findings": {"approved": True}},
            objective={
                "observations.ci_status": "green",
                "findings.approved": True,
            },
            stop_prose="ignored when objective passes",
        )
        assert v.fired is True
        assert v.via == "objective"
        assert fake_judge == []  # no replies were consumed

    async def test_judge_consulted_when_objective_unmet(self, fake_judge):
        fake_judge.append('{"done": true, "reason": "looks good"}')
        v = await evaluate_stop(
            envelope={"observations": {"ci_status": "red"}},
            objective={"observations.ci_status": "green"},
            stop_prose="Stop when ci is green",
        )
        assert v.fired is True
        assert v.via == "judge"
        assert "looks good" in v.reason

    async def test_judge_says_not_done(self, fake_judge):
        fake_judge.append('{"done": false, "reason": "blockers remain"}')
        v = await evaluate_stop(
            envelope={},
            objective={},
            stop_prose="Stop when no blockers",
        )
        assert v.fired is False
        assert v.via == "judge"
        assert "blockers remain" in v.reason

    async def test_empty_prose_and_no_objective(self, fake_judge):
        # Nothing to check at all — return the harmless not-fired verdict
        # without calling the judge.
        v = await evaluate_stop(envelope={}, objective={}, stop_prose="")
        assert v.fired is False
        assert fake_judge == []

    async def test_judge_error_does_not_terminate_loop(self, monkeypatch):
        async def _boom(fn):
            raise JudgeError("session provisioning failed")

        monkeypatch.setattr(loop_judge, "_with_judge_session", _boom)
        v = await evaluate_stop(
            envelope={},
            objective={},
            stop_prose="Stop when done",
        )
        # Default to "not done" on judge errors — keep iterating until
        # max_iterations bails us out. This is the right failure mode:
        # silent termination on transient infra failure would lose work.
        assert v.fired is False
        assert v.via == "judge-error"

    async def test_unparseable_judge_reply_does_not_fire(self, fake_judge):
        fake_judge.append("the model wrote a haiku and forgot the JSON")
        v = await evaluate_stop(
            envelope={},
            objective={},
            stop_prose="Stop when",
        )
        assert v.fired is False  # default to safer non-firing


class TestEvaluateEscalation:
    async def test_iteration_cap_fires_objectively(self, fake_judge):
        v = await evaluate_escalation(
            envelope={},
            escalation_prose="ignored",
            iteration=3,
            max_iterations=3,
            cost_usd=0.0,
            budget_usd=None,
        )
        assert v.fired is True
        assert v.via == "objective"
        assert "iteration cap" in v.reason
        assert fake_judge == []  # no judge call

    async def test_budget_exceeded_fires_objectively(self, fake_judge):
        v = await evaluate_escalation(
            envelope={},
            escalation_prose="ignored",
            iteration=1,
            max_iterations=10,
            cost_usd=5.0,
            budget_usd=2.0,
        )
        assert v.fired is True
        assert v.via == "objective"
        assert "budget" in v.reason

    async def test_no_budget_means_no_budget_check(self, fake_judge):
        # budget_usd=None must not crash on comparison.
        fake_judge.append('{"escalate": false, "reason": "fine"}')
        v = await evaluate_escalation(
            envelope={},
            escalation_prose="Page on doom",
            iteration=1,
            max_iterations=10,
            cost_usd=999.0,
            budget_usd=None,
        )
        assert v.fired is False

    async def test_prose_judge_says_escalate(self, fake_judge):
        fake_judge.append('{"escalate": true, "reason": "touched /security/"}')
        v = await evaluate_escalation(
            envelope={"observations": {"files_touched": ["/security/keys.py"]}},
            escalation_prose="Page on /security/ touches",
            iteration=1,
            max_iterations=10,
            cost_usd=0.5,
            budget_usd=2.0,
        )
        assert v.fired is True
        assert v.via == "judge"
        assert "security" in v.reason

    async def test_no_clauses_does_not_fire(self, fake_judge):
        v = await evaluate_escalation(
            envelope={},
            escalation_prose="",
            iteration=1,
            max_iterations=10,
            cost_usd=0.0,
            budget_usd=2.0,
        )
        assert v.fired is False
        assert v.via == "missing"


class TestJudgeVerdictShape:
    def test_verdict_is_dataclass(self):
        v = JudgeVerdict(fired=True, reason="x", via="objective")
        assert v.fired is True
        assert v.reason == "x"
        assert v.via == "objective"

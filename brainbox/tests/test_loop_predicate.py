"""Cross-language contract tests for the Loop predicate language.

Mirrors app/loop_predicate_test.go — same expressions, same envelope shapes,
same coercion expectations. If a test changes here, the matching test must
change on the Go side too. If the contract diverges, predicate authoring
becomes a per-side guessing game and we lose the "one language across the
runner" invariant.

Tests also cover the additional Python-only surface: pydantic-model envelopes
(round-trip through model_dump), nested findings shapes the first
review-driven loop will use.
"""

from __future__ import annotations

import pytest

from brainbox.loop_predicate import eval_metric, eval_predicate
from brainbox.loops import HandoffEnvelope


def test_predicate_empty_expression_is_always_true():
    assert eval_predicate({}, "") is True


def test_predicate_blocker_count_comparisons():
    env = HandoffEnvelope(
        findings={
            "blockers": [
                {"file": "a.go", "line": 1},
                {"file": "b.go", "line": 2},
            ],
        },
    )
    cases = [
        ("length(findings.blockers) > `0`", True),
        ("length(findings.blockers) == `0`", False),
        ("length(findings.blockers) >= `2`", True),
        ("length(findings.blockers) > `5`", False),
    ]
    for expr, want in cases:
        got = eval_predicate(env, expr)
        assert got is want, f"expr {expr!r} expected {want}, got {got}"


def test_predicate_missing_field_is_false():
    env = HandoffEnvelope()
    assert eval_predicate(env, "findings.approved") is False


def test_predicate_string_equality():
    env = HandoffEnvelope(observations={"ci_status": "green"})
    assert eval_predicate(env, "observations.ci_status == 'green'") is True
    assert eval_predicate(env, "observations.ci_status == 'red'") is False


def test_predicate_boolean_field():
    env = HandoffEnvelope(findings={"approved": True})
    assert eval_predicate(env, "findings.approved") is True


def test_predicate_malformed_expression_raises():
    with pytest.raises(ValueError):
        eval_predicate({}, "this is not jmespath !!")


def test_predicate_accepts_plain_dict_envelope():
    env = {"findings": {"blockers": [1, 2, 3]}}
    assert eval_predicate(env, "length(findings.blockers) > `2`") is True


def test_metric_blocker_count():
    env = HandoffEnvelope(
        findings={
            "blockers": [
                {"file": "a.go"},
                {"file": "b.go"},
                {"file": "c.go"},
            ],
        },
    )
    assert eval_metric(env, "length(findings.blockers)") == 3.0


def test_metric_missing_field_is_zero():
    env = HandoffEnvelope()
    assert eval_metric(env, "observations.diff_lines") == 0.0


def test_metric_empty_expression_errors():
    with pytest.raises(ValueError, match="empty metric"):
        eval_metric(HandoffEnvelope(), "")


def test_metric_non_numeric_errors():
    env = HandoffEnvelope(observations={"ci_status": "green"})
    with pytest.raises(ValueError, match="non-numeric"):
        eval_metric(env, "observations.ci_status")


# ---------------------------------------------------------------------------
# Convergence-predicate shapes the pr-review-loop template will use
# ---------------------------------------------------------------------------


def test_convergence_predicate_canonical_review_loop_shape():
    expr = "length(findings.blockers) == `0` && observations.ci_status == 'green'"

    converged = HandoffEnvelope(
        findings={"blockers": []},
        observations={"ci_status": "green"},
    )
    assert eval_predicate(converged, expr) is True

    still_blocked = HandoffEnvelope(
        findings={"blockers": [{"file": "a.go"}]},
        observations={"ci_status": "green"},
    )
    assert eval_predicate(still_blocked, expr) is False

    ci_red = HandoffEnvelope(
        findings={"blockers": []},
        observations={"ci_status": "red"},
    )
    assert eval_predicate(ci_red, expr) is False

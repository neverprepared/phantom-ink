"""Tests for the loop_runner._envelope_from_task bridge parser (Phase B2).

The bridge accepts four shapes for task.result and must never raise — a
malformed result quietly becomes an empty envelope, the runner falls into
the next predicate evaluation, and the operator sees a non-converging loop
instead of a crashed daemon.

Five canonical shapes covered:
  - HandoffEnvelope instance (direct typed return)
  - dict that validates (structured hub message)
  - JSON string (the existing complete.sh path; agent writes envelope to
    a file and calls `complete.sh "$(cat /tmp/loop-envelope.json)"`)
  - malformed JSON string (falls through to empty)
  - plain text / None / other (falls through to empty)
"""

from __future__ import annotations

import json

from brainbox.loop_runner import _envelope_from_task
from brainbox.loops import HandoffEnvelope
from brainbox.models import Task, TaskStatus


def _task(result):
    return Task(
        id="t-1",
        description="x",
        agent_name="reviewer",
        status=TaskStatus.COMPLETED,
        created_at=1,
        updated_at=1,
        result=result,
    )


class TestEnvelopeShapes:
    def test_handoff_envelope_returns_as_is(self):
        env = HandoffEnvelope(findings={"blockers": [1, 2]})
        result = _envelope_from_task(_task(env))
        assert result is env

    def test_dict_validates_into_envelope(self):
        result = _envelope_from_task(_task({
            "findings": {"blockers": [{"file": "a.go"}]},
            "observations": {"ci_status": "green"},
        }))
        assert result.findings["blockers"][0]["file"] == "a.go"
        assert result.observations["ci_status"] == "green"

    def test_json_string_parses_then_validates(self):
        envelope_dict = {
            "findings": {"blockers": [], "approved": True},
            "observations": {"ci_status": "green", "diff_lines": 12},
        }
        result = _envelope_from_task(_task(json.dumps(envelope_dict)))
        assert result.findings["approved"] is True
        assert result.observations["ci_status"] == "green"

    def test_json_string_from_reviewer_envelope_file(self):
        # The canonical reviewer.md path: agent writes the envelope to a
        # file and passes its contents to complete.sh. Matches the schema
        # the pr-review-loop template's convergence predicate reads.
        envelope_json = """\
        {
          "findings": {"blockers": [], "approved": true},
          "observations": {"ci_status": "green", "diff_lines": 47}
        }"""
        result = _envelope_from_task(_task(envelope_json))
        assert result.findings["approved"] is True
        assert len(result.findings["blockers"]) == 0
        assert result.observations["ci_status"] == "green"


class TestFallthroughShapes:
    def test_none_returns_empty_envelope(self):
        result = _envelope_from_task(_task(None))
        assert result.findings == {}
        assert result.observations == {}

    def test_plain_text_returns_empty_envelope(self):
        # Legacy "complete.sh 'review done'" path — string is not JSON,
        # bridge must not raise. Empty envelope means the runner reads
        # zero blockers from a missing field, which is the right behavior
        # for "agent said done but didn't tell me anything structured."
        result = _envelope_from_task(_task("Review complete. Looks fine."))
        assert result.findings == {}

    def test_malformed_json_string_returns_empty_envelope(self):
        result = _envelope_from_task(_task("{not valid json"))
        assert result.findings == {}

    def test_json_array_not_object_returns_empty(self):
        # Valid JSON but not a dict; HandoffEnvelope can't be built from a list.
        result = _envelope_from_task(_task("[1, 2, 3]"))
        assert result.findings == {}

    def test_dict_with_unknown_fields_validates_to_partial_envelope(self):
        # Forward-compat: an envelope with extra fields (future schema version)
        # validates and keeps the known fields. Unknown fields are dropped at
        # the pydantic boundary, not at the bridge.
        result = _envelope_from_task(_task({
            "findings": {"blockers": []},
            "future_field": {"new": "thing"},
        }))
        assert result.findings == {"blockers": []}

    def test_int_result_returns_empty_envelope(self):
        # Defensive: a numeric result shouldn't crash the bridge.
        result = _envelope_from_task(_task(42))
        assert result.findings == {}


class TestRegressionCanonicalShapes:
    """The bridge already supported HandoffEnvelope + dict in A3c — these
    tests pin that those shapes still work after the string-JSON branch
    was added. If anyone restructures _envelope_from_task, the dict and
    typed-envelope shapes must still take precedence over the string path."""

    def test_handoff_envelope_takes_precedence_over_string_serialization(self):
        env = HandoffEnvelope(findings={"blockers": [1]})
        result = _envelope_from_task(_task(env))
        assert isinstance(result, HandoffEnvelope)
        assert result.findings["blockers"] == [1]

    def test_dict_with_required_envelope_shape_validates(self):
        result = _envelope_from_task(_task({
            "loop_id": "loop-abc",
            "iteration": 3,
            "findings": {"blockers": []},
        }))
        assert result.loop_id == "loop-abc"
        assert result.iteration == 3

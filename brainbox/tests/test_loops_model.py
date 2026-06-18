"""Tests for the LoopSpec / HandoffEnvelope / LoopInstance pydantic mirrors.

Covers the validation guarantees the runner relies on:
- LoopSpec REQUIRES a convergence predicate (either Intent.convergence or
  LoopSpec.convergence_predicate). Forcing function for rigorous intent —
  Kilo names vague intent as the root cause of thrashing.
- HandoffEnvelope round-trips through JSON without losing fields.
- The schema_version is stamped on every fresh envelope.
- Edge aliases ``from`` -> ``from_`` (Python keyword clash) round-trip cleanly.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from brainbox.loops import (
    ENVELOPE_SCHEMA_VERSION,
    Body,
    Edge,
    EdgeTransform,
    HandoffEnvelope,
    Intent,
    LoopInstance,
    LoopSpec,
    LoopStatus,
    Node,
    NodeExecutor,
    NodeKind,
    PermissionTier,
    StopCondition,
)


# ---------------------------------------------------------------------------
# Intent + LoopSpec — the convergence-required forcing function
# ---------------------------------------------------------------------------


def _minimal_spec(**overrides) -> LoopSpec:
    return LoopSpec(
        intent=overrides.pop(
            "intent",
            Intent(outcome="x", convergence="length(findings.blockers) == `0`"),
        ),
        body=overrides.pop("body", Body()),
        **overrides,
    )


def test_loopspec_inherits_convergence_from_intent():
    spec = _minimal_spec()
    assert spec.convergence_predicate == "length(findings.blockers) == `0`"


def test_loopspec_explicit_convergence_predicate_overrides_intent():
    # Either-or: an explicit override on LoopSpec wins over Intent.convergence.
    spec = _minimal_spec(
        intent=Intent(outcome="x", convergence="iteration >= `1`"),
        convergence_predicate="findings.approved",
    )
    assert spec.convergence_predicate == "findings.approved"


def test_loopspec_fails_without_any_convergence():
    with pytest.raises(ValueError, match="convergence predicate"):
        LoopSpec(
            intent=Intent(outcome="x", convergence=""),
            body=Body(),
        )


def test_loopspec_intent_convergence_required_by_pydantic():
    # Intent.convergence is a required field on the pydantic model itself —
    # not just a runtime check. Templates must declare it on disk.
    with pytest.raises(ValidationError):
        Intent(outcome="x")  # type: ignore[call-arg]


def test_loopspec_defaults_permission_tier_to_default():
    spec = _minimal_spec()
    assert spec.permissions == PermissionTier.DEFAULT


def test_loopspec_default_max_iterations_is_five():
    # Conservative cap — start small, raise if the first review-driven loop
    # convergence rate calls for it.
    spec = _minimal_spec()
    assert spec.max_iterations == 5


# ---------------------------------------------------------------------------
# Body / Node / Edge
# ---------------------------------------------------------------------------


def test_node_defaults_to_host_cli_agent():
    n = Node(id="step1")
    assert n.kind == NodeKind.AGENT
    assert n.executor == NodeExecutor.HOST_CLI


def test_edge_serializes_with_from_alias():
    # `from` is a Python keyword; the field is `from_` internally but must
    # serialize as `"from"` so the JSON shape matches the Go-side Edge.
    e = Edge(from_="reviewer", to="worker", predicate="length(findings.blockers) > `0`")
    dumped = e.model_dump(by_alias=True)
    assert dumped["from"] == "reviewer"
    assert dumped["to"] == "worker"
    assert "from_" not in dumped


def test_edge_round_trips_through_json():
    e = Edge(
        from_="reviewer",
        to="worker",
        predicate="findings.approved == `false`",
        transform=EdgeTransform(select=["findings.blockers"], omit=["scope_grants"]),
    )
    wire = json.dumps(e.model_dump(by_alias=True))
    reloaded = Edge.model_validate(json.loads(wire))
    assert reloaded.from_ == "reviewer"
    assert reloaded.predicate == "findings.approved == `false`"
    assert reloaded.transform is not None
    assert reloaded.transform.select == ["findings.blockers"]


def test_edge_predicate_defaults_to_always_fire():
    e = Edge(from_="a", to="b")
    assert e.predicate == ""


# ---------------------------------------------------------------------------
# HandoffEnvelope — additive-only discipline + schema_version + round-trip
# ---------------------------------------------------------------------------


def test_envelope_default_schema_version_is_current():
    env = HandoffEnvelope()
    assert env.schema_version == ENVELOPE_SCHEMA_VERSION


def test_envelope_round_trips_through_json_without_field_loss():
    env = HandoffEnvelope(
        loop_id="loop-abc",
        iteration=3,
        from_node="reviewer",
        to_node="worker",
        artifact_refs={"pr_number": 123, "repo": "neverprepared/phantom-ink"},
        observations={"ci_status": "green", "diff_lines": 47},
        findings={"blockers": [{"file": "a.go", "line": 12}], "approved": False},
        memory_refs=["wiki/foo", "wiki/bar"],
        trace_id="trace-xyz",
        scope_grants={"github_token": "redacted"},
        context_carry={"prior_attempts": 2},
    )
    wire = json.dumps(env.model_dump())
    reloaded = HandoffEnvelope.model_validate(json.loads(wire))
    assert reloaded.iteration == 3
    assert reloaded.findings["approved"] is False
    assert reloaded.artifact_refs["pr_number"] == 123
    assert reloaded.memory_refs == ["wiki/foo", "wiki/bar"]


def test_envelope_accepts_unknown_extra_fields_gracefully():
    # Additive-only discipline implies forward-compat: an envelope authored
    # against schema_version N+1 (with new optional fields) must NOT crash a
    # runner at schema_version N. Currently pydantic's default is "ignore
    # extras" — this test pins that behavior so a future model_config change
    # to "forbid" would visibly fail here.
    wire = json.dumps({
        "schema_version": ENVELOPE_SCHEMA_VERSION + 1,
        "loop_id": "x",
        "iteration": 0,
        "future_field_not_in_v1": {"surprise": True},
    })
    reloaded = HandoffEnvelope.model_validate(json.loads(wire))
    assert reloaded.loop_id == "x"
    assert reloaded.schema_version == ENVELOPE_SCHEMA_VERSION + 1


# ---------------------------------------------------------------------------
# LoopInstance — basic shape sanity
# ---------------------------------------------------------------------------


def test_loop_instance_starts_pending():
    spec = _minimal_spec()
    inst = LoopInstance(
        id="loop-1",
        spec_snapshot=spec,
        parent_task_id="task-parent",
        envelope=HandoffEnvelope(loop_id="loop-1"),
        created_at=1,
        updated_at=1,
    )
    assert inst.status == LoopStatus.PENDING
    assert inst.iteration == 0
    assert inst.metric_history == []
    assert inst.current_child_id is None
    assert inst.stop_reason is None


def test_stop_condition_carries_reason_tag():
    sc = StopCondition(
        predicate="observations.diff_lines > `1000`",
        reason="diff_too_large",
    )
    assert sc.reason == "diff_too_large"


# ---------------------------------------------------------------------------
# RequiredRef + LoopSpec.required_refs
# ---------------------------------------------------------------------------


def test_loopspec_required_refs_defaults_to_empty():
    spec = _minimal_spec()
    assert spec.required_refs == []


def test_loopspec_accepts_required_refs_declarations():
    from brainbox.loops import RequiredRef, RequiredRefType

    spec = _minimal_spec()
    spec = LoopSpec(
        intent=Intent(outcome="x", convergence="`true`"),
        body=Body(nodes=[Node(id="n", role="reviewer")]),
        required_refs=[
            RequiredRef(name="pr_number", type=RequiredRefType.INT,
                        description="GitHub PR number"),
            RequiredRef(name="repo", type=RequiredRefType.STRING,
                        description="owner/name"),
            RequiredRef(name="head_sha", type=RequiredRefType.SHA,
                        description="head commit", required=False),
        ],
    )
    assert len(spec.required_refs) == 3
    assert spec.required_refs[0].name == "pr_number"
    assert spec.required_refs[0].type == RequiredRefType.INT
    assert spec.required_refs[2].required is False


def test_required_ref_defaults():
    from brainbox.loops import RequiredRef, RequiredRefType

    ref = RequiredRef(name="x")
    assert ref.type == RequiredRefType.STRING
    assert ref.required is True
    assert ref.description == ""

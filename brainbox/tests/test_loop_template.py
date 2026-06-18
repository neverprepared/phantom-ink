"""Tests for the Loop template loader (Phase B1).

Two layers:
  1. Parser correctness — frontmatter extraction, YAML errors surfaced
     with useful messages, missing-frontmatter rejected loud, LoopSpec
     validation forwarded through TemplateError.
  2. The bundled pr-review-loop template loads cleanly and matches the
     plan's stated convergence predicate / metric / iteration cap.

A separate integration test feeds the loaded template through
start_loop to confirm the on-disk shape is what the runner actually
consumes — the template is the *contract* between authoring and
execution; if it deserializes but start_loop can't accept it, the
template is broken regardless of YAML correctness.
"""

from __future__ import annotations

import pytest

import brainbox.registry as reg_module
from brainbox.loop_runner import start_loop
from brainbox.loop_template import (
    TemplateError,
    list_templates,
    load_template,
    parse_template,
    template_path,
)
from brainbox.loops import HandoffEnvelope, LoopStatus, NodeExecutor, NodeKind
from brainbox.models import AgentDefinition


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------


_MINIMAL_VALID = """---
name: minimal
intent:
  outcome: be done
  convergence: "`true`"
body:
  nodes:
    - id: only
      role: reviewer
convergence_metric: "`0`"
---

# minimal

doc body
"""


class TestFrontmatterParsing:
    def test_minimal_template_loads(self):
        spec = parse_template(_MINIMAL_VALID)
        assert spec.name == "minimal"
        assert spec.body.nodes[0].id == "only"

    def test_missing_opening_fence_rejected(self):
        with pytest.raises(TemplateError, match="frontmatter fence"):
            parse_template("name: x\n")

    def test_missing_closing_fence_rejected(self):
        with pytest.raises(TemplateError, match="closing"):
            parse_template("---\nname: x\nno closing fence here")

    def test_invalid_yaml_rejected_with_useful_message(self):
        bad = "---\nname: [unclosed\n---\nbody\n"
        with pytest.raises(TemplateError, match="not valid YAML"):
            parse_template(bad)

    def test_non_mapping_frontmatter_rejected(self):
        bad = "---\n- just\n- a\n- list\n---\nbody\n"
        with pytest.raises(TemplateError, match="mapping"):
            parse_template(bad)

    def test_loopspec_validation_forwarded(self):
        # No convergence anywhere → LoopSpec validator fails →
        # surfaces as TemplateError, not raw pydantic.
        bad = """---
name: no-convergence
intent:
  outcome: x
body:
  nodes:
    - id: x
      role: reviewer
---
"""
        with pytest.raises(TemplateError, match="validation"):
            parse_template(bad)


# ---------------------------------------------------------------------------
# Built-in template discovery
# ---------------------------------------------------------------------------


class TestBuiltinTemplates:
    def test_list_includes_pr_review_loop(self):
        names = list_templates()
        assert "pr-review-loop" in names

    def test_template_path_resolves_pr_review_loop(self):
        path = template_path("pr-review-loop")
        assert path is not None
        assert path.name == "pr-review-loop.md"

    def test_unknown_template_path_is_none(self):
        assert template_path("does-not-exist") is None

    def test_load_unknown_template_raises(self):
        with pytest.raises(TemplateError, match="not found"):
            load_template("does-not-exist")


# ---------------------------------------------------------------------------
# pr-review-loop content invariants
# ---------------------------------------------------------------------------


class TestPRReviewLoopContent:
    def test_loads_and_validates(self):
        spec = load_template("pr-review-loop")
        assert spec.name == "pr-review-loop"

    def test_convergence_predicate_matches_plan(self):
        spec = load_template("pr-review-loop")
        # Plan: length(findings.blockers) == 0 && observations.ci_status == 'green'
        assert "findings.blockers" in spec.convergence_predicate
        assert "ci_status" in spec.convergence_predicate

    def test_convergence_metric_is_blocker_count(self):
        spec = load_template("pr-review-loop")
        assert spec.convergence_metric == "length(findings.blockers)"

    def test_conservative_iteration_cap(self):
        # Plan starts max_iterations at 3 — explicit so the test fails
        # if someone bumps it without thinking about thrash semantics.
        spec = load_template("pr-review-loop")
        assert spec.max_iterations == 3

    def test_has_diff_size_stop_condition(self):
        spec = load_template("pr-review-loop")
        reasons = [sc.reason for sc in spec.stop_conditions]
        assert "diff_too_large" in reasons

    def test_reviewer_node_is_brainbox_session(self):
        spec = load_template("pr-review-loop")
        reviewer = spec.body.nodes[0]
        assert reviewer.id == "reviewer"
        assert reviewer.kind == NodeKind.AGENT
        assert reviewer.executor == NodeExecutor.BRAINBOX_SESSION
        assert reviewer.role == "reviewer"

    def test_reviewer_node_requires_repo_read_only(self):
        # default permission tier means destructive scopes require explicit
        # listing — reviewer should NOT request repo:write or merge.
        spec = load_template("pr-review-loop")
        reviewer = spec.body.nodes[0]
        assert "repo:read" in reviewer.requires
        assert not any(r.startswith("repo:write") for r in reviewer.requires)


# ---------------------------------------------------------------------------
# Integration — template → start_loop end-to-end
# ---------------------------------------------------------------------------


@pytest.fixture
def reviewer_agent():
    agent = AgentDefinition(name="reviewer", image="test-image", capabilities=["hub_messaging"])
    reg_module._agents["reviewer"] = agent
    return agent


class TestTemplateToStartLoop:
    @pytest.mark.asyncio
    async def test_pr_review_loop_starts_cleanly(self, reviewer_agent):
        spec = load_template("pr-review-loop")
        envelope = HandoffEnvelope(
            artifact_refs={
                "pr_number": 117,
                "repo": "neverprepared/phantom-ink",
                "head_sha": "abc123",
            }
        )
        inst = await start_loop(spec, envelope)
        assert inst.status == LoopStatus.RUNNING
        assert inst.iteration == 1
        # The snapshot pinned by start_loop carries the template's predicate
        # — once a loop starts, the template is immutable for its life.
        assert inst.spec_snapshot.convergence_predicate == spec.convergence_predicate
        assert inst.spec_snapshot.max_iterations == 3
        # Initial envelope's artifact_refs survive into iteration 1
        assert inst.envelope.artifact_refs["pr_number"] == 117

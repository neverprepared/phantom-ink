"""Tests for the mermaid diagram generator.

The generator is a pure function from parsed ``LoopMarkdown`` to a
mermaid ``graph TD`` string. We don't validate that mermaid actually
renders the output (that's the frontend's job); we validate that two
templates with different shapes produce visibly different graphs and
that the structural nodes / edges the frontend looks for are present.
"""

from __future__ import annotations

from brainbox.loop_md import parse
from brainbox.loop_mermaid import render


_WITH_OBJECTIVE = """\
---
name: pr-review-loop
trigger: github:pull_request
max_iterations: 3
budget_usd: 2.00
objective:
  observations.ci_status: green
  findings.approved: true
required_refs:
  - name: pr_number
    type: int
  - name: repo
    type: string
  - name: head_sha
    type: sha
    required: false
---

# Role
You review PRs.

# When to stop
- CI green and approved.

# When to escalate
- Same blocker twice.
"""


_NO_OBJECTIVE = """\
---
name: minimal
trigger: manual
max_iterations: 1
---

# Role
You do a thing.

# When to stop
- Done.

# When to escalate
- Broken.
"""


_SCHEDULED = """\
---
name: dependabot-sweep
trigger: schedule:daily
max_iterations: 2
---

# Role
You sweep deps.

# When to stop
- All safe PRs merged.

# When to escalate
- Volume spike.
"""


class TestStructure:
    def test_starts_with_graph_td(self):
        assert render(parse(_WITH_OBJECTIVE)).startswith("graph TD")

    def test_has_core_nodes(self):
        out = render(parse(_WITH_OBJECTIVE))
        for node_id in ("T", "W", "E", "J", "S", "H"):
            assert f" {node_id}" in out or f"    {node_id}" in out

    def test_envelope_label_constant(self):
        out = render(parse(_WITH_OBJECTIVE))
        assert "artifact_refs" in out and "findings" in out

    def test_terminals_have_emoji_markers(self):
        # Operator-facing affordances — at-a-glance recognition.
        out = render(parse(_WITH_OBJECTIVE))
        assert "✓ Converged" in out
        assert "🚨 Human attention" in out

    def test_iteration_edge_carries_cap(self):
        out = render(parse(_WITH_OBJECTIVE))
        assert "iter &lt; 3" in out

    def test_escalation_edge_mentions_budget(self):
        out = render(parse(_WITH_OBJECTIVE))
        assert "$2.00" in out
        assert "iter ≥ 3" in out

    def test_escalation_omits_budget_when_unset(self):
        out = render(parse(_NO_OBJECTIVE))
        assert "$" not in out


class TestTriggerFamily:
    def test_webhook_uses_stadium_shape(self):
        out = render(parse(_WITH_OBJECTIVE))
        # Stadium nodes use `([...])` syntax in mermaid.
        assert "T([" in out

    def test_schedule_uses_cylinder_shape(self):
        out = render(parse(_SCHEDULED))
        # Cylinder nodes use `[(...)]` syntax in mermaid.
        assert "T[(" in out

    def test_manual_uses_rectangle(self):
        out = render(parse(_NO_OBJECTIVE))
        assert 'T["' in out


class TestRequiredRefsNode:
    def test_refs_node_present_when_template_declares_them(self):
        out = render(parse(_WITH_OBJECTIVE))
        assert 'R["Required refs' in out
        # Three refs declared, one optional
        assert "pr_number (int)" in out
        assert "repo (string)" in out
        assert "head_sha (sha)?" in out  # optional gets ? suffix

    def test_refs_node_absent_when_template_has_none(self):
        out = render(parse(_NO_OBJECTIVE))
        assert "Required refs" not in out

    def test_edges_route_through_refs_when_present(self):
        out = render(parse(_WITH_OBJECTIVE))
        assert "T --> R" in out
        assert "R --> W" in out

    def test_edges_skip_refs_when_absent(self):
        out = render(parse(_NO_OBJECTIVE))
        assert "T --> W" in out
        assert "T --> R" not in out


class TestWorkerLabel:
    def test_worker_carries_agent_iter_budget(self):
        out = render(parse(_WITH_OBJECTIVE))
        assert "agent: worker" in out  # default from loop_md
        assert "max 3 iter" in out
        assert "budget $2.00" in out

    def test_worker_omits_budget_when_unset(self):
        out = render(parse(_NO_OBJECTIVE))
        assert "max 1 iter" in out
        assert "budget" not in out


class TestObjectiveEdges:
    def test_one_edge_per_objective_rule(self):
        out = render(parse(_WITH_OBJECTIVE))
        # Two objective entries → two edges to S
        assert "observations.ci_status == green" in out
        # truthy true renders as just the path
        assert 'J -- "findings.approved" --> S' in out

    def test_no_objective_means_no_objective_edges(self):
        out = render(parse(_NO_OBJECTIVE))
        # No objective entries → only the prose dashed edge into S
        assert 'J -- "' not in out.replace("J -- \"iter", "")  # iter edge is the only solid J-- edge

    def test_comparator_rendered_with_symbol(self):
        text = _WITH_OBJECTIVE.replace(
            "findings.approved: true",
            "diff_lines: {\"<=\": 500}",
        )
        out = render(parse(text))
        assert "diff_lines ≤ 500" in out

    def test_in_clause_collapses_list(self):
        text = _WITH_OBJECTIVE.replace(
            "findings.approved: true",
            'status: {"in": ["a", "b", "c", "d", "e"]}',
        )
        out = render(parse(text))
        # First three shown, rest collapsed
        assert "status in [a, b, c, …]" in out

    def test_objective_truncates_past_max(self):
        text = (
            _WITH_OBJECTIVE.replace(
                "findings.approved: true",
                "findings.approved: true\n  k1: 1\n  k2: 2\n  k3: 3\n  k4: 4",
            )
        )
        out = render(parse(text))
        assert "more objective rule" in out


class TestProseHints:
    def test_stop_prose_first_bullet_surfaced(self):
        out = render(parse(_WITH_OBJECTIVE))
        # "CI green and approved." should be in a dashed edge to S
        assert "CI green and approved" in out

    def test_escalate_prose_first_bullet_surfaced(self):
        out = render(parse(_WITH_OBJECTIVE))
        assert "Same blocker twice" in out

    def test_prose_truncated_when_long(self):
        text = _NO_OBJECTIVE.replace(
            "- Done.",
            "- " + "a very long stop description " * 10,
        )
        out = render(parse(text))
        assert "…" in out


class TestEscaping:
    def test_quote_in_label_is_escaped(self):
        from brainbox.loop_mermaid import _escape

        assert _escape('foo "bar" baz') == "foo &quot;bar&quot; baz"
        assert _escape("multi\nline") == "multi line"


class TestDistinguishability:
    """The diagrams for two different templates should be visibly different
    in their actual text — not just in label values that look the same."""

    def test_different_templates_render_different_strings(self):
        a = render(parse(_WITH_OBJECTIVE))
        b = render(parse(_NO_OBJECTIVE))
        c = render(parse(_SCHEDULED))
        assert a != b
        assert a != c
        assert b != c

    def test_meaningful_structural_diff(self):
        # The required-refs node only exists in templates that declare
        # them — that alone makes the two graphs structurally different.
        a = render(parse(_WITH_OBJECTIVE))
        b = render(parse(_NO_OBJECTIVE))
        assert "Required refs" in a
        assert "Required refs" not in b

"""Tests for the mermaid diagram generator.

The generator is a pure function from parsed ``LoopMarkdown`` to a
mermaid ``graph TD`` string. We don't validate that mermaid renders
the output (that's the frontend's job); we validate that the string
contains the structural nodes the frontend will look for, plus the
template-specific labels (trigger name, max_iterations, budget).
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
---

# Role
You review PRs.

# When to stop
- CI green.

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


_LONG_ROLE = """\
---
name: with-long-role
trigger: manual
max_iterations: 2
---

# Role
This is a very long role description that should be truncated in the diagram label because we only want a single line hint and the full role lives in the markdown file itself. The diagram is a visualization not a spec.

Additional paragraphs.

# When to stop
- Done.

# When to escalate
- Broken.
"""


class TestRender:
    def test_starts_with_graph_td(self):
        out = render(parse(_WITH_OBJECTIVE))
        assert out.startswith("graph TD")

    def test_includes_trigger_label(self):
        out = render(parse(_WITH_OBJECTIVE))
        assert "github:pull_request" in out

    def test_includes_worker_node(self):
        out = render(parse(_WITH_OBJECTIVE))
        assert "Worker" in out

    def test_includes_judge_node(self):
        out = render(parse(_WITH_OBJECTIVE))
        assert "Judge" in out

    def test_includes_converged_terminal(self):
        out = render(parse(_WITH_OBJECTIVE))
        assert "Converged" in out

    def test_includes_human_escalation(self):
        out = render(parse(_WITH_OBJECTIVE))
        assert "human" in out
        assert "bus.attention" in out

    def test_includes_iteration_cap(self):
        out = render(parse(_WITH_OBJECTIVE))
        assert "3" in out  # max_iterations referenced in both edges

    def test_includes_budget_when_set(self):
        out = render(parse(_WITH_OBJECTIVE))
        assert "budget $2.00" in out

    def test_no_budget_label_when_unset(self):
        out = render(parse(_NO_OBJECTIVE))
        assert "no budget cap" in out

    def test_objective_label_lists_keys(self):
        out = render(parse(_WITH_OBJECTIVE))
        # Two objective keys present, less than the 3-key truncation
        # threshold, so both should appear.
        assert "observations.ci_status" in out
        assert "findings.approved" in out

    def test_no_objective_renders_prose_only(self):
        out = render(parse(_NO_OBJECTIVE))
        assert "prose only" in out

    def test_long_role_truncated_with_ellipsis(self):
        out = render(parse(_LONG_ROLE))
        assert "…" in out
        # And it's only ONE line — no newline mid-label
        worker_line = next(ln for ln in out.splitlines() if "Worker" in ln)
        assert "\n" not in worker_line.strip()

    def test_objective_truncates_past_three_keys(self):
        text = _WITH_OBJECTIVE.replace(
            "  findings.approved: true",
            "  findings.approved: true\n  observations.diff_lines: 1\n  observations.tests: 1\n  observations.lint: 1",
        )
        out = render(parse(text))
        assert "+2 more" in out

    def test_output_lines_have_trailing_arrows(self):
        # Sanity: the structural edges Trigger→Worker, Worker→Envelope,
        # Envelope→Judge, Judge→Converged|Worker|Human should all be present.
        out = render(parse(_WITH_OBJECTIVE))
        assert "T[" in out and "--> W" in out
        assert "E[" in out and "--> J" in out
        # Three Judge edges (converged, iterate back to Worker, escalate)
        assert out.count("J -- ") >= 3

    def test_quote_in_label_is_escaped(self):
        # Smoke test the escape helper directly — the YAML loader strips
        # surrounding quotes, so an embedded quote needs explicit YAML
        # escaping to survive parsing.
        from brainbox.loop_mermaid import _escape

        assert _escape('foo "bar" baz') == "foo &quot;bar&quot; baz"
        assert _escape("multi\nline") == "multi line"

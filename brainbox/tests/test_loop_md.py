"""Tests for the markdown loop template parser (loop_md).

Two layers:

  - frontmatter parsing: required key enforcement, type coercion,
    objective dict handling, required_refs shape
  - section parsing: heading split, required section enforcement,
    case insensitivity

The parser is the single source of truth for the loop runtime — every
runner / judge / mermaid generator call reads from a parsed
``LoopMarkdown``. Parse failures must produce single-line, actionable
operator messages.
"""

from __future__ import annotations

import pytest

from brainbox.loop_md import LoopMarkdown, LoopMarkdownError, parse
from brainbox.loops import RequiredRefType


_MINIMAL = """\
---
name: minimal-loop
trigger: manual
max_iterations: 1
---

# Role
You do one thing.

# When to stop
- The thing is done.

# When to escalate
- The thing breaks.
"""


_FULL = """\
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
    description: PR number
  - name: head_sha
    type: sha
    required: false
---

# Role
You review PRs.

# When to stop
- CI is green.
- No blockers remain.

# When to escalate
- Same blocker twice in a row.

# Tools
- gh
- repo r/o

# Notes
Read prior envelope.
"""


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


class TestParseMinimal:
    def test_returns_loop_markdown(self):
        loop = parse(_MINIMAL)
        assert isinstance(loop, LoopMarkdown)

    def test_extracts_frontmatter_fields(self):
        loop = parse(_MINIMAL)
        assert loop.name == "minimal-loop"
        assert loop.trigger == "manual"
        assert loop.max_iterations == 1
        assert loop.budget_usd is None
        assert loop.objective == {}
        assert loop.required_refs == []

    def test_extracts_required_sections(self):
        loop = parse(_MINIMAL)
        assert loop.role.startswith("You do one thing")
        assert "thing is done" in loop.stop_prose
        assert "thing breaks" in loop.escalation_prose

    def test_optional_sections_default_empty(self):
        loop = parse(_MINIMAL)
        assert loop.tools_prose == ""
        assert loop.notes_prose == ""

    def test_has_objective_false_when_empty(self):
        assert parse(_MINIMAL).has_objective is False


class TestParseFull:
    def test_extracts_budget_as_float(self):
        loop = parse(_FULL)
        assert loop.budget_usd == 2.00

    def test_extracts_objective_dict(self):
        loop = parse(_FULL)
        assert loop.objective == {
            "observations.ci_status": "green",
            "findings.approved": True,
        }
        assert loop.has_objective is True

    def test_parses_required_refs(self):
        loop = parse(_FULL)
        assert len(loop.required_refs) == 2
        pr_number, head_sha = loop.required_refs
        assert pr_number.name == "pr_number"
        assert pr_number.type == RequiredRefType.INT
        assert pr_number.required is True
        assert pr_number.description == "PR number"
        assert head_sha.name == "head_sha"
        assert head_sha.type == RequiredRefType.SHA
        assert head_sha.required is False

    def test_extracts_optional_sections(self):
        loop = parse(_FULL)
        assert "gh" in loop.tools_prose
        assert "Read prior envelope" in loop.notes_prose

    def test_round_trips_raw_text(self):
        loop = parse(_FULL)
        assert loop.raw == _FULL

    def test_preserves_frontmatter_dict_for_assist(self):
        # AI Assist should be able to read the raw frontmatter back —
        # tests guard against the parser silently dropping unknown keys.
        loop = parse(_FULL)
        assert loop.frontmatter["budget_usd"] == 2.00
        assert "objective" in loop.frontmatter


# ---------------------------------------------------------------------------
# Frontmatter validation failures — every message must point at the fix
# ---------------------------------------------------------------------------


class TestFrontmatterFailures:
    def test_missing_frontmatter_fence(self):
        with pytest.raises(LoopMarkdownError, match="missing frontmatter"):
            parse("# Role\nsomething")

    def test_unclosed_frontmatter(self):
        with pytest.raises(LoopMarkdownError, match="malformed"):
            parse("---\nname: foo\n# Role\nbody")

    def test_invalid_yaml_in_frontmatter(self):
        with pytest.raises(LoopMarkdownError, match="not valid YAML"):
            parse("---\nname: foo\n  bad: [unclosed\n---\n# Role\nx\n# When to stop\nx\n# When to escalate\nx\n")

    def test_missing_required_keys(self):
        text = "---\nname: x\n---\n# Role\nx\n# When to stop\nx\n# When to escalate\nx\n"
        with pytest.raises(LoopMarkdownError, match="trigger, max_iterations"):
            parse(text)

    def test_invalid_slug(self):
        text = _MINIMAL.replace("name: minimal-loop", "name: NotASlug")
        with pytest.raises(LoopMarkdownError, match="slug"):
            parse(text)

    def test_negative_max_iterations(self):
        text = _MINIMAL.replace("max_iterations: 1", "max_iterations: 0")
        with pytest.raises(LoopMarkdownError, match="positive integer"):
            parse(text)

    def test_negative_budget(self):
        text = _MINIMAL.replace(
            "max_iterations: 1",
            "max_iterations: 1\nbudget_usd: -0.50",
        )
        with pytest.raises(LoopMarkdownError, match="positive number"):
            parse(text)

    def test_non_mapping_objective(self):
        text = _MINIMAL.replace(
            "max_iterations: 1",
            "max_iterations: 1\nobjective: not_a_dict",
        )
        with pytest.raises(LoopMarkdownError, match="objective"):
            parse(text)

    def test_required_ref_bad_type(self):
        text = _MINIMAL.replace(
            "max_iterations: 1",
            "max_iterations: 1\nrequired_refs:\n  - {name: x, type: bigint}",
        )
        with pytest.raises(LoopMarkdownError, match="int\\|string\\|sha"):
            parse(text)


# ---------------------------------------------------------------------------
# Section validation failures
# ---------------------------------------------------------------------------


class TestSectionFailures:
    def test_missing_role(self):
        text = _MINIMAL.replace("# Role\nYou do one thing.\n\n", "")
        with pytest.raises(LoopMarkdownError, match="Role"):
            parse(text)

    def test_missing_when_to_stop(self):
        text = _MINIMAL.replace("# When to stop\n- The thing is done.\n\n", "")
        with pytest.raises(LoopMarkdownError, match="When To Stop"):
            parse(text)

    def test_missing_when_to_escalate(self):
        text = _MINIMAL.replace("# When to escalate\n- The thing breaks.\n", "")
        with pytest.raises(LoopMarkdownError, match="When To Escalate"):
            parse(text)

    def test_empty_role_section(self):
        text = _MINIMAL.replace("You do one thing.", "")
        with pytest.raises(LoopMarkdownError, match="empty"):
            parse(text)

    def test_case_insensitive_headings(self):
        # An operator writing "# ROLE" or "# role" should work the same
        text = _MINIMAL.replace("# Role", "# ROLE").replace(
            "# When to stop", "# when to stop"
        )
        loop = parse(text)
        assert loop.role.startswith("You do one thing")

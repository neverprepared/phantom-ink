"""Tests for the markdown loop template loader.

Covers parser correctness (frontmatter + sections), the bundled
pr-review-loop template loading cleanly, and TemplateError surfacing
of malformed input. CRUD + dry-run live in test_loop_template_api.py.
"""

from __future__ import annotations

import pytest

import brainbox.loop_template as loop_template_module
from brainbox.loop_template import (
    TemplateError,
    list_templates,
    load_template,
    parse_template,
    template_path,
)
from brainbox.loops import PermissionTier, RequiredRefType


_MINIMAL = """\
---
name: test-loop
trigger: manual
max_iterations: 3
---

# Role
You do a thing.

# When to stop
- The thing is done.

# When to escalate
- The thing breaks.
"""


@pytest.fixture
def isolated_user_dir(tmp_path, monkeypatch):
    """Redirect the user templates dir to a tmp path so list_templates
    and template_path don't see operator-level overrides."""
    user_dir = tmp_path / "loop-templates"
    monkeypatch.setattr(loop_template_module, "_user_templates_dir", lambda: user_dir)
    return user_dir


# ---------------------------------------------------------------------------
# parse_template — happy paths
# ---------------------------------------------------------------------------


class TestParseMinimal:
    def test_parses_minimal_template(self):
        loop = parse_template(_MINIMAL)
        assert loop.name == "test-loop"
        assert loop.trigger == "manual"
        assert loop.max_iterations == 3

    def test_agent_defaults_to_name(self):
        """When frontmatter omits 'agent', it falls back to the loop name."""
        loop = parse_template(_MINIMAL)
        assert loop.agent == "test-loop"

    def test_agent_override_wins(self):
        text = _MINIMAL.replace(
            "max_iterations: 3\n",
            "max_iterations: 3\nagent: reviewer\n",
        )
        loop = parse_template(text)
        assert loop.agent == "reviewer"

    def test_permissions_default_is_default(self):
        loop = parse_template(_MINIMAL)
        assert loop.permissions == PermissionTier.DEFAULT

    def test_required_sections_parsed(self):
        loop = parse_template(_MINIMAL)
        assert "thing is done" in loop.stop_prose
        assert "thing breaks" in loop.escalation_prose
        assert "do a thing" in loop.role

    def test_objective_empty_by_default(self):
        loop = parse_template(_MINIMAL)
        assert loop.objective == {}
        assert loop.has_objective is False


# ---------------------------------------------------------------------------
# parse_template — frontmatter edge cases
# ---------------------------------------------------------------------------


class TestFrontmatterValidation:
    def test_missing_frontmatter_raises(self):
        with pytest.raises(TemplateError, match="frontmatter"):
            parse_template("# Role\nhi\n# When to stop\nx\n# When to escalate\ny\n")

    def test_unclosed_frontmatter_raises(self):
        with pytest.raises(TemplateError, match="frontmatter"):
            parse_template("---\nname: x\n# Role\nx\n")

    def test_missing_required_key_raises(self):
        text = """---
name: foo
trigger: manual
---

# Role
x
# When to stop
y
# When to escalate
z
"""
        with pytest.raises(TemplateError, match="max_iterations"):
            parse_template(text)

    def test_invalid_slug_raises(self):
        bad = _MINIMAL.replace("name: test-loop", "name: Test_Loop")
        with pytest.raises(TemplateError, match="slug"):
            parse_template(bad)

    def test_invalid_permissions_raises(self):
        text = _MINIMAL.replace(
            "max_iterations: 3\n",
            "max_iterations: 3\npermissions: superuser\n",
        )
        with pytest.raises(TemplateError, match="permissions"):
            parse_template(text)

    def test_permissions_enum_accepts_strict(self):
        text = _MINIMAL.replace(
            "max_iterations: 3\n",
            "max_iterations: 3\npermissions: strict\n",
        )
        loop = parse_template(text)
        assert loop.permissions == PermissionTier.STRICT

    def test_negative_max_iterations_raises(self):
        bad = _MINIMAL.replace("max_iterations: 3", "max_iterations: 0")
        with pytest.raises(TemplateError, match="max_iterations"):
            parse_template(bad)


# ---------------------------------------------------------------------------
# Required body sections
# ---------------------------------------------------------------------------


class TestRequiredSections:
    def test_missing_role_raises(self):
        text = """---
name: foo
trigger: manual
max_iterations: 1
---

# When to stop
x
# When to escalate
y
"""
        with pytest.raises(TemplateError, match="Role"):
            parse_template(text)

    def test_missing_stop_raises(self):
        text = """---
name: foo
trigger: manual
max_iterations: 1
---

# Role
x
# When to escalate
y
"""
        with pytest.raises(TemplateError, match="(?i)stop"):
            parse_template(text)

    def test_empty_section_raises(self):
        text = """---
name: foo
trigger: manual
max_iterations: 1
---

# Role

# When to stop
x
# When to escalate
y
"""
        with pytest.raises(TemplateError, match="empty"):
            parse_template(text)


# ---------------------------------------------------------------------------
# Bundled pr-review-loop template
# ---------------------------------------------------------------------------


class TestBundledTemplate:
    def test_pr_review_loop_listed(self):
        assert "pr-review-loop" in list_templates()

    def test_pr_review_loop_loads(self):
        loop = load_template("pr-review-loop")
        assert loop.name == "pr-review-loop"
        assert loop.trigger == "github:pull_request"
        assert loop.max_iterations == 3
        assert loop.budget_usd == 2.00
        assert loop.agent == "reviewer"

    def test_pr_review_loop_objective(self):
        loop = load_template("pr-review-loop")
        # objective is a dict of envelope-path -> expected value
        assert loop.objective.get("observations.ci_status") == "green"
        assert loop.objective.get("findings.approved") is True
        assert loop.has_objective is True

    def test_pr_review_loop_required_refs(self):
        loop = load_template("pr-review-loop")
        by_name = {r.name: r for r in loop.required_refs}
        assert by_name["pr_number"].type == RequiredRefType.INT
        assert by_name["repo"].type == RequiredRefType.STRING
        assert by_name["head_sha"].type == RequiredRefType.SHA
        assert by_name["head_sha"].required is False
        assert by_name["pr_number"].required is True  # defaults to True

    def test_pr_review_loop_role_nonempty(self):
        loop = load_template("pr-review-loop")
        assert "reviewer" in loop.role.lower()
        assert loop.stop_prose
        assert loop.escalation_prose


# ---------------------------------------------------------------------------
# template_path + missing templates
# ---------------------------------------------------------------------------


class TestTemplatePath:
    def test_missing_returns_none(self):
        assert template_path("nope-not-real") is None

    def test_bundled_returns_path(self):
        p = template_path("pr-review-loop")
        assert p is not None
        assert p.name == "pr-review-loop.md"

    def test_load_missing_raises(self):
        with pytest.raises(TemplateError, match="not found"):
            load_template("does-not-exist-xyz")

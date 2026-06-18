"""Tests for the template CRUD + schema + validate + dry-run API surface
(loop-spec PR 2). Two layers:

  - Module-level: read_raw_template, write_user_template,
    delete_user_template, validate_yaml, build_dry_run_plan (each
    covers the path-traversal / fork / atomic-write semantics)
  - HTTP layer: ASGI client through every endpoint with happy + 404 +
    403 + 409 + 422 + 400 paths

User-templates dir is monkey-patched to a tmp_path so tests never
touch the real ~/.config/.../loop-templates/.
"""

from __future__ import annotations

import yaml
import pytest

from brainbox.loop_template import (
    TemplateError,
    build_dry_run_plan,
    delete_user_template,
    load_template,
    read_raw_template,
    template_path,
    validate_yaml,
    write_user_template,
)
import brainbox.loop_template as loop_template_module
from brainbox.loops import HandoffEnvelope, LoopSpec, NodeExecutor, NodeKind


_MINIMAL = """\
---
name: tmp-test
intent:
  outcome: x
  convergence: "`true`"
body:
  nodes:
    - id: n1
      role: reviewer
convergence_metric: "`0`"
---
body docs
"""


@pytest.fixture
def isolated_user_dir(tmp_path, monkeypatch):
    """Redirect the user templates dir to a tmp path so tests can write
    without touching the operator's real config dir."""
    user_dir = tmp_path / "loop-templates"
    monkeypatch.setattr(loop_template_module, "_user_templates_dir", lambda: user_dir)
    return user_dir


# ---------------------------------------------------------------------------
# read_raw_template
# ---------------------------------------------------------------------------


class TestReadRawTemplate:
    def test_reads_builtin_pr_review_loop(self):
        data = read_raw_template("pr-review-loop")
        assert data["name"] == "pr-review-loop"
        assert data["origin"] == "built-in"
        assert data["yaml"].startswith("---")
        assert len(data["hash"]) == 16

    def test_unknown_template_raises(self):
        with pytest.raises(TemplateError, match="not found"):
            read_raw_template("does-not-exist")

    def test_user_template_origin_marked(self, isolated_user_dir):
        write_user_template("my-tpl", _MINIMAL)
        data = read_raw_template("my-tpl")
        assert data["origin"] == "user"

    def test_user_override_shadows_builtin(self, isolated_user_dir):
        # Write a user-dir copy of pr-review-loop (override) — should appear
        # as origin "user" because the loader returns user-dir first.
        write_user_template("pr-review-loop", _MINIMAL, fork_from_builtin=True)
        data = read_raw_template("pr-review-loop")
        assert data["origin"] == "user"


# ---------------------------------------------------------------------------
# write_user_template
# ---------------------------------------------------------------------------


class TestWriteUserTemplate:
    def test_happy_path_writes_file(self, isolated_user_dir):
        result = write_user_template("my-tpl", _MINIMAL)
        assert result["origin"] == "user"
        assert (isolated_user_dir / "my-tpl.md").is_file()

    def test_invalid_yaml_does_not_create_file(self, isolated_user_dir):
        with pytest.raises(TemplateError):
            write_user_template("broken", "---\nname: [unclosed\n---\nbody\n")
        # Nothing left on disk — atomic write should clean up the tmp,
        # never leave a partial broken template
        assert not (isolated_user_dir / "broken.md").exists()

    def test_path_traversal_rejected(self, isolated_user_dir):
        with pytest.raises(TemplateError, match="invalid template name"):
            write_user_template("../evil", _MINIMAL)
        with pytest.raises(TemplateError, match="invalid template name"):
            write_user_template("foo/bar", _MINIMAL)
        with pytest.raises(TemplateError, match="invalid template name"):
            write_user_template("", _MINIMAL)

    def test_overwriting_builtin_without_fork_rejected(self, isolated_user_dir):
        with pytest.raises(TemplateError, match="fork=true"):
            write_user_template("pr-review-loop", _MINIMAL)
        # Nothing written to user dir
        assert not (isolated_user_dir / "pr-review-loop.md").exists()

    def test_fork_from_builtin_writes_user_copy(self, isolated_user_dir):
        result = write_user_template("pr-review-loop", _MINIMAL, fork_from_builtin=True)
        assert result["origin"] == "user"
        assert (isolated_user_dir / "pr-review-loop.md").is_file()


# ---------------------------------------------------------------------------
# delete_user_template
# ---------------------------------------------------------------------------


class TestDeleteUserTemplate:
    def test_deletes_user_template(self, isolated_user_dir):
        write_user_template("my-tpl", _MINIMAL)
        delete_user_template("my-tpl")
        assert not (isolated_user_dir / "my-tpl.md").exists()

    def test_nonexistent_raises(self, isolated_user_dir):
        with pytest.raises(TemplateError, match="not found"):
            delete_user_template("ghost")

    def test_path_traversal_rejected(self, isolated_user_dir):
        with pytest.raises(TemplateError, match="invalid template name"):
            delete_user_template("../evil")


# ---------------------------------------------------------------------------
# validate_yaml
# ---------------------------------------------------------------------------


class TestValidateYaml:
    def test_valid_template_returns_ok(self):
        result = validate_yaml(_MINIMAL)
        assert result["ok"] is True
        assert result["errors"] == []

    def test_missing_frontmatter_returns_error(self):
        result = validate_yaml("just markdown, no fences\n")
        assert result["ok"] is False
        assert any("fence" in e["message"] for e in result["errors"])

    def test_yaml_syntax_error_includes_line_col(self):
        result = validate_yaml("---\nname: [unclosed\n---\nbody\n")
        assert result["ok"] is False
        # Should have at least one error with line+col info
        assert any(e["line"] is not None for e in result["errors"])

    def test_missing_convergence_returns_field_error(self):
        bad = """\
---
name: no-conv
intent:
  outcome: x
body:
  nodes:
    - id: n
      role: reviewer
---
"""
        result = validate_yaml(bad)
        assert result["ok"] is False
        # Should mention convergence somewhere
        assert any(
            "convergence" in (e.get("message") or "").lower()
            or "convergence" in (e.get("field") or "").lower()
            for e in result["errors"]
        )

    def test_non_mapping_frontmatter_returns_error(self):
        result = validate_yaml("---\n- just\n- a list\n---\nbody\n")
        assert result["ok"] is False
        assert any("mapping" in e["message"] for e in result["errors"])


# ---------------------------------------------------------------------------
# build_dry_run_plan
# ---------------------------------------------------------------------------


class TestBuildDryRunPlan:
    def test_empty_body_raises(self):
        # model_construct bypasses validation so we can build a "broken"
        # spec for the negative test without LoopSpec.model_post_init
        # complaining first.
        spec = LoopSpec.model_construct(
            name="empty",
            convergence_predicate="`true`",
            convergence_metric="",
            stop_conditions=[],
        )
        # body needs the right pydantic-shape, not a dict
        from brainbox.loops import Body
        spec.body = Body(nodes=[], edges=[])
        from brainbox.loops import PermissionTier
        spec.permissions = PermissionTier.DEFAULT
        with pytest.raises(TemplateError, match="no nodes"):
            build_dry_run_plan(spec)

    def test_pr_review_loop_dry_run_shape(self):
        spec = load_template("pr-review-loop")
        plan = build_dry_run_plan(
            spec,
            {"artifact_refs": {"pr_number": 117, "repo": "owner/name"}},
        )
        assert plan["first_iteration"]["node_id"] == "reviewer"
        assert plan["first_iteration"]["node_kind"] == NodeKind.AGENT.value
        assert plan["first_iteration"]["node_executor"] == NodeExecutor.BRAINBOX_SESSION.value
        assert plan["max_iterations"] == 3
        assert plan["permissions"] == "default"
        # Sample envelope has no findings at all → length(findings.blockers)
        # tries to call length() on null → JMESPath errors. dry-run reports
        # this as ``would_fire: null`` plus an ``error`` message — that's
        # itself the useful diagnostic ("your predicate references a field
        # the sample envelope doesn't carry").
        assert plan["convergence_predicate"]["would_fire"] is None
        assert "error" in plan["convergence_predicate"]

    def test_convergence_fires_with_zero_blockers_and_green_ci(self):
        spec = load_template("pr-review-loop")
        plan = build_dry_run_plan(
            spec,
            {
                "findings": {"blockers": []},
                "observations": {"ci_status": "green"},
            },
        )
        assert plan["convergence_predicate"]["would_fire"] is True

    def test_stop_conditions_evaluated(self):
        spec = load_template("pr-review-loop")
        plan = build_dry_run_plan(
            spec,
            {
                "findings": {"blockers": []},  # makes convergence evaluable
                "observations": {"diff_lines": 1000, "ci_status": "red"},
            },
        )
        # pr-review-loop has stop_conditions: diff_lines > 500
        fires = [sc for sc in plan["stop_conditions"] if sc["would_fire"]]
        assert any("diff_too_large" in sc["reason"] for sc in fires)


# ---------------------------------------------------------------------------
# HTTP layer — full API surface via ASGI client
# ---------------------------------------------------------------------------


class TestSchemaEndpoint:
    @pytest.mark.asyncio
    async def test_returns_json_schema(self, client):
        async with client as c:
            resp = await c.get("/api/loops/templates/schema")
        assert resp.status_code == 200
        schema = resp.json()
        # Standard JSON Schema artifacts
        assert "properties" in schema
        # LoopSpec has convergence_predicate as a top-level property
        assert "convergence_predicate" in schema["properties"]


class TestGetTemplateEndpoint:
    @pytest.mark.asyncio
    async def test_returns_builtin_pr_review_loop(self, client):
        async with client as c:
            resp = await c.get("/api/loops/templates/pr-review-loop")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "pr-review-loop"
        assert data["origin"] == "built-in"
        assert data["yaml"].startswith("---")

    @pytest.mark.asyncio
    async def test_unknown_template_returns_404(self, client):
        async with client as c:
            resp = await c.get("/api/loops/templates/does-not-exist")
        assert resp.status_code == 404


class TestPutTemplateEndpoint:
    @pytest.mark.asyncio
    async def test_writes_new_user_template(self, client, isolated_user_dir):
        async with client as c:
            resp = await c.put(
                "/api/loops/templates/my-tpl",
                json={"yaml": _MINIMAL},
            )
        assert resp.status_code == 200
        assert resp.json()["origin"] == "user"
        assert (isolated_user_dir / "my-tpl.md").is_file()

    @pytest.mark.asyncio
    async def test_overwriting_builtin_without_fork_returns_409(self, client, isolated_user_dir):
        async with client as c:
            resp = await c.put(
                "/api/loops/templates/pr-review-loop",
                json={"yaml": _MINIMAL},
            )
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_fork_query_writes_user_override(self, client, isolated_user_dir):
        async with client as c:
            resp = await c.put(
                "/api/loops/templates/pr-review-loop?fork=true",
                json={"yaml": _MINIMAL},
            )
        assert resp.status_code == 200
        assert resp.json()["origin"] == "user"

    @pytest.mark.asyncio
    async def test_path_traversal_rejected_by_module(self, client, isolated_user_dir):
        # Starlette URL-decodes %2F to / before we see the name; our route
        # parameter then doesn't match. The MODULE-level rejection in
        # _is_safe_name is the real defense — tested directly in
        # TestWriteUserTemplate.test_path_traversal_rejected. This test
        # just confirms the module rejection surfaces through the API
        # route when the name DOES reach the handler (e.g. via a path with
        # backslash characters).
        async with client as c:
            resp = await c.put(
                "/api/loops/templates/evil%5Cname",  # %5C = backslash
                json={"yaml": _MINIMAL},
            )
        # Either Starlette mangles it (4xx) or the module rejects it (400).
        assert 400 <= resp.status_code < 500

    @pytest.mark.asyncio
    async def test_invalid_yaml_returns_422(self, client, isolated_user_dir):
        async with client as c:
            resp = await c.put(
                "/api/loops/templates/broken",
                json={"yaml": "no frontmatter at all\n"},
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_missing_yaml_field_returns_400(self, client, isolated_user_dir):
        async with client as c:
            resp = await c.put("/api/loops/templates/missing", json={})
        assert resp.status_code == 400


class TestDeleteTemplateEndpoint:
    @pytest.mark.asyncio
    async def test_deletes_user_template(self, client, isolated_user_dir):
        async with client as c:
            await c.put(
                "/api/loops/templates/disposable",
                json={"yaml": _MINIMAL},
            )
            resp = await c.delete("/api/loops/templates/disposable")
        assert resp.status_code == 200
        assert resp.json()["deleted"] == "disposable"

    @pytest.mark.asyncio
    async def test_deleting_builtin_returns_403(self, client):
        async with client as c:
            resp = await c.delete("/api/loops/templates/pr-review-loop")
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_deleting_unknown_returns_404(self, client, isolated_user_dir):
        async with client as c:
            resp = await c.delete("/api/loops/templates/ghost")
        assert resp.status_code == 404


class TestValidateEndpoint:
    @pytest.mark.asyncio
    async def test_valid_yaml_returns_ok_true(self, client):
        async with client as c:
            resp = await c.post(
                "/api/loops/templates/validate",
                json={"yaml": _MINIMAL},
            )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    @pytest.mark.asyncio
    async def test_invalid_yaml_returns_ok_false(self, client):
        async with client as c:
            resp = await c.post(
                "/api/loops/templates/validate",
                json={"yaml": "no frontmatter"},
            )
        assert resp.status_code == 200
        assert resp.json()["ok"] is False
        assert len(resp.json()["errors"]) > 0

    @pytest.mark.asyncio
    async def test_missing_yaml_field_returns_400(self, client):
        async with client as c:
            resp = await c.post("/api/loops/templates/validate", json={})
        assert resp.status_code == 400


class TestDryRunEndpoint:
    @pytest.mark.asyncio
    async def test_pr_review_loop_dry_run(self, client):
        async with client as c:
            resp = await c.post(
                "/api/loops/templates/pr-review-loop/dry-run",
                json={
                    "envelope": {
                        "findings": {"blockers": []},
                        "observations": {"ci_status": "green"},
                    },
                },
            )
        assert resp.status_code == 200
        plan = resp.json()
        assert plan["first_iteration"]["node_id"] == "reviewer"
        assert plan["convergence_predicate"]["would_fire"] is True

    @pytest.mark.asyncio
    async def test_unknown_template_returns_404(self, client):
        async with client as c:
            resp = await c.post(
                "/api/loops/templates/ghost/dry-run",
                json={},
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_dry_run_with_no_envelope_uses_empty(self, client):
        async with client as c:
            resp = await c.post(
                "/api/loops/templates/pr-review-loop/dry-run",
                json={},
            )
        assert resp.status_code == 200
        plan = resp.json()
        # Empty envelope → length(null) errors → would_fire null + error
        # surfaces the diagnostic to the operator.
        assert plan["convergence_predicate"]["would_fire"] is None
        assert "error" in plan["convergence_predicate"]

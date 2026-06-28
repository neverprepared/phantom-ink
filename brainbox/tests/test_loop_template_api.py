"""Tests for the markdown loop template CRUD + schema + validate +
dry-run API surface.

Two layers:
  - Module-level helpers: read_raw_template, write_user_template,
    delete_user_template, validate_markdown, build_dry_run_plan
    (path-traversal / fork / atomic-write semantics)
  - HTTP layer: ASGI client through every endpoint, covering happy +
    404 + 403 + 409 + 422 + 400 paths

User templates dir is monkey-patched to a tmp_path so tests never
touch the operator's real ~/.config/.../loop-templates/.
"""

from __future__ import annotations

import pytest

import brainbox.loop_template as loop_template_module
from brainbox.loop_template import (
    TemplateError,
    build_dry_run_plan,
    delete_user_template,
    load_template,
    read_raw_template,
    template_path,
    validate_markdown,
    validate_yaml,
    write_user_template,
)


_MINIMAL = """\
---
name: tmp-test
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
    """Redirect the user templates dir to a tmp path so writes don't
    pollute the operator's real config dir."""
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
        assert data["markdown"].startswith("---")
        assert len(data["hash"]) == 16

    def test_unknown_template_raises(self):
        with pytest.raises(TemplateError, match="not found"):
            read_raw_template("does-not-exist")

    def test_user_template_origin_marked(self, isolated_user_dir):
        write_user_template("my-tpl", _MINIMAL)
        data = read_raw_template("my-tpl")
        assert data["origin"] == "user"

    def test_user_override_shadows_builtin(self, isolated_user_dir):
        write_user_template("pr-review-loop", _MINIMAL, fork_from_builtin=True)
        data = read_raw_template("pr-review-loop")
        assert data["origin"] == "user"


# ---------------------------------------------------------------------------
# write_user_template
# ---------------------------------------------------------------------------


class TestWriteUserTemplate:
    def test_writes_new_template(self, isolated_user_dir):
        result = write_user_template("my-tpl", _MINIMAL)
        assert result["name"] == "my-tpl"
        assert result["origin"] == "user"
        assert (isolated_user_dir / "my-tpl.md").is_file()

    def test_overwrites_existing_user_template(self, isolated_user_dir):
        write_user_template("my-tpl", _MINIMAL)
        updated = _MINIMAL.replace("max_iterations: 3", "max_iterations: 5")
        result = write_user_template("my-tpl", updated)
        assert "max_iterations: 5" in result["markdown"]

    def test_refuses_builtin_overwrite_without_fork(self, isolated_user_dir):
        with pytest.raises(TemplateError, match="fork=true"):
            write_user_template("pr-review-loop", _MINIMAL)

    def test_fork_allows_builtin_name_override(self, isolated_user_dir):
        result = write_user_template(
            "pr-review-loop", _MINIMAL, fork_from_builtin=True
        )
        assert result["origin"] == "user"
        assert result["name"] == "pr-review-loop"

    def test_invalid_name_rejected(self, isolated_user_dir):
        with pytest.raises(TemplateError, match="invalid template name"):
            write_user_template("../evil", _MINIMAL)

    def test_invalid_name_slash_rejected(self, isolated_user_dir):
        with pytest.raises(TemplateError, match="invalid template name"):
            write_user_template("dir/name", _MINIMAL)

    def test_malformed_markdown_rejected_before_disk_touch(self, isolated_user_dir):
        with pytest.raises(TemplateError):
            write_user_template("bad", "not yaml at all\n# Role\nx\n")
        assert not (isolated_user_dir / "bad.md").exists()

    def test_atomic_write_leaves_no_tmp_files(self, isolated_user_dir):
        write_user_template("my-tpl", _MINIMAL)
        leftover = list(isolated_user_dir.glob(".*.tmp"))
        assert leftover == []


# ---------------------------------------------------------------------------
# delete_user_template
# ---------------------------------------------------------------------------


class TestDeleteUserTemplate:
    def test_deletes_existing_user_template(self, isolated_user_dir):
        write_user_template("my-tpl", _MINIMAL)
        delete_user_template("my-tpl")
        assert not (isolated_user_dir / "my-tpl.md").exists()

    def test_missing_raises(self, isolated_user_dir):
        with pytest.raises(TemplateError, match="not found"):
            delete_user_template("nope")

    def test_invalid_name_rejected(self, isolated_user_dir):
        with pytest.raises(TemplateError, match="invalid template name"):
            delete_user_template("../evil")


# ---------------------------------------------------------------------------
# validate_markdown
# ---------------------------------------------------------------------------


class TestValidateMarkdown:
    def test_valid_markdown_ok(self):
        result = validate_markdown(_MINIMAL)
        assert result["ok"] is True
        assert result["errors"] == []

    def test_invalid_markdown_returns_errors(self):
        result = validate_markdown("not even close")
        assert result["ok"] is False
        assert len(result["errors"]) == 1
        assert "message" in result["errors"][0]

    def test_missing_section_returns_error(self):
        text = """---
name: foo
trigger: manual
max_iterations: 1
---

# Role
x
"""
        result = validate_markdown(text)
        assert result["ok"] is False

    def test_validate_yaml_alias(self):
        # Back-compat alias must behave identically.
        assert validate_yaml is validate_markdown


# ---------------------------------------------------------------------------
# build_dry_run_plan
# ---------------------------------------------------------------------------


class TestBuildDryRunPlan:
    def test_dry_run_for_pr_review_loop(self):
        loop = load_template("pr-review-loop")
        plan = build_dry_run_plan(loop, {})
        assert plan["first_iteration"]["iteration"] == 1
        assert plan["first_iteration"]["agent_name"] == "reviewer"
        assert "role_preview" in plan["first_iteration"]
        assert "task_description" in plan["first_iteration"]
        assert plan["max_iterations"] == 3
        assert plan["budget_usd"] == 2.00
        assert plan["permissions"] == "default"
        assert "objective" in plan
        assert "entries" in plan["objective"]
        assert "would_fire" in plan["objective"]
        assert "reason" in plan["objective"]
        assert plan["stop_prose"]
        assert plan["escalation_prose"]
        assert isinstance(plan["mermaid"], str) and plan["mermaid"]

    def test_dry_run_objective_fires_with_matching_envelope(self):
        loop = load_template("pr-review-loop")
        envelope = {
            "observations": {"ci_status": "green"},
            "findings": {"approved": True},
        }
        plan = build_dry_run_plan(loop, envelope)
        assert plan["objective"]["would_fire"] is True

    def test_dry_run_objective_does_not_fire_on_empty_envelope(self):
        loop = load_template("pr-review-loop")
        plan = build_dry_run_plan(loop, {})
        assert plan["objective"]["would_fire"] is False


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestHTTPListAndSchema:
    async def test_list_returns_bundled(self, client):
        async with client as c:
            r = await c.get("/api/loops/templates")
        assert r.status_code == 200
        assert "pr-review-loop" in r.json()["templates"]

    async def test_schema_shape(self, client):
        async with client as c:
            r = await c.get("/api/loops/templates/schema")
        assert r.status_code == 200
        body = r.json()
        assert body["frontmatter"]["required"] == ["name", "trigger", "max_iterations"]
        assert "agent" in body["frontmatter"]["optional"]
        assert body["sections"]["required"] == ["Role", "When to stop", "When to escalate"]
        assert body["permissions"] == ["inherit", "default", "strict"]
        assert body["required_ref_types"] == ["int", "string", "sha"]


@pytest.mark.asyncio
class TestHTTPValidate:
    async def test_validate_ok(self, client):
        async with client as c:
            r = await c.post(
                "/api/loops/templates/validate", json={"markdown": _MINIMAL}
            )
        assert r.status_code == 200
        assert r.json()["ok"] is True

    async def test_validate_legacy_yaml_key(self, client):
        async with client as c:
            r = await c.post(
                "/api/loops/templates/validate", json={"yaml": _MINIMAL}
            )
        assert r.status_code == 200
        assert r.json()["ok"] is True

    async def test_validate_invalid_returns_200_with_errors(self, client):
        async with client as c:
            r = await c.post(
                "/api/loops/templates/validate", json={"markdown": "broken"}
            )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert body["errors"]

    async def test_validate_missing_body_rejected(self, client):
        async with client as c:
            r = await c.post("/api/loops/templates/validate", json={})
        assert r.status_code == 400


@pytest.mark.asyncio
class TestHTTPGet:
    async def test_get_builtin(self, client):
        async with client as c:
            r = await c.get("/api/loops/templates/pr-review-loop")
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "pr-review-loop"
        assert body["origin"] == "built-in"
        assert "markdown" in body

    async def test_get_missing_404(self, client):
        async with client as c:
            r = await c.get("/api/loops/templates/does-not-exist-xyz")
        assert r.status_code == 404


@pytest.mark.asyncio
class TestHTTPPut:
    async def test_put_new_user_template(self, client, isolated_user_dir):
        async with client as c:
            r = await c.put(
                "/api/loops/templates/my-new-tpl", json={"markdown": _MINIMAL}
            )
        assert r.status_code == 200
        body = r.json()
        assert body["origin"] == "user"
        assert body["name"] == "my-new-tpl"

    async def test_put_builtin_without_fork_returns_409(self, client, isolated_user_dir):
        async with client as c:
            r = await c.put(
                "/api/loops/templates/pr-review-loop", json={"markdown": _MINIMAL}
            )
        assert r.status_code == 409

    async def test_put_builtin_with_fork_succeeds(self, client, isolated_user_dir):
        async with client as c:
            r = await c.put(
                "/api/loops/templates/pr-review-loop?fork=true",
                json={"markdown": _MINIMAL},
            )
        assert r.status_code == 200
        assert r.json()["origin"] == "user"

    async def test_put_invalid_name_returns_400(self, client, isolated_user_dir):
        async with client as c:
            r = await c.put(
                "/api/loops/templates/evil$name", json={"markdown": _MINIMAL}
            )
        assert r.status_code == 400

    async def test_put_malformed_markdown_returns_422(self, client, isolated_user_dir):
        bad = _MINIMAL.replace("max_iterations: 3", "max_iterations: -1")
        async with client as c:
            r = await c.put(
                "/api/loops/templates/bad-tpl", json={"markdown": bad}
            )
        assert r.status_code == 422

    async def test_put_non_string_body_returns_400(self, client, isolated_user_dir):
        async with client as c:
            r = await c.put(
                "/api/loops/templates/my-tpl", json={"markdown": 123}
            )
        assert r.status_code == 400

    async def test_put_legacy_yaml_body_key(self, client, isolated_user_dir):
        async with client as c:
            r = await c.put(
                "/api/loops/templates/my-tpl", json={"yaml": _MINIMAL}
            )
        assert r.status_code == 200


@pytest.mark.asyncio
class TestHTTPDelete:
    async def test_delete_user_template(self, client, isolated_user_dir):
        async with client as c:
            await c.put(
                "/api/loops/templates/my-tpl", json={"markdown": _MINIMAL}
            )
            r = await c.delete("/api/loops/templates/my-tpl")
        assert r.status_code == 200
        assert r.json()["deleted"] == "my-tpl"

    async def test_delete_builtin_returns_403(self, client, isolated_user_dir):
        async with client as c:
            r = await c.delete("/api/loops/templates/pr-review-loop")
        assert r.status_code == 403

    async def test_delete_missing_returns_404(self, client, isolated_user_dir):
        async with client as c:
            r = await c.delete("/api/loops/templates/does-not-exist-xyz")
        assert r.status_code == 404


@pytest.mark.asyncio
class TestHTTPDryRun:
    async def test_dry_run_for_pr_review_loop(self, client):
        async with client as c:
            r = await c.post(
                "/api/loops/templates/pr-review-loop/dry-run",
                json={"envelope": {}},
            )
        assert r.status_code == 200
        plan = r.json()
        assert plan["first_iteration"]["agent_name"] == "reviewer"
        assert plan["max_iterations"] == 3
        assert plan["permissions"] == "default"
        assert "mermaid" in plan and plan["mermaid"]

    async def test_dry_run_with_matching_envelope_would_fire(self, client):
        async with client as c:
            r = await c.post(
                "/api/loops/templates/pr-review-loop/dry-run",
                json={
                    "envelope": {
                        "observations": {"ci_status": "green"},
                        "findings": {"approved": True},
                    }
                },
            )
        assert r.status_code == 200
        assert r.json()["objective"]["would_fire"] is True

    async def test_dry_run_missing_template_404(self, client):
        async with client as c:
            r = await c.post(
                "/api/loops/templates/nope-xyz/dry-run", json={"envelope": {}}
            )
        assert r.status_code == 404

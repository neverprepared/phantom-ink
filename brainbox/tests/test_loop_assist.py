"""Tests for the AI Assist authoring path (loop-spec PR 6, markdown edition).

Two layers:

  - loop_assist module: system-prompt composition, validate-and-retry
    loop, response cleanup (code-fence stripping)
  - HTTP route: bad body, missing prompt, mode dispatch, upstream
    session failures → 502

No real session is provisioned. The session-call path
(``_call_session``) is monkey-patched to return canned responses, and
``_with_assist_session`` is short-circuited so tests don't need a
running brainbox.

AI Assist now authors MARKDOWN templates parsed by ``brainbox.loop_md``.
The validate-and-retry shape is unchanged; only the artifact format
changed (YAML LoopSpec → markdown frontmatter + sections).
"""

from __future__ import annotations

import pytest

import brainbox.loop_assist as loop_assist
from brainbox.loop_assist import (
    AssistError,
    _strip_fences,
    _validate_markdown,
    assist,
    build_system_prompt,
)


_VALID_LOOP_MD = """\
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


# Missing "# When to stop" section — fails markdown validation.
_INVALID_LOOP_MD = """\
---
name: test-loop
trigger: manual
max_iterations: 3
---

# Role
You do a thing.

# When to escalate
- The thing breaks.
"""


# ---------------------------------------------------------------------------
# System prompt composer
# ---------------------------------------------------------------------------


class TestBuildSystemPrompt:
    def test_generate_includes_schema_and_example(self):
        prompt = build_system_prompt("generate")
        # The canonical example references pr_number — pulled from the
        # bundled pr-review-loop template.
        assert "pr_number" in prompt

    def test_generate_includes_hard_rules(self):
        prompt = build_system_prompt("generate")
        # Hard rules name the stop/escalate sections as required.
        assert "when to stop" in prompt.lower()
        assert "when to escalate" in prompt.lower()

    def test_explain_is_short_and_skips_schema(self):
        prompt = build_system_prompt("explain")
        # Explain is a read task, not authoring — no full example.
        assert "pr_number" not in prompt
        assert "natural-language" in prompt.lower() or "explanation" in prompt.lower()


# ---------------------------------------------------------------------------
# Markdown validation helper
# ---------------------------------------------------------------------------


class TestValidateMarkdown:
    def test_valid_passes(self):
        ok, warnings = _validate_markdown(_VALID_LOOP_MD)
        assert ok is True
        assert warnings == []

    def test_missing_frontmatter_returns_warning(self):
        ok, warnings = _validate_markdown("# Role\nbare body, no frontmatter\n")
        assert ok is False
        assert len(warnings) > 0

    def test_missing_section_returns_warning(self):
        ok, warnings = _validate_markdown(_INVALID_LOOP_MD)
        assert ok is False
        assert any(
            "stop" in (w.message or "").lower()
            for w in warnings
        )


# ---------------------------------------------------------------------------
# Fence stripping (defensive cleanup of model output)
# ---------------------------------------------------------------------------


class TestStripFences:
    def test_no_fences_passthrough(self):
        assert _strip_fences("name: x") == "name: x"

    def test_strips_markdown_fence(self):
        assert _strip_fences("```markdown\nname: x\n```") == "name: x"

    def test_strips_bare_fence(self):
        assert _strip_fences("```\nname: x\n```") == "name: x"

    def test_strips_leading_fence_only(self):
        assert _strip_fences("```markdown\nname: x") == "name: x"


# ---------------------------------------------------------------------------
# Validate-and-retry loop — session-backed
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_session(monkeypatch):
    """Patch out the session-provisioning + LLM-call path.

    Returns a list the test populates with the sequence of responses
    the simulated session should hand back. Each call pops one off the
    front. ``_with_assist_session`` is short-circuited so no real
    brainbox session is created."""
    responses: list[dict] = []

    async def _fake_call(client, session_name, *, system, user):
        if not responses:
            raise AssertionError("test ran out of fake session responses")
        return responses.pop(0)

    async def _fake_with_session(fn):
        # Sentinel client + name — _fake_call ignores them.
        return await fn(object(), "fake-assist-session")

    monkeypatch.setattr(loop_assist, "_call_session", _fake_call)
    monkeypatch.setattr(loop_assist, "_with_assist_session", _fake_with_session)
    return responses


class TestRetryLoop:
    @pytest.mark.asyncio
    async def test_succeeds_on_first_valid_output(self, fake_session):
        fake_session.append({"text": _VALID_LOOP_MD, "input_tokens": 0, "output_tokens": 0})
        result = await assist(mode="generate", prompt="build me a thing")
        assert result.retries == 0
        assert result.warnings == []
        assert "test-loop" in result.yaml
        assert result.model == "brainbox-session"
        # Session path doesn't surface token usage.
        assert result.input_tokens == 0
        assert result.output_tokens == 0
        assert result.cost_usd == 0.0

    @pytest.mark.asyncio
    async def test_retries_on_invalid_and_succeeds(self, fake_session):
        fake_session.append({"text": _INVALID_LOOP_MD, "input_tokens": 0, "output_tokens": 0})
        fake_session.append({"text": _VALID_LOOP_MD, "input_tokens": 0, "output_tokens": 0})
        result = await assist(mode="generate", prompt="x")
        assert result.retries == 1
        assert result.warnings == []

    @pytest.mark.asyncio
    async def test_exhausts_retries_returns_last_markdown_with_warnings(self, fake_session):
        # 4 invalid responses: initial + 3 retries
        for _ in range(4):
            fake_session.append({
                "text": _INVALID_LOOP_MD,
                "input_tokens": 0,
                "output_tokens": 0,
            })
        result = await assist(mode="generate", prompt="x")
        assert result.retries == 3
        # Last markdown returned even when invalid — operator can hand-fix.
        assert "test-loop" in result.yaml
        assert len(result.warnings) > 0

    @pytest.mark.asyncio
    async def test_strips_fences_before_validating(self, fake_session):
        fenced = "```markdown\n" + _VALID_LOOP_MD + "```"
        fake_session.append({"text": fenced, "input_tokens": 0, "output_tokens": 0})
        result = await assist(mode="generate", prompt="x")
        assert result.retries == 0
        assert "```" not in result.yaml


class TestRefineMode:
    @pytest.mark.asyncio
    async def test_refine_assembles_head_replacement_tail(self, fake_session):
        # Replace the frontmatter "name:" line of the valid template with
        # a refined name. Lines are 1-indexed; line 2 is "name: test-loop".
        current = _VALID_LOOP_MD
        replacement = "name: refined-loop"
        fake_session.append({"text": replacement, "input_tokens": 0, "output_tokens": 0})
        result = await assist(
            mode="refine",
            prompt="rename it",
            current_yaml=current,
            selection={"start_line": 2, "end_line": 2},
        )
        assert result.retries == 0
        assert "refined-loop" in result.yaml
        # Following content preserved
        assert "# Role" in result.yaml
        assert "# When to stop" in result.yaml

    @pytest.mark.asyncio
    async def test_refine_without_selection_errors(self):
        with pytest.raises(AssistError, match="selection"):
            await assist(
                mode="refine",
                prompt="x",
                current_yaml=_VALID_LOOP_MD,
                selection=None,
            )


class TestExplainMode:
    @pytest.mark.asyncio
    async def test_explain_returns_text_no_validation(self, fake_session):
        fake_session.append({
            "text": "This section says the loop stops when the thing is done.",
            "input_tokens": 0,
            "output_tokens": 0,
        })
        result = await assist(
            mode="explain",
            prompt="what does this stop section do?",
            current_yaml=_VALID_LOOP_MD,
            selection={"start_line": 9, "end_line": 10},
        )
        assert result.yaml == ""
        assert "stops" in result.explanation
        assert result.model == "brainbox-session"

    @pytest.mark.asyncio
    async def test_explain_no_selection_works(self, fake_session):
        fake_session.append({
            "text": "A general answer.",
            "input_tokens": 0,
            "output_tokens": 0,
        })
        result = await assist(mode="explain", prompt="explain loops")
        assert "general answer" in result.explanation


class TestModeDispatch:
    @pytest.mark.asyncio
    async def test_unknown_mode_raises(self):
        with pytest.raises(AssistError, match="unknown mode"):
            await assist(mode="frobnicate", prompt="x")

    @pytest.mark.asyncio
    async def test_empty_prompt_raises(self):
        with pytest.raises(AssistError, match="prompt"):
            await assist(mode="generate", prompt="")
        with pytest.raises(AssistError, match="prompt"):
            await assist(mode="generate", prompt="   ")


# ---------------------------------------------------------------------------
# HTTP route
# ---------------------------------------------------------------------------


class TestAssistEndpoint:
    @pytest.mark.asyncio
    async def test_happy_path(self, client, fake_session):
        fake_session.append({
            "text": _VALID_LOOP_MD,
            "input_tokens": 0,
            "output_tokens": 0,
        })
        async with client as c:
            resp = await c.post(
                "/api/loops/templates/assist",
                json={"mode": "generate", "prompt": "build a thing"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "test-loop" in data["yaml"]
        # No API keys path — token + cost surfaces are placeholders.
        assert data["tokens"]["input"] == 0
        assert data["cost_usd"] == 0.0
        assert data["model"] == "brainbox-session"

    @pytest.mark.asyncio
    async def test_session_failure_returns_502(self, client, monkeypatch):
        async def _boom(fn):
            raise loop_assist.AssistError("upstream session call failed: boom")

        monkeypatch.setattr(loop_assist, "_with_assist_session", _boom)
        async with client as c:
            resp = await c.post(
                "/api/loops/templates/assist",
                json={"mode": "generate", "prompt": "build a thing"},
            )
        assert resp.status_code == 502

    @pytest.mark.asyncio
    async def test_bad_mode_returns_400(self, client):
        async with client as c:
            resp = await c.post(
                "/api/loops/templates/assist",
                json={"mode": "ghost", "prompt": "x"},
            )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_missing_prompt_returns_400(self, client):
        async with client as c:
            resp = await c.post(
                "/api/loops/templates/assist",
                json={"mode": "generate"},
            )
        assert resp.status_code == 400

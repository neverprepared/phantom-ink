"""Tests for the AI Assist authoring path (loop-spec PR 6).

Two layers:

  - loop_assist module: system-prompt composition, validate-and-retry
    loop, cost computation, response cleanup (code-fence stripping)
  - HTTP route: bad body, missing prompt, missing API key → 503,
    mode dispatch

The anthropic client is monkey-patched throughout — no real API calls.
Tests own a fake _call_anthropic that returns whatever the test wants
to feed into the retry loop.
"""

from __future__ import annotations

import pytest

import brainbox.loop_assist as loop_assist
from brainbox.config import settings
from brainbox.loop_assist import (
    AssistError,
    AssistResult,
    AssistWarning,
    _cost,
    _simplify_schema,
    _strip_fences,
    _validate_yaml,
    assist,
    build_system_prompt,
)


_VALID_LOOP_YAML = """\
name: test-loop
intent:
  outcome: x
  convergence: "`true`"
body:
  nodes:
    - id: n
      role: reviewer
convergence_metric: "`0`"
"""


# ---------------------------------------------------------------------------
# System prompt composer
# ---------------------------------------------------------------------------


class TestBuildSystemPrompt:
    def test_generate_includes_schema_and_example(self):
        prompt = build_system_prompt("generate")
        assert "schema" in prompt.lower()
        # The canonical example references pr_number — pulled from the
        # bundled pr-review-loop template.
        assert "pr_number" in prompt

    def test_generate_includes_hard_rules(self):
        prompt = build_system_prompt("generate")
        # Hard rules name convergence as required — this is the central
        # forcing function and the prompt must reinforce it.
        assert "convergence" in prompt.lower()

    def test_explain_is_short_and_skips_schema(self):
        prompt = build_system_prompt("explain")
        # Schema is heavy; explain mode runs on haiku and stays cheap.
        assert "pr_number" not in prompt  # no full example
        assert "natural-language" in prompt.lower() or "explanation" in prompt.lower()


class TestSimplifySchema:
    def test_keeps_top_level_properties(self):
        schema = {
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {"type": "string", "description": "name"},
                "max_iterations": {"type": "integer", "default": 5},
            },
            "$defs": {"some": {"type": "object"}},  # noise; should be dropped
        }
        slim = _simplify_schema(schema)
        assert slim["type"] == "object"
        assert slim["required"] == ["name"]
        assert "$defs" not in slim
        assert slim["properties"]["name"]["type"] == "string"
        assert slim["properties"]["max_iterations"]["default"] == 5


# ---------------------------------------------------------------------------
# Cost computation
# ---------------------------------------------------------------------------


class TestCost:
    def test_known_model_pricing(self):
        # claude-sonnet-4-6: $3/M input, $15/M output
        assert _cost("claude-sonnet-4-6", 1_000_000, 0) == pytest.approx(3.00)
        assert _cost("claude-sonnet-4-6", 0, 1_000_000) == pytest.approx(15.00)

    def test_unknown_model_falls_back_to_sonnet_pricing(self):
        # Defensive: an unrecognized model name shouldn't crash; fall
        # back to a reasonable estimate so the operator-facing ticker
        # still gives a ballpark.
        assert _cost("imaginary-model", 1_000_000, 0) == pytest.approx(3.00)


# ---------------------------------------------------------------------------
# YAML validation helper
# ---------------------------------------------------------------------------


class TestValidateYaml:
    def test_valid_passes(self):
        ok, warnings = _validate_yaml(_VALID_LOOP_YAML)
        assert ok is True
        assert warnings == []

    def test_invalid_yaml_returns_warnings(self):
        ok, warnings = _validate_yaml("not [valid")
        assert ok is False
        assert len(warnings) > 0

    def test_non_mapping_returns_warning(self):
        ok, warnings = _validate_yaml("- just\n- a list\n")
        assert ok is False

    def test_missing_convergence_returns_field_warning(self):
        bad = """\
name: no-conv
intent:
  outcome: x
body:
  nodes:
    - id: n
      role: reviewer
"""
        ok, warnings = _validate_yaml(bad)
        assert ok is False
        # Should mention convergence in either message or field
        assert any(
            "convergence" in (w.message or "").lower()
            or "convergence" in (w.field or "").lower()
            for w in warnings
        )


# ---------------------------------------------------------------------------
# Fence stripping (defensive cleanup of model output)
# ---------------------------------------------------------------------------


class TestStripFences:
    def test_no_fences_passthrough(self):
        assert _strip_fences("name: x") == "name: x"

    def test_strips_yaml_fence(self):
        assert _strip_fences("```yaml\nname: x\n```") == "name: x"

    def test_strips_bare_fence(self):
        assert _strip_fences("```\nname: x\n```") == "name: x"

    def test_strips_leading_fence_only(self):
        # Half-fenced output — be tolerant
        assert _strip_fences("```yaml\nname: x") == "name: x"


# ---------------------------------------------------------------------------
# Validate-and-retry loop
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_llm(monkeypatch):
    """Replace _call_anthropic with a test-controlled responder. Returns
    a list the test populates with the sequence of (text, in_tokens,
    out_tokens) responses the model should hand back. Each call to the
    LLM pops one off the front."""
    responses: list[dict] = []

    def _fake(model, system, user, max_tokens=4096):
        if not responses:
            raise AssertionError("test ran out of fake LLM responses")
        return responses.pop(0)

    monkeypatch.setattr(loop_assist, "_call_anthropic", _fake)
    return responses


class TestRetryLoop:
    def test_succeeds_on_first_valid_output(self, fake_llm):
        fake_llm.append({"text": _VALID_LOOP_YAML, "input_tokens": 100, "output_tokens": 50})
        result = assist(mode="generate", prompt="build me a thing")
        assert result.retries == 0
        assert result.warnings == []
        assert "test-loop" in result.yaml
        assert result.input_tokens == 100
        assert result.output_tokens == 50
        # Cost reflects the default model (claude-sonnet-4-6)
        assert result.model == settings.loop_assist_model

    def test_retries_on_invalid_and_succeeds(self, fake_llm):
        fake_llm.append({"text": "not [valid yaml", "input_tokens": 50, "output_tokens": 10})
        fake_llm.append({"text": _VALID_LOOP_YAML, "input_tokens": 60, "output_tokens": 40})
        result = assist(mode="generate", prompt="x")
        assert result.retries == 1
        assert result.warnings == []
        # Aggregate tokens accumulate across retries
        assert result.input_tokens == 110
        assert result.output_tokens == 50

    def test_exhausts_retries_returns_last_yaml_with_warnings(self, fake_llm):
        # 4 invalid responses: initial + 3 retries
        for _ in range(4):
            fake_llm.append({
                "text": "name: no-convergence-here\n",
                "input_tokens": 20,
                "output_tokens": 10,
            })
        result = assist(mode="generate", prompt="x")
        assert result.retries == 3
        # Last YAML is returned even when invalid — operator can hand-fix
        assert "no-convergence-here" in result.yaml
        # Warnings carry the validation error from the final attempt
        assert len(result.warnings) > 0

    def test_strips_fences_before_validating(self, fake_llm):
        fenced = "```yaml\n" + _VALID_LOOP_YAML + "```"
        fake_llm.append({"text": fenced, "input_tokens": 50, "output_tokens": 30})
        result = assist(mode="generate", prompt="x")
        # Validation succeeded because the fence was stripped first
        assert result.retries == 0
        assert "```" not in result.yaml


class TestRefineMode:
    def test_refine_assembles_head_replacement_tail(self, fake_llm):
        current = _VALID_LOOP_YAML
        # Replace lines 1-2 (the `name: test-loop` and `intent:` lines)
        replacement = "name: refined-loop\nintent:"
        fake_llm.append({"text": replacement, "input_tokens": 80, "output_tokens": 20})
        result = assist(
            mode="refine",
            prompt="rename it",
            current_yaml=current,
            selection={"start_line": 1, "end_line": 2},
        )
        assert result.retries == 0
        assert "refined-loop" in result.yaml
        # Following content preserved
        assert "outcome: x" in result.yaml

    def test_refine_without_selection_errors(self):
        with pytest.raises(AssistError, match="selection"):
            assist(
                mode="refine",
                prompt="x",
                current_yaml=_VALID_LOOP_YAML,
                selection=None,
            )


class TestExplainMode:
    def test_explain_returns_text_no_validation(self, fake_llm, monkeypatch):
        # Explain runs on the explain model — different default
        fake_llm.append({
            "text": "This predicate checks that no blockers remain and CI is green.",
            "input_tokens": 100,
            "output_tokens": 30,
        })
        result = assist(
            mode="explain",
            prompt="what does this convergence predicate do?",
            current_yaml=_VALID_LOOP_YAML,
            selection={"start_line": 3, "end_line": 4},
        )
        assert result.yaml == ""
        assert "blockers" in result.explanation
        assert result.model == settings.loop_assist_explain_model

    def test_explain_no_selection_works(self, fake_llm):
        fake_llm.append({
            "text": "A general answer.",
            "input_tokens": 50,
            "output_tokens": 10,
        })
        result = assist(mode="explain", prompt="explain loops")
        assert "general answer" in result.explanation


class TestModeDispatch:
    def test_unknown_mode_raises(self):
        with pytest.raises(AssistError, match="unknown mode"):
            assist(mode="frobnicate", prompt="x")

    def test_empty_prompt_raises(self):
        with pytest.raises(AssistError, match="prompt"):
            assist(mode="generate", prompt="")
        with pytest.raises(AssistError, match="prompt"):
            assist(mode="generate", prompt="   ")


# ---------------------------------------------------------------------------
# HTTP route
# ---------------------------------------------------------------------------


class TestAssistEndpoint:
    @pytest.mark.asyncio
    async def test_happy_path(self, client, fake_llm):
        fake_llm.append({
            "text": _VALID_LOOP_YAML,
            "input_tokens": 100,
            "output_tokens": 50,
        })
        async with client as c:
            resp = await c.post(
                "/api/loops/templates/assist",
                json={"mode": "generate", "prompt": "build a thing"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "test-loop" in data["yaml"]
        assert data["tokens"]["input"] == 100
        assert data["cost_usd"] >= 0

    @pytest.mark.asyncio
    async def test_missing_api_key_returns_503(self, client, monkeypatch):
        # Force the LLM call path (no fake_llm patch) so the real
        # _call_anthropic runs and raises AssistError on missing key.
        monkeypatch.setattr(settings, "anthropic_api_key", "")
        async with client as c:
            resp = await c.post(
                "/api/loops/templates/assist",
                json={"mode": "generate", "prompt": "build a thing"},
            )
        assert resp.status_code == 503

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

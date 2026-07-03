"""Tests for declarative step compilation (manifest → concrete binding)."""

from __future__ import annotations

import pytest

from brainbox import step_spec
from brainbox.config import settings
from brainbox.step_spec import StepSpec, StepValidationError, compile_step


@pytest.fixture(autouse=True)
def _providers(monkeypatch):
    monkeypatch.setattr(settings.orchestration, "default_ceiling", "public")
    monkeypatch.setattr(settings.orchestration, "ollama_url", "http://localhost:11434")
    monkeypatch.setattr(settings.orchestration, "claude_url", "https://api.anthropic.com")
    monkeypatch.setattr(settings.orchestration, "codex_url", "https://api.openai.com")


class TestParse:
    def test_from_dict(self):
        s = StepSpec.from_dict({"residency": "infra", "requires": ["coding"], "prefers": ["cheap"]})
        assert s.residency == "infra" and s.requires == ("coding",) and s.prefers == ("cheap",)

    def test_empty_and_is_declared(self):
        assert not StepSpec.from_dict({}).is_declared()
        assert not StepSpec.from_dict(None).is_declared()
        assert StepSpec.from_dict({"requires": ["coding"]}).is_declared()


class TestCompile:
    def test_local_ceiling_binds_ollama(self):
        rs = compile_step("fresh", StepSpec(residency="local", requires=("coding",)))
        assert rs.provider == "ollama"
        assert rs.ceiling == "local"

    def test_missing_residency_uses_profile_default(self, monkeypatch):
        # global default is public → claude eligible for a vision step
        rs = compile_step("fresh", StepSpec(requires=("vision",)))
        assert rs.ceiling == "public"
        assert rs.provider == "claude"

    def test_blocked_is_compile_error(self):
        # vision only claude (public); local ceiling → no compliant provider
        with pytest.raises(StepValidationError) as exc:
            compile_step("fresh", StepSpec(residency="local", requires=("vision",)))
        assert "fail-closed" in str(exc.value)

    def test_bad_zone_is_compile_error(self):
        with pytest.raises(StepValidationError):
            compile_step("fresh", StepSpec(residency="trusted"))

    def test_resolved_step_carries_plan(self):
        rs = compile_step("fresh", StepSpec(residency="public", requires=("coding",), prefers=("cheap",)))
        assert rs.provider == "ollama"                 # cheap preference
        assert rs.plan.blocked is False
        assert isinstance(rs.eligible_tools, tuple)


def test_module_exports():
    assert hasattr(step_spec, "compile_step")

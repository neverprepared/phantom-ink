"""Tests for per-task ModelTarget (ADR-001 Phase 2, core slice).

Covers the new selector model, the scheduler's task→pipeline kwargs mapping
(including the default-preservation invariant), and submit_task threading.
The provision/env-injection path is unchanged and already covered by
test_ollama_lifecycle.py.
"""

from __future__ import annotations

import pydantic
import pytest

import brainbox.registry as reg_module
import brainbox.router as router_module
from brainbox.models import AgentDefinition, ModelTarget, Task, TaskStatus
from brainbox.scheduler import _model_pipeline_kwargs


@pytest.fixture
def worker_agent():
    agent = AgentDefinition(
        name="worker", image="test-image",
        capabilities=["shell_exec", "write_code", "task_submit"],
    )
    reg_module._agents["worker"] = agent
    return agent


def _task(**kw) -> Task:
    return Task(
        id="t-1", description="d", agent_name="worker",
        status=TaskStatus.PENDING, created_at=0, updated_at=0, **kw,
    )


class TestModelPipelineKwargs:
    def test_none_target_returns_empty(self):
        # Default-preservation invariant: no target → no llm_* kwargs passed,
        # so dispatch is byte-for-byte the legacy claude-default path.
        assert _model_pipeline_kwargs(_task()) == {}

    def test_populated_target_maps_all_fields(self):
        mt = ModelTarget(provider="ollama", model="qwen3:8b", effort=None)
        assert _model_pipeline_kwargs(_task(model_target=mt)) == {
            "llm_provider": "ollama",
            "llm_model": "qwen3:8b",
            "llm_effort": None,
        }

    def test_provider_defaults_to_claude_when_only_model_set(self):
        mt = ModelTarget(model="some-model")
        kw = _model_pipeline_kwargs(_task(model_target=mt))
        assert kw["llm_provider"] == "claude"
        assert kw["llm_model"] == "some-model"


class TestModelTargetValidation:
    def test_rejects_unknown_provider(self):
        with pytest.raises(pydantic.ValidationError):
            ModelTarget(provider="gpt5")

    def test_all_fields_optional(self):
        mt = ModelTarget()
        assert mt.provider is None and mt.model is None and mt.effort is None


class TestSubmitTaskCarriesTarget:
    @pytest.mark.asyncio
    async def test_submit_task_stores_model_target(self, worker_agent):
        mt = ModelTarget(provider="codex", model="some-codex-model")
        task = await router_module.submit_task("do work", "worker", model_target=mt)
        stored = router_module.get_task(task.id)
        assert stored.model_target == mt
        assert stored.model_target.provider == "codex"

    @pytest.mark.asyncio
    async def test_submit_task_without_target_is_none(self, worker_agent):
        task = await router_module.submit_task("do work", "worker")
        assert router_module.get_task(task.id).model_target is None

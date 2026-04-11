"""Tests for pipeline orchestration module."""

from __future__ import annotations

import asyncio
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from brainbox.pipeline import (
    Pipeline,
    PipelineRun,
    PipelineStep,
    RunStatus,
    StepResult,
    StepStatus,
    StepType,
    _build_step_context,
    _interpolate,
    _parse_yaml,
    cancel_run,
    get_pipeline,
    get_run,
    get_state,
    list_pipelines,
    list_runs,
    load_pipelines,
    resolve_pipelines,
    restore_state,
    start_run,
    topo_sort,
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TestPipelineStep:
    def test_basic(self):
        s = PipelineStep(name="step1", type=StepType.OLLAMA_CHAT)
        assert s.name == "step1"
        assert s.type == StepType.OLLAMA_CHAT
        assert s.depends_on == []
        assert s.config == {}
        assert s.timeout is None

    def test_with_deps(self):
        s = PipelineStep(
            name="step2",
            type=StepType.CONTAINER_EXEC,
            depends_on=["step1"],
            config={"command": "echo hi"},
            timeout=30,
        )
        assert s.depends_on == ["step1"]
        assert s.config["command"] == "echo hi"
        assert s.timeout == 30


class TestPipeline:
    def test_basic(self):
        p = Pipeline(
            name="test",
            steps=[PipelineStep(name="s1", type=StepType.API_CALL)],
        )
        assert p.name == "test"
        assert p.version == "1"
        assert len(p.steps) == 1

    def test_defaults(self):
        p = Pipeline(name="p", steps=[])
        assert p.description == ""
        assert p.defaults == {}
        assert p.source_file is None


class TestStepResult:
    def test_defaults(self):
        sr = StepResult(step_name="x")
        assert sr.status == StepStatus.PENDING
        assert sr.output is None
        assert sr.error is None

    def test_fields(self):
        sr = StepResult(
            step_name="x",
            status=StepStatus.COMPLETED,
            output="data",
            started_at=1000,
            finished_at=2000,
            duration_ms=1000,
        )
        assert sr.duration_ms == 1000


class TestPipelineRun:
    def test_defaults(self):
        r = PipelineRun(id="r1", pipeline_name="p", created_at=1000)
        assert r.status == RunStatus.PENDING
        assert r.steps == {}
        assert r.params == {}


# ---------------------------------------------------------------------------
# YAML parsing
# ---------------------------------------------------------------------------


class TestParseYaml:
    def test_parse(self, tmp_path: Path):
        yaml_content = textwrap.dedent("""\
            name: test-pipeline
            description: A test
            version: "2"
            steps:
              - name: step1
                type: ollama-chat
                config:
                  model: qwen3:8b
                  prompt: hello
              - name: step2
                type: container-exec
                depends_on: [step1]
                config:
                  session: my-session
                  command: echo done
                timeout: 60
        """)
        f = tmp_path / "test.yaml"
        f.write_text(yaml_content)

        p = _parse_yaml(f)
        assert p.name == "test-pipeline"
        assert p.description == "A test"
        assert p.version == "2"
        assert len(p.steps) == 2
        assert p.steps[0].type == StepType.OLLAMA_CHAT
        assert p.steps[1].depends_on == ["step1"]
        assert p.steps[1].timeout == 60

    def test_minimal(self, tmp_path: Path):
        yaml_content = textwrap.dedent("""\
            steps:
              - name: s1
                type: api-call
        """)
        f = tmp_path / "minimal.yaml"
        f.write_text(yaml_content)

        p = _parse_yaml(f)
        assert p.name == "minimal"  # derived from filename
        assert len(p.steps) == 1

    def test_invalid_yaml(self, tmp_path: Path):
        f = tmp_path / "bad.yaml"
        f.write_text("just a string")
        with pytest.raises(ValueError, match="Expected YAML dict"):
            _parse_yaml(f)


class TestLoadPipelines:
    def test_load_from_dir(self, tmp_path: Path):
        yaml_content = textwrap.dedent("""\
            name: loaded
            steps:
              - name: s1
                type: api-call
        """)
        (tmp_path / "test.yaml").write_text(yaml_content)

        with patch("brainbox.pipeline._generic_dirs", return_value=[tmp_path]):
            result = load_pipelines()

        assert "loaded" in result
        assert get_pipeline("loaded") is not None
        assert len(list_pipelines()) >= 1

    def test_priority_override(self, tmp_path: Path):
        """Workspace tier overrides generic tier for same-name pipeline."""
        generic_dir = tmp_path / "generic"
        workspace_dir = tmp_path / "workspace" / "pipelines"
        generic_dir.mkdir()
        workspace_dir.mkdir(parents=True)

        (generic_dir / "p.yaml").write_text("name: p\ndescription: builtin\nsteps: []")
        (workspace_dir / "p.yaml").write_text("name: p\ndescription: override\nsteps: []")

        with patch("brainbox.pipeline._generic_dirs", return_value=[generic_dir]):
            load_pipelines()
            result = resolve_pipelines(workspace=str(tmp_path / "workspace"))

        assert result["p"].description == "override"
        assert result["p"].source_tier == "workspace"

    def test_repo_overrides_workspace(self, tmp_path: Path):
        """Repo tier overrides workspace tier for same-name pipeline."""
        generic_dir = tmp_path / "generic"
        workspace_dir = tmp_path / "workspace" / "pipelines"
        repo_dir = tmp_path / "repo" / ".brainbox" / "pipelines"
        generic_dir.mkdir()
        workspace_dir.mkdir(parents=True)
        repo_dir.mkdir(parents=True)

        (generic_dir / "p.yaml").write_text("name: p\ndescription: generic\nsteps: []")
        (workspace_dir / "p.yaml").write_text("name: p\ndescription: workspace\nsteps: []")
        (repo_dir / "p.yaml").write_text("name: p\ndescription: repo\nsteps: []")

        with patch("brainbox.pipeline._generic_dirs", return_value=[generic_dir]):
            load_pipelines()
            result = resolve_pipelines(
                workspace=str(tmp_path / "workspace"),
                repo=str(tmp_path / "repo"),
            )

        assert result["p"].description == "repo"
        assert result["p"].source_tier == "repo"

    def test_tiers_merge_unique_names(self, tmp_path: Path):
        """Pipelines from different tiers with different names all appear."""
        generic_dir = tmp_path / "generic"
        workspace_dir = tmp_path / "workspace" / "pipelines"
        generic_dir.mkdir()
        workspace_dir.mkdir(parents=True)

        (generic_dir / "a.yaml").write_text("name: a\nsteps: []")
        (workspace_dir / "b.yaml").write_text("name: b\nsteps: []")

        with patch("brainbox.pipeline._generic_dirs", return_value=[generic_dir]):
            load_pipelines()
            result = resolve_pipelines(workspace=str(tmp_path / "workspace"))

        assert "a" in result
        assert "b" in result
        assert result["a"].source_tier == "generic"
        assert result["b"].source_tier == "workspace"

    def test_skip_bad_files(self, tmp_path: Path):
        (tmp_path / "good.yaml").write_text("name: good\nsteps:\n  - name: s\n    type: api-call")
        (tmp_path / "bad.yaml").write_text("not valid yaml: [")

        with patch("brainbox.pipeline._generic_dirs", return_value=[tmp_path]):
            result = load_pipelines()

        assert "good" in result


# ---------------------------------------------------------------------------
# Topological sort
# ---------------------------------------------------------------------------


class TestTopoSort:
    def test_no_deps(self):
        steps = [
            PipelineStep(name="a", type=StepType.API_CALL),
            PipelineStep(name="b", type=StepType.API_CALL),
        ]
        waves = topo_sort(steps)
        assert len(waves) == 1
        assert sorted(waves[0]) == ["a", "b"]

    def test_linear_chain(self):
        steps = [
            PipelineStep(name="a", type=StepType.API_CALL),
            PipelineStep(name="b", type=StepType.API_CALL, depends_on=["a"]),
            PipelineStep(name="c", type=StepType.API_CALL, depends_on=["b"]),
        ]
        waves = topo_sort(steps)
        assert waves == [["a"], ["b"], ["c"]]

    def test_diamond(self):
        steps = [
            PipelineStep(name="a", type=StepType.API_CALL),
            PipelineStep(name="b", type=StepType.API_CALL, depends_on=["a"]),
            PipelineStep(name="c", type=StepType.API_CALL, depends_on=["a"]),
            PipelineStep(name="d", type=StepType.API_CALL, depends_on=["b", "c"]),
        ]
        waves = topo_sort(steps)
        assert len(waves) == 3
        assert waves[0] == ["a"]
        assert sorted(waves[1]) == ["b", "c"]
        assert waves[2] == ["d"]

    def test_cycle_detection(self):
        steps = [
            PipelineStep(name="a", type=StepType.API_CALL, depends_on=["b"]),
            PipelineStep(name="b", type=StepType.API_CALL, depends_on=["a"]),
        ]
        with pytest.raises(ValueError, match="Cycle detected"):
            topo_sort(steps)

    def test_unknown_dependency(self):
        steps = [
            PipelineStep(name="a", type=StepType.API_CALL, depends_on=["missing"]),
        ]
        with pytest.raises(ValueError, match="unknown step"):
            topo_sort(steps)

    def test_empty(self):
        assert topo_sort([]) == []


# ---------------------------------------------------------------------------
# Interpolation
# ---------------------------------------------------------------------------


class TestInterpolation:
    def test_basic(self):
        result = _interpolate("Hello ${name}!", {"name": "world"})
        assert result == "Hello world!"

    def test_multiple(self):
        result = _interpolate("${a} and ${b}", {"a": "x", "b": "y"})
        assert result == "x and y"

    def test_no_match(self):
        result = _interpolate("no vars here", {"a": "b"})
        assert result == "no vars here"

    def test_step_context(self):
        run = PipelineRun(
            id="r1",
            pipeline_name="p",
            created_at=1000,
            params={"session": "my-session"},
        )
        run.steps["extract"] = StepResult(
            step_name="extract",
            status=StepStatus.COMPLETED,
            output="extracted text",
        )
        ctx = _build_step_context(run)
        assert ctx["steps.extract.output"] == "extracted text"
        assert ctx["params.session"] == "my-session"


# ---------------------------------------------------------------------------
# State serialization
# ---------------------------------------------------------------------------


class TestState:
    def test_get_state_empty(self):
        # Clear state first
        from brainbox.pipeline import _runs

        _runs.clear()
        state = get_state()
        assert state["runs"] == []

    def test_restore_marks_interrupted(self):
        from brainbox.pipeline import _runs

        _runs.clear()

        state = {
            "runs": [
                (
                    "r1",
                    PipelineRun(
                        id="r1",
                        pipeline_name="p",
                        status=RunStatus.RUNNING,
                        created_at=1000,
                        steps={
                            "s1": StepResult(step_name="s1", status=StepStatus.RUNNING).model_dump()
                        },
                    ).model_dump(),
                )
            ]
        }
        restore_state(state)

        run = _runs["r1"]
        assert run.status == RunStatus.FAILED
        assert "restart" in run.error.lower()
        assert run.steps["s1"].status == StepStatus.SKIPPED

    def test_restore_none(self):
        restore_state(None)  # should not raise


# ---------------------------------------------------------------------------
# Run lifecycle
# ---------------------------------------------------------------------------


class TestStartRun:
    @pytest.mark.asyncio
    async def test_unknown_pipeline(self):
        from brainbox.pipeline import _pipelines

        _pipelines.clear()
        with pytest.raises(ValueError, match="not found"):
            await start_run("nonexistent")

    @pytest.mark.asyncio
    async def test_start_creates_run(self):
        from brainbox.pipeline import _pipelines, _runs

        _pipelines.clear()
        _runs.clear()

        _pipelines["test"] = Pipeline(
            name="test",
            steps=[PipelineStep(name="s1", type=StepType.API_CALL, config={"url": "http://x"})],
        )

        with patch("brainbox.pipeline._execute_run", new_callable=AsyncMock):
            run = await start_run("test")

        assert run.pipeline_name == "test"
        assert "s1" in run.steps
        assert run.id in _runs


class TestCancelRun:
    @pytest.mark.asyncio
    async def test_cancel_unknown(self):
        from brainbox.pipeline import _runs

        _runs.clear()
        with pytest.raises(ValueError, match="not found"):
            await cancel_run("nope")

    @pytest.mark.asyncio
    async def test_cancel_running(self):
        from brainbox.pipeline import _runs

        _runs.clear()
        run = PipelineRun(
            id="r1",
            pipeline_name="p",
            status=RunStatus.RUNNING,
            created_at=1000,
            steps={"s1": StepResult(step_name="s1", status=StepStatus.PENDING)},
        )
        _runs["r1"] = run

        result = await cancel_run("r1")
        assert result.status == RunStatus.CANCELLED
        assert result.steps["s1"].status == StepStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_cancel_completed_fails(self):
        from brainbox.pipeline import _runs

        _runs.clear()
        run = PipelineRun(
            id="r2",
            pipeline_name="p",
            status=RunStatus.COMPLETED,
            created_at=1000,
        )
        _runs["r2"] = run

        with pytest.raises(ValueError, match="cannot be cancelled"):
            await cancel_run("r2")


class TestListRuns:
    def test_filter_by_pipeline(self):
        from brainbox.pipeline import _runs

        _runs.clear()
        _runs["r1"] = PipelineRun(id="r1", pipeline_name="a", created_at=1000)
        _runs["r2"] = PipelineRun(id="r2", pipeline_name="b", created_at=2000)

        result = list_runs(pipeline_name="a")
        assert len(result) == 1
        assert result[0].pipeline_name == "a"

    def test_filter_by_status(self):
        from brainbox.pipeline import _runs

        _runs.clear()
        _runs["r1"] = PipelineRun(
            id="r1", pipeline_name="p", status=RunStatus.RUNNING, created_at=1000
        )
        _runs["r2"] = PipelineRun(
            id="r2", pipeline_name="p", status=RunStatus.COMPLETED, created_at=2000
        )

        result = list_runs(status="running")
        assert len(result) == 1

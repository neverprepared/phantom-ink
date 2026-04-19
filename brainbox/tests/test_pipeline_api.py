"""Tests for pipeline API endpoints — skipped: module was removed."""
import pytest
pytestmark = pytest.mark.skip(reason="brainbox.pipeline module was removed")

from __future__ import annotations

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
)


@pytest.fixture()
def client():
    from httpx import ASGITransport, AsyncClient

    from brainbox.api import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ---------------------------------------------------------------------------
# GET /api/pipelines
# ---------------------------------------------------------------------------


class TestListPipelines:
    @pytest.mark.asyncio
    async def test_empty(self, client):
        with patch("brainbox.api.pipeline_resolve_all", return_value={}):
            resp = await client.get("/api/pipelines")
        assert resp.status_code == 200
        assert resp.json()["pipelines"] == []

    @pytest.mark.asyncio
    async def test_with_pipelines(self, client):
        p = Pipeline(
            name="test",
            description="A test",
            steps=[PipelineStep(name="s1", type=StepType.API_CALL)],
            source_file="/tmp/test.yaml",
        )
        with patch("brainbox.api.pipeline_resolve_all", return_value={"test": p}):
            resp = await client.get("/api/pipelines")
        data = resp.json()
        assert len(data["pipelines"]) == 1
        assert data["pipelines"][0]["name"] == "test"
        assert data["pipelines"][0]["steps"] == 1
        assert data["pipelines"][0]["source_tier"] == "generic"


# ---------------------------------------------------------------------------
# GET /api/pipelines/{name}
# ---------------------------------------------------------------------------


class TestGetPipeline:
    @pytest.mark.asyncio
    async def test_found(self, client):
        p = Pipeline(
            name="test",
            steps=[PipelineStep(name="s1", type=StepType.API_CALL)],
        )
        with patch("brainbox.api.pipeline_get", return_value=p):
            resp = await client.get("/api/pipelines/test")
        assert resp.status_code == 200
        assert resp.json()["name"] == "test"

    @pytest.mark.asyncio
    async def test_not_found(self, client):
        with patch("brainbox.api.pipeline_get", return_value=None):
            resp = await client.get("/api/pipelines/nonexistent")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/pipelines/{name}/run
# ---------------------------------------------------------------------------


class TestStartPipelineRun:
    @pytest.mark.asyncio
    async def test_success(self, client):
        run = PipelineRun(
            id="r1",
            pipeline_name="test",
            status=RunStatus.PENDING,
            created_at=1000,
        )
        with patch("brainbox.api.pipeline_start_run", new_callable=AsyncMock, return_value=run):
            resp = await client.post("/api/pipelines/test/run", json={"params": {"key": "val"}})
        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == "r1"
        assert data["pipeline"] == "test"

    @pytest.mark.asyncio
    async def test_not_found(self, client):
        with patch(
            "brainbox.api.pipeline_start_run",
            new_callable=AsyncMock,
            side_effect=ValueError("Pipeline 'x' not found"),
        ):
            resp = await client.post("/api/pipelines/x/run", json={})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/pipelines/runs
# ---------------------------------------------------------------------------


class TestListRuns:
    @pytest.mark.asyncio
    async def test_empty(self, client):
        with patch("brainbox.api.pipeline_list_runs", return_value=[]):
            resp = await client.get("/api/pipelines/runs")
        assert resp.status_code == 200
        assert resp.json()["runs"] == []

    @pytest.mark.asyncio
    async def test_with_filter(self, client):
        run = PipelineRun(
            id="r1",
            pipeline_name="p",
            status=RunStatus.RUNNING,
            created_at=1000,
        )
        with patch("brainbox.api.pipeline_list_runs", return_value=[run]) as mock:
            resp = await client.get("/api/pipelines/runs?pipeline_name=p&status=running")
        assert resp.status_code == 200
        mock.assert_called_once_with(pipeline_name="p", status="running")


# ---------------------------------------------------------------------------
# GET /api/pipelines/runs/{run_id}
# ---------------------------------------------------------------------------


class TestGetRun:
    @pytest.mark.asyncio
    async def test_found(self, client):
        run = PipelineRun(
            id="r1",
            pipeline_name="p",
            created_at=1000,
            steps={"s1": StepResult(step_name="s1", status=StepStatus.COMPLETED, output="data")},
        )
        with patch("brainbox.api.pipeline_get_run", return_value=run):
            resp = await client.get("/api/pipelines/runs/r1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "r1"
        assert data["steps"]["s1"]["output"] == "data"

    @pytest.mark.asyncio
    async def test_not_found(self, client):
        with patch("brainbox.api.pipeline_get_run", return_value=None):
            resp = await client.get("/api/pipelines/runs/nope")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/pipelines/runs/{run_id}/cancel
# ---------------------------------------------------------------------------


class TestCancelRun:
    @pytest.mark.asyncio
    async def test_success(self, client):
        run = PipelineRun(
            id="r1",
            pipeline_name="p",
            status=RunStatus.CANCELLED,
            created_at=1000,
        )
        with patch("brainbox.api.pipeline_cancel_run", new_callable=AsyncMock, return_value=run):
            resp = await client.post("/api/pipelines/runs/r1/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_invalid(self, client):
        with patch(
            "brainbox.api.pipeline_cancel_run",
            new_callable=AsyncMock,
            side_effect=ValueError("cannot be cancelled"),
        ):
            resp = await client.post("/api/pipelines/runs/r1/cancel")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/pipelines/runs/{run_id}/steps/{step_name}
# ---------------------------------------------------------------------------


class TestGetStep:
    @pytest.mark.asyncio
    async def test_found(self, client):
        run = PipelineRun(
            id="r1",
            pipeline_name="p",
            created_at=1000,
            steps={"s1": StepResult(step_name="s1", status=StepStatus.COMPLETED, output="ok")},
        )
        with patch("brainbox.api.pipeline_get_run", return_value=run):
            resp = await client.get("/api/pipelines/runs/r1/steps/s1")
        assert resp.status_code == 200
        assert resp.json()["output"] == "ok"

    @pytest.mark.asyncio
    async def test_run_not_found(self, client):
        with patch("brainbox.api.pipeline_get_run", return_value=None):
            resp = await client.get("/api/pipelines/runs/nope/steps/s1")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_step_not_found(self, client):
        run = PipelineRun(id="r1", pipeline_name="p", created_at=1000)
        with patch("brainbox.api.pipeline_get_run", return_value=run):
            resp = await client.get("/api/pipelines/runs/r1/steps/missing")
        assert resp.status_code == 404

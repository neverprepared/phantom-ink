"""Tests for LangFuse API proxy endpoints."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from brainbox.config import settings
from brainbox.langfuse_client import (
    LangfuseError,
    ObservationResult,
    SessionSummary,
    TraceResult,
)


class TestLangfuseHealthEndpoint:
    @pytest.fixture()
    def client(self):
        from httpx import ASGITransport, AsyncClient

        from brainbox.api import app

        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    @pytest.mark.asyncio
    async def test_healthy(self, client, monkeypatch):
        monkeypatch.setattr(settings.langfuse, "mode", "warn")
        with patch("brainbox.api.langfuse_health_check", return_value=True):
            resp = await client.get("/api/langfuse/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["healthy"] is True
        assert data["mode"] == "warn"

    @pytest.mark.asyncio
    async def test_unhealthy(self, client, monkeypatch):
        monkeypatch.setattr(settings.langfuse, "mode", "warn")
        with patch("brainbox.api.langfuse_health_check", return_value=False):
            resp = await client.get("/api/langfuse/health")
        assert resp.status_code == 200
        assert resp.json()["healthy"] is False

    @pytest.mark.asyncio
    async def test_off_mode(self, client, monkeypatch):
        monkeypatch.setattr(settings.langfuse, "mode", "off")
        resp = await client.get("/api/langfuse/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["healthy"] is False
        assert data["mode"] == "off"


class TestLangfuseSessionTraces:
    @pytest.fixture()
    def client(self):
        from httpx import ASGITransport, AsyncClient

        from brainbox.api import app

        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    @pytest.mark.asyncio
    async def test_returns_traces(self, client, monkeypatch):
        monkeypatch.setattr(settings.langfuse, "mode", "warn")
        traces = [
            TraceResult(
                id="t1",
                name="tool_call",
                session_id="test-session",
                timestamp="2025-01-01T00:00:00Z",
                status="ok",
            ),
        ]
        with patch("brainbox.api.langfuse_list_traces", return_value=traces):
            resp = await client.get("/api/langfuse/sessions/test-session/traces")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == "t1"

    @pytest.mark.asyncio
    async def test_off_returns_503(self, client, monkeypatch):
        monkeypatch.setattr(settings.langfuse, "mode", "off")
        resp = await client.get("/api/langfuse/sessions/test-session/traces")
        assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_warn_swallows_errors(self, client, monkeypatch):
        monkeypatch.setattr(settings.langfuse, "mode", "warn")
        with patch(
            "brainbox.api.langfuse_list_traces",
            side_effect=LangfuseError("list_traces", "connection refused"),
        ):
            resp = await client.get("/api/langfuse/sessions/test-session/traces")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_enforce_returns_502(self, client, monkeypatch):
        monkeypatch.setattr(settings.langfuse, "mode", "enforce")
        with patch(
            "brainbox.api.langfuse_list_traces",
            side_effect=LangfuseError("list_traces", "connection refused"),
        ):
            resp = await client.get("/api/langfuse/sessions/test-session/traces")
        assert resp.status_code == 502


class TestLangfuseSessionSummary:
    @pytest.fixture()
    def client(self):
        from httpx import ASGITransport, AsyncClient

        from brainbox.api import app

        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    @pytest.mark.asyncio
    async def test_returns_summary(self, client, monkeypatch):
        monkeypatch.setattr(settings.langfuse, "mode", "warn")
        summary = SessionSummary(
            session_id="test-session",
            total_traces=10,
            total_observations=25,
            error_count=2,
            tool_counts={"Read": 15, "Write": 10},
        )
        with patch("brainbox.api.get_session_traces_summary", return_value=summary):
            resp = await client.get("/api/langfuse/sessions/test-session/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_traces"] == 10
        assert data["error_count"] == 2
        assert data["tool_counts"]["Read"] == 15

    @pytest.mark.asyncio
    async def test_off_returns_503(self, client, monkeypatch):
        monkeypatch.setattr(settings.langfuse, "mode", "off")
        resp = await client.get("/api/langfuse/sessions/test-session/summary")
        assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_warn_returns_empty_on_error(self, client, monkeypatch):
        monkeypatch.setattr(settings.langfuse, "mode", "warn")
        with patch(
            "brainbox.api.get_session_traces_summary",
            side_effect=LangfuseError("get_session_traces_summary", "timeout"),
        ):
            resp = await client.get("/api/langfuse/sessions/test-session/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_traces"] == 0
        assert data["tool_counts"] == {}


class TestLangfuseTraceDetail:
    @pytest.fixture()
    def client(self):
        from httpx import ASGITransport, AsyncClient

        from brainbox.api import app

        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    @pytest.mark.asyncio
    async def test_returns_detail(self, client, monkeypatch):
        monkeypatch.setattr(settings.langfuse, "mode", "warn")
        trace = TraceResult(
            id="t1",
            name="call",
            session_id="s1",
            timestamp="ts",
            status="ok",
        )
        observations = [
            ObservationResult(
                id="o1",
                trace_id="t1",
                name="Read",
                type="SPAN",
                start_time="ts1",
                end_time="ts2",
            ),
        ]
        with patch("brainbox.api.langfuse_get_trace", return_value=(trace, observations)):
            resp = await client.get("/api/langfuse/traces/t1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["trace"]["id"] == "t1"
        assert len(data["observations"]) == 1
        assert data["observations"][0]["name"] == "Read"

    @pytest.mark.asyncio
    async def test_off_returns_503(self, client, monkeypatch):
        monkeypatch.setattr(settings.langfuse, "mode", "off")
        resp = await client.get("/api/langfuse/traces/t1")
        assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_warn_not_available_returns_404(self, client, monkeypatch):
        monkeypatch.setattr(settings.langfuse, "mode", "warn")
        with patch(
            "brainbox.api.langfuse_get_trace",
            side_effect=LangfuseError("get_trace", "connection refused"),
        ):
            resp = await client.get("/api/langfuse/traces/t1")
        assert resp.status_code == 404


class TestMetricsTraceCountMerge:
    @pytest.fixture()
    def client(self):
        from httpx import ASGITransport, AsyncClient

        from brainbox.api import app

        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    @pytest.mark.asyncio
    async def test_metrics_include_trace_counts(self, client, monkeypatch):
        """When LangFuse is available, metrics include trace_count and error_count."""
        monkeypatch.setattr(settings.langfuse, "mode", "warn")

        # Mock Docker to return no containers (simplest case)
        with patch(
            "brainbox.api._get_container_metrics",
            return_value=[
                {
                    "name": "developer-test",
                    "session_name": "test",
                    "role": "developer",
                    "llm_provider": "claude",
                    "workspace_profile": "",
                    "cpu_percent": 5.0,
                    "mem_usage": 1000,
                    "mem_usage_human": "1.0 KB",
                    "mem_limit": 2000,
                    "mem_limit_human": "2.0 KB",
                    "uptime_seconds": 300,
                    "trace_count": 42,
                    "error_count": 3,
                }
            ],
        ):
            resp = await client.get("/api/metrics/containers")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["trace_count"] == 42
        assert data[0]["error_count"] == 3

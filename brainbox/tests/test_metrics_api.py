"""Tests for the container metrics endpoint.

Preserves the pass-through coverage that used to live with the retired
trace-backend tests. The route itself outlived that integration; the
per-session trace and error counts it used to carry were dropped with it.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


class TestContainerMetrics:
    @pytest.mark.asyncio
    async def test_returns_collected_metrics(self, client):
        """/api/metrics/containers serialises whatever the collector returns."""
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
                }
            ],
        ):
            resp = await client.get("/api/metrics/containers")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "developer-test"
        assert data[0]["cpu_percent"] == 5.0
        assert data[0]["uptime_seconds"] == 300
        # Dropped with the trace backend rather than left pinned at zero.
        assert "trace_count" not in data[0]
        assert "error_count" not in data[0]

"""Tests for the Ollama LLM proxy API endpoints.

These tests need to mock TWO things, not one: (a) the pool's pick(), so the
endpoint doesn't early-return 503 with "no Ollama instances available", and
(b) the underlying ollama_* call. The pool plumbing landed when the runner
fleet acquired a multi-host Ollama instance pool; tests written before that
only patched (b) and started failing silently.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from brainbox.ollama import ChatMessage, ChatResult, ModelInfo, OllamaError
from brainbox.ollama_pool import OllamaInstance


def _fake_instance() -> OllamaInstance:
    """A healthy fake pool member for tests that need a non-empty pool."""
    return OllamaInstance(
        runner_name="test-runner",
        url="http://test-runner:11434",
        healthy=True,
        verify_tls=False,
    )


@contextmanager
def _pool_with_instance():
    """Patch get_pool() so pick() returns the fake instance and acquire/release are no-ops."""
    pool = MagicMock()
    pool.pick.return_value = _fake_instance()
    pool.acquire = MagicMock()
    pool.release = MagicMock()
    with patch("brainbox.api.get_pool", return_value=pool):
        yield pool


class TestOllamaHealthEndpoint:

    @pytest.mark.asyncio
    async def test_healthy(self, client):
        with _pool_with_instance(), patch(
            "brainbox.api.ollama_health_check", return_value=True
        ):
            resp = await client.get("/api/ollama/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["healthy"] is True
        assert "host" in data

    @pytest.mark.asyncio
    async def test_unhealthy(self, client):
        # No pool instance → endpoint short-circuits to healthy=False; no need
        # to patch ollama_health_check here.
        empty_pool = MagicMock()
        empty_pool.pick.return_value = None
        with patch("brainbox.api.get_pool", return_value=empty_pool):
            resp = await client.get("/api/ollama/health")
        assert resp.status_code == 200
        assert resp.json()["healthy"] is False


class TestOllamaChatEndpoint:
    @pytest.mark.asyncio
    async def test_success(self, client):
        result = ChatResult(
            model="qwen3:8b",
            message=ChatMessage(role="assistant", content="Hello!"),
            total_duration=5000,
            eval_count=42,
        )
        with _pool_with_instance(), patch(
            "brainbox.api.ollama_chat", return_value=result
        ):
            resp = await client.post(
                "/api/ollama/chat",
                json={"messages": [{"role": "user", "content": "Hi"}]},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["model"] == "qwen3:8b"
        assert data["message"]["content"] == "Hello!"
        assert data["total_duration"] == 5000

    @pytest.mark.asyncio
    async def test_with_model(self, client):
        result = ChatResult(
            model="llama3.2",
            message=ChatMessage(role="assistant", content="ok"),
        )
        with _pool_with_instance(), patch(
            "brainbox.api.ollama_chat", return_value=result
        ):
            resp = await client.post(
                "/api/ollama/chat",
                json={
                    "model": "llama3.2",
                    "messages": [{"role": "user", "content": "test"}],
                },
            )
        assert resp.status_code == 200
        assert resp.json()["model"] == "llama3.2"

    @pytest.mark.asyncio
    async def test_ollama_unreachable_returns_502(self, client):
        with _pool_with_instance(), patch(
            "brainbox.api.ollama_chat",
            side_effect=OllamaError("chat", "could not connect"),
        ):
            resp = await client.post(
                "/api/ollama/chat",
                json={"messages": [{"role": "user", "content": "Hi"}]},
            )
        assert resp.status_code == 502

    @pytest.mark.asyncio
    async def test_missing_messages_returns_422(self, client):
        resp = await client.post("/api/ollama/chat", json={})
        assert resp.status_code == 422


class TestOllamaModelsEndpoint:
    @pytest.mark.asyncio
    async def test_success(self, client):
        models = [
            ModelInfo(
                name="llama3.2:latest", size=4_000_000_000, modified_at="2025-01-01", digest="abc"
            ),
            ModelInfo(name="qwen3:8b", size=8_000_000_000, modified_at="2025-02-01", digest="def"),
        ]
        with _pool_with_instance(), patch(
            "brainbox.api.ollama_list_models", return_value=models
        ):
            resp = await client.get("/api/ollama/models")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["models"]) == 2
        assert data["models"][0]["name"] == "llama3.2:latest"

    @pytest.mark.asyncio
    async def test_empty(self, client):
        with _pool_with_instance(), patch(
            "brainbox.api.ollama_list_models", return_value=[]
        ):
            resp = await client.get("/api/ollama/models")
        assert resp.status_code == 200
        assert resp.json()["models"] == []

    @pytest.mark.asyncio
    async def test_error_returns_502(self, client):
        with _pool_with_instance(), patch(
            "brainbox.api.ollama_list_models",
            side_effect=OllamaError("list_models", "connection refused"),
        ):
            resp = await client.get("/api/ollama/models")
        assert resp.status_code == 502


class TestOllamaPullEndpoint:
    @pytest.mark.asyncio
    async def test_success(self, client):
        with _pool_with_instance(), patch(
            "brainbox.api.ollama_pull_model", return_value="success"
        ):
            resp = await client.post(
                "/api/ollama/pull",
                json={"name": "llama3.2"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["model"] == "llama3.2"

    @pytest.mark.asyncio
    async def test_error_returns_502(self, client):
        with _pool_with_instance(), patch(
            "brainbox.api.ollama_pull_model",
            side_effect=OllamaError("pull_model", "connection refused"),
        ):
            resp = await client.post(
                "/api/ollama/pull",
                json={"name": "nonexistent"},
            )
        assert resp.status_code == 502

    @pytest.mark.asyncio
    async def test_missing_name_returns_422(self, client):
        resp = await client.post("/api/ollama/pull", json={})
        assert resp.status_code == 422

"""Tests for Ollama API client."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from brainbox.ollama import (
    ChatMessage,
    ChatResult,
    ModelInfo,
    OllamaError,
    chat,
    health_check,
    list_models,
    pull_model,
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


class TestChatMessage:
    def test_fields(self):
        m = ChatMessage(role="assistant", content="hello")
        assert m.role == "assistant"
        assert m.content == "hello"

    def test_frozen(self):
        m = ChatMessage(role="user", content="hi")
        with pytest.raises(AttributeError):
            m.role = "other"  # type: ignore[misc]


class TestChatResult:
    def test_fields(self):
        r = ChatResult(
            model="qwen3:8b",
            message=ChatMessage(role="assistant", content="hi"),
            total_duration=1000,
            eval_count=10,
        )
        assert r.model == "qwen3:8b"
        assert r.message.content == "hi"
        assert r.total_duration == 1000

    def test_defaults(self):
        r = ChatResult(
            model="test",
            message=ChatMessage(role="assistant", content=""),
        )
        assert r.total_duration == 0
        assert r.eval_count == 0

    def test_frozen(self):
        r = ChatResult(model="m", message=ChatMessage(role="a", content="b"))
        with pytest.raises(AttributeError):
            r.model = "other"  # type: ignore[misc]


class TestModelInfo:
    def test_fields(self):
        m = ModelInfo(
            name="llama3.2", size=4_000_000_000, modified_at="2025-01-01", digest="abc123"
        )
        assert m.name == "llama3.2"
        assert m.size == 4_000_000_000

    def test_frozen(self):
        m = ModelInfo(name="n", size=0, modified_at="", digest="")
        with pytest.raises(AttributeError):
            m.name = "other"  # type: ignore[misc]


class TestOllamaError:
    def test_message_format(self):
        err = OllamaError("chat", "connection refused")
        assert "chat" in str(err)
        assert "connection refused" in str(err)

    def test_fields(self):
        err = OllamaError("op", "reason")
        assert err.operation == "op"
        assert err.reason == "reason"

    def test_is_runtime_error(self):
        assert isinstance(OllamaError("op", "r"), RuntimeError)


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------


class TestHealthCheck:
    @patch("brainbox.ollama._client")
    def test_healthy(self, mock_client_fn):
        mock_client = MagicMock()
        mock_client.get.return_value = MagicMock(status_code=200)
        mock_client_fn.return_value = mock_client

        assert health_check() is True

    @patch("brainbox.ollama._client")
    def test_unhealthy(self, mock_client_fn):
        mock_client = MagicMock()
        mock_client.get.side_effect = httpx.ConnectError("refused")
        mock_client_fn.return_value = mock_client

        assert health_check() is False

    @patch("brainbox.ollama._client")
    def test_non_200(self, mock_client_fn):
        mock_client = MagicMock()
        mock_client.get.return_value = MagicMock(status_code=503)
        mock_client_fn.return_value = mock_client

        assert health_check() is False


# ---------------------------------------------------------------------------
# chat
# ---------------------------------------------------------------------------


class TestChat:
    @patch("brainbox.ollama._client")
    def test_success(self, mock_client_fn):
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "model": "qwen3:8b",
            "message": {"role": "assistant", "content": "Hello!"},
            "total_duration": 5000,
            "eval_count": 42,
        }
        mock_resp.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client_fn.return_value = mock_client

        result = chat([{"role": "user", "content": "Hi"}])

        assert result.model == "qwen3:8b"
        assert result.message.role == "assistant"
        assert result.message.content == "Hello!"
        assert result.total_duration == 5000
        assert result.eval_count == 42

    @patch("brainbox.ollama._client")
    def test_custom_model(self, mock_client_fn):
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "model": "llama3.2",
            "message": {"role": "assistant", "content": "ok"},
        }
        mock_resp.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client_fn.return_value = mock_client

        result = chat([{"role": "user", "content": "test"}], model="llama3.2")

        # Verify the model was passed in the request
        call_args = mock_client.post.call_args
        assert call_args[1]["json"]["model"] == "llama3.2"
        assert result.model == "llama3.2"

    @patch("brainbox.ollama._client")
    def test_connect_error(self, mock_client_fn):
        mock_client = MagicMock()
        mock_client.post.side_effect = httpx.ConnectError("refused")
        mock_client_fn.return_value = mock_client

        with pytest.raises(OllamaError, match="chat"):
            chat([{"role": "user", "content": "Hi"}])

    @patch("brainbox.ollama._client")
    def test_http_status_error(self, mock_client_fn):
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=mock_resp
        )
        mock_client.post.return_value = mock_resp
        mock_client_fn.return_value = mock_client

        with pytest.raises(OllamaError, match="chat"):
            chat([{"role": "user", "content": "Hi"}])

    @patch("brainbox.ollama._client")
    def test_malformed_response(self, mock_client_fn):
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"unexpected": "structure"}
        mock_resp.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client_fn.return_value = mock_client

        with pytest.raises(OllamaError, match="unexpected response"):
            chat([{"role": "user", "content": "Hi"}])


# ---------------------------------------------------------------------------
# list_models
# ---------------------------------------------------------------------------


class TestListModels:
    @patch("brainbox.ollama._client")
    def test_success(self, mock_client_fn):
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "models": [
                {
                    "name": "llama3.2:latest",
                    "size": 4_000_000_000,
                    "modified_at": "2025-01-01T00:00:00Z",
                    "digest": "abc123",
                },
                {
                    "name": "qwen3:8b",
                    "size": 8_000_000_000,
                    "modified_at": "2025-02-01T00:00:00Z",
                    "digest": "def456",
                },
            ]
        }
        mock_resp.raise_for_status = MagicMock()
        mock_client.get.return_value = mock_resp
        mock_client_fn.return_value = mock_client

        models = list_models()

        assert len(models) == 2
        assert models[0].name == "llama3.2:latest"
        assert models[1].name == "qwen3:8b"

    @patch("brainbox.ollama._client")
    def test_empty(self, mock_client_fn):
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"models": []}
        mock_resp.raise_for_status = MagicMock()
        mock_client.get.return_value = mock_resp
        mock_client_fn.return_value = mock_client

        assert list_models() == []

    @patch("brainbox.ollama._client")
    def test_connect_error(self, mock_client_fn):
        mock_client = MagicMock()
        mock_client.get.side_effect = httpx.ConnectError("refused")
        mock_client_fn.return_value = mock_client

        with pytest.raises(OllamaError, match="list_models"):
            list_models()


# ---------------------------------------------------------------------------
# pull_model
# ---------------------------------------------------------------------------


class TestPullModel:
    @patch("brainbox.ollama._client")
    def test_success(self, mock_client_fn):
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"status": "success"}
        mock_resp.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client_fn.return_value = mock_client

        status = pull_model("llama3.2")
        assert status == "success"

    @patch("brainbox.ollama._client")
    def test_connect_error(self, mock_client_fn):
        mock_client = MagicMock()
        mock_client.post.side_effect = httpx.ConnectError("refused")
        mock_client_fn.return_value = mock_client

        with pytest.raises(OllamaError, match="pull_model"):
            pull_model("llama3.2")

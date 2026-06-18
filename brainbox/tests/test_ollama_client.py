"""Tests for the sync Ollama client surface (chat / health_check / list_models / pull_model).

These tests mock ``brainbox.ollama._curl_request`` — the single subprocess
wrapper that every sync public function goes through. The legacy httpx
``_client`` attribute was removed when ollama.py moved to a curl-subprocess
transport (see the module's "Implementation note" for the macOS+py3.14
rationale); these tests were rewritten to match the current surface.
"""

from __future__ import annotations

import json
from unittest.mock import patch

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


def _ok(body: dict | str) -> tuple[int, str]:
    """200-status curl response with the given JSON body (or raw string)."""
    text = json.dumps(body) if isinstance(body, dict) else body
    return 200, text


def _err(status: int, text: str = "") -> tuple[int, str]:
    return status, text


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
# health_check — never raises; returns bool. Maps any non-200 / OSError to False.
# ---------------------------------------------------------------------------


class TestHealthCheck:
    @patch("brainbox.ollama._curl_request")
    def test_healthy(self, mock_curl):
        mock_curl.return_value = _ok({})
        assert health_check() is True

    @patch("brainbox.ollama._curl_request")
    def test_unhealthy(self, mock_curl):
        mock_curl.side_effect = OSError("curl failed (rc=7): Connection refused")
        assert health_check() is False

    @patch("brainbox.ollama._curl_request")
    def test_non_200(self, mock_curl):
        mock_curl.return_value = _err(503, "")
        assert health_check() is False


# ---------------------------------------------------------------------------
# chat — raises OllamaError on connect/HTTP/parse failure; returns ChatResult on success.
# ---------------------------------------------------------------------------


class TestChat:
    @patch("brainbox.ollama._curl_request")
    def test_success(self, mock_curl):
        mock_curl.return_value = _ok({
            "model": "qwen3:8b",
            "message": {"role": "assistant", "content": "Hello!"},
            "total_duration": 5000,
            "eval_count": 42,
        })

        result = chat([{"role": "user", "content": "Hi"}])

        assert result.model == "qwen3:8b"
        assert result.message.role == "assistant"
        assert result.message.content == "Hello!"
        assert result.total_duration == 5000
        assert result.eval_count == 42

    @patch("brainbox.ollama._curl_request")
    def test_custom_model(self, mock_curl):
        mock_curl.return_value = _ok({
            "model": "llama3.2",
            "message": {"role": "assistant", "content": "ok"},
        })

        result = chat([{"role": "user", "content": "test"}], model="llama3.2")

        # Verify the body payload sent over curl carried the requested model
        call_kwargs = mock_curl.call_args.kwargs
        sent_body = call_kwargs["body"]
        assert sent_body["model"] == "llama3.2"
        assert result.model == "llama3.2"

    @patch("brainbox.ollama._curl_request")
    def test_connect_error(self, mock_curl):
        mock_curl.side_effect = OSError("curl failed (rc=7): Connection refused")

        with pytest.raises(OllamaError, match="chat"):
            chat([{"role": "user", "content": "Hi"}])

    @patch("brainbox.ollama._curl_request")
    def test_http_status_error(self, mock_curl):
        mock_curl.return_value = _err(500, "Internal Server Error")

        with pytest.raises(OllamaError, match="chat"):
            chat([{"role": "user", "content": "Hi"}])

    @patch("brainbox.ollama._curl_request")
    def test_malformed_response(self, mock_curl):
        mock_curl.return_value = _ok({"unexpected": "structure"})

        with pytest.raises(OllamaError, match="unexpected response"):
            chat([{"role": "user", "content": "Hi"}])


# ---------------------------------------------------------------------------
# list_models
# ---------------------------------------------------------------------------


class TestListModels:
    @patch("brainbox.ollama._curl_request")
    def test_success(self, mock_curl):
        mock_curl.return_value = _ok({
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
        })

        models = list_models()

        assert len(models) == 2
        assert models[0].name == "llama3.2:latest"
        assert models[1].name == "qwen3:8b"

    @patch("brainbox.ollama._curl_request")
    def test_empty(self, mock_curl):
        mock_curl.return_value = _ok({"models": []})
        assert list_models() == []

    @patch("brainbox.ollama._curl_request")
    def test_connect_error(self, mock_curl):
        mock_curl.side_effect = OSError("curl failed (rc=7): Connection refused")

        with pytest.raises(OllamaError, match="list_models"):
            list_models()


# ---------------------------------------------------------------------------
# pull_model
# ---------------------------------------------------------------------------


class TestPullModel:
    @patch("brainbox.ollama._curl_request")
    def test_success(self, mock_curl):
        mock_curl.return_value = _ok({"status": "success"})
        assert pull_model("llama3.2") == "success"

    @patch("brainbox.ollama._curl_request")
    def test_connect_error(self, mock_curl):
        mock_curl.side_effect = OSError("curl failed (rc=7): Connection refused")

        with pytest.raises(OllamaError, match="pull_model"):
            pull_model("llama3.2")

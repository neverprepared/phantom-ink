"""Tests for Phase 2: session identity + capability enforcement.

Covers:
- require_capability() dependency (auth.py)
- BRAINBOX_TOKEN_ID injection (lifecycle.py — env resolution layer)
- POST /api/hub/tasks capability gate
- POST /api/hub/channels/{id}/messages membership + from_participant override
"""

from __future__ import annotations

import time
import uuid
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

import brainbox.auth as auth_module
import brainbox.registry as reg_module
from brainbox.auth import require_capability
from brainbox.models import AgentDefinition, Token


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def api_key(monkeypatch):
    key = "test-api-key-abcdef1234567890"
    monkeypatch.setattr(auth_module, "_api_key", key)
    return key


def _make_agent(name: str, capabilities: list[str]) -> AgentDefinition:
    agent = AgentDefinition(
        name=name,
        image="test-image",
        capabilities=capabilities,
    )
    reg_module._agents[name] = agent
    return agent


def _issue_token(agent_name: str, capabilities: list[str]) -> Token:
    _make_agent(agent_name, capabilities)
    now = int(time.time() * 1000)
    token = Token(
        token_id=str(uuid.uuid4()),
        agent_name=agent_name,
        task_id=str(uuid.uuid4()),
        capabilities=capabilities,
        issued=now,
        expiry=now + 3_600_000,
    )
    reg_module._tokens[token.token_id] = token
    return token


# ---------------------------------------------------------------------------
# require_capability()
# ---------------------------------------------------------------------------


class TestRequireCapability:
    """require_capability returns a FastAPI dependency factory."""

    def _make_request(self, *, api_key: str | None = None, bearer: str | None = None):
        req = MagicMock()
        headers = {}
        if api_key:
            headers["x-api-key"] = api_key
        if bearer:
            headers["authorization"] = f"Bearer {bearer}"
        req.headers = headers
        return req

    def test_api_key_passes_returns_none(self, api_key, monkeypatch):
        dep = require_capability("hub_messaging")
        req = self._make_request(api_key=api_key)
        result = dep(req)
        assert result is None

    def test_wrong_api_key_falls_through_to_token_check(self, monkeypatch):
        monkeypatch.setattr(auth_module, "_api_key", "correct-key")
        dep = require_capability("hub_messaging")
        req = self._make_request(api_key="wrong-key")
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            dep(req)
        assert exc_info.value.status_code == 401

    def test_bearer_with_capability_passes(self, monkeypatch):
        monkeypatch.setattr(auth_module, "_api_key", "the-key")
        token = _issue_token("supervisor", ["hub_messaging", "message_agents"])
        dep = require_capability("hub_messaging")
        req = self._make_request(bearer=token.token_id)
        result = dep(req)
        assert result is not None
        assert result.token_id == token.token_id

    def test_bearer_missing_capability_raises_403(self, monkeypatch):
        monkeypatch.setattr(auth_module, "_api_key", "the-key")
        token = _issue_token("worker", ["message_agents"])  # no hub_messaging
        dep = require_capability("hub_messaging")
        req = self._make_request(bearer=token.token_id)
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            dep(req)
        assert exc_info.value.status_code == 403
        assert "hub_messaging" in str(exc_info.value.detail)

    def test_no_credentials_raises_401(self, monkeypatch):
        monkeypatch.setattr(auth_module, "_api_key", "the-key")
        dep = require_capability("hub_messaging")
        req = self._make_request()
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            dep(req)
        assert exc_info.value.status_code == 401

    def test_expired_token_raises_401(self, monkeypatch):
        monkeypatch.setattr(auth_module, "_api_key", "the-key")
        _make_agent("worker", ["hub_messaging"])
        now = int(time.time() * 1000)
        expired_token = Token(
            token_id=str(uuid.uuid4()),
            agent_name="worker",
            task_id="t1",
            capabilities=["hub_messaging"],
            issued=now - 7_200_000,
            expiry=now - 3_600_000,  # expired 1 hour ago
        )
        reg_module._tokens[expired_token.token_id] = expired_token
        dep = require_capability("hub_messaging")
        req = self._make_request(bearer=expired_token.token_id)
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            dep(req)
        assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# API endpoint tests (hub/tasks and hub/channels)
# ---------------------------------------------------------------------------


@pytest.fixture()
async def client(api_key):
    from brainbox.api import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c.headers["x-api-key"] = api_key
        yield c


@pytest.fixture()
def token_with_capability(api_key):
    return _issue_token("supervisor", ["task_submit", "hub_messaging", "message_agents"])


@pytest.fixture()
def token_without_capability(api_key):
    return _issue_token("worker", ["hub_messaging", "message_agents"])  # no task_submit


class TestHubTasksCapabilityGate:
    async def test_api_key_can_submit_task(self, client):
        """App (API key) can always submit tasks."""
        _make_agent("worker", ["hub_messaging"])
        with patch("brainbox.router.submit_task") as mock_submit:
            from brainbox.models import Task, TaskStatus
            now = int(time.time() * 1000)
            mock_task = Task(
                id="t1", description="test", agent_name="worker",
                status=TaskStatus.RUNNING, created_at=now, updated_at=now,
            )
            mock_submit.return_value = mock_task
            resp = await client.post("/api/hub/tasks", json={
                "description": "test task", "agent_name": "worker"
            })
        assert resp.status_code == 201

    async def test_bearer_with_capability_can_submit(self, client, token_with_capability):
        """Session token with task_submit can submit tasks."""
        _make_agent("worker", ["hub_messaging"])
        with patch("brainbox.router.submit_task") as mock_submit:
            from brainbox.models import Task, TaskStatus
            now = int(time.time() * 1000)
            mock_task = Task(
                id="t1", description="test", agent_name="worker",
                status=TaskStatus.RUNNING, created_at=now, updated_at=now,
            )
            mock_submit.return_value = mock_task
            resp = await client.post(
                "/api/hub/tasks",
                json={"description": "test", "agent_name": "worker"},
                headers={"authorization": f"Bearer {token_with_capability.token_id}",
                         "x-api-key": ""},
            )
        assert resp.status_code == 201

    async def test_bearer_without_capability_is_denied(self, client, token_without_capability):
        """Session token lacking task_submit gets 403."""
        resp = await client.post(
            "/api/hub/tasks",
            json={"description": "test", "agent_name": "worker"},
            headers={"authorization": f"Bearer {token_without_capability.token_id}",
                     "x-api-key": ""},
        )
        assert resp.status_code == 403


class TestChannelMessagesIdentityGate:
    async def test_api_key_can_post_as_any_participant(self, client):
        """App (API key) can post as any from_participant (no identity override)."""
        from brainbox.channels import create_channel
        from brainbox.models import ChannelParticipant
        channel = create_channel("test", [
            ChannelParticipant(name="alice", type="session", session_name="sess-alice"),
        ])
        resp = await client.post(
            f"/api/hub/channels/{channel.id}/messages",
            json={"from_participant": "alice", "content": "hello"},
        )
        assert resp.status_code == 200
        assert resp.json()["from_participant"] == "alice"

    async def test_bearer_without_membership_is_rejected(self, client, token_with_capability):
        """Session token not in channel gets 403."""
        from brainbox.channels import create_channel
        from brainbox.models import ChannelParticipant
        channel = create_channel("test", [
            ChannelParticipant(name="alice", type="session", session_name="sess-alice"),
        ])
        resp = await client.post(
            f"/api/hub/channels/{channel.id}/messages",
            json={"from_participant": "alice", "content": "hi"},
            headers={"authorization": f"Bearer {token_with_capability.token_id}",
                     "x-api-key": ""},
        )
        assert resp.status_code == 403

    async def test_bearer_from_participant_override_replaces_false_claim(self, client):
        """A token-bearing agent claiming another participant's name gets their identity replaced."""
        import brainbox.router as router_module
        from brainbox.channels import create_channel
        from brainbox.models import ChannelParticipant, Task, TaskStatus

        # Set up: agent "bob" in the channel with session "sess-bob"
        token = _issue_token("bob", ["hub_messaging"])
        now = int(time.time() * 1000)
        task = Task(
            id=token.task_id,
            description="work",
            agent_name="bob",
            status=TaskStatus.RUNNING,
            created_at=now,
            updated_at=now,
            session_name="sess-bob",
        )
        router_module._tasks[task.id] = task

        channel = create_channel("test", [
            ChannelParticipant(name="alice", type="session", session_name="sess-alice"),
            ChannelParticipant(name="sess-bob", type="session", session_name="sess-bob"),
        ])

        # Bob claims to be alice — the override must replace it with "sess-bob"
        resp = await client.post(
            f"/api/hub/channels/{channel.id}/messages",
            json={"from_participant": "alice", "content": "not alice"},
            headers={"authorization": f"Bearer {token.token_id}", "x-api-key": ""},
        )
        assert resp.status_code == 200
        assert resp.json()["from_participant"] == "sess-bob"

    async def test_bearer_from_participant_override_uses_agent_name_when_no_session(self, client):
        """When token has no linked task session, falls back to token.agent_name as identity."""
        from brainbox.channels import create_channel
        from brainbox.models import ChannelParticipant

        # Token with no task_id → no session name → identity = agent_name
        token = _issue_token("carol", ["hub_messaging"])
        # Don't create a Task, so get_task returns None

        channel = create_channel("test", [
            ChannelParticipant(name="carol", type="session", session_name=None),
        ])

        resp = await client.post(
            f"/api/hub/channels/{channel.id}/messages",
            json={"from_participant": "impostor", "content": "hi"},
            headers={"authorization": f"Bearer {token.token_id}", "x-api-key": ""},
        )
        assert resp.status_code == 200
        assert resp.json()["from_participant"] == "carol"

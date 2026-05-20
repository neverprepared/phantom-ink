"""Tests for task ownership: agents can cancel their own running tasks via bearer token."""

from __future__ import annotations

import time
import uuid

import pytest

import brainbox.auth as auth_module
import brainbox.registry as reg_module
import brainbox.router as router_module
from brainbox.models import AgentDefinition, Task, TaskStatus, Token


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def api_key(monkeypatch):
    key = "test-api-key-abcdef1234567890"
    monkeypatch.setattr(auth_module, "_api_key", key)
    return key


def _make_agent(name: str = "worker") -> AgentDefinition:
    agent = AgentDefinition(name=name, image="test-image", capabilities=["hub_messaging"])
    reg_module._agents[name] = agent
    return agent


def _make_running_task(agent_name: str = "worker") -> tuple[Task, Token]:
    """Create a RUNNING task and issue a token for it."""
    _make_agent(agent_name)
    task_id = str(uuid.uuid4())
    now = int(time.time() * 1000)
    task = Task(
        id=task_id,
        description="do work",
        agent_name=agent_name,
        status=TaskStatus.RUNNING,
        created_at=now,
        updated_at=now,
        session_name=f"task-{task_id[:8]}",
    )
    router_module._tasks[task_id] = task

    token = Token(
        token_id=str(uuid.uuid4()),
        agent_name=agent_name,
        task_id=task_id,
        capabilities=["hub_messaging"],
        issued=now,
        expiry=now + 3_600_000,
    )
    reg_module._tokens[token.token_id] = token
    task.token_id = token.token_id
    return task, token


def _make_unrelated_token(agent_name: str = "other") -> Token:
    """Issue a token with a different task_id (unrelated to any specific task)."""
    _make_agent(agent_name)
    now = int(time.time() * 1000)
    token = Token(
        token_id=str(uuid.uuid4()),
        agent_name=agent_name,
        task_id=str(uuid.uuid4()),
        capabilities=["hub_messaging"],
        issued=now,
        expiry=now + 3_600_000,
    )
    reg_module._tokens[token.token_id] = token
    return token


# ---------------------------------------------------------------------------
# TestCancelTaskOwnership
# ---------------------------------------------------------------------------


class TestCancelTaskOwnership:
    async def test_api_key_can_cancel_any_task(self, client, api_key):
        task, _ = _make_running_task()
        resp = await client.delete(
            f"/api/hub/tasks/{task.id}",
            headers={"x-api-key": api_key},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    async def test_owning_token_can_cancel_own_task(self, client, api_key):
        task, token = _make_running_task()
        resp = await client.delete(
            f"/api/hub/tasks/{task.id}",
            headers={"authorization": f"Bearer {token.token_id}", "x-api-key": ""},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    async def test_unrelated_token_cannot_cancel(self, client, api_key):
        task, _ = _make_running_task()
        other = _make_unrelated_token()
        resp = await client.delete(
            f"/api/hub/tasks/{task.id}",
            headers={"authorization": f"Bearer {other.token_id}", "x-api-key": ""},
        )
        assert resp.status_code == 403

    async def test_no_credentials_returns_401(self, client, api_key):
        task, _ = _make_running_task()
        resp = await client.delete(
            f"/api/hub/tasks/{task.id}",
            headers={"x-api-key": ""},
        )
        assert resp.status_code == 401

    async def test_expired_token_returns_401(self, client, api_key):
        task, _ = _make_running_task()
        now = int(time.time() * 1000)
        expired = Token(
            token_id=str(uuid.uuid4()),
            agent_name="worker",
            task_id=task.id,
            capabilities=["hub_messaging"],
            issued=now - 7_200_000,
            expiry=now - 3_600_000,
        )
        reg_module._tokens[expired.token_id] = expired
        resp = await client.delete(
            f"/api/hub/tasks/{task.id}",
            headers={"authorization": f"Bearer {expired.token_id}", "x-api-key": ""},
        )
        assert resp.status_code == 401

    async def test_cancel_nonexistent_task_returns_400(self, client, api_key):
        resp = await client.delete(
            "/api/hub/tasks/no-such-task",
            headers={"x-api-key": api_key},
        )
        assert resp.status_code == 400

    async def test_cancel_already_completed_task_returns_400(self, client, api_key):
        task, _ = _make_running_task()
        task.status = TaskStatus.COMPLETED
        resp = await client.delete(
            f"/api/hub/tasks/{task.id}",
            headers={"x-api-key": api_key},
        )
        assert resp.status_code == 400

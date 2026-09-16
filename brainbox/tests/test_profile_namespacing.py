"""Tests for workspace_profile filtering across hub tasks.

The channel half went out with the channels engine in PR4; conversation
profile scoping is covered by test_conversation_profile_scope.py and
test_conversations_api.py.
"""

from __future__ import annotations

import time
import uuid


import brainbox.router as router_module
from brainbox.models import AgentDefinition, Task, TaskStatus
import brainbox.registry as reg_module
from brainbox.router import list_tasks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent(name: str = "worker") -> AgentDefinition:
    agent = AgentDefinition(name=name, image="test-image", capabilities=["hub_messaging"])
    reg_module._agents[name] = agent
    return agent


def _make_task(profile: str | None = None) -> Task:
    _make_agent()
    now = int(time.time() * 1000)
    task_id = str(uuid.uuid4())
    task = Task(
        id=task_id,
        description="work",
        agent_name="worker",
        status=TaskStatus.PENDING,
        created_at=now,
        updated_at=now,
        workspace_profile=profile,
    )
    router_module._tasks[task_id] = task
    return task


# ---------------------------------------------------------------------------
# TestListTasksProfileFilter
# ---------------------------------------------------------------------------


class TestListTasksProfileFilter:
    def test_no_filter_returns_all(self):
        _make_task("personal")
        _make_task("work")
        _make_task(None)
        assert len(list_tasks(limit=None)) == 3

    def test_filter_returns_only_matching(self):
        _make_task("personal")
        _make_task("personal")
        _make_task("work")
        result = list_tasks(workspace_profile="personal", limit=None)
        assert len(result) == 2
        assert all(t.workspace_profile == "personal" for t in result)

    def test_filter_none_profile_returns_unscoped_tasks(self):
        _make_task(None)
        _make_task("personal")
        result = list_tasks(workspace_profile=None, limit=None)
        assert len(result) == 2  # None means no filter → all

    def test_filter_no_match_returns_empty(self):
        _make_task("personal")
        result = list_tasks(workspace_profile="nonexistent", limit=None)
        assert result == []

    def test_filter_combines_with_status(self):
        t1 = _make_task("personal")
        t2 = _make_task("personal")
        t2.status = TaskStatus.RUNNING
        _make_task("work")
        result = list_tasks(workspace_profile="personal", status="pending", limit=None)
        assert len(result) == 1
        assert result[0].id == t1.id


# ---------------------------------------------------------------------------
# TestAPIEndpointsProfileFilter
# ---------------------------------------------------------------------------


class TestAPIEndpointsProfileFilter:
    async def test_hub_list_tasks_filters_by_profile(self, client):
        _make_task("personal")
        _make_task("work")
        resp = await client.get("/api/hub/tasks?workspace_profile=personal")
        assert resp.status_code == 200
        result = resp.json()
        assert len(result) == 1
        assert result[0]["workspace_profile"] == "personal"

    async def test_hub_list_tasks_without_filter_returns_all(self, client):
        _make_task("personal")
        _make_task("work")
        resp = await client.get("/api/hub/tasks")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

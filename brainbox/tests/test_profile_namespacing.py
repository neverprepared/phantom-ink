"""Tests for workspace_profile filtering across tasks, repos, and channels."""

from __future__ import annotations

import time
import uuid

import pytest

import brainbox.router as router_module
from brainbox.channels import create_channel
from brainbox.models import AgentDefinition, ChannelParticipant, Repository, Task, TaskStatus
import brainbox.registry as reg_module
from brainbox.router import add_repo, list_repos, list_tasks


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


def _make_repo(profile: str | None = None) -> Repository:
    name = f"repo-{uuid.uuid4().hex[:6]}"
    return add_repo(f"https://github.com/org/{name}", name=name, workspace_profile=profile)


def _make_channel(profile: str | None = None):
    return create_channel(
        f"chan-{uuid.uuid4().hex[:6]}",
        [ChannelParticipant(name="alice", type="user")],
        workspace_profile=profile,
    )


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
# TestListReposProfileFilter
# ---------------------------------------------------------------------------


class TestListReposProfileFilter:
    def test_no_filter_returns_all(self):
        _make_repo("personal")
        _make_repo("work")
        assert len(list_repos()) == 2

    def test_filter_returns_only_matching(self):
        _make_repo("personal")
        _make_repo("personal")
        _make_repo("work")
        result = list_repos(workspace_profile="personal")
        assert len(result) == 2
        assert all(r.workspace_profile == "personal" for r in result)

    def test_filter_no_match_returns_empty(self):
        _make_repo("personal")
        result = list_repos(workspace_profile="nonexistent")
        assert result == []


# ---------------------------------------------------------------------------
# TestListChannelsProfileFilter
# ---------------------------------------------------------------------------


class TestListChannelsProfileFilter:
    def test_channel_stores_workspace_profile(self):
        ch = _make_channel("personal")
        assert ch.workspace_profile == "personal"

    def test_channel_without_profile_defaults_to_none(self):
        ch = _make_channel(None)
        assert ch.workspace_profile is None

    def test_no_filter_returns_all(self):
        from brainbox.channels import list_channels
        _make_channel("personal")
        _make_channel("work")
        assert len(list_channels()) == 2

    def test_filter_returns_only_matching(self):
        from brainbox.channels import list_channels
        _make_channel("personal")
        _make_channel("personal")
        _make_channel("work")
        result = list_channels(workspace_profile="personal")
        assert len(result) == 2

    def test_filter_no_match_returns_empty(self):
        from brainbox.channels import list_channels
        _make_channel("work")
        result = list_channels(workspace_profile="personal")
        assert result == []


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

    async def test_hub_list_repos_filters_by_profile(self, client):
        _make_repo("personal")
        _make_repo("work")
        resp = await client.get("/api/hub/repos?workspace_profile=personal")
        assert resp.status_code == 200
        result = resp.json()
        assert len(result) == 1
        assert result[0]["workspace_profile"] == "personal"

    async def test_hub_list_channels_filters_by_profile(self, client):
        _make_channel("personal")
        _make_channel("work")
        resp = await client.get("/api/hub/channels?workspace_profile=personal")
        assert resp.status_code == 200
        result = resp.json()
        assert len(result) == 1
        assert result[0]["workspace_profile"] == "personal"

    async def test_create_channel_stores_profile(self, client):
        resp = await client.post("/api/hub/channels", json={
            "name": "test-chan",
            "participants": [{"name": "alice", "type": "user"}],
            "workspace_profile": "personal",
        })
        assert resp.status_code == 200
        assert resp.json()["workspace_profile"] == "personal"

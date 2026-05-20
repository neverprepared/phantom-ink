"""Tests for channel ↔ task bidirectional coupling and the /wait endpoint."""

from __future__ import annotations

import asyncio
import time
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import brainbox.channels as ch_module
import brainbox.router as router_module
from brainbox.channels import complete_channel, create_channel, get_channel, post_message
from brainbox.models import AgentDefinition, ChannelParticipant, TaskStatus
import brainbox.registry as reg_module
from brainbox.router import get_task, submit_task


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _participant(name: str = "alice") -> ChannelParticipant:
    return ChannelParticipant(name=name, type="user")


def _make_agent(name: str = "worker") -> AgentDefinition:
    agent = AgentDefinition(name=name, image="test-image", capabilities=["hub_messaging"])
    reg_module._agents[name] = agent
    return agent


async def _make_task(agent_name: str = "worker") -> object:
    _make_agent(agent_name)
    return await submit_task("do work", agent_name)


# ---------------------------------------------------------------------------
# TestCreateChannelWithTask
# ---------------------------------------------------------------------------


class TestCreateChannelWithTask:
    def test_parent_task_id_stored_on_channel(self):
        task_id = str(uuid.uuid4())
        channel = create_channel("ch", [_participant()], parent_task_id=task_id)
        assert channel.parent_task_id == task_id

    def test_no_parent_task_id_defaults_to_none(self):
        channel = create_channel("ch", [_participant()])
        assert channel.parent_task_id is None

    async def test_channel_recorded_on_task_at_create(self):
        task = await _make_task()
        channel = create_channel("ch", [_participant()], parent_task_id=task.id)

        from brainbox.router import _add_channel_to_task
        _add_channel_to_task(task.id, channel.id)

        task = get_task(task.id)
        assert channel.id in task.channel_ids

    async def test_nonexistent_task_add_channel_is_noop(self):
        # _add_channel_to_task with unknown task_id must not raise
        from brainbox.router import _add_channel_to_task
        _add_channel_to_task("does-not-exist", "ch-abc")  # no exception


# ---------------------------------------------------------------------------
# TestCompleteChannelSignal
# ---------------------------------------------------------------------------


class TestCompleteChannelSignal:
    async def test_completing_linked_channel_emits_task_signal(self):
        task = await _make_task()
        channel = create_channel("ch", [_participant()], parent_task_id=task.id)

        events = []
        router_module._listeners.append(lambda e, t: events.append(e))

        complete_channel(channel.id, by="alice")

        assert "task.signal" in events

    async def test_completing_unlinked_channel_does_not_emit_task_signal(self):
        await _make_task()
        channel = create_channel("ch", [_participant()])

        events = []
        router_module._listeners.append(lambda e, t: events.append(e))

        complete_channel(channel.id, by="alice")

        assert "task.signal" not in events

    async def test_callback_failure_does_not_abort_completion(self):
        task = await _make_task()
        channel = create_channel("ch", [_participant()], parent_task_id=task.id)

        # Patch router.on_channel_completed to raise
        with patch("brainbox.router.on_channel_completed", side_effect=RuntimeError("boom")):
            result = complete_channel(channel.id, by="alice")

        assert result.status == "completed"

    async def test_on_channel_completed_unknown_task_is_noop(self):
        from brainbox.router import on_channel_completed
        events = []
        router_module._listeners.append(lambda e, t: events.append(e))
        on_channel_completed("ghost-task", "ch-abc", "done")
        assert events == []


# ---------------------------------------------------------------------------
# TestJoinChannelRecordsTaskLink (via API endpoint)
# ---------------------------------------------------------------------------


class TestJoinChannelRecordsTaskLink:
    async def test_join_records_channel_on_task(self, client):
        # Create task, issue token, create channel, join via API
        _make_agent()
        task = await submit_task("do work", "worker")
        task.session_name = f"task-{task.id[:8]}"

        # Issue a token with hub_messaging capability
        from brainbox.registry import issue_token
        token = issue_token("worker", task.id, ttl=3600_000)
        # Add hub_messaging capability
        token.capabilities.append("hub_messaging")

        channel = create_channel("ch", [_participant()])

        response = await client.post(
            f"/api/hub/channels/{channel.id}/join",
            headers={"Authorization": f"Bearer {token.token_id}"},
        )
        assert response.status_code == 200

        task = get_task(task.id)
        assert channel.id in task.channel_ids

    async def test_join_sets_parent_task_id_if_unset(self, client):
        _make_agent()
        task = await submit_task("do work", "worker")
        task.session_name = f"task-{task.id[:8]}"

        from brainbox.registry import issue_token
        token = issue_token("worker", task.id, ttl=3600_000)
        token.capabilities.append("hub_messaging")

        channel = create_channel("ch", [_participant()])
        assert channel.parent_task_id is None

        await client.post(
            f"/api/hub/channels/{channel.id}/join",
            headers={"Authorization": f"Bearer {token.token_id}"},
        )

        assert channel.parent_task_id == task.id

    async def test_join_does_not_overwrite_existing_parent_task_id(self, client):
        _make_agent()
        other_task_id = str(uuid.uuid4())

        task = await submit_task("do work", "worker")
        task.session_name = f"task-{task.id[:8]}"

        from brainbox.registry import issue_token
        token = issue_token("worker", task.id, ttl=3600_000)
        token.capabilities.append("hub_messaging")

        channel = create_channel("ch", [_participant()], parent_task_id=other_task_id)

        await client.post(
            f"/api/hub/channels/{channel.id}/join",
            headers={"Authorization": f"Bearer {token.token_id}"},
        )

        assert channel.parent_task_id == other_task_id


# ---------------------------------------------------------------------------
# TestWaitEndpoint
# ---------------------------------------------------------------------------


class TestWaitEndpoint:
    async def test_returns_immediately_when_messages_exist(self, client):
        channel = create_channel("ch", [_participant()])
        post_message(channel.id, "alice", "hello")

        response = await client.get(f"/api/hub/channels/{channel.id}/wait")
        assert response.status_code == 200
        data = response.json()
        assert len(data["messages"]) == 1
        assert data["completed"] is False

    async def test_returns_completed_true_when_channel_done(self, client):
        channel = create_channel("ch", [_participant()])
        complete_channel(channel.id, by="alice")

        response = await client.get(f"/api/hub/channels/{channel.id}/wait")
        assert response.status_code == 200
        data = response.json()
        assert data["completed"] is True
        assert data["completion_reason"] == "alice"

    async def test_404_for_unknown_channel(self, client):
        response = await client.get("/api/hub/channels/no-such-id/wait")
        assert response.status_code == 404

    async def test_times_out_cleanly_when_no_activity(self, client):
        channel = create_channel("ch", [_participant()])

        response = await client.get(
            f"/api/hub/channels/{channel.id}/wait",
            params={"timeout": 1.0},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["messages"] == []
        assert data["completed"] is False

    async def test_wakes_on_new_message(self, client):
        channel = create_channel("ch", [_participant()])

        async def _post_after_delay():
            await asyncio.sleep(0.1)
            post_message(channel.id, "alice", "hello")

        task = asyncio.create_task(_post_after_delay())
        response = await client.get(
            f"/api/hub/channels/{channel.id}/wait",
            params={"timeout": 5.0},
        )
        await task

        assert response.status_code == 200
        data = response.json()
        assert len(data["messages"]) >= 1

    async def test_wakes_on_channel_completion(self, client):
        channel = create_channel("ch", [_participant()])

        async def _complete_after_delay():
            await asyncio.sleep(0.1)
            complete_channel(channel.id, by="alice", reason="done")

        task = asyncio.create_task(_complete_after_delay())
        response = await client.get(
            f"/api/hub/channels/{channel.id}/wait",
            params={"timeout": 5.0},
        )
        await task

        assert response.status_code == 200
        data = response.json()
        assert data["completed"] is True

    async def test_since_id_filters_returned_messages(self, client):
        channel = create_channel("ch", [_participant()])
        m1 = post_message(channel.id, "alice", "first")
        post_message(channel.id, "alice", "second")

        response = await client.get(
            f"/api/hub/channels/{channel.id}/wait",
            params={"since_id": m1.id},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["messages"]) == 1
        assert data["messages"][0]["content"] == "second"

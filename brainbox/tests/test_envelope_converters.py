"""Tests for agent_store envelope converters (channel/hub-task)."""

from __future__ import annotations

import brainbox.channels as channels
from brainbox.agent_store import (
    envelope_from_channel,
    envelope_from_hub_task,
)
from brainbox.models import ChannelParticipant, Task, TaskStatus


class TestChannelConverter:
    def _channel(self, **kwargs):
        return channels.create_channel(
            "review", [ChannelParticipant(name="alice", type="user")], **kwargs
        )

    def test_created(self):
        ch = self._channel(workspace_profile="personal", parent_task_id="task-9")
        env = envelope_from_channel("channel.created", ch)
        assert env.id == f"channel:{ch.id}"
        assert env.status == "active"
        assert env.workspace == "personal"
        assert env.parent_id == "hub-task:task-9"
        assert env.metadata["participants"] == 1

    def test_completed(self):
        ch = self._channel()
        ch.status = "completed"
        env = envelope_from_channel("channel.completed", ch)
        assert env.status == "done"

    def test_message_is_excluded_from_durable_bus(self):
        assert envelope_from_channel("channel.message", {"channel_id": "c1"}) is None

    def test_participant_event_dict_shape(self):
        ch = self._channel()
        env = envelope_from_channel(
            "channel.participant_removed", {"channel_id": ch.id, "name": "alice"}
        )
        assert env.id == f"channel:{ch.id}"
        assert env.type == "channel.participant_removed"
        assert env.metadata["name"] == "alice"


class TestHubTaskProvenance:
    def _task(self, **kwargs) -> Task:
        return Task(
            id="t1", description="do it", agent_name="worker",
            status=TaskStatus.PENDING, created_at=1, updated_at=1, **kwargs,
        )

    def test_provenance_stamped_when_set(self):
        env = envelope_from_hub_task(
            "task.queued", self._task(origin_rule_id="rule-1", rule_chain_depth=3)
        )
        assert env.metadata["origin_rule_id"] == "rule-1"
        assert env.metadata["rule_chain_depth"] == 3

    def test_clean_envelope_when_default(self):
        env = envelope_from_hub_task("task.queued", self._task())
        assert "origin_rule_id" not in env.metadata
        assert "rule_chain_depth" not in env.metadata

"""Tests for agent_store envelope converters (playbook/channel/hub-task)."""

from __future__ import annotations

import brainbox.channels as channels
import brainbox.playbooks as playbooks
from brainbox.agent_store import (
    envelope_from_channel,
    envelope_from_hub_task,
    envelope_from_playbook,
)
from brainbox.models import ChannelParticipant, Task, TaskStatus


class TestPlaybookConverter:
    def _pb(self, **kwargs):
        return playbooks.create_playbook(
            kwargs.pop("name", "deploy"),
            "- [ ] step one\n- [ ] step two\n",
            **kwargs,
        )

    def test_lifecycle_event(self):
        pb = self._pb(workspace_profile="personal")
        env = envelope_from_playbook("playbook.created", pb)
        assert env.id == f"playbook:{pb.id}"
        assert env.type == "playbook.created"
        assert env.status == "upcoming"
        assert env.workspace == "personal"
        assert env.metadata["tasks_total"] == 2
        assert env.tags == ["playbook"]

    def test_status_mapping(self):
        pb = self._pb()
        for raw, mapped in [
            ("running", "active"), ("completed", "done"),
            ("failed", "failed"), ("cancelled", "done"),
        ]:
            pb.status = raw
            assert envelope_from_playbook("playbook.updated", pb).status == mapped

    def test_global_workspace_normalizes_to_none(self):
        pb = self._pb()  # defaults to "global"
        env = envelope_from_playbook("playbook.created", pb)
        assert env.workspace is None

    def test_provenance_metadata(self):
        pb = self._pb()
        pb.origin_rule_id = "rule-1"
        pb.rule_chain_depth = 2
        env = envelope_from_playbook("playbook.started", pb)
        assert env.metadata["origin_rule_id"] == "rule-1"
        assert env.metadata["rule_chain_depth"] == 2

    def test_no_depth_key_when_zero(self):
        env = envelope_from_playbook("playbook.created", self._pb())
        assert "rule_chain_depth" not in env.metadata

    def test_task_done_dict_shape(self):
        pb = self._pb()
        env = envelope_from_playbook(
            "playbook.task_done",
            {"playbook_id": pb.id, "task_id": "t1", "status": "completed"},
        )
        assert env.id == f"playbook:{pb.id}"
        assert env.type == "playbook.task_done"
        assert env.title == pb.name
        assert env.metadata["task_id"] == "t1"

    def test_unresolvable_dict_returns_none(self):
        assert envelope_from_playbook("playbook.task_done", {"task_id": "t1"}) is None


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

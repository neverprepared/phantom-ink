"""Tests for agent_store envelope converters (hub-task).

The channel converter these tests used to cover went out with the channels
engine in PR4; conversation envelopes are covered by test_conversation_bus.py.
"""

from __future__ import annotations

from brainbox.agent_store import envelope_from_hub_task
from brainbox.models import Task, TaskStatus


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

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


class TestHubTaskPrompt:
    """`title` is a one-line label; `description` carries the whole prompt.

    The title is deliberately lossy -- first line, 120 chars -- so the stream
    cards stay one line tall. That made the bus unable to answer "what was this
    task actually asked to do", because nothing else carried the prompt and
    `envelope.description` went out empty on every event.
    """

    def _task(self, description: str, **kwargs) -> Task:
        return Task(
            id="t1", description=description, agent_name="worker",
            status=TaskStatus.PENDING, created_at=1, updated_at=1, **kwargs,
        )

    def test_full_prompt_kept_when_title_truncates(self):
        prompt = "Line one is quite long " * 12 + "\nline two\nline three"
        env = envelope_from_hub_task("task.queued", self._task(prompt))
        assert len(env.title) == 120
        assert env.title == prompt.strip().splitlines()[0][:120]
        assert env.description == prompt

    def test_no_description_when_it_adds_nothing(self):
        # A short single-line prompt survives the title intact, so repeating it
        # in `description` would be pure noise in the payload view.
        env = envelope_from_hub_task("task.queued", self._task("do it"))
        assert env.title == "do it"
        assert env.description is None

    def test_no_description_when_task_has_no_prompt(self):
        env = envelope_from_hub_task("task.queued", self._task(""))
        assert env.title == "t1"
        assert env.description is None

    def test_pathological_prompt_is_capped(self):
        # Every lifecycle event for a task repeats this, so an unbounded prompt
        # would multiply into the append-only table.
        env = envelope_from_hub_task("task.queued", self._task("x" * 9000))
        assert env.description is not None
        assert len(env.description) <= 4096
        assert env.description.endswith("…")

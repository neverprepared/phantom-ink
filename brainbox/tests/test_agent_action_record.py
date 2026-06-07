"""Tests for the brainbox-side action outcome recorder."""

from __future__ import annotations

import pytest

from brainbox import agent_store


@pytest.mark.asyncio
async def test_arecord_action_writes_success_envelope():
    seed_id = "hub-task:abc"

    async def do_work():
        return "ok"

    result = await agent_store.arecord_action(
        target_id=seed_id,
        action_name="cancel",
        actor=agent_store.ACTOR_USER,
        fn=do_work,
    )
    assert result == "ok"

    events = agent_store.list_events(parent_id=seed_id)
    assert len(events) == 1
    env = events[0]["envelope"]
    assert env["type"] == "action.cancel"
    assert env["status"] == "done"
    assert env["parent_id"] == seed_id
    assert env["outcome"]["ok"] is True
    assert env["outcome"]["actor"] == agent_store.ACTOR_USER
    assert env["outcome"]["error"] is None
    assert env["outcome"]["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_arecord_action_records_failure_and_reraises():
    seed_id = "hub-task:xyz"

    async def boom():
        raise ValueError("nope")

    with pytest.raises(ValueError):
        await agent_store.arecord_action(
            target_id=seed_id,
            action_name="cancel",
            actor=agent_store.ACTOR_SYSTEM,
            fn=boom,
        )

    events = agent_store.list_events(parent_id=seed_id)
    assert len(events) == 1
    outcome = events[0]["envelope"]["outcome"]
    assert outcome["ok"] is False
    assert "nope" in outcome["error"]
    assert outcome["actor"] == agent_store.ACTOR_SYSTEM


@pytest.mark.asyncio
async def test_arecord_action_accepts_sync_callable():
    seed_id = "hub-task:sync"

    def sync_fn():
        return 42

    result = await agent_store.arecord_action(
        target_id=seed_id,
        action_name="restart",
        actor="agent:supervisor",
        fn=sync_fn,
    )
    assert result == 42
    events = agent_store.list_events(parent_id=seed_id)
    assert len(events) == 1
    assert events[0]["envelope"]["outcome"]["actor"] == "agent:supervisor"

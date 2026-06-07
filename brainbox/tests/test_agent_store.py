"""Unit tests for the agent event bus storage layer."""

from __future__ import annotations

import pytest

from brainbox import agent_store
from brainbox.agent_store import AgentEnvelope


@pytest.fixture(autouse=True)
def _clear_listeners():
    agent_store._listeners.clear()
    yield
    agent_store._listeners.clear()


def _env(**overrides) -> dict:
    base = {
        "id": "task:t1",
        "kind": "event",
        "source": "wails-queue@laptop",
        "type": "task.queued",
        "status": "upcoming",
        "title": "Test task",
        "workspace": "personal",
    }
    base.update(overrides)
    return base


def test_ingest_upserts_state_and_appends_event():
    agent_store.ingest(_env())
    state = agent_store.get_state("task:t1")
    assert state is not None
    assert state["status"] == "upcoming"
    events = agent_store.list_events(envelope_id="task:t1")
    assert len(events) == 1
    assert events[0]["type"] == "task.queued"


def test_ingest_same_id_mutates_state_appends_event():
    agent_store.ingest(_env())
    agent_store.ingest(_env(type="task.running", status="active"))
    agent_store.ingest(_env(type="task.failed", status="failed", end_at=1234567890000))

    state = agent_store.get_state("task:t1")
    assert state["status"] == "failed"
    assert state["end_at"] == 1234567890000

    events = agent_store.list_events(envelope_id="task:t1")
    assert [e["type"] for e in events] == ["task.queued", "task.running", "task.failed"]


def test_list_state_filters_by_status_and_workspace():
    agent_store.ingest(_env(id="a", status="failed", workspace="personal"))
    agent_store.ingest(_env(id="b", status="done", workspace="personal"))
    agent_store.ingest(_env(id="c", status="needs_action", workspace="work"))
    agent_store.ingest(_env(id="d", status="blocked", workspace="personal"))

    attention = agent_store.list_attention(workspace="personal")
    assert {r["id"] for r in attention} == {"a", "d"}

    all_attention = agent_store.list_attention()
    assert {r["id"] for r in all_attention} == {"a", "c", "d"}


def test_list_events_by_parent_id_returns_family():
    agent_store.ingest(_env(id="chain:r1", type="chain.run.start", status="active"))
    agent_store.ingest(_env(id="step:r1.0", type="chain.step.start", status="active",
                            parent_id="chain:r1"))
    agent_store.ingest(_env(id="step:r1.1", type="chain.step.start", status="active",
                            parent_id="chain:r1"))

    family = agent_store.list_events(parent_id="chain:r1")
    assert len(family) == 2
    assert {e["id"] for e in family} == {"step:r1.0", "step:r1.1"}


def test_outcome_block_round_trips():
    agent_store.ingest({
        "id": "action:task:t1:retry:1",
        "kind": "event",
        "source": "wails-ui@laptop",
        "type": "action.retry",
        "status": "done",
        "title": "Retry",
        "parent_id": "task:t1",
        "outcome": {"ok": True, "actor": "user", "duration_ms": 184},
    })
    state = agent_store.get_state("action:task:t1:retry:1")
    assert state["outcome"] == {
        "ok": True, "actor": "user", "duration_ms": 184, "error": None,
    }


def test_listener_fires_on_successful_ingest():
    seen: list[AgentEnvelope] = []
    agent_store.on_event(seen.append)
    agent_store.ingest(_env())
    assert len(seen) == 1
    assert seen[0].id == "task:t1"


def test_listener_exception_does_not_break_ingest():
    def bad(_env):
        raise RuntimeError("listener error")

    agent_store.on_event(bad)
    # Ingest should still succeed even though the listener throws.
    agent_store.ingest(_env())
    assert agent_store.get_state("task:t1") is not None


def test_envelope_from_hub_task_maps_status():
    from brainbox.models import Task, TaskStatus

    task = Task(
        id="abc-123",
        description="Deploy the thing",
        agent_name="developer",
        status=TaskStatus.FAILED,
        created_at=1000,
        updated_at=2000,
        workspace_profile="personal",
        last_error="exit 1",
    )
    env = agent_store.envelope_from_hub_task("task.failed", task)
    assert env.id == "hub-task:abc-123"
    assert env.status == "failed"
    assert env.source == "brainbox-hub"
    assert env.workspace == "personal"
    assert env.metadata["last_error"] == "exit 1"
    assert env.end_at == 2000  # terminal -> end_at set


def test_envelope_from_hub_task_blocked_passes_through():
    from brainbox.models import Task, TaskStatus

    task = Task(
        id="x",
        description="waiting",
        agent_name="developer",
        status=TaskStatus.BLOCKED,
        created_at=1000,
        updated_at=2000,
    )
    env = agent_store.envelope_from_hub_task("task.signal", task)
    assert env.status == "blocked"
    assert env.end_at is None  # not terminal

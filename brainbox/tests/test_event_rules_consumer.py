"""Integration tests for the event-rules consumer (real Postgres via conftest).

The background task is never started; tests drive the consumer through
event_rules.run_once() and the DAOs directly.
"""

from __future__ import annotations

import pytest

import brainbox.event_rules as er
from brainbox import agent_store
from brainbox.agent_store import AgentEnvelope
from brainbox.config import settings


def _ingest(type_: str, *, workspace: str | None = "personal", metadata: dict | None = None):
    return agent_store.ingest(AgentEnvelope(
        id=f"test:{type_}:{workspace}",
        title=f"test {type_}",
        source="test",
        type=type_,
        status="failed",
        workspace=workspace,
        metadata=metadata or {},
    ))


def _rule(pattern: dict, *, profile: str = "", name: str = "r") -> er.EventRule:
    return er.upsert_rule(er.EventRule(
        name=name,
        profile=profile,
        pattern=pattern,
        actions=[er.SubmitTaskAction(description="Triage: {title}", agent_name="worker")],
    ))


@pytest.fixture
def stub_executor(monkeypatch):
    """Replace action execution with a recorder so no real dispatch happens."""
    calls: list[tuple[str, str, dict]] = []

    async def _fake(action, rule, doc):
        calls.append((action.type, rule.id, doc))
        return {"stubbed": True}

    monkeypatch.setattr(er, "_execute_action", _fake)
    return calls


class TestCursor:
    def test_first_boot_starts_at_max_seq(self):
        _ingest("task.failed")
        _ingest("task.completed")
        cursor = er.init_cursor_if_absent()
        assert cursor == 2  # RESTART IDENTITY per test → seq is deterministic

    def test_empty_table_starts_at_zero(self):
        assert er.init_cursor_if_absent() == 0

    def test_init_is_idempotent(self):
        er.init_cursor_if_absent()
        _ingest("task.failed")
        assert er.init_cursor_if_absent() == 0  # unchanged by later events

    async def test_pre_cursor_history_never_fires(self, stub_executor):
        _ingest("task.failed")  # before consumer init — history
        er.init_cursor_if_absent()
        _rule({"type": ["task.failed"]})
        await er.run_once()
        assert stub_executor == []
        assert er.list_executions() == []


class TestMatchingAndEnqueue:
    async def test_match_enqueues_and_runs(self, stub_executor):
        er.init_cursor_if_absent()
        rule = _rule({"type": ["task.failed"]})
        _ingest("task.failed")
        _ingest("task.completed")  # no match
        processed = await er.run_once()
        assert processed == 2
        assert len(stub_executor) == 1
        assert stub_executor[0][1] == rule.id
        execs = er.list_executions()
        assert len(execs) == 1
        assert execs[0].status == "ok"
        assert execs[0].result == {"stubbed": True}

    async def test_cursor_advances_past_processed(self, stub_executor):
        er.init_cursor_if_absent()
        _rule({"type": ["task.failed"]})
        _ingest("task.failed")
        await er.run_once()
        assert er.get_cursor() == 1
        # The ok execution emitted a rule.execution envelope — exactly one
        # more event to consume (which matches no rule), then quiescent.
        assert await er.run_once() == 1
        assert await er.run_once() == 0

    async def test_unique_key_dedupes_reprocessing(self, stub_executor):
        er.init_cursor_if_absent()
        _rule({"type": ["task.failed"]})
        _ingest("task.failed")
        await er.run_once()
        # Simulate a crash-window re-read: rewind the cursor and run again.
        from brainbox.store import _conn
        with _conn() as c:
            c.execute("UPDATE event_rule_cursor SET last_seq = 0 WHERE name = %s", (er.CURSOR_NAME,))
        await er.run_once()
        assert len(er.list_executions()) == 1  # no duplicate row

    async def test_one_execution_per_action(self, stub_executor):
        er.init_cursor_if_absent()
        er.upsert_rule(er.EventRule(
            name="multi",
            pattern={"type": ["task.failed"]},
            actions=[
                er.SubmitTaskAction(description="a", agent_name="worker"),
                er.SubmitTaskAction(description="b", agent_name="worker"),
            ],
        ))
        _ingest("task.failed")
        await er.run_once()
        execs = er.list_executions()
        assert sorted(e.action_index for e in execs) == [0, 1]

    async def test_trigger_stats_bumped(self, stub_executor):
        er.init_cursor_if_absent()
        rule = _rule({"type": ["task.failed"]})
        _ingest("task.failed")
        await er.run_once()
        updated = er.get_rule(rule.id)
        assert updated.trigger_count == 1
        assert updated.last_triggered_at is not None

    async def test_disabled_rule_never_fires(self, stub_executor):
        er.init_cursor_if_absent()
        rule = _rule({"type": ["task.failed"]})
        er.set_rule_enabled(rule.id, False)
        _ingest("task.failed")
        await er.run_once()
        assert er.list_executions() == []


class TestProfileScoping:
    async def test_profile_rule_ignores_other_workspaces(self, stub_executor):
        er.init_cursor_if_absent()
        _rule({"type": ["task.failed"]}, profile="gsa")
        _ingest("task.failed", workspace="personal")
        await er.run_once()
        assert er.list_executions() == []

    async def test_profile_rule_matches_own_workspace(self, stub_executor):
        er.init_cursor_if_absent()
        _rule({"type": ["task.failed"]}, profile="personal")
        _ingest("task.failed", workspace="personal")
        await er.run_once()
        assert len(er.list_executions()) == 1

    async def test_global_rule_matches_everything(self, stub_executor):
        er.init_cursor_if_absent()
        _rule({"type": ["task.failed"]}, profile="global")
        _ingest("task.failed", workspace="personal")
        _ingest("task.failed", workspace=None)
        await er.run_once()
        assert len(er.list_executions()) == 2


class TestLoopPrevention:
    async def test_chain_depth_refusal(self, stub_executor):
        er.init_cursor_if_absent()
        _rule({"type": ["task.failed"]})
        _ingest("task.failed", metadata={"rule_chain_depth": settings.rules.max_chain_depth})
        await er.run_once()
        assert er.list_executions() == []

    async def test_below_max_depth_fires(self, stub_executor):
        er.init_cursor_if_absent()
        _rule({"type": ["task.failed"]})
        _ingest("task.failed", metadata={"rule_chain_depth": settings.rules.max_chain_depth - 1})
        await er.run_once()
        assert len(er.list_executions()) == 1

    async def test_rate_limit_throttles(self, stub_executor, monkeypatch):
        monkeypatch.setattr(settings.rules, "rate_limit_per_minute", 2)
        er.init_cursor_if_absent()
        rule = _rule({"type": [{"prefix": "task."}]})
        for i in range(4):
            agent_store.ingest(AgentEnvelope(
                id=f"test:burst:{i}", title=f"burst {i}", source="test",
                type="task.failed", workspace="personal",
            ))
        await er.run_once()
        execs = er.list_executions(rule_id=rule.id)
        by_status = {}
        for e in execs:
            by_status.setdefault(e.status, 0)
            by_status[e.status] += 1
        assert by_status.get("ok") == 2
        assert by_status.get("throttled") == 2


class TestRecovery:
    async def test_recover_stuck_running(self, stub_executor):
        er.init_cursor_if_absent()
        _rule({"type": ["task.failed"]})
        _ingest("task.failed")
        await er.run_once()
        # Force the row back to running with an old timestamp.
        from brainbox.store import _conn
        with _conn() as c:
            c.execute(
                "UPDATE event_rule_executions SET status = 'running', updated_at = 1"
            )
        assert er.recover_stuck_running(1000) == 1
        execs = er.list_executions()
        assert execs[0].status == "queued"

    def test_requeue_only_terminal(self):
        er.init_cursor_if_absent()
        rule = _rule({"type": ["task.failed"]})
        from brainbox.store import _conn
        with _conn() as c:
            c.execute(
                """
                INSERT INTO event_rule_executions
                  (rule_id, event_seq, event_id, action_index, action_type,
                   status, attempts, created_at, updated_at)
                VALUES (%s, 1, 'e', 0, 'submit_task', 'dead', 3, 1, 1)
                """,
                (rule.id,),
            )
        ex = er.list_executions()[0]
        requeued = er.requeue_execution(ex.id)
        assert requeued is not None and requeued.status == "queued" and requeued.attempts == 0
        # queued is not requeueable
        assert er.requeue_execution(ex.id) is None

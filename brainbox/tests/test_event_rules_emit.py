"""Terminal rule-execution envelopes on the agent bus."""

from __future__ import annotations

import pytest

import brainbox.event_rules as er
import brainbox.router as router
from brainbox import agent_store
from brainbox.agent_store import AgentEnvelope
from brainbox.config import settings
from brainbox.models import Task, TaskStatus


def _ingest(type_: str = "task.failed", *, metadata: dict | None = None, suffix: str = ""):
    return agent_store.ingest(AgentEnvelope(
        id=f"emit-test:{type_}{suffix}", title="source event", source="test",
        type=type_, status="failed", workspace="personal", metadata=metadata or {},
    ))


@pytest.fixture
def ok_executor(monkeypatch):
    async def _ok(action, rule, doc):
        return {"stubbed": True}
    monkeypatch.setattr(er, "_execute_action", _ok)


@pytest.fixture
def failing_executor(monkeypatch):
    async def _boom(action, rule, doc):
        raise RuntimeError("boom")
    monkeypatch.setattr(er, "_execute_action", _boom)


def _rule(name="r", actions=None):
    return er.upsert_rule(er.EventRule(
        name=name,
        pattern={"type": ["task.failed"]},
        actions=actions or [er.SubmitTaskAction(description="d", agent_name="a")],
    ))


class TestEmission:
    async def test_ok_emits_done_envelope(self, ok_executor):
        er.init_cursor_if_absent()
        rule = _rule("triage")
        _ingest()
        await er.run_once()

        ex = er.list_executions(rule_id=rule.id)[0]
        state = agent_store.get_state(f"rule-exec:{ex.id}")
        assert state is not None
        assert state["status"] == "done"
        assert state["type"] == "rule.execution"
        assert state["source"] == "brainbox-rules"
        assert state["workspace"] == "personal"
        assert state["parent_id"] == "emit-test:task.failed"
        assert state["title"] == "triage → submit_task"
        assert state["metadata"]["rule_chain_depth"] == 1  # source depth 0 + 1
        assert state["metadata"]["origin_rule_id"] == rule.id
        assert state["metadata"]["event_seq"] == ex.event_seq
        assert state["outcome"]["ok"] is True

    async def test_dead_emits_failed_and_enters_attention(self, failing_executor):
        er.init_cursor_if_absent()
        rule = _rule()
        _ingest()
        await er.run_once()

        ex = er.list_executions(rule_id=rule.id)[0]
        assert ex.status == "dead"
        state = agent_store.get_state(f"rule-exec:{ex.id}")
        assert state["status"] == "failed"
        assert "boom" in state["metadata"]["error"]
        attention_ids = {a["id"] for a in agent_store.list_attention()}
        assert f"rule-exec:{ex.id}" in attention_ids

    async def test_depth_propagates_from_source_event(self, ok_executor):
        er.init_cursor_if_absent()
        _rule()
        _ingest(metadata={"rule_chain_depth": 2})
        await er.run_once()
        ex = er.list_executions()[0]
        state = agent_store.get_state(f"rule-exec:{ex.id}")
        assert state["metadata"]["rule_chain_depth"] == 3

    async def test_throttled_never_emits(self, ok_executor, monkeypatch):
        monkeypatch.setattr(settings.rules, "rate_limit_per_minute", 1)
        er.init_cursor_if_absent()
        _rule()
        _ingest(suffix=":a")
        _ingest(suffix=":b")  # over the limit → throttled
        await er.run_once()
        throttled = [e for e in er.list_executions() if e.status == "throttled"]
        assert len(throttled) == 1
        assert agent_store.get_state(f"rule-exec:{throttled[0].id}") is None

    async def test_meta_rule_fires_on_execution_event_and_depth_caps(self, ok_executor):
        er.init_cursor_if_absent()
        _rule("base")
        meta = er.upsert_rule(er.EventRule(
            name="meta",
            pattern={"type": ["rule.execution"]},
            actions=[er.SubmitTaskAction(description="meta: {title}", agent_name="a")],
        ))
        _ingest()
        # Pass 1: base fires on task.failed, emits rule-exec envelope.
        await er.run_once()
        assert er.list_executions(rule_id=meta.id) == []
        # Pass 2: meta fires on the emitted rule.execution event.
        await er.run_once()
        metas = er.list_executions(rule_id=meta.id)
        assert len(metas) == 1
        # Keep passing: the chain must stop at max_chain_depth, not run forever.
        for _ in range(settings.rules.max_chain_depth + 2):
            await er.run_once()
        total = len(er.list_executions(rule_id=meta.id))
        assert total <= settings.rules.max_chain_depth

    async def test_ingest_failure_never_breaks_execution(self, ok_executor, monkeypatch):
        er.init_cursor_if_absent()
        rule = _rule()
        _ingest()

        def _boom_ingest(env):
            raise RuntimeError("bus down")

        monkeypatch.setattr(agent_store, "ingest", _boom_ingest)
        await er.run_once()
        ex = er.list_executions(rule_id=rule.id)[0]
        assert ex.status == "ok"  # outcome recorded despite emit failure

    async def test_dlq_retry_success_clears_attention_card(self, monkeypatch):
        """The envelope id is per execution row; a DLQ retry that succeeds
        upserts the same agent_state id from failed to done."""
        calls = {"n": 0}

        async def _flaky(action, rule, doc):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("first time fails")
            return {"ok": True}

        monkeypatch.setattr(er, "_execute_action", _flaky)
        er.init_cursor_if_absent()
        rule = _rule()
        _ingest()
        await er.run_once()
        ex = er.list_executions(rule_id=rule.id)[0]
        assert ex.status == "dead"
        assert agent_store.get_state(f"rule-exec:{ex.id}")["status"] == "failed"

        er.requeue_execution(ex.id)
        await er.run_once()
        assert er.get_execution(ex.id).status == "ok"
        assert agent_store.get_state(f"rule-exec:{ex.id}")["status"] == "done"


class TestRulesStatusEndpoint:
    async def _get(self, client, path):
        return await client.get(path)

    async def test_status_counts_and_lag(self, client, ok_executor):
        er.init_cursor_if_absent()
        rule = _rule()
        _ingest()
        await er.run_once()
        # Events emitted by the execution sit past the cursor? No — run_once
        # loops until drained; the rule-exec event was matched in-loop only
        # if another pass ran. Ingest one more event WITHOUT running to
        # create real lag.
        _ingest(suffix=":lagged")

        from brainbox.store import _conn
        with _conn() as c:
            c.execute(
                """
                INSERT INTO event_rule_executions
                  (rule_id, event_seq, event_id, action_index, action_type,
                   status, attempts, created_at, updated_at)
                VALUES (%s, 999, 'e', 1, 'webhook', 'dead', 3, 1, 1)
                """,
                (rule.id,),
            )

        r = await self._get(client, "/api/rules/status")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["counts"]["dead"] == 1
        assert body["counts"]["ok_24h"] == 1
        assert body["counts"]["queued"] == 0
        assert body["lag"] == body["head_seq"] - body["cursor"]
        assert body["lag"] >= 1
        assert body["sink"]["enabled"] is False

    async def test_status_not_shadowed_by_rule_id_route(self, client):
        r = await self._get(client, "/api/rules/status")
        assert r.status_code == 200  # not a 404 from /api/rules/{rule_id}
        assert "counts" in r.json()

    async def test_status_sink_block_when_enabled(self, client, monkeypatch):
        monkeypatch.setattr(settings.opensearch, "addresses", ["http://x:9200"])
        agent_store.ingest(AgentEnvelope(id="s:1", title="x", source="t", type="y"))
        r = await self._get(client, "/api/rules/status")
        body = r.json()
        assert body["sink"]["enabled"] is True
        assert body["sink"]["lag"] == 1  # cursor 0, head 1

"""Tests for event-rule action execution + templating (PR1: submit_task)."""

from __future__ import annotations

import pytest

import brainbox.event_rules as er
import brainbox.router as router
from brainbox import agent_store
from brainbox.agent_store import AgentEnvelope
from brainbox.models import Task, TaskStatus


DOC = {
    "seq": 7,
    "ts": 1700000000000,
    "id": "hub-task:abc",
    "type": "task.failed",
    "title": "run the tests",
    "workspace": "personal",
    "tags": ["ci"],
    "metadata": {"agent_name": "worker-1", "cost_usd": 1.5, "rule_chain_depth": 1},
    "outcome": {"ok": False, "error": "boom"},
}


class TestTemplating:
    def test_top_level_fields(self):
        assert er._render("Triage: {title}", DOC) == "Triage: run the tests"
        assert er._render("{type}/{workspace}", DOC) == "task.failed/personal"

    def test_metadata_dotted_path(self):
        assert er._render("{metadata.agent_name}", DOC) == "worker-1"
        assert er._render("{metadata.cost_usd}", DOC) == "1.5"

    def test_outcome_path(self):
        assert er._render("{outcome.error}", DOC) == "boom"
        assert er._render("{outcome.ok}", DOC) == "false"

    def test_missing_path_renders_empty(self):
        assert er._render("[{metadata.nope}]", DOC) == "[]"
        assert er._render("[{nope.at.all}]", DOC) == "[]"

    def test_list_renders_as_json(self):
        assert er._render("{tags}", DOC) == '["ci"]'

    def test_envelope_placeholder_excludes_seq_ts(self):
        out = er._render("{envelope}", DOC)
        assert '"seq"' not in out and '"title":"run the tests"' in out

    def test_brace_escaping(self):
        assert er._render("{{literal}} {title}", DOC) == "{literal} run the tests"

    def test_no_reexpansion(self):
        doc = dict(DOC, title="{metadata.agent_name}")
        assert er._render("{title}", doc) == "{metadata.agent_name}"

    def test_render_leaves(self):
        rendered = er._render_leaves(
            {"a": "{title}", "b": {"c": ["{type}", 5]}, "d": True}, DOC
        )
        assert rendered == {
            "a": "run the tests", "b": {"c": ["task.failed", 5]}, "d": True,
        }


class TestSubmitTaskExecutor:
    @pytest.fixture
    def captured(self, monkeypatch):
        calls: list[dict] = []

        async def _fake_submit(description, agent_name, **kwargs):
            calls.append({"description": description, "agent_name": agent_name, **kwargs})
            return Task(
                id="t-1", description=description, agent_name=agent_name,
                status=TaskStatus.PENDING, created_at=1, updated_at=1,
            )

        monkeypatch.setattr(router, "submit_task", _fake_submit)
        return calls

    async def test_renders_and_stamps_provenance(self, captured):
        rule = er.EventRule(
            name="triage",
            pattern={"type": ["task.failed"]},
            actions=[er.SubmitTaskAction(
                description="Triage: {title} ({outcome.error})",
                agent_name="triager",
                priority=5,
            )],
        )
        result = await er._exec_submit_task(rule.actions[0], rule, DOC)
        assert result == {"task_id": "t-1"}
        call = captured[0]
        assert call["description"] == "Triage: run the tests (boom)"
        assert call["agent_name"] == "triager"
        assert call["priority"] == 5
        assert call["origin_rule_id"] == rule.id
        assert call["rule_chain_depth"] == 2  # event depth 1 + 1

    async def test_workspace_inherits_from_event(self, captured):
        rule = er.EventRule(
            name="r", pattern={"type": ["task.failed"]},
            actions=[er.SubmitTaskAction(description="d", agent_name="a")],
        )
        await er._exec_submit_task(rule.actions[0], rule, DOC)
        assert captured[0]["workspace_profile"] == "personal"

    async def test_explicit_workspace_wins(self, captured):
        rule = er.EventRule(
            name="r", pattern={"type": ["task.failed"]},
            actions=[er.SubmitTaskAction(
                description="d", agent_name="a", workspace_profile="gsa",
            )],
        )
        await er._exec_submit_task(rule.actions[0], rule, DOC)
        assert captured[0]["workspace_profile"] == "gsa"


class TestFailureHandling:
    async def test_submit_task_failure_is_dead_no_retry(self, monkeypatch):
        """Config errors (unknown agent, policy denial) go straight to dead —
        the submitted task has its own retry machinery; retrying here risks
        double-created work."""
        er.init_cursor_if_absent()
        rule = er.upsert_rule(er.EventRule(
            name="r", pattern={"type": ["task.failed"]},
            actions=[er.SubmitTaskAction(description="d", agent_name="ghost-agent")],
        ))
        agent_store.ingest(AgentEnvelope(
            id="test:x", title="x", source="test", type="task.failed",
        ))
        await er.run_once()
        execs = er.list_executions(rule_id=rule.id)
        assert len(execs) == 1
        assert execs[0].status == "dead"
        assert execs[0].attempts == 1
        assert "ghost-agent" in (execs[0].error or "")

    async def test_unimplemented_executor_dead_letters(self):
        er.init_cursor_if_absent()
        rule = er.upsert_rule(er.EventRule(
            name="r", pattern={"type": ["task.failed"]},
            actions=[er.WebhookAction(url="http://example.invalid/hook")],
        ))
        agent_store.ingest(AgentEnvelope(
            id="test:x", title="x", source="test", type="task.failed",
        ))
        await er.run_once()
        execs = er.list_executions(rule_id=rule.id)
        assert execs[0].status == "dead"
        assert "no executor" in (execs[0].error or "")

    async def test_deleted_rule_dead_letters_gracefully(self, monkeypatch):
        er.init_cursor_if_absent()
        rule = er.upsert_rule(er.EventRule(
            name="r", pattern={"type": ["task.failed"]},
            actions=[er.SubmitTaskAction(description="d", agent_name="a")],
        ))
        agent_store.ingest(AgentEnvelope(
            id="test:x", title="x", source="test", type="task.failed",
        ))

        # Enqueue happens, then the rule vanishes before dispatch.
        real_claim = er.claim_queued_executions

        def _claim_then_delete(limit):
            rows = real_claim(limit)
            if rows:
                er.delete_rule(rule.id)
            return rows

        monkeypatch.setattr(er, "claim_queued_executions", _claim_then_delete)
        await er.run_once()
        execs = er.list_executions()
        assert execs[0].status == "dead"
        assert "no longer available" in (execs[0].error or "")


class TestOutputCap:
    def test_cap_truncates(self, monkeypatch):
        from brainbox.config import settings
        monkeypatch.setattr(settings.rules, "output_cap_bytes", 10)
        assert er._cap("short") == "short"
        capped = er._cap("x" * 100)
        assert capped.endswith("…[truncated]")
        assert len(capped.encode()) < 40

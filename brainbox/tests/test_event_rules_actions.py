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


class TestRunPlaybookExecutor:
    @pytest.fixture(autouse=True)
    def _fresh_playbooks(self):
        # playbooks module state is not covered by the conftest reset —
        # clear it so name-resolution tests are order-independent.
        import brainbox.playbooks as playbooks
        playbooks._playbooks.clear()
        yield
        playbooks._playbooks.clear()

    @pytest.fixture
    def captured(self, monkeypatch):
        import brainbox.playbooks as playbooks
        calls: list[dict] = []
        pb = playbooks.create_playbook("deploy", "- [ ] step\n")

        async def _fake_run(playbook_id, **kwargs):
            calls.append({"playbook_id": playbook_id, **kwargs})
            return pb

        monkeypatch.setattr(playbooks, "run_playbook", _fake_run)
        return pb, calls

    async def test_resolves_by_id_and_stamps_provenance(self, captured):
        pb, calls = captured
        rule = er.EventRule(
            name="r", pattern={"type": ["task.failed"]},
            actions=[er.RunPlaybookAction(playbook=pb.id)],
        )
        result = await er._exec_run_playbook(rule.actions[0], rule, DOC)
        assert result == {"playbook_id": pb.id}
        assert calls[0]["origin_rule_id"] == rule.id
        assert calls[0]["rule_chain_depth"] == 2
        assert calls[0]["workspace_profile"] == "personal"  # inherited

    async def test_resolves_by_unique_name(self, captured):
        pb, calls = captured
        rule = er.EventRule(
            name="r", pattern={"x": ["y"]},
            actions=[er.RunPlaybookAction(playbook="deploy")],
        )
        await er._exec_run_playbook(rule.actions[0], rule, DOC)
        assert calls[0]["playbook_id"] == pb.id

    async def test_not_found_is_permanent(self):
        rule = er.EventRule(
            name="r", pattern={"x": ["y"]},
            actions=[er.RunPlaybookAction(playbook="ghost")],
        )
        with pytest.raises(er._PermanentActionError, match="not found"):
            await er._exec_run_playbook(rule.actions[0], rule, DOC)

    async def test_ambiguous_name_is_permanent(self, captured):
        import brainbox.playbooks as playbooks
        playbooks.create_playbook("deploy", "- [ ] other\n")  # second "deploy"
        rule = er.EventRule(
            name="r", pattern={"x": ["y"]},
            actions=[er.RunPlaybookAction(playbook="deploy")],
        )
        with pytest.raises(er._PermanentActionError, match="ambiguous"):
            await er._exec_run_playbook(rule.actions[0], rule, DOC)


class TestStartLoopExecutor:
    async def test_renders_refs_and_stamps_provenance(self, monkeypatch):
        import brainbox.loop_runner as loop_runner
        import brainbox.loop_template as loop_template

        calls: list[dict] = []

        class _Inst:
            id = "loop-1"
            parent_task_id = "parent-1"

        async def _fake_start(spec, envelope, **kwargs):
            calls.append({"spec": spec, "envelope": envelope, **kwargs})
            return _Inst()

        monkeypatch.setattr(loop_template, "load_template", lambda name: f"spec:{name}")
        monkeypatch.setattr(loop_runner, "start_loop", _fake_start)

        rule = er.EventRule(
            name="r", pattern={"x": ["y"]},
            actions=[er.StartLoopAction(
                template_name="pr-review-loop",
                artifact_refs={"title": "{title}", "n": 5},
            )],
        )
        result = await er._exec_start_loop(rule.actions[0], rule, DOC)
        assert result == {"loop_id": "loop-1", "parent_task_id": "parent-1"}
        call = calls[0]
        assert call["spec"] == "spec:pr-review-loop"
        assert call["envelope"].artifact_refs == {"title": "run the tests", "n": 5}
        assert call["origin_rule_id"] == rule.id
        assert call["rule_chain_depth"] == 2
        assert call["workspace_profile"] == "personal"


class TestWebhookExecutor:
    def _rule(self, **action_kwargs):
        return er.EventRule(
            name="hook-rule", pattern={"x": ["y"]},
            actions=[er.WebhookAction(url="http://127.0.0.1:1/hook", **action_kwargs)],
        )

    @pytest.fixture
    def curl(self, monkeypatch):
        import brainbox.ollama as ollama
        calls: list[dict] = []
        responses: list[tuple[int, str]] = [(200, '{"ok":true}')]

        async def _fake(method, base_url, path, *, body=None, headers=None, verify=True, timeout=300.0):
            calls.append({
                "method": method, "url": base_url, "body": body,
                "headers": headers, "timeout": timeout,
            })
            return responses[min(len(calls), len(responses)) - 1]

        monkeypatch.setattr(ollama, "acurl_request", _fake)
        return calls, responses

    async def test_full_envelope_body_with_brainbox_stamp(self, curl):
        calls, _ = curl
        rule = self._rule()
        result = await er._exec_webhook(rule.actions[0], rule, DOC)
        assert result["http_status"] == 200
        body = calls[0]["body"]
        assert body["title"] == "run the tests"
        assert "seq" not in body and "ts" not in body
        assert body["_brainbox"]["rule_id"] == rule.id
        assert body["_brainbox"]["event_seq"] == 7
        assert body["_brainbox"]["chain_depth"] == 2

    async def test_templated_body_and_headers(self, curl):
        calls, _ = curl
        rule = self._rule(
            body={"text": "failed: {title}"},
            headers={"X-Event": "{type}"},
        )
        await er._exec_webhook(rule.actions[0], rule, DOC)
        assert calls[0]["body"]["text"] == "failed: run the tests"
        assert calls[0]["headers"]["X-Event"] == "task.failed"

    async def test_4xx_is_permanent(self, curl):
        calls, responses = curl
        responses[0] = (403, "forbidden")
        rule = self._rule()
        with pytest.raises(er._PermanentActionError, match="403"):
            await er._exec_webhook(rule.actions[0], rule, DOC)

    async def test_5xx_is_transient(self, curl):
        calls, responses = curl
        responses[0] = (503, "nope")
        rule = self._rule()
        with pytest.raises(RuntimeError, match="503"):
            await er._exec_webhook(rule.actions[0], rule, DOC)

    async def test_5xx_retries_then_dead(self, curl, monkeypatch):
        from brainbox.config import settings
        monkeypatch.setattr(settings.rules, "retry_backoff_s", 0.01)
        monkeypatch.setattr(settings.rules, "max_attempts", 3)
        calls, responses = curl
        responses[0] = (500, "boom")

        er.init_cursor_if_absent()
        rule = er.upsert_rule(er.EventRule(
            name="hook-rule", pattern={"type": ["y"]},
            actions=[er.WebhookAction(url="http://127.0.0.1:1/hook")],
        ))
        agent_store.ingest(AgentEnvelope(id="w:2", title="x", source="t", type="y"))
        await er.run_once()
        execs = er.list_executions(rule_id=rule.id)
        assert execs[0].status == "dead"
        assert execs[0].attempts == 3

    async def test_4xx_dead_without_retry(self, curl, monkeypatch):
        from brainbox.config import settings
        monkeypatch.setattr(settings.rules, "retry_backoff_s", 0.01)
        calls, responses = curl
        responses[0] = (404, "gone")
        er.init_cursor_if_absent()
        rule = er.upsert_rule(er.EventRule(
            name="hook-rule", pattern={"type": ["y"]},
            actions=[er.WebhookAction(url="http://127.0.0.1:1/hook")],
        ))
        agent_store.ingest(AgentEnvelope(id="w:3", title="x", source="t", type="y"))
        await er.run_once()
        execs = er.list_executions(rule_id=rule.id)
        assert execs[0].status == "dead"
        assert execs[0].attempts == 1


class TestRunScriptExecutor:
    @pytest.fixture(autouse=True)
    def _allow(self, monkeypatch):
        from brainbox.config import settings
        monkeypatch.setattr(settings.rules, "allow_run_script", True)

    def _rule(self, argv, **kwargs):
        return er.EventRule(
            name="script-rule", pattern={"x": ["y"]},
            actions=[er.RunScriptAction(argv=argv, **kwargs)],
        )

    async def test_receives_event_on_stdin(self):
        rule = self._rule(["/bin/cat"])
        result = await er._exec_run_script(rule.actions[0], rule, DOC)
        assert result["exit_code"] == 0
        import json as _json
        echoed = _json.loads(result["stdout"])
        assert echoed["title"] == "run the tests"
        assert echoed["seq"] == 7

    async def test_env_vars_injected(self):
        rule = self._rule([
            "/bin/sh", "-c",
            'printf "%s|%s|%s" "$BRAINBOX_EVENT_TYPE" "$BRAINBOX_RULE_NAME" "$BRAINBOX_CHAIN_DEPTH"',
        ])
        result = await er._exec_run_script(rule.actions[0], rule, DOC)
        assert result["stdout"] == "task.failed|script-rule|2"

    async def test_nonzero_exit_raises(self):
        rule = self._rule(["/bin/sh", "-c", "echo oops >&2; exit 3"])
        with pytest.raises(RuntimeError, match="exited 3"):
            await er._exec_run_script(rule.actions[0], rule, DOC)

    async def test_timeout_kills(self):
        rule = self._rule(["/bin/sleep", "5"], timeout_s=0.2)
        with pytest.raises(RuntimeError, match="timed out"):
            await er._exec_run_script(rule.actions[0], rule, DOC)

    async def test_output_capped(self, monkeypatch):
        from brainbox.config import settings
        monkeypatch.setattr(settings.rules, "output_cap_bytes", 16)
        rule = self._rule(["/bin/sh", "-c", "head -c 1000 /dev/zero | tr '\\0' 'x'"])
        result = await er._exec_run_script(rule.actions[0], rule, DOC)
        assert result["stdout"].endswith("…[truncated]")

    async def test_disabled_flag_blocks_execution(self, monkeypatch):
        from brainbox.config import settings
        monkeypatch.setattr(settings.rules, "allow_run_script", False)
        rule = self._rule(["/bin/true"])
        with pytest.raises(er._PermanentActionError, match="disabled"):
            await er._exec_run_script(rule.actions[0], rule, DOC)

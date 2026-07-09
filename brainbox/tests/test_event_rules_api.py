"""API tests for /api/rules (CRUD, validation, test endpoint, executions)."""

from __future__ import annotations

from brainbox import agent_store
from brainbox.agent_store import AgentEnvelope
from brainbox.config import settings

RULE_BODY = {
    "name": "triage-failed",
    "profile": "personal",
    "pattern": {"type": ["task.failed"]},
    "actions": [
        {"type": "submit_task", "description": "Triage: {title}", "agent_name": "triager"}
    ],
}


class TestCrud:
    async def test_create_and_get(self, client):
        r = await client.post("/api/rules", json=RULE_BODY)
        assert r.status_code == 201, r.text
        rule = r.json()
        assert rule["name"] == "triage-failed"
        assert rule["enabled"] is True

        r = await client.get(f"/api/rules/{rule['id']}")
        assert r.status_code == 200
        assert r.json()["pattern"] == {"type": ["task.failed"]}

    async def test_list_profile_filter_includes_global(self, client):
        await client.post("/api/rules", json=dict(RULE_BODY, name="p", profile="personal"))
        await client.post("/api/rules", json=dict(RULE_BODY, name="g", profile=""))
        await client.post("/api/rules", json=dict(RULE_BODY, name="other", profile="gsa"))

        r = await client.get("/api/rules", params={"profile": "personal"})
        names = {x["name"] for x in r.json()["rules"]}
        assert names == {"p", "g"}

        r = await client.get("/api/rules")
        assert r.json()["count"] == 3

    async def test_update_preserves_stats(self, client):
        r = await client.post("/api/rules", json=RULE_BODY)
        rule_id = r.json()["id"]
        from brainbox.store import _conn
        with _conn() as c:
            c.execute(
                "UPDATE event_rules SET trigger_count = 5, last_triggered_at = 123 WHERE id = %s",
                (rule_id,),
            )
        r = await client.put(f"/api/rules/{rule_id}", json=dict(RULE_BODY, name="renamed"))
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "renamed"
        assert body["trigger_count"] == 5
        assert body["last_triggered_at"] == 123

    async def test_delete(self, client):
        r = await client.post("/api/rules", json=RULE_BODY)
        rule_id = r.json()["id"]
        r = await client.delete(f"/api/rules/{rule_id}")
        assert r.status_code == 204
        r = await client.get(f"/api/rules/{rule_id}")
        assert r.status_code == 404

    async def test_enable_disable(self, client):
        r = await client.post("/api/rules", json=RULE_BODY)
        rule_id = r.json()["id"]
        r = await client.post(f"/api/rules/{rule_id}/disable")
        assert r.json() == {"id": rule_id, "enabled": False}
        r = await client.post(f"/api/rules/{rule_id}/enable")
        assert r.json() == {"id": rule_id, "enabled": True}

    async def test_missing_rule_404(self, client):
        assert (await client.get("/api/rules/nope")).status_code == 404
        assert (await client.delete("/api/rules/nope")).status_code == 404
        assert (await client.post("/api/rules/nope/enable")).status_code == 404


class TestValidation:
    async def test_invalid_pattern_400(self, client):
        r = await client.post("/api/rules", json=dict(RULE_BODY, pattern={"type": []}))
        assert r.status_code == 400
        assert "pattern_errors" in r.json()["detail"]

    async def test_missing_actions_422(self, client):
        body = dict(RULE_BODY)
        body["actions"] = []
        r = await client.post("/api/rules", json=body)
        assert r.status_code == 422

    async def test_unknown_action_type_422(self, client):
        body = dict(RULE_BODY, actions=[{"type": "explode"}])
        r = await client.post("/api/rules", json=body)
        assert r.status_code == 422

    async def test_run_script_gated(self, client, monkeypatch):
        body = dict(RULE_BODY, actions=[{"type": "run_script", "argv": ["/bin/true"]}])
        monkeypatch.setattr(settings.rules, "allow_run_script", False)
        r = await client.post("/api/rules", json=body)
        assert r.status_code == 400
        assert "CL_RULES__ALLOW_RUN_SCRIPT" in r.json()["detail"]

        monkeypatch.setattr(settings.rules, "allow_run_script", True)
        r = await client.post("/api/rules", json=body)
        assert r.status_code == 201


class TestTestEndpoint:
    async def test_event_mode(self, client):
        r = await client.post("/api/rules/test", json={
            "pattern": {"type": ["task.failed"]},
            "event": {"type": "task.failed"},
        })
        assert r.json() == {"valid": True, "errors": [], "matched": True}

        r = await client.post("/api/rules/test", json={
            "pattern": {"type": ["task.failed"]},
            "event": {"type": "task.completed"},
        })
        assert r.json()["matched"] is False

    async def test_invalid_pattern_reported(self, client):
        r = await client.post("/api/rules/test", json={"pattern": {"x": []}})
        body = r.json()
        assert body["valid"] is False and body["errors"]

    async def test_sample_mode(self, client):
        agent_store.ingest(AgentEnvelope(id="s:1", title="a", source="t", type="task.failed"))
        agent_store.ingest(AgentEnvelope(id="s:2", title="b", source="t", type="task.completed"))
        r = await client.post("/api/rules/test", json={
            "pattern": {"type": ["task.failed"]},
            "sample": {"limit": 10},
        })
        body = r.json()
        assert body["scanned"] == 2
        assert len(body["matches"]) == 1
        assert body["matches"][0]["id"] == "s:1"


class TestExecutionsEndpoints:
    def _seed_execution(self, rule_id: str, status: str = "dead") -> int:
        from brainbox.store import _conn
        with _conn() as c:
            row = c.execute(
                """
                INSERT INTO event_rule_executions
                  (rule_id, event_seq, event_id, action_index, action_type,
                   status, attempts, created_at, updated_at)
                VALUES (%s, 1, 'e', 0, 'submit_task', %s, 1, 1, 1)
                RETURNING id
                """,
                (rule_id, status),
            ).fetchone()
        return row["id"]

    async def test_per_rule_and_dlq_views(self, client):
        r = await client.post("/api/rules", json=RULE_BODY)
        rule_id = r.json()["id"]
        self._seed_execution(rule_id, "dead")
        self._seed_execution("other-rule", "ok")

        r = await client.get(f"/api/rules/{rule_id}/executions")
        assert r.json()["count"] == 1

        r = await client.get("/api/rules/executions", params={"status": "dead"})
        body = r.json()
        assert body["count"] == 1
        assert body["executions"][0]["rule_id"] == rule_id

    async def test_retry_requeues_dead(self, client):
        ex_id = self._seed_execution("r1", "dead")
        r = await client.post(f"/api/rules/executions/{ex_id}/retry")
        assert r.status_code == 200
        assert r.json()["status"] == "queued"
        assert r.json()["attempts"] == 0

    async def test_retry_conflicts_on_nonterminal(self, client):
        ex_id = self._seed_execution("r1", "ok")
        r = await client.post(f"/api/rules/executions/{ex_id}/retry")
        assert r.status_code == 409

    async def test_retry_404(self, client):
        r = await client.post("/api/rules/executions/999999/retry")
        assert r.status_code == 404

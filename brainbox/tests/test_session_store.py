"""Session store: durable task/result/handoff objects (PG-primary, MinIO mirror)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import brainbox.session_store as ss
import brainbox.router as router
from brainbox.config import settings
from brainbox.registry import _tokens, issue_bare_session_token, validate_token


# ---------------------------------------------------------------------------
# Module: put/get
# ---------------------------------------------------------------------------


class TestPutGet:
    def test_pg_round_trip_and_upsert(self, monkeypatch):
        monkeypatch.setattr(settings.minio, "enabled", False)
        ss.put("s1", ss.KEY_TASK, b'{"task": "one"}', profile="personal", task_id="t1")
        got = ss.get("s1", ss.KEY_TASK)
        assert got == (b'{"task": "one"}', "application/json")

        ss.put("s1", ss.KEY_TASK, b'{"task": "two"}', profile="personal")
        content, _ = ss.get("s1", ss.KEY_TASK)
        assert json.loads(content)["task"] == "two"
        # COALESCE keeps the original task_id on overwrite without one
        assert ss.get_by_task_id("t1")["session_name"] == "s1"

    def test_size_cap(self, monkeypatch):
        monkeypatch.setattr(settings.minio, "enabled", False)
        with pytest.raises(ValueError, match="exceeds"):
            ss.put("s1", ss.KEY_TASK, b"x" * (ss.MAX_OBJECT_BYTES + 1))

    def test_mirror_write_through(self, monkeypatch):
        calls = []
        monkeypatch.setattr(settings.minio, "enabled", True)

        def _fake_put(bucket, key, data, **kwargs):
            calls.append((bucket, key, data, kwargs))

        import brainbox.artifacts as artifacts
        monkeypatch.setattr(artifacts, "put_object", _fake_put)
        ss.put("s2", ss.KEY_TASK, b"{}", profile="personal", task_id="t2")
        assert calls[0][0] == "artifacts"
        assert calls[0][1] == "personal/sessions/s2/task.json"

    def test_mirror_failure_never_fails_put(self, monkeypatch):
        monkeypatch.setattr(settings.minio, "enabled", True)

        def _boom(*a, **k):
            raise RuntimeError("cross-profile 403")

        import brainbox.artifacts as artifacts
        monkeypatch.setattr(artifacts, "put_object", _boom)
        ss.put("s3", ss.KEY_RESULT, b"{}", profile="gsa")
        assert ss.get("s3", ss.KEY_RESULT) is not None

    def test_get_minio_fallback_when_pg_absent(self, monkeypatch):
        """Covers objects the agent wrote DIRECTLY to MinIO (handoff.md)."""
        monkeypatch.setattr(settings.minio, "enabled", True)
        # A task row establishes the session's profile for the lookup.
        monkeypatch.setattr(
            "brainbox.artifacts.put_object", lambda *a, **k: None
        )
        ss.put("s4", ss.KEY_TASK, b"{}", profile="personal")

        def _fake_get(bucket, key):
            if key == "personal/sessions/s4/handoff.md":
                return b"# handoff written directly"
            raise RuntimeError("NoSuchKey")

        monkeypatch.setattr("brainbox.artifacts.get_object", _fake_get)
        got = ss.get("s4", ss.KEY_HANDOFF)
        assert got == (b"# handoff written directly", "text/markdown")

    def test_object_key_no_profile(self):
        assert ss.object_key("", "s", "task.json") == "_none/sessions/s/task.json"


# ---------------------------------------------------------------------------
# Create endpoint integration
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_pipeline(monkeypatch):
    """No-op run_pipeline capturing kwargs; MinIO off for PG-only flow."""
    captured = {}

    async def _fake(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(backend="docker", session_name=kwargs.get("session_name"))

    import brainbox.api as api
    monkeypatch.setattr(api, "run_pipeline", _fake)
    monkeypatch.setattr(settings.minio, "enabled", False)
    return captured


class TestCreateWritesTask:
    async def test_task_row_written_with_token(self, client, fake_pipeline):
        r = await client.post("/api/create", json={
            "name": "cs1", "task": "do the thing", "workspace_profile": "personal",
        })
        assert r.status_code == 200, r.text
        row = ss.get_by_task_id(fake_pipeline["task_id"])
        assert row is not None
        assert row["session_name"] == "cs1"
        assert row["profile"] == "personal"
        assert row["token_id"]  # bare token minted (role unregistered in tests)
        doc = json.loads(row["content"])
        assert doc["task"] == "do the thing"
        assert "brainbox-complete" in doc["footer"]

    async def test_no_task_no_row(self, client, fake_pipeline):
        r = await client.post("/api/create", json={"name": "cs2"})
        assert r.status_code == 200, r.text
        assert ss.list_keys("cs2") == []

    async def test_env_contract(self, client, fake_pipeline, monkeypatch):
        monkeypatch.setattr(settings, "public_url", "https://api.example.com")
        r = await client.post("/api/create", json={
            "name": "cs3", "task": "t", "workspace_profile": "personal",
            "env": {"MY_VAR": "1", "BRAINBOX_SESSION_NAME": "spoofed"},
        })
        assert r.status_code == 200, r.text
        env = fake_pipeline["extra_env"]
        assert env["BRAINBOX_SESSION_NAME"] == "cs3"  # contract wins over body.env
        assert env["BRAINBOX_HUB_URL_PUBLIC"] == "https://api.example.com"
        assert env["BRAINBOX_TOKEN"]
        assert env["BRAINBOX_TASK_ID"] == fake_pipeline["task_id"]
        assert env["BRAINBOX_PROFILE"] == "personal"
        assert env["MY_VAR"] == "1"  # caller env still flows

    async def test_env_contract_provider(self, client, fake_pipeline):
        r = await client.post("/api/create", json={
            "name": "cprov", "llm_provider": "ollama", "llm_model": "qwen3:8b",
        })
        assert r.status_code == 200, r.text
        env = fake_pipeline["extra_env"]
        assert env["LLM_PROVIDER"] == "ollama"     # rides the contract to the runner
        assert env["CLAUDE_MODEL"] == "qwen3:8b"

    async def test_env_contract_provider_defaults_claude(self, client, fake_pipeline):
        r = await client.post("/api/create", json={"name": "cprov2"})
        assert r.status_code == 200
        assert fake_pipeline["extra_env"]["LLM_PROVIDER"] == "claude"

    async def test_env_contract_public_url_fallback(self, client, fake_pipeline, monkeypatch):
        monkeypatch.setattr(settings, "public_url", "")
        r = await client.post("/api/create", json={"name": "cs4", "task": "t"})
        assert r.status_code == 200
        assert "host.docker.internal" in fake_pipeline["extra_env"]["BRAINBOX_HUB_URL_PUBLIC"]

    async def test_s3_allowlist_merged(self, client, fake_pipeline, monkeypatch):
        import brainbox.gateway_secrets as gs
        monkeypatch.setattr(gs, "get_profile_env", lambda p: {
            "BRAINBOX_S3_ENDPOINT": "http://minio:9000",
            "BRAINBOX_S3_ACCESS_KEY": "ak",
            "BRAINBOX_S3_SECRET_KEY": "sk",
            "BRAINBOX_S3_BUCKET": "phantom-artifacts",
            "UNRELATED_SECRET": "never",
        })
        r = await client.post("/api/create", json={
            "name": "cs5", "task": "t", "workspace_profile": "personal",
        })
        assert r.status_code == 200
        env = fake_pipeline["extra_env"]
        assert env["BRAINBOX_S3_ENDPOINT"] == "http://minio:9000"
        assert env["BRAINBOX_S3_PREFIX"] == "personal/sessions/cs5/"
        assert "UNRELATED_SECRET" not in env

    async def test_bare_token_minted_for_unregistered_role(self, client, fake_pipeline):
        r = await client.post("/api/create", json={"name": "cs6", "task": "t"})
        assert r.status_code == 200
        token_id = fake_pipeline["extra_env"]["BRAINBOX_TOKEN"]
        token = validate_token(token_id)
        assert token is not None and token.capabilities == []


# ---------------------------------------------------------------------------
# GET /api/session-store/task
# ---------------------------------------------------------------------------


class TestGetTask:
    def _seed(self, session="gt1", task="fix it"):
        token = issue_bare_session_token("assistant", f"tid-{session}")
        doc = {"task": task, "task_id": f"tid-{session}", "session_name": session,
               "footer": "run: brainbox-complete"}
        ss.put(session, ss.KEY_TASK, json.dumps(doc).encode(),
               profile="personal", task_id=f"tid-{session}", token_id=token.token_id)
        return token

    async def test_text_plain_composed(self, client, monkeypatch):
        monkeypatch.setattr(settings.minio, "enabled", False)
        token = self._seed()
        r = await client.get("/api/session-store/task",
                             headers={"Authorization": f"Bearer {token.token_id}"})
        assert r.status_code == 200
        assert r.text == "fix it\n\nrun: brainbox-complete"
        assert r.headers["content-type"].startswith("text/plain")

    async def test_json_via_accept(self, client, monkeypatch):
        monkeypatch.setattr(settings.minio, "enabled", False)
        token = self._seed("gt2")
        r = await client.get("/api/session-store/task", headers={
            "Authorization": f"Bearer {token.token_id}", "Accept": "application/json",
        })
        assert r.json()["session_name"] == "gt2"

    async def test_no_token_401(self, client):
        r = await client.get("/api/session-store/task")
        assert r.status_code == 401

    async def test_taskless_session_404(self, client):
        token = issue_bare_session_token("assistant", "tid-nothing")
        r = await client.get("/api/session-store/task",
                             headers={"Authorization": f"Bearer {token.token_id}"})
        assert r.status_code == 404

    async def test_survives_router_restart(self, client, monkeypatch):
        """Resolution is PG-only — clearing router._tasks must not matter."""
        monkeypatch.setattr(settings.minio, "enabled", False)
        token = self._seed("gt3")
        router._tasks.clear()
        r = await client.get("/api/session-store/task",
                             headers={"Authorization": f"Bearer {token.token_id}"})
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# PUT result / handoff
# ---------------------------------------------------------------------------


class TestPutResult:
    def _seed_with_hub_task(self, session="pr1", monkeypatch=None):
        from brainbox.models import Task, TaskStatus
        from brainbox.utils import now_ms
        token = issue_bare_session_token("assistant", f"tid-{session}")
        router._tasks[f"tid-{session}"] = Task(
            id=f"tid-{session}", description="d", agent_name="assistant",
            status=TaskStatus.RUNNING, created_at=now_ms(), updated_at=now_ms(),
            session_name=session,
        )
        ss.put(session, ss.KEY_TASK, b'{"task": "d"}', profile="personal",
               task_id=f"tid-{session}", token_id=token.token_id)
        return token

    async def test_valid_token_stores_and_completes(self, client, monkeypatch):
        monkeypatch.setattr(settings.minio, "enabled", False)
        # complete_task finalizes sessions (docker calls) — stub it.
        import brainbox.api as api
        completed = {}

        async def _fake_complete(task_id, result):
            completed["task_id"] = task_id

        monkeypatch.setattr(api, "complete_task", _fake_complete)
        token = self._seed_with_hub_task("pr1")
        r = await client.put("/api/session-store/result",
                             headers={"Authorization": f"Bearer {token.token_id}"},
                             json={"result": "all done"})
        assert r.status_code == 200
        assert r.json() == {"stored": True, "completed": True}
        assert completed["task_id"] == "tid-pr1"
        doc = json.loads(ss.get("pr1", ss.KEY_RESULT)[0])
        assert doc["result"] == "all done" and doc["via"] == "token"

    async def test_expired_token_matches_pg_row(self, client, monkeypatch):
        monkeypatch.setattr(settings.minio, "enabled", False)
        import brainbox.api as api

        async def _fake_complete(task_id, result):
            pass

        monkeypatch.setattr(api, "complete_task", _fake_complete)
        token = self._seed_with_hub_task("pr2")
        _tokens.pop(token.token_id, None)  # simulate expiry/pop
        r = await client.put("/api/session-store/result",
                             headers={"Authorization": f"Bearer {token.token_id}"},
                             json={"result": "late finish"})
        assert r.status_code == 200
        doc = json.loads(ss.get("pr2", ss.KEY_RESULT)[0])
        assert doc["via"] == "token-expired"

    async def test_api_key_with_task_id(self, client, monkeypatch):
        monkeypatch.setattr(settings.minio, "enabled", False)
        import brainbox.api as api

        async def _fake_complete(task_id, result):
            pass

        monkeypatch.setattr(api, "complete_task", _fake_complete)
        self._seed_with_hub_task("pr3")
        # conftest disables require_api_key but _resolve_session_store_writer
        # checks the header itself — set a real key.
        import brainbox.auth as auth
        auth._api_key = "test-key"
        r = await client.put("/api/session-store/result",
                             headers={"X-API-Key": "test-key"},
                             json={"result": "via key", "task_id": "tid-pr3"})
        assert r.status_code == 200
        assert json.loads(ss.get("pr3", ss.KEY_RESULT)[0])["via"] == "api-key"

    async def test_unknown_token_401(self, client):
        r = await client.put("/api/session-store/result",
                             headers={"Authorization": "Bearer nope"},
                             json={"result": "x"})
        assert r.status_code == 401

    async def test_oversize_413(self, client, monkeypatch):
        monkeypatch.setattr(settings.minio, "enabled", False)
        token = self._seed_with_hub_task("pr4")
        r = await client.put("/api/session-store/result",
                             headers={"Authorization": f"Bearer {token.token_id}"},
                             json={"result": "x" * (ss.MAX_OBJECT_BYTES + 10)})
        assert r.status_code == 413

    async def test_already_completed_still_stores(self, client, monkeypatch):
        monkeypatch.setattr(settings.minio, "enabled", False)
        import brainbox.api as api

        async def _raise(task_id, result):
            raise ValueError("already completed")

        monkeypatch.setattr(api, "complete_task", _raise)
        token = self._seed_with_hub_task("pr5")
        r = await client.put("/api/session-store/result",
                             headers={"Authorization": f"Bearer {token.token_id}"},
                             json={"result": "again"})
        assert r.json() == {"stored": True, "completed": False}

    async def test_put_handoff(self, client, monkeypatch):
        monkeypatch.setattr(settings.minio, "enabled", False)
        token = self._seed_with_hub_task("pr6")
        r = await client.put("/api/session-store/handoff",
                             headers={"Authorization": f"Bearer {token.token_id}"},
                             json={"handoff": "# Where I left off"})
        assert r.status_code == 200
        got = ss.get("pr6", ss.KEY_HANDOFF)
        assert got == (b"# Where I left off", "text/markdown")


# ---------------------------------------------------------------------------
# continue_from + operator GET
# ---------------------------------------------------------------------------


class TestContinueFrom:
    async def test_prepends_handoff(self, client, fake_pipeline):
        ss.put("old-sess", ss.KEY_HANDOFF, b"prior context here",
               profile="personal", content_type="text/markdown")
        r = await client.post("/api/create", json={
            "name": "cf1", "task": "keep going", "continue_from": "old-sess",
        })
        assert r.status_code == 200, r.text
        doc = json.loads(ss.get("cf1", ss.KEY_TASK)[0])
        assert "Context from previous session old-sess" in doc["task"]
        assert "prior context here" in doc["task"]
        assert doc["task"].endswith("keep going")

    async def test_missing_handoff_400(self, client, fake_pipeline):
        r = await client.post("/api/create", json={
            "name": "cf2", "task": "t", "continue_from": "ghost-sess",
        })
        assert r.status_code == 400

    async def test_handoff_only_prompt(self, client, fake_pipeline):
        ss.put("old2", ss.KEY_HANDOFF, b"ctx", profile="", content_type="text/markdown")
        r = await client.post("/api/create", json={"name": "cf3", "continue_from": "old2"})
        assert r.status_code == 200
        doc = json.loads(ss.get("cf3", ss.KEY_TASK)[0])
        assert "Continue the work described above." in doc["task"]


class TestOperatorGet:
    async def test_get_object(self, client, monkeypatch):
        monkeypatch.setattr(settings.minio, "enabled", False)
        ss.put("og1", ss.KEY_HANDOFF, b"# doc", content_type="text/markdown")
        r = await client.get("/api/session-store/og1/handoff.md")
        assert r.status_code == 200
        assert r.text == "# doc"
        assert r.headers["content-type"].startswith("text/markdown")

    async def test_bad_key_400(self, client):
        r = await client.get("/api/session-store/og1/secrets.env")
        assert r.status_code == 400

    async def test_missing_404(self, client, monkeypatch):
        monkeypatch.setattr(settings.minio, "enabled", False)
        r = await client.get("/api/session-store/nope/task.json")
        assert r.status_code == 404


class TestLocalContainerEnv:
    def test_profile_env_key_passes_into_container_env(self):
        """PROFILE_ENV_KEY must be real container env on the local path —
        the entrypoint wrapper gates baked-credential decryption on it."""
        from brainbox.backends.docker import _build_container_env
        from brainbox.models import SessionContext

        ctx = SessionContext(
            session_name="s", container_name="c", port=1, role="assistant",
            created_at=1, ttl=3600,
            extra_env={"PROFILE_ENV_KEY": "k123", "OTHER": "no"},
        )
        env = _build_container_env(ctx)
        assert env["PROFILE_ENV_KEY"] == "k123"
        assert "OTHER" not in env  # only the decrypt key passes through

    def test_no_key_no_var(self):
        from brainbox.backends.docker import _build_container_env
        from brainbox.models import SessionContext

        ctx = SessionContext(
            session_name="s", container_name="c", port=1, role="assistant",
            created_at=1, ttl=3600,
        )
        assert "PROFILE_ENV_KEY" not in _build_container_env(ctx)

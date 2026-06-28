"""Tests for the A2A protocol façade (ADR-001 Phase 1, core task lifecycle).

Exercises the operator-facing A2A surface via the FastAPI ASGI client:
  - agent card discovery (per agent, 404 for unknown, inert roles excluded)
  - JSON-RPC message/send → task creation
  - tasks/get round-trip + TaskNotFound
  - tasks/cancel mechanics
  - unknown method → method-not-found
  - the brainbox→A2A status serializer (_to_a2a_task)
"""

from __future__ import annotations

import pytest

import brainbox.auth as auth_module
import brainbox.registry as reg_module
import brainbox.router as router_module
from brainbox.a2a import _to_a2a_task
from brainbox.models import AgentDefinition, SuspensionKind, Task, TaskStatus

API_KEY = "test-a2a-key"


@pytest.fixture
def agents():
    """Register the four spawnable agents; leave inert md-only roles absent."""
    defs = {
        "supervisor": ["shell_exec", "task_submit", "hub_messaging"],
        "worker": ["shell_exec", "write_code", "task_submit"],
        "assistant": ["hub_messaging"],
        "reviewer": ["read_code"],
    }
    for name, caps in defs.items():
        reg_module._agents[name] = AgentDefinition(
            name=name, image="test-image", description=f"{name} agent", capabilities=caps
        )
    return defs


@pytest.fixture
def api_key():
    """Set a real API key so require_capability('task_submit') accepts X-API-Key."""
    auth_module._api_key = API_KEY
    return API_KEY


def _now() -> int:
    return 1_700_000_000_000


def _make_task(status: TaskStatus = TaskStatus.PENDING, **kw) -> Task:
    return Task(
        id=kw.pop("id", "t-1"),
        description=kw.pop("description", "do a thing"),
        agent_name=kw.pop("agent_name", "worker"),
        status=status,
        created_at=_now(),
        updated_at=_now(),
        **kw,
    )


# ---------------------------------------------------------------------------
# Agent card discovery
# ---------------------------------------------------------------------------


class TestAgentCard:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", ["supervisor", "worker", "assistant", "reviewer"])
    async def test_card_for_each_spawnable_agent(self, client, agents, name):
        async with client as c:
            resp = await c.get(f"/a2a/{name}/.well-known/agent-card.json")
        assert resp.status_code == 200
        card = resp.json()
        assert card["name"] == name
        assert card["url"].endswith(f"/a2a/{name}")
        assert card["capabilities"]["streaming"] is True
        skill_ids = {s["id"] for s in card["skills"]}
        assert set(agents[name]) == skill_ids

    @pytest.mark.asyncio
    async def test_unknown_agent_returns_404(self, client, agents):
        async with client as c:
            resp = await c.get("/a2a/ghost/.well-known/agent-card.json")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_inert_role_without_definition_is_absent(self, client, agents):
        # merge-queue has a role .md but no JSON AgentDefinition → not registered.
        async with client as c:
            resp = await c.get("/a2a/merge-queue/.well-known/agent-card.json")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_card_is_public_no_auth(self, client, agents):
        # No API key set, no header — discovery must still succeed.
        async with client as c:
            resp = await c.get("/a2a/worker/.well-known/agent-card.json")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# JSON-RPC: message/send
# ---------------------------------------------------------------------------


class TestMessageSend:
    @pytest.mark.asyncio
    async def test_creates_task_and_returns_submitted(self, client, agents, api_key):
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "message/send",
            "params": {"message": {"role": "user", "parts": [{"kind": "text", "text": "hi"}]}},
        }
        async with client as c:
            resp = await c.post("/a2a/worker", json=body, headers={"x-api-key": api_key})
        assert resp.status_code == 200
        env = resp.json()
        assert env["jsonrpc"] == "2.0"
        assert env["id"] == 1
        task = env["result"]
        assert task["status"]["state"] == "submitted"
        assert task["id"]
        assert task["kind"] == "task"
        # Task actually landed in the router store.
        assert router_module.get_task(task["id"]) is not None

    @pytest.mark.asyncio
    async def test_empty_text_is_invalid_params(self, client, agents, api_key):
        body = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "message/send",
            "params": {"message": {"role": "user", "parts": []}},
        }
        async with client as c:
            resp = await c.post("/a2a/worker", json=body, headers={"x-api-key": api_key})
        assert resp.json()["error"]["code"] == -32602

    @pytest.mark.asyncio
    async def test_missing_api_key_is_unauthorized(self, client, agents):
        body = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "message/send",
            "params": {"message": {"role": "user", "parts": [{"kind": "text", "text": "hi"}]}},
        }
        async with client as c:
            resp = await c.post("/a2a/worker", json=body)  # no key set, no header
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_unknown_agent_returns_404(self, client, agents, api_key):
        body = {"jsonrpc": "2.0", "id": 4, "method": "tasks/get", "params": {"id": "x"}}
        async with client as c:
            resp = await c.post("/a2a/ghost", json=body, headers={"x-api-key": api_key})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# JSON-RPC: message/send continuation (input-required → resume)
# ---------------------------------------------------------------------------


def _send_with_task(req_id, task_id, text="here is the input"):
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "taskId": task_id,
                "parts": [{"kind": "text", "text": text}],
            }
        },
    }


class TestMessageSendContinuation:
    @pytest.mark.asyncio
    async def test_resumes_input_required_task(self, client, agents, api_key):
        task = await router_module.submit_task("needs input", "worker")
        router_module.suspend_task(task.id, SuspensionKind.HUMAN)
        assert router_module.get_task(task.id).status == TaskStatus.NEEDS_ACTION

        async with client as c:
            resp = await c.post(
                "/a2a/worker", json=_send_with_task(9, task.id), headers={"x-api-key": api_key}
            )
        assert resp.status_code == 200
        # resume_task moves NEEDS_ACTION → PENDING → A2A "submitted".
        assert resp.json()["result"]["status"]["state"] == "submitted"
        resumed = router_module.get_task(task.id)
        assert resumed.status == TaskStatus.PENDING
        assert resumed.resume_payload.get("input") == "here is the input"

    @pytest.mark.asyncio
    async def test_continuation_unknown_task_is_task_not_found(self, client, agents, api_key):
        async with client as c:
            resp = await c.post(
                "/a2a/worker", json=_send_with_task(10, "ghost"), headers={"x-api-key": api_key}
            )
        assert resp.json()["error"]["code"] == -32001

    @pytest.mark.asyncio
    async def test_continuation_on_non_input_required_task_is_not_resumable(
        self, client, agents, api_key
    ):
        task = await router_module.submit_task("still pending", "worker")  # PENDING, not parked
        async with client as c:
            resp = await c.post(
                "/a2a/worker", json=_send_with_task(11, task.id), headers={"x-api-key": api_key}
            )
        assert resp.json()["error"]["code"] == -32003


# ---------------------------------------------------------------------------
# JSON-RPC: tasks/get, tasks/cancel, unknown method
# ---------------------------------------------------------------------------


class TestTaskMethods:
    @pytest.mark.asyncio
    async def test_tasks_get_round_trip(self, client, agents, api_key):
        task = await router_module.submit_task("review pr", "worker")
        body = {"jsonrpc": "2.0", "id": 5, "method": "tasks/get", "params": {"id": task.id}}
        async with client as c:
            resp = await c.post("/a2a/worker", json=body, headers={"x-api-key": api_key})
        assert resp.json()["result"]["id"] == task.id

    @pytest.mark.asyncio
    async def test_tasks_get_unknown_id_is_task_not_found(self, client, agents, api_key):
        body = {"jsonrpc": "2.0", "id": 6, "method": "tasks/get", "params": {"id": "nope"}}
        async with client as c:
            resp = await c.post("/a2a/worker", json=body, headers={"x-api-key": api_key})
        assert resp.json()["error"]["code"] == -32001

    @pytest.mark.asyncio
    async def test_tasks_cancel_transitions_to_canceled(self, client, agents, api_key):
        task = await router_module.submit_task("long job", "worker")
        body = {"jsonrpc": "2.0", "id": 7, "method": "tasks/cancel", "params": {"id": task.id}}
        async with client as c:
            resp = await c.post("/a2a/worker", json=body, headers={"x-api-key": api_key})
        assert resp.json()["result"]["status"]["state"] == "canceled"

    @pytest.mark.asyncio
    async def test_unknown_method_is_method_not_found(self, client, agents, api_key):
        body = {"jsonrpc": "2.0", "id": 8, "method": "tasks/explode", "params": {}}
        async with client as c:
            resp = await c.post("/a2a/worker", json=body, headers={"x-api-key": api_key})
        assert resp.json()["error"]["code"] == -32601


# ---------------------------------------------------------------------------
# SSE stream
# ---------------------------------------------------------------------------


class TestStream:
    @pytest.mark.asyncio
    async def test_stream_emits_status_for_terminal_task(self, client, agents, api_key):
        # A terminal task short-circuits after the initial snapshot, so the
        # stream closes immediately (no hang).
        task = await router_module.submit_task("done", "worker")
        task.status = TaskStatus.COMPLETED
        lines: list[str] = []
        async with client as c:
            async with c.stream("GET", f"/a2a/worker/stream?taskId={task.id}") as resp:
                assert resp.status_code == 200
                async for line in resp.aiter_lines():
                    lines.append(line)
                    if "final" in line:
                        break
        body = "\n".join(lines)
        assert "completed" in body and "true" in body

    @pytest.mark.asyncio
    async def test_stream_unknown_task_returns_404(self, client, agents, api_key):
        async with client as c:
            resp = await c.get("/a2a/worker/stream?taskId=nope")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Serializer unit test
# ---------------------------------------------------------------------------


class TestToA2ATask:
    @pytest.mark.parametrize(
        "status,expected",
        [
            (TaskStatus.PENDING, "submitted"),
            (TaskStatus.RUNNING, "working"),
            (TaskStatus.BLOCKED, "working"),
            (TaskStatus.NEEDS_ACTION, "input-required"),
            (TaskStatus.COMPLETED, "completed"),
            (TaskStatus.FAILED, "failed"),
            (TaskStatus.CANCELLED, "canceled"),
        ],
    )
    def test_status_map(self, status, expected):
        assert _to_a2a_task(_make_task(status))["status"]["state"] == expected

    def test_context_id_defaults_to_task_id_when_no_job(self):
        obj = _to_a2a_task(_make_task(id="abc", job_id=None))
        assert obj["contextId"] == "abc"

    def test_completed_task_includes_artifact(self):
        obj = _to_a2a_task(_make_task(TaskStatus.COMPLETED, result="all done"))
        assert obj["artifacts"][0]["parts"][0]["text"] == "all done"

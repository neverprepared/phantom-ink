"""Tests for the conversation REST + SSE surface (multi-agent Chat, PR1).

Covers:
  - CRUD routes (create / list / get / archive / messages)
  - server-side profile scoping on every route (cross-profile reads and writes
    are a 404, never a leak)
  - the SSE contract: created → delta* → done ordering, for both a human post
    and a persona reply
  - the persona reply path through the ``complete()`` seam, with a fake backend
    injected into the llm registry (no network, no models)
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from brainbox import conversation_runtime as runtime
from brainbox import conversation_store as cs
from brainbox import llm
from brainbox.llm import Completion


PROFILE = "personal"


class FakeBackend:
    """Minimal llm backend that answers with a fixed string."""

    name = "ollama"

    def __init__(self, answer="Hello there friend"):
        self.answer = answer
        self.calls: list[list[dict]] = []

    def estimates_cost(self):
        return False

    async def complete(self, messages, *, model, ctx, profile):
        self.calls.append(messages)
        return Completion(text=self.answer, backend=self.name, model=model or "fake")


@pytest.fixture
async def c(client):
    """The shared AsyncClient, opened once per test.

    ``client`` is a single-use instance (httpx refuses to reopen a closed
    client), so every test takes the opened one rather than entering it in
    each helper.
    """
    async with client as opened:
        yield opened


@pytest.fixture
async def live_url():
    """A real uvicorn server bound to an ephemeral port.

    The in-process ASGI transport buffers a response body, so it can never
    observe an SSE frame arriving mid-stream — verifying the streaming endpoint
    at all requires a real socket.
    """
    import uvicorn

    from brainbox.api import app

    # lifespan="off": the app's startup hook wires docker watchers and hub
    # background tasks, none of which this test needs.
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    await task


@pytest.fixture
def fake_llm():
    backend = FakeBackend()
    llm.set_registry({"ollama": backend})
    return backend


async def _create(c, *, profile=PROFILE, title="planning", persona=True):
    participants = [{"name": "user", "kind": "human"}]
    if persona:
        participants.append(
            {
                "name": "sage",
                "kind": "persona",
                "model_target": {"provider": "ollama", "model": "qwen3:8b"},
                "role_prompt": "Be terse.",
            }
        )
    resp = await c.post(
        "/api/conversations",
        json={"title": title, "profile": profile, "participants": participants},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestConversationCrudRoutes:
    async def test_create_returns_record(self, c):
        conv = await _create(c)
        assert conv["title"] == "planning"
        assert conv["profile"] == PROFILE
        assert conv["status"] == "active"
        assert len(conv["id"]) == 26
        assert [p["name"] for p in conv["participants"]] == ["user", "sage"]

    async def test_list_and_get(self, c):
        conv = await _create(c)
        listed = await c.get("/api/conversations", params={"profile": PROFILE})
        got = await c.get(f"/api/conversations/{conv['id']}", params={"profile": PROFILE})
        assert [x["id"] for x in listed.json()] == [conv["id"]]
        assert got.json()["id"] == conv["id"]

    async def test_archive_then_excluded_from_active_list(self, c):
        conv = await _create(c)
        arch = await c.post(
            f"/api/conversations/{conv['id']}/archive", params={"profile": PROFILE}
        )
        active = await c.get(
            "/api/conversations", params={"profile": PROFILE, "include_archived": False}
        )
        assert arch.json()["status"] == "archived"
        assert active.json() == []

    async def test_archived_conversation_rejects_new_messages(self, c, fake_llm):
        conv = await _create(c)
        await c.post(f"/api/conversations/{conv['id']}/archive", params={"profile": PROFILE})
        resp = await c.post(
            f"/api/conversations/{conv['id']}/messages",
            params={"profile": PROFILE},
            json={"author": "user", "content": "still there?"},
        )
        assert resp.status_code == 400

    async def test_profile_is_required(self, c):
        resp = await c.get("/api/conversations")
        assert resp.status_code == 422  # missing required query param

    async def test_unknown_conversation_is_404(self, c):
        resp = await c.get(
            "/api/conversations/01JNOPEXXXXXXXXXXXXXXXXXXX", params={"profile": PROFILE}
        )
        assert resp.status_code == 404


class TestProfileScopingOverHttp:
    async def test_cross_profile_get_is_404(self, c):
        conv = await _create(c, profile="personal")
        resp = await c.get(f"/api/conversations/{conv['id']}", params={"profile": "work"})
        assert resp.status_code == 404

    async def test_cross_profile_list_does_not_leak(self, c):
        await _create(c, profile="personal", title="mine")
        resp = await c.get("/api/conversations", params={"profile": "work"})
        assert resp.json() == []

    async def test_cross_profile_post_is_404_and_persists_nothing(self, c, fake_llm):
        conv = await _create(c, profile="personal")
        resp = await c.post(
            f"/api/conversations/{conv['id']}/messages",
            params={"profile": "work"},
            json={"author": "intruder", "content": "hi"},
        )
        assert resp.status_code == 404
        assert cs.list_messages(conv["id"], profile="personal") == []

    async def test_cross_profile_archive_is_404(self, c):
        conv = await _create(c, profile="personal")
        resp = await c.post(
            f"/api/conversations/{conv['id']}/archive", params={"profile": "work"}
        )
        assert resp.status_code == 404
        assert cs.get_conversation(conv["id"], profile="personal").status == "active"

    async def test_cross_profile_stream_is_404(self, c):
        conv = await _create(c, profile="personal")
        resp = await c.get(
            f"/api/conversations/{conv['id']}/stream", params={"profile": "work"}
        )
        assert resp.status_code == 404


class TestPostMessageAndPersonaReply:
    async def test_human_message_persists_and_persona_answers(self, c, fake_llm):
        conv = await _create(c)
        resp = await c.post(
            f"/api/conversations/{conv['id']}/messages",
            params={"profile": PROFILE},
            json={"author": "user", "content": "what next?"},
        )
        assert resp.status_code == 200
        assert resp.json()["author"] == "user"

        await _drain_replies()

        msgs = cs.list_messages(conv["id"], profile=PROFILE)
        assert [(m.author, m.content) for m in msgs] == [
            ("user", "what next?"),
            ("sage", "Hello there friend"),
        ]
        # The persona reply is threaded to the message that triggered it.
        assert msgs[1].in_reply_to == msgs[0].id

    async def test_prompt_carries_role_prompt_and_history(self, c, fake_llm):
        conv = await _create(c)
        await c.post(
            f"/api/conversations/{conv['id']}/messages",
            params={"profile": PROFILE},
            json={"author": "user", "content": "ping"},
        )
        await _drain_replies()

        sent = fake_llm.calls[0]
        assert sent[0] == {"role": "system", "content": "Be terse."}
        assert sent[-1] == {"role": "user", "content": "[user]: ping"}

    async def test_no_persona_means_no_reply(self, c, fake_llm):
        conv = await _create(c, persona=False)
        await c.post(
            f"/api/conversations/{conv['id']}/messages",
            params={"profile": PROFILE},
            json={"author": "user", "content": "anyone?"},
        )
        await _drain_replies()
        assert len(cs.list_messages(conv["id"], profile=PROFILE)) == 1

    async def test_message_addressed_to_someone_else_does_not_wake_persona(self, c, fake_llm):
        conv = await _create(c)
        await c.post(
            f"/api/conversations/{conv['id']}/messages",
            params={"profile": PROFILE},
            json={"author": "user", "content": "hi", "addressed_to": "someone-else"},
        )
        await _drain_replies()
        assert len(cs.list_messages(conv["id"], profile=PROFILE)) == 1

    async def test_list_messages_supports_since_id(self, c, fake_llm):
        conv = await _create(c, persona=False)
        first = await c.post(
            f"/api/conversations/{conv['id']}/messages",
            params={"profile": PROFILE},
            json={"author": "user", "content": "one"},
        )
        await c.post(
            f"/api/conversations/{conv['id']}/messages",
            params={"profile": PROFILE},
            json={"author": "user", "content": "two"},
        )
        resp = await c.get(
            f"/api/conversations/{conv['id']}/messages",
            params={"profile": PROFILE, "since_id": first.json()["id"]},
        )
        assert [m["content"] for m in resp.json()] == ["two"]


# --------------------------------------------------------------------------- #
# SSE contract                                                                 #
# --------------------------------------------------------------------------- #


async def _drain_replies():
    """Let the fire-and-forget persona reply task finish.

    The POST route returns as soon as the human message is durable and schedules
    the reply; tests await the scheduled tasks rather than sleeping.
    """
    from brainbox.api import _persona_reply_tasks

    for _ in range(10):
        pending = [t for t in list(_persona_reply_tasks) if not t.done()]
        if not pending:
            await asyncio.sleep(0)
            if not [t for t in list(_persona_reply_tasks) if not t.done()]:
                return
            continue
        await asyncio.gather(*pending, return_exceptions=True)


def _events(frames: list[str]) -> list[tuple[str, dict]]:
    out = []
    for raw in frames:
        parsed = json.loads(raw)
        out.append((parsed["event"], parsed["data"]))
    return out


class TestSseContract:
    async def test_human_message_emits_created_then_done(self, c, fake_llm):
        conv = await _create(c, persona=False)
        q = runtime.subscribe(conv["id"])
        await c.post(
            f"/api/conversations/{conv['id']}/messages",
            params={"profile": PROFILE},
            json={"author": "user", "content": "hello"},
        )
        frames = _events([q.get_nowait() for _ in range(q.qsize())])
        assert [name for name, _ in frames] == ["message.created", "message.done"]
        assert frames[1][1]["message"]["content"] == "hello"

    async def test_persona_reply_streams_created_deltas_then_done(self, c, fake_llm):
        conv = await _create(c)
        q = runtime.subscribe(conv["id"])
        await c.post(
            f"/api/conversations/{conv['id']}/messages",
            params={"profile": PROFILE},
            json={"author": "user", "content": "hi"},
        )
        await _drain_replies()

        frames = _events([q.get_nowait() for _ in range(q.qsize())])
        names = [name for name, _ in frames]
        # Human message first, then the persona's thinking → created → deltas → done.
        assert names[:2] == ["message.created", "message.done"]
        assert names[2] == "thinking"
        assert names[3] == "message.created"
        assert names[-1] == "message.done"
        assert set(names[4:-1]) == {"message.delta"}

        shell = frames[3][1]["message"]
        assert shell["author"] == "sage"
        assert shell["content"] == ""

        deltas = [data["delta"] for name, data in frames[4:-1]]
        assert "".join(deltas) == "Hello there friend"
        # Every delta is keyed to the id announced by message.created, so the
        # client never has to reconcile a placeholder.
        assert {data["id"] for _, data in frames[4:-1]} == {shell["id"]}

        done = frames[-1][1]["message"]
        assert done["id"] == shell["id"]
        assert done["content"] == "Hello there friend"

    async def test_failed_completion_emits_error_not_a_stuck_bubble(self, c):
        class Boom(FakeBackend):
            async def complete(self, messages, *, model, ctx, profile):
                raise llm.LlmError("backend down")

        llm.set_registry({"ollama": Boom()})
        conv = await _create(c)
        q = runtime.subscribe(conv["id"])
        await c.post(
            f"/api/conversations/{conv['id']}/messages",
            params={"profile": PROFILE},
            json={"author": "user", "content": "hi"},
        )
        await _drain_replies()

        names = [name for name, _ in _events([q.get_nowait() for _ in range(q.qsize())])]
        assert names[-1] == "error"
        # Only the human message was persisted; no half-written persona row.
        assert len(cs.list_messages(conv["id"], profile=PROFILE)) == 1

    async def test_stream_endpoint_delivers_published_frames(self, live_url):
        """End-to-end over a real socket: connect handshake, then a live frame."""
        conv = cs.create_conversation(profile=PROFILE, title="streamy", participants=[])
        lines: list[str] = []
        async with httpx.AsyncClient(base_url=live_url) as hc:
            async with hc.stream(
                "GET",
                f"/api/conversations/{conv.id}/stream",
                params={"profile": PROFILE},
            ) as resp:
                assert resp.status_code == 200
                frames = resp.aiter_lines()
                async for line in frames:
                    if line.startswith("data: "):
                        lines.append(line[6:])
                        break
                runtime.publish(conv.id, runtime.EV_THINKING, {"author": "sage"})
                async for line in frames:
                    if line.startswith("data: "):
                        lines.append(line[6:])
                        break

        assert json.loads(lines[0])["event"] == "connected"
        assert json.loads(lines[1])["event"] == "thinking"
        assert json.loads(lines[1])["conversation_id"] == conv.id

    async def test_unsubscribe_drops_the_queue(self, c):
        conv = await _create(c)
        q = runtime.subscribe(conv["id"])
        assert runtime.subscriber_count(conv["id"]) == 1
        runtime.unsubscribe(conv["id"], q)
        assert runtime.subscriber_count(conv["id"]) == 0
        # Publishing to a room with no subscribers is a no-op, not an error.
        runtime.publish(conv["id"], runtime.EV_THINKING, {"author": "sage"})

"""Promote a conversation message → memory / todo / task (PR3, spec §8).

The ``session`` target (PR4) lives in test_conversation_session.py.

Every downstream is mocked: the two vault targets through an httpx
``MockTransport`` (so the real request the brain daemon would receive is
asserted — URL, bearer, payload), and the task target by standing in for the
hub's ``submit_task``. Nothing here talks to a network or a container.
"""

from __future__ import annotations

import json

import httpx
import pytest

from brainbox import agent_store
from brainbox import conversation_promote as promote
from brainbox import conversation_runtime as runtime
from brainbox import conversation_store as cs
from brainbox.models_api import PromoteMessageRequest


PROFILE = "personal"
BRAIN_ENV = {
    "CL_BRAIN_API": "http://brain.test:9998",
    "CL_BRAIN_API_TOKEN": "memory-token",
    "CL_TODO_API_TOKEN": "todo-token",
}


@pytest.fixture
def brain_env(monkeypatch):
    """Stand in for the encrypted per-profile env store."""
    seen: dict[str, str] = {}

    def _env(profile: str) -> dict[str, str]:
        seen["profile"] = profile
        return dict(BRAIN_ENV)

    monkeypatch.setattr(promote, "_profile_env", _env)
    return seen


@pytest.fixture
def learn_calls(monkeypatch):
    """Capture what ``brain_learn`` was asked to write, without HTTP."""
    calls: list[dict] = []

    async def _learn(*, api, token, title, body, tags=None, transport=None):
        calls.append(
            {"api": api, "token": token, "title": title, "body": body, "tags": tags}
        )
        return "sha-" + token

    monkeypatch.setattr(promote, "brain_learn", _learn)
    return calls


def _room(title="planning", profile=PROFILE):
    return cs.create_conversation(profile=profile, title=title)


def _msg(conv, author="sage", content="We should ship the ratchet on Friday."):
    return cs.add_message(
        conversation_id=conv.id, profile=conv.profile, author=author, content=content
    )


# ---------------------------------------------------------------------------
# Content shaping
# ---------------------------------------------------------------------------


class TestPromotedContent:
    def test_title_is_the_first_line_by_default(self):
        conv = cs.Conversation(profile=PROFILE, title="planning")
        msg = cs.Message(
            conversation_id=conv.id, profile=PROFILE, author="sage",
            content="Ship on Friday.\nAnd tell the team.",
        )
        assert promote.build_title(conv, msg) == "Ship on Friday."

    def test_title_override_wins(self):
        conv = cs.Conversation(profile=PROFILE, title="planning")
        msg = cs.Message(conversation_id=conv.id, profile=PROFILE, author="sage", content="x")
        assert promote.build_title(conv, msg, "  Release plan  ") == "Release plan"

    def test_empty_message_falls_back_to_room_and_author(self):
        conv = cs.Conversation(profile=PROFILE, title="planning")
        msg = cs.Message(conversation_id=conv.id, profile=PROFILE, author="sage", content="")
        assert promote.build_title(conv, msg) == "planning — sage"

    def test_body_carries_provenance(self):
        conv = cs.Conversation(profile=PROFILE, title="planning")
        msg = cs.Message(
            conversation_id=conv.id, profile=PROFILE, author="sage", content="Ship it."
        )
        body = promote.build_body(conv, msg, note="agreed in standup")
        assert body.startswith("Ship it.")
        assert "agreed in standup" in body
        assert conv.id in body and msg.id in body and "sage" in body


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


class TestBrainCredentials:
    def test_resolved_per_profile_and_per_vault(self, brain_env):
        assert promote.brain_credentials(PROFILE, "memory") == (
            "http://brain.test:9998",
            "memory-token",
        )
        assert promote.brain_credentials(PROFILE, "todo")[1] == "todo-token"
        assert brain_env["profile"] == PROFILE

    def test_trailing_slash_is_normalized(self, monkeypatch):
        monkeypatch.setattr(
            promote, "_profile_env",
            lambda p: {"CL_BRAIN_API": "http://brain.test:9998/", "CL_BRAIN_API_TOKEN": "t"},
        )
        assert promote.brain_credentials(PROFILE, "memory")[0] == "http://brain.test:9998"

    def test_missing_api_is_a_promote_error(self, monkeypatch):
        monkeypatch.setattr(promote, "_profile_env", lambda p: {"CL_BRAIN_API_TOKEN": "t"})
        with pytest.raises(promote.PromoteError, match="CL_BRAIN_API"):
            promote.brain_credentials(PROFILE, "memory")

    def test_missing_vault_token_never_falls_back_to_another_vault(self, monkeypatch):
        """A todo written with the memory token would silently land in the wrong
        vault. Failing is the correct behaviour."""
        monkeypatch.setattr(
            promote, "_profile_env",
            lambda p: {"CL_BRAIN_API": "http://brain.test:9998", "CL_BRAIN_API_TOKEN": "m"},
        )
        with pytest.raises(promote.PromoteError, match="CL_TODO_API_TOKEN"):
            promote.brain_credentials(PROFILE, "todo")


# ---------------------------------------------------------------------------
# The learn call itself (mocked transport — asserts the real wire request)
# ---------------------------------------------------------------------------


class TestBrainLearnWire:
    async def test_posts_to_the_learn_endpoint_with_the_bearer(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("authorization")
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"sha": "canonical-sha"})

        sha = await promote.brain_learn(
            api="http://brain.test:9998",
            token="tok",
            title="Ship it",
            body="Ship it on Friday",
            tags=["conversation"],
            transport=httpx.MockTransport(handler),
        )

        assert sha == "canonical-sha"
        assert seen["url"] == "http://brain.test:9998/api/brain/learn"
        assert seen["auth"] == "Bearer tok"
        assert seen["body"]["title"] == "Ship it"
        assert seen["body"]["tags"] == ["conversation"]
        # A well-formed content hash is sent; the daemon re-derives canonically.
        assert len(seen["body"]["sha"]) == 64

    async def test_client_sha_is_used_when_the_daemon_returns_none(self):
        sha = await promote.brain_learn(
            api="http://brain.test:9998", token="tok", title="t", body="b",
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
        )
        assert len(sha) == 64

    async def test_401_is_a_promote_error(self):
        with pytest.raises(promote.PromoteError, match="401"):
            await promote.brain_learn(
                api="http://brain.test:9998", token="bad", title="t", body="b",
                transport=httpx.MockTransport(lambda r: httpx.Response(401)),
            )

    async def test_5xx_is_a_promote_error(self):
        with pytest.raises(promote.PromoteError, match="HTTP 503"):
            await promote.brain_learn(
                api="http://brain.test:9998", token="t", title="t", body="b",
                transport=httpx.MockTransport(lambda r: httpx.Response(503)),
            )

    async def test_unreachable_daemon_is_a_promote_error(self):
        def boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route", request=request)

        with pytest.raises(promote.PromoteError, match="unreachable"):
            await promote.brain_learn(
                api="http://brain.test:9998", token="t", title="t", body="b",
                transport=httpx.MockTransport(boom),
            )


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


class TestPromoteDispatch:
    async def test_memory_writes_to_the_memory_vault(self, brain_env, learn_calls):
        conv = _room()
        msg = _msg(conv)
        result = await promote.promote_message(
            profile=PROFILE, conv=conv, msg=msg,
            body=PromoteMessageRequest(target="memory"),
        )
        assert result.ok and result.target == "memory"
        assert result.sha == "sha-memory-token"
        assert learn_calls[0]["token"] == "memory-token"
        assert "conversation" in learn_calls[0]["tags"]
        assert "memory" in learn_calls[0]["tags"]

    async def test_todo_writes_to_the_todo_vault(self, brain_env, learn_calls):
        conv = _room()
        msg = _msg(conv)
        result = await promote.promote_message(
            profile=PROFILE, conv=conv, msg=msg,
            body=PromoteMessageRequest(target="todo", tags=["urgent"]),
        )
        assert result.sha == "sha-todo-token"
        assert learn_calls[0]["token"] == "todo-token"
        assert learn_calls[0]["tags"][:1] == ["urgent"]

    async def test_task_submits_to_the_hub_in_the_callers_profile(self, monkeypatch):
        import brainbox.router as router

        submitted: dict = {}

        async def fake_submit(description, agent_name, **kwargs):
            submitted.update(
                {"description": description, "agent_name": agent_name, **kwargs}
            )

            class _T:
                id = "task-123"

            return _T()

        monkeypatch.setattr(router, "submit_task", fake_submit)
        conv = _room()
        msg = _msg(conv)
        result = await promote.promote_message(
            profile=PROFILE, conv=conv, msg=msg,
            body=PromoteMessageRequest(
                target="task", agent_name="worker", repo_url="https://example.test/r.git"
            ),
        )

        assert result.task_id == "task-123"
        assert submitted["agent_name"] == "worker"
        assert submitted["workspace_profile"] == PROFILE
        assert submitted["repo_url"] == "https://example.test/r.git"
        assert msg.content in submitted["description"]

    async def test_task_rejects_an_unknown_agent(self):
        conv = _room()
        msg = _msg(conv)
        with pytest.raises(promote.PromoteError):
            await promote.promote_message(
                profile=PROFILE, conv=conv, msg=msg,
                body=PromoteMessageRequest(target="task", agent_name="nope"),
            )


# ---------------------------------------------------------------------------
# The route
# ---------------------------------------------------------------------------


class TestPromoteRoute:
    @pytest.fixture
    async def c(self, client):
        async with client as opened:
            yield opened

    async def _room_with_message(self, c, profile=PROFILE):
        conv = (
            await c.post(
                "/api/conversations",
                json={"title": "planning", "profile": profile, "participants": [
                    {"name": "user", "kind": "human"}
                ]},
            )
        ).json()
        msg = cs.add_message(
            conversation_id=conv["id"], profile=profile, author="user", content="ship it"
        )
        return conv, msg

    async def test_promote_to_memory_over_http(self, c, brain_env, learn_calls):
        conv, msg = await self._room_with_message(c)
        resp = await c.post(
            f"/api/conversations/{conv['id']}/messages/{msg.id}/promote",
            params={"profile": PROFILE},
            json={"target": "memory", "note": "keep this"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True and body["target"] == "memory"
        assert body["sha"] == "sha-memory-token"
        assert "keep this" in learn_calls[0]["body"]

    async def test_promote_to_todo_over_http(self, c, brain_env, learn_calls):
        conv, msg = await self._room_with_message(c)
        resp = await c.post(
            f"/api/conversations/{conv['id']}/messages/{msg.id}/promote",
            params={"profile": PROFILE},
            json={"target": "todo"},
        )
        assert resp.json()["sha"] == "sha-todo-token"

    async def test_promotion_lands_on_the_conversation_timeline_entity(
        self, c, brain_env, learn_calls
    ):
        conv, msg = await self._room_with_message(c)
        await c.post(
            f"/api/conversations/{conv['id']}/messages/{msg.id}/promote",
            params={"profile": PROFILE},
            json={"target": "memory"},
        )
        state = agent_store.get_state(f"conversation:{conv['id']}")
        assert state["type"] == runtime.BUS_PROMOTED
        assert state["metadata"]["promote_target"] == "memory"
        assert state["metadata"]["message_id"] == msg.id

    async def test_unconfigured_vault_is_a_400_not_a_500(self, c, monkeypatch):
        monkeypatch.setattr(promote, "_profile_env", lambda p: {})
        conv, msg = await self._room_with_message(c)
        resp = await c.post(
            f"/api/conversations/{conv['id']}/messages/{msg.id}/promote",
            params={"profile": PROFILE},
            json={"target": "memory"},
        )
        assert resp.status_code == 400
        assert "CL_BRAIN_API" in resp.json()["detail"]

    async def test_unknown_message_is_404(self, c, brain_env):
        conv, _msg = await self._room_with_message(c)
        resp = await c.post(
            f"/api/conversations/{conv['id']}/messages/01JNOPEXXXXXXXXXXXXXXXXXXX/promote",
            params={"profile": PROFILE},
            json={"target": "memory"},
        )
        assert resp.status_code == 404

    async def test_cross_profile_promote_is_404_and_writes_nothing(
        self, c, brain_env, learn_calls
    ):
        conv, msg = await self._room_with_message(c, profile="personal")
        resp = await c.post(
            f"/api/conversations/{conv['id']}/messages/{msg.id}/promote",
            params={"profile": "work"},
            json={"target": "memory"},
        )
        assert resp.status_code == 404
        assert learn_calls == []

    async def test_a_message_from_another_room_is_404(self, c, brain_env, learn_calls):
        """The message id must belong to the conversation in the path — the
        store filters on both, so a valid id from another room is a miss."""
        conv_a, msg_a = await self._room_with_message(c)
        conv_b, _ = await self._room_with_message(c)
        resp = await c.post(
            f"/api/conversations/{conv_b['id']}/messages/{msg_a.id}/promote",
            params={"profile": PROFILE},
            json={"target": "memory"},
        )
        assert resp.status_code == 404
        assert learn_calls == []

    async def test_unknown_target_is_rejected_at_the_boundary(self, c, brain_env):
        conv, msg = await self._room_with_message(c)
        resp = await c.post(
            f"/api/conversations/{conv['id']}/messages/{msg.id}/promote",
            params={"profile": PROFILE},
            json={"target": "slack"},  # not a promotion surface
        )
        assert resp.status_code == 422


class TestStoreGetMessage:
    def test_profile_is_in_the_sql(self):
        conv = cs.create_conversation(profile="personal", title="room")
        msg = cs.add_message(
            conversation_id=conv.id, profile="personal", author="user", content="hi"
        )
        assert cs.get_message(msg.id, conversation_id=conv.id, profile="personal").id == msg.id
        assert cs.get_message(msg.id, conversation_id=conv.id, profile="work") is None

    def test_tombstoned_message_is_gone(self):
        conv = cs.create_conversation(profile="personal", title="room")
        msg = cs.add_message(
            conversation_id=conv.id, profile="personal", author="user", content="hi"
        )
        cs.delete_conversation(conv.id, profile="personal")
        assert cs.get_message(msg.id, conversation_id=conv.id, profile="personal") is None

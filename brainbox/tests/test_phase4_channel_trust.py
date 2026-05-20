"""Tests for Phase 4: channel trust + session self-join.

Covers:
- join_channel() — idempotency, join message, non-member, completed channel
- POST /api/hub/channels/{id}/join — bearer-only, capability check, membership
"""

from __future__ import annotations

import time
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

import brainbox.auth as auth_module
import brainbox.channels as ch_module
import brainbox.registry as reg_module
from brainbox.channels import create_channel, get_messages, join_channel
from brainbox.models import AgentDefinition, ChannelParticipant, Token


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def api_key(monkeypatch):
    key = "test-api-key-xyz"
    monkeypatch.setattr(auth_module, "_api_key", key)
    return key


def _make_channel(participant_session: str = "sess-alice"):
    return create_channel("test-room", [
        ChannelParticipant(name="alice", type="session", session_name=participant_session),
    ])


def _issue_token(capabilities: list[str], session_name: str = "sess-bob") -> tuple[Token, str]:
    agent_name = f"agent-{uuid.uuid4().hex[:6]}"
    agent = AgentDefinition(name=agent_name, image="img", capabilities=capabilities)
    reg_module._agents[agent_name] = agent
    task_id = str(uuid.uuid4())
    now = int(time.time() * 1000)
    token = Token(
        token_id=str(uuid.uuid4()),
        agent_name=agent_name,
        task_id=task_id,
        capabilities=capabilities,
        issued=now,
        expiry=now + 3_600_000,
    )
    reg_module._tokens[token.token_id] = token
    return token, session_name


# ---------------------------------------------------------------------------
# join_channel() unit tests
# ---------------------------------------------------------------------------


class TestJoinChannel:
    def test_join_adds_participant(self):
        channel = _make_channel()
        updated = join_channel(channel.id, session_name="sess-bob")
        names = [p.name for p in updated.participants]
        assert "sess-bob" in names

    def test_join_uses_display_name_when_provided(self):
        channel = _make_channel()
        join_channel(channel.id, session_name="sess-bob", display_name="bob")
        channel = ch_module._channels[channel.id]
        names = [p.name for p in channel.participants]
        assert "bob" in names

    def test_join_posts_join_message(self):
        channel = _make_channel()
        join_channel(channel.id, session_name="sess-bob")
        msgs = get_messages(channel.id)
        join_msgs = [m for m in msgs if m.type == "join"]
        assert len(join_msgs) == 1
        assert "joined" in join_msgs[0].content

    def test_join_is_idempotent_by_session_name(self):
        channel = _make_channel()
        join_channel(channel.id, session_name="sess-bob")
        join_channel(channel.id, session_name="sess-bob")  # second call
        # Should still only be 2 participants (alice + bob)
        updated = ch_module._channels[channel.id]
        assert len(updated.participants) == 2
        # And only one join message
        msgs = get_messages(channel.id)
        assert len([m for m in msgs if m.type == "join"]) == 1

    def test_join_is_idempotent_by_display_name(self):
        channel = _make_channel()
        join_channel(channel.id, session_name="sess-bob", display_name="bob")
        join_channel(channel.id, session_name="sess-bob2", display_name="bob")  # same display name
        updated = ch_module._channels[channel.id]
        assert len(updated.participants) == 2  # still just alice + bob

    def test_join_nonexistent_channel_raises(self):
        with pytest.raises(ValueError, match="not found"):
            join_channel("no-such-channel", session_name="sess-x")

    def test_join_completed_channel_raises(self):
        from brainbox.channels import complete_channel
        channel = _make_channel()
        complete_channel(channel.id, by="alice")
        with pytest.raises(ValueError, match="already completed"):
            join_channel(channel.id, session_name="sess-bob")

    def test_join_emits_event(self):
        events = []
        ch_module.on_event(lambda e, d: events.append(e))
        channel = _make_channel()
        join_channel(channel.id, session_name="sess-bob")
        assert "channel.participant_joined" in events

    def test_join_participant_has_correct_type_and_session(self):
        channel = _make_channel()
        join_channel(channel.id, session_name="sess-bob", display_name="bob")
        updated = ch_module._channels[channel.id]
        bob = next(p for p in updated.participants if p.name == "bob")
        assert bob.type == "session"
        assert bob.session_name == "sess-bob"


# ---------------------------------------------------------------------------
# POST /api/hub/channels/{id}/join API tests
# ---------------------------------------------------------------------------


@pytest.fixture()
async def client(api_key):
    from brainbox.api import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c.headers["x-api-key"] = api_key
        yield c


class TestJoinChannelEndpoint:
    async def test_bearer_without_capability_gets_403(self, client):
        channel = _make_channel()
        token, _ = _issue_token(capabilities=["message_agents"])  # no hub_messaging
        resp = await client.post(
            f"/api/hub/channels/{channel.id}/join",
            headers={"authorization": f"Bearer {token.token_id}", "x-api-key": ""},
        )
        assert resp.status_code == 403

    async def test_api_key_gets_401_no_bearer(self, client):
        """Join endpoint requires a bearer token — API key alone isn't enough."""
        channel = _make_channel()
        resp = await client.post(f"/api/hub/channels/{channel.id}/join")
        assert resp.status_code == 401

    async def test_nonexistent_channel_gets_404(self, client):
        token, _ = _issue_token(capabilities=["hub_messaging"])
        resp = await client.post(
            "/api/hub/channels/no-such-channel/join",
            headers={"authorization": f"Bearer {token.token_id}", "x-api-key": ""},
        )
        assert resp.status_code == 404

    async def test_bearer_with_capability_joins_channel(self, client):
        channel = _make_channel()
        token, session_name = _issue_token(capabilities=["hub_messaging"])

        # Wire up a task so the endpoint can resolve session_name
        from brainbox.models import Task, TaskStatus
        from brainbox.router import _tasks
        now = int(time.time() * 1000)
        task = Task(
            id=token.task_id,
            description="test",
            agent_name=token.agent_name,
            status=TaskStatus.RUNNING,
            created_at=now,
            updated_at=now,
            session_name=session_name,
        )
        _tasks[token.task_id] = task

        resp = await client.post(
            f"/api/hub/channels/{channel.id}/join",
            headers={"authorization": f"Bearer {token.token_id}", "x-api-key": ""},
        )
        assert resp.status_code == 200
        body = resp.json()
        participant_names = [p["name"] for p in body["participants"]]
        assert session_name in participant_names

    async def test_join_is_idempotent_via_api(self, client):
        """Calling join twice returns 200 both times."""
        channel = _make_channel()
        token, session_name = _issue_token(capabilities=["hub_messaging"])

        from brainbox.models import Task, TaskStatus
        from brainbox.router import _tasks
        now = int(time.time() * 1000)
        task = Task(
            id=token.task_id, description="test", agent_name=token.agent_name,
            status=TaskStatus.RUNNING, created_at=now, updated_at=now,
            session_name=session_name,
        )
        _tasks[token.task_id] = task

        headers = {"authorization": f"Bearer {token.token_id}", "x-api-key": ""}
        r1 = await client.post(f"/api/hub/channels/{channel.id}/join", headers=headers)
        r2 = await client.post(f"/api/hub/channels/{channel.id}/join", headers=headers)
        assert r1.status_code == 200
        assert r2.status_code == 200

        # Still only 2 participants
        body = r2.json()
        assert len(body["participants"]) == 2

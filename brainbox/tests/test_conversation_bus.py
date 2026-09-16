"""Conversation → agent event bus (PR3, design spec §8).

A finalized conversation message becomes an envelope keyed
``conversation:<conversation_id>`` on the SAME bus every other producer uses
(``agent_store.ingest`` → ``agent_state`` + ``agent_events``). Two properties
matter and are asserted here:

- **the key** — one ``agent_state`` row per room, upserted, so the conversation
  reads as a single timeline entity rather than N cards;
- **the history** — one ``agent_events`` row per event, so the room's turns are
  still individually auditable.
"""

from __future__ import annotations

import pytest

from brainbox import agent_store
from brainbox import conversation_runtime as runtime
from brainbox import conversation_store as cs


PROFILE = "personal"


def _room(title="planning", profile=PROFILE, status="active"):
    return cs.Conversation(profile=profile, title=title, status=status)


def _msg(conv, author="sage", content="ship it", **kw):
    return cs.Message(conversation_id=conv.id, profile=conv.profile, author=author,
                      content=content, **kw)


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------


class TestEnvelopeFromConversation:
    def test_id_is_keyed_on_the_conversation(self):
        conv = _room()
        env = agent_store.envelope_from_conversation(runtime.BUS_MESSAGE, conv, _msg(conv))
        assert env.id == f"conversation:{conv.id}"
        assert env.id == agent_store.conversation_envelope_id(conv.id)

    def test_every_event_shares_one_id_so_the_room_is_one_entity(self):
        conv = _room()
        ids = {
            agent_store.envelope_from_conversation(ev, conv, _msg(conv)).id
            for ev in (runtime.BUS_CREATED, runtime.BUS_MESSAGE, runtime.BUS_PROMOTED)
        }
        assert len(ids) == 1

    def test_routing_and_display_fields(self):
        conv = _room(title="roadmap")
        conv.participants = [
            cs.Participant(name="user", kind="human"),
            cs.Participant(name="sage", kind="persona"),
        ]
        msg = _msg(conv, author="sage", content="Ship it.\nThen tell the team.")
        env = agent_store.envelope_from_conversation(runtime.BUS_MESSAGE, conv, msg)

        assert env.source == "brainbox-conversations"
        assert env.type == runtime.BUS_MESSAGE
        assert env.title == "roadmap"
        # workspace is the routing/tenancy key — it must be the room's profile.
        assert env.workspace == PROFILE
        assert env.status.value == "active"
        assert env.tags == ["conversation"]
        assert env.subtitle == "sage"
        # Display detail is the first line only, never the whole turn.
        assert env.description == "Ship it."
        assert env.metadata["message_id"] == msg.id
        assert env.metadata["author"] == "sage"
        assert env.metadata["participants"] == 2
        assert env.metadata["personas"] == ["sage"]

    def test_archived_room_is_terminal(self):
        conv = _room(status="archived")
        env = agent_store.envelope_from_conversation(runtime.BUS_ARCHIVED, conv)
        assert env.status.value == "done"
        assert env.end_at == conv.updated_at
        # No message on a lifecycle-only event.
        assert "message_id" not in env.metadata

    def test_extra_metadata_is_merged(self):
        conv = _room()
        env = agent_store.envelope_from_conversation(
            runtime.BUS_PROMOTED, conv, _msg(conv), extra={"promote_target": "todo"}
        )
        assert env.metadata["promote_target"] == "todo"
        assert env.metadata["conversation_id"] == conv.id


# ---------------------------------------------------------------------------
# Emission
# ---------------------------------------------------------------------------


class TestEmitBusEnvelope:
    async def test_finalize_message_ingests_under_the_conversation_key(self):
        conv = cs.create_conversation(profile=PROFILE, title="planning")
        msg = cs.add_message(
            conversation_id=conv.id, profile=PROFILE, author="user", content="hello"
        )

        await runtime.finalize_message(msg, conv)

        state = agent_store.get_state(f"conversation:{conv.id}")
        assert state is not None
        assert state["type"] == runtime.BUS_MESSAGE
        assert state["workspace"] == PROFILE
        assert state["metadata"]["message_id"] == msg.id

    async def test_many_messages_upsert_one_state_row_and_append_history(self):
        conv = cs.create_conversation(profile=PROFILE, title="planning")
        for i in range(3):
            msg = cs.add_message(
                conversation_id=conv.id, profile=PROFILE, author="user", content=f"m{i}"
            )
            await runtime.finalize_message(msg, conv)

        rows = agent_store.list_state(source="brainbox-conversations")
        assert len(rows) == 1  # the room is ONE timeline entity
        events = agent_store.list_events(envelope_id=f"conversation:{conv.id}")
        assert len(events) == 3  # …with a full audit trail
        assert [e["envelope"]["metadata"]["author"] for e in events] == ["user"] * 3

    async def test_finalize_message_also_publishes_the_sse_done_frame(self):
        conv = cs.create_conversation(profile=PROFILE, title="planning")
        q = runtime.subscribe(conv.id)
        msg = cs.add_message(
            conversation_id=conv.id, profile=PROFILE, author="user", content="hello"
        )

        await runtime.finalize_message(msg, conv)

        frames = []
        while not q.empty():
            frames.append(q.get_nowait())
        assert [f.split('"event": "')[1].split('"')[0] for f in frames] == [
            runtime.EV_MESSAGE_CREATED,
            runtime.EV_MESSAGE_DONE,
        ]

    async def test_a_bus_failure_never_breaks_a_finalized_message(self, monkeypatch):
        """The store is the source of truth; a bus hiccup must not raise into
        the request (or the persona reply) that already persisted a message."""
        conv = cs.create_conversation(profile=PROFILE, title="planning")
        msg = cs.add_message(
            conversation_id=conv.id, profile=PROFILE, author="user", content="hello"
        )

        async def boom(_env):
            raise RuntimeError("bus down")

        monkeypatch.setattr(agent_store, "async_ingest", boom)
        await runtime.finalize_message(msg, conv)  # does not raise

        assert agent_store.get_state(f"conversation:{conv.id}") is None


class TestBusEnvelopesOverHttp:
    """The routes are the real producers — assert they emit, not just that the
    helper can."""

    @pytest.fixture
    async def c(self, client):
        async with client as opened:
            yield opened

    async def test_create_emits_a_conversation_envelope(self, c):
        resp = await c.post(
            "/api/conversations",
            json={"title": "planning", "profile": PROFILE, "participants": []},
        )
        conv_id = resp.json()["id"]
        state = agent_store.get_state(f"conversation:{conv_id}")
        assert state is not None
        assert state["type"] == runtime.BUS_CREATED
        assert state["status"] == "active"

    async def test_posting_a_message_emits_a_message_envelope(self, c):
        conv = (
            await c.post(
                "/api/conversations",
                json={
                    "title": "planning",
                    "profile": PROFILE,
                    "participants": [{"name": "user", "kind": "human"}],
                },
            )
        ).json()
        await c.post(
            f"/api/conversations/{conv['id']}/messages",
            params={"profile": PROFILE},
            json={"author": "user", "content": "hello"},
        )
        state = agent_store.get_state(f"conversation:{conv['id']}")
        assert state["type"] == runtime.BUS_MESSAGE
        assert state["metadata"]["author"] == "user"

    async def test_archive_flips_the_entity_terminal(self, c):
        conv = (
            await c.post(
                "/api/conversations",
                json={"title": "planning", "profile": PROFILE, "participants": []},
            )
        ).json()
        await c.post(f"/api/conversations/{conv['id']}/archive", params={"profile": PROFILE})
        state = agent_store.get_state(f"conversation:{conv['id']}")
        assert state["type"] == runtime.BUS_ARCHIVED
        assert state["status"] == "done"

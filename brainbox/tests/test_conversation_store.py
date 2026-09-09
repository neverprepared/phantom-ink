"""Tests for the ConversationStore — the local-first records behind Chat PR1.

Covers CRUD, the local-first substrate (ULID ids, node_id stamp, tombstones)
and — the part that actually matters for isolation — profile scoping on every
read and write.
"""

from __future__ import annotations

import pytest

from brainbox import conversation_store as cs
from brainbox.node_identity import node_id


def _conv(profile="personal", title="room", participants=None):
    return cs.create_conversation(
        profile=profile,
        title=title,
        participants=participants
        if participants is not None
        else [{"name": "user", "kind": "human"}, {"name": "sage", "kind": "persona"}],
    )


class TestConversationCrud:
    def test_create_stamps_local_first_identity(self):
        conv = _conv()
        assert len(conv.id) == 26  # ULID
        assert conv.node_id == node_id()
        assert conv.status == "active"
        assert conv.deleted_at is None
        assert [p.name for p in conv.participants] == ["user", "sage"]

    def test_get_round_trips_participants(self):
        conv = _conv(
            participants=[
                {
                    "name": "sage",
                    "kind": "persona",
                    "model_target": {"provider": "ollama", "model": "qwen3:8b"},
                    "role_prompt": "Be terse.",
                }
            ]
        )
        loaded = cs.get_conversation(conv.id, profile="personal")
        assert loaded is not None
        assert loaded.participants[0].model_target == {"provider": "ollama", "model": "qwen3:8b"}
        assert loaded.participants[0].role_prompt == "Be terse."

    def test_list_orders_by_recent_activity(self):
        first = _conv(title="first")
        second = _conv(title="second")
        # Activity on `first` should float it above `second`.
        cs.add_message(
            conversation_id=first.id, profile="personal", author="user", content="hi"
        )
        ids = [c.id for c in cs.list_conversations(profile="personal")]
        assert ids[0] == first.id
        assert second.id in ids

    def test_archive_flips_status_and_can_be_filtered_out(self):
        conv = _conv()
        archived = cs.archive_conversation(conv.id, profile="personal")
        assert archived.status == "archived"
        assert cs.list_conversations(profile="personal", include_archived=False) == []
        assert len(cs.list_conversations(profile="personal", include_archived=True)) == 1

    def test_archive_missing_raises(self):
        with pytest.raises(cs.ProfileScopeError):
            cs.archive_conversation("01JNOTAREALULIDXXXXXXXXXXX", profile="personal")

    def test_delete_tombstones_conversation_and_messages(self):
        conv = _conv()
        cs.add_message(
            conversation_id=conv.id, profile="personal", author="user", content="hello"
        )
        cs.delete_conversation(conv.id, profile="personal")

        assert cs.get_conversation(conv.id, profile="personal") is None
        assert cs.list_conversations(profile="personal") == []
        # Rows survive as tombstones rather than being erased, so the delete
        # can win a P2P merge against a node that never saw it.
        with cs._conn() as c:
            row = c.execute(
                "SELECT deleted_at FROM conversations WHERE id = %s", (conv.id,)
            ).fetchone()
            msg = c.execute(
                "SELECT deleted_at FROM conversation_messages WHERE conversation_id = %s",
                (conv.id,),
            ).fetchone()
        assert row["deleted_at"] is not None
        assert msg["deleted_at"] is not None


class TestMessages:
    def test_add_and_list_in_ulid_order(self):
        conv = _conv()
        a = cs.add_message(
            conversation_id=conv.id, profile="personal", author="user", content="one"
        )
        b = cs.add_message(
            conversation_id=conv.id, profile="personal", author="sage", content="two"
        )
        msgs = cs.list_messages(conv.id, profile="personal")
        assert [m.id for m in msgs] == [a.id, b.id]
        assert a.id < b.id  # ULIDs sort by creation time
        assert msgs[0].node_id == node_id()

    def test_since_id_returns_only_newer(self):
        conv = _conv()
        a = cs.add_message(
            conversation_id=conv.id, profile="personal", author="user", content="one"
        )
        b = cs.add_message(
            conversation_id=conv.id, profile="personal", author="sage", content="two"
        )
        newer = cs.list_messages(conv.id, profile="personal", since_id=a.id)
        assert [m.id for m in newer] == [b.id]

    def test_reserved_message_id_is_honoured(self):
        """The streaming path emits message.created with the final id before the
        content exists, then persists under that same id."""
        conv = _conv()
        reserved = "01JRESERVEDIDAAAAAAAAAAAAA"
        msg = cs.add_message(
            conversation_id=conv.id,
            profile="personal",
            author="sage",
            content="streamed",
            message_id=reserved,
        )
        assert msg.id == reserved
        assert cs.list_messages(conv.id, profile="personal")[0].id == reserved

    def test_add_message_to_missing_conversation_raises(self):
        with pytest.raises(cs.ProfileScopeError):
            cs.add_message(
                conversation_id="01JNOPEXXXXXXXXXXXXXXXXXXX",
                profile="personal",
                author="user",
                content="hi",
            )

    def test_kinds_beyond_message_persist(self):
        conv = _conv()
        cs.add_message(
            conversation_id=conv.id,
            profile="personal",
            author="system",
            content="sage joined",
            kind="join",
        )
        assert cs.list_messages(conv.id, profile="personal")[0].kind == "join"


class TestProfileScoping:
    """The isolation guarantee: scoping lives in the store's SQL, so a caller
    in the wrong profile cannot read, write, archive or delete."""

    def test_get_is_scoped(self):
        conv = _conv(profile="personal")
        assert cs.get_conversation(conv.id, profile="personal") is not None
        assert cs.get_conversation(conv.id, profile="work") is None

    def test_list_is_scoped(self):
        _conv(profile="personal", title="mine")
        _conv(profile="work", title="theirs")
        assert [c.title for c in cs.list_conversations(profile="personal")] == ["mine"]
        assert [c.title for c in cs.list_conversations(profile="work")] == ["theirs"]

    def test_cross_profile_write_is_rejected(self):
        conv = _conv(profile="personal")
        with pytest.raises(cs.ProfileScopeError):
            cs.add_message(
                conversation_id=conv.id, profile="work", author="intruder", content="hi"
            )
        assert cs.list_messages(conv.id, profile="personal") == []

    def test_cross_profile_archive_and_delete_are_rejected(self):
        conv = _conv(profile="personal")
        with pytest.raises(cs.ProfileScopeError):
            cs.archive_conversation(conv.id, profile="work")
        with pytest.raises(cs.ProfileScopeError):
            cs.delete_conversation(conv.id, profile="work")
        assert cs.get_conversation(conv.id, profile="personal").status == "active"

    def test_messages_are_not_readable_cross_profile(self):
        conv = _conv(profile="personal")
        cs.add_message(
            conversation_id=conv.id, profile="personal", author="user", content="secret"
        )
        assert cs.list_messages(conv.id, profile="work") == []


class TestParticipantManagement:
    """Roster edits on a live room (PR2's persona-management path)."""

    def test_add_appends_a_persona(self):
        conv = _conv()
        updated = cs.add_participant(
            conv.id,
            profile="personal",
            participant={
                "name": "scribe",
                "kind": "persona",
                "role_prompt": "Take notes.",
                "cooldown_s": 5,
            },
        )
        assert [p.name for p in updated.participants] == ["user", "sage", "scribe"]
        assert updated.participants[-1].role_prompt == "Take notes."
        assert updated.participants[-1].cooldown_s == 5

    def test_adding_an_existing_name_updates_in_place(self):
        conv = _conv()
        updated = cs.add_participant(
            conv.id,
            profile="personal",
            participant={"name": "SAGE", "kind": "persona", "role_prompt": "Be blunt."},
        )
        # One entry, not two: the name is the addressing key for @mentions and
        # for the orchestrator's cooldown bookkeeping.
        assert len([p for p in updated.participants if p.name.casefold() == "sage"]) == 1
        assert updated.participants[-1].role_prompt == "Be blunt."

    def test_add_bumps_updated_at_and_persists(self):
        conv = _conv()
        cs.add_participant(
            conv.id, profile="personal", participant={"name": "scribe", "kind": "persona"}
        )
        reread = cs.get_conversation(conv.id, profile="personal")
        assert [p.name for p in reread.participants] == ["user", "sage", "scribe"]
        assert reread.updated_at >= conv.updated_at

    def test_remove_drops_the_participant_but_keeps_history(self):
        conv = _conv()
        cs.add_message(
            conversation_id=conv.id, profile="personal", author="sage", content="my turn"
        )
        updated = cs.remove_participant(conv.id, profile="personal", name="sage")
        assert [p.name for p in updated.participants] == ["user"]
        assert [m.author for m in cs.list_messages(conv.id, profile="personal")] == ["sage"]

    def test_remove_of_an_unknown_name_raises(self):
        conv = _conv()
        with pytest.raises(KeyError):
            cs.remove_participant(conv.id, profile="personal", name="ghost")

    def test_participant_writes_are_profile_scoped(self):
        conv = _conv(profile="personal")
        with pytest.raises(cs.ProfileScopeError):
            cs.add_participant(
                conv.id, profile="work", participant={"name": "intruder", "kind": "persona"}
            )
        with pytest.raises(cs.ProfileScopeError):
            cs.remove_participant(conv.id, profile="work", name="sage")
        assert [p.name for p in cs.get_conversation(conv.id, profile="personal").participants] == [
            "user",
            "sage",
        ]

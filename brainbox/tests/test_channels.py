"""Unit tests for the channels module."""

from __future__ import annotations

import pytest

from brainbox.channels import (
    complete_channel,
    create_channel,
    get_channel,
    get_messages,
    get_state,
    list_channels,
    post_message,
    restore_state,
)
from brainbox.models import Channel, ChannelMessage, ChannelParticipant


@pytest.fixture(autouse=True)
def reset_channels():
    """Reset channel state between tests."""
    import brainbox.channels as ch
    ch._channels.clear()
    ch._messages.clear()
    ch._listeners.clear()
    ch._ollama_last_read.clear()
    yield
    ch._channels.clear()
    ch._messages.clear()
    ch._listeners.clear()
    ch._ollama_last_read.clear()


def _make_participants():
    return [
        ChannelParticipant(name="alice", type="session", session_name="sess-1"),
        ChannelParticipant(name="bob", type="ollama", ollama_model="llama3"),
    ]


class TestCreateChannel:
    def test_creates_channel_with_id(self):
        channel = create_channel("debate", _make_participants())
        assert isinstance(channel, Channel)
        assert channel.name == "debate"
        assert channel.status == "active"
        assert len(channel.id) == 8

    def test_listed_after_creation(self):
        create_channel("chat", _make_participants())
        channels = list_channels()
        assert len(channels) == 1
        assert channels[0].name == "chat"

    def test_get_channel_by_id(self):
        channel = create_channel("test", _make_participants())
        fetched = get_channel(channel.id)
        assert fetched is not None
        assert fetched.id == channel.id

    def test_get_channel_missing_returns_none(self):
        assert get_channel("nonexistent") is None

    def test_messages_initialized_empty(self):
        channel = create_channel("empty", _make_participants())
        assert get_messages(channel.id) == []


class TestPostMessage:
    def test_post_message_returns_channel_message(self):
        channel = create_channel("chat", _make_participants())
        msg = post_message(channel.id, "alice", "Hello!")
        assert isinstance(msg, ChannelMessage)
        assert msg.content == "Hello!"
        assert msg.from_participant == "alice"
        assert msg.channel_id == channel.id

    def test_messages_accumulate(self):
        channel = create_channel("chat", _make_participants())
        post_message(channel.id, "alice", "msg1")
        post_message(channel.id, "bob", "msg2")
        msgs = get_messages(channel.id)
        assert len(msgs) == 2
        assert msgs[0].content == "msg1"
        assert msgs[1].content == "msg2"

    def test_post_with_summary_and_addressed_to(self):
        channel = create_channel("chat", _make_participants())
        msg = post_message(
            channel.id,
            "alice",
            "Long content here",
            summary="Brief summary",
            addressed_to="bob",
        )
        assert msg.summary == "Brief summary"
        assert msg.addressed_to == "bob"

    def test_post_to_missing_channel_raises(self):
        with pytest.raises(ValueError, match="not found"):
            post_message("noexist", "alice", "Hi")

    def test_post_to_completed_channel_raises(self):
        channel = create_channel("chat", _make_participants())
        complete_channel(channel.id, by="alice")
        with pytest.raises(ValueError, match="already completed"):
            post_message(channel.id, "alice", "Too late")


class TestGetMessagesSinceId:
    def test_since_id_returns_later_messages(self):
        channel = create_channel("chat", _make_participants())
        m1 = post_message(channel.id, "alice", "first")
        m2 = post_message(channel.id, "bob", "second")
        m3 = post_message(channel.id, "alice", "third")

        since = get_messages(channel.id, since_id=m1.id)
        assert len(since) == 2
        assert since[0].id == m2.id
        assert since[1].id == m3.id

    def test_since_last_returns_empty(self):
        channel = create_channel("chat", _make_participants())
        m1 = post_message(channel.id, "alice", "only")
        since = get_messages(channel.id, since_id=m1.id)
        assert since == []

    def test_since_unknown_id_returns_all(self):
        channel = create_channel("chat", _make_participants())
        post_message(channel.id, "alice", "hello")
        msgs = get_messages(channel.id, since_id="unknown-id")
        assert len(msgs) == 1

    def test_since_none_returns_all(self):
        channel = create_channel("chat", _make_participants())
        post_message(channel.id, "alice", "a")
        post_message(channel.id, "bob", "b")
        assert len(get_messages(channel.id)) == 2


class TestCompleteChannel:
    def test_complete_sets_status(self):
        channel = create_channel("chat", _make_participants())
        updated = complete_channel(channel.id, by="alice", reason="Done!")
        assert updated.status == "completed"
        assert updated.completed_by == "alice"
        assert updated.completed_at is not None

    def test_complete_posts_completion_message(self):
        channel = create_channel("chat", _make_participants())
        complete_channel(channel.id, by="alice", reason="Concluded")
        msgs = get_messages(channel.id)
        assert any(m.type == "completion" for m in msgs)

    def test_complete_missing_channel_raises(self):
        with pytest.raises(ValueError, match="not found"):
            complete_channel("noexist", by="alice")


class TestStateRoundTrip:
    def test_get_state_restore_state_roundtrip(self):
        channel = create_channel("persist", _make_participants())
        post_message(channel.id, "alice", "hello", summary="hi")
        post_message(channel.id, "bob", "world")

        state = get_state()

        # Reset and restore
        import brainbox.channels as ch
        ch._channels.clear()
        ch._messages.clear()

        restore_state(state)

        restored = get_channel(channel.id)
        assert restored is not None
        assert restored.name == "persist"
        assert restored.status == "active"

        msgs = get_messages(channel.id)
        assert len(msgs) == 2
        assert msgs[0].content == "hello"
        assert msgs[0].summary == "hi"
        assert msgs[1].content == "world"

    def test_restore_none_is_noop(self):
        restore_state(None)
        assert list_channels() == []

    def test_restore_empty_dict_is_noop(self):
        restore_state({})
        assert list_channels() == []

    def test_multiple_channels_persist(self):
        c1 = create_channel("channel-1", _make_participants())
        c2 = create_channel("channel-2", _make_participants())
        post_message(c1.id, "alice", "in c1")
        post_message(c2.id, "bob", "in c2")

        state = get_state()

        import brainbox.channels as ch
        ch._channels.clear()
        ch._messages.clear()

        restore_state(state)

        assert len(list_channels()) == 2
        assert len(get_messages(c1.id)) == 1
        assert len(get_messages(c2.id)) == 1


class TestEventListeners:
    def test_listener_called_on_message(self):
        events = []

        import brainbox.channels as ch
        ch.on_event(lambda e, d: events.append((e, d)))

        channel = create_channel("test", _make_participants())
        post_message(channel.id, "alice", "ping")

        assert any(e == "channel.message" for e, _ in events)

    def test_listener_called_on_create(self):
        events = []

        import brainbox.channels as ch
        ch.on_event(lambda e, d: events.append(e))

        create_channel("newchan", _make_participants())
        assert "channel.created" in events

    def test_listener_called_on_complete(self):
        events = []

        import brainbox.channels as ch
        ch.on_event(lambda e, d: events.append(e))

        channel = create_channel("done", _make_participants())
        complete_channel(channel.id, by="alice")
        assert "channel.completed" in events

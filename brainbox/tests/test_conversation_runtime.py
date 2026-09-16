"""Unit tests for the conversation runtime helpers (fanout, prompt, chunking)."""

from __future__ import annotations

from brainbox import conversation_runtime as runtime
from brainbox.conversation_store import Conversation, Message, Participant


def _conv(participants):
    return Conversation(profile="personal", title="room", participants=participants)


class TestBuildPrompt:
    def _history(self):
        return [
            Message(conversation_id="c", profile="personal", author="user", content="hi"),
            Message(conversation_id="c", profile="personal", author="sage", content="hello"),
            Message(
                conversation_id="c",
                profile="personal",
                author="system",
                content="scribe joined",
                kind="join",
            ),
        ]

    def test_roles_are_assigned_from_authorship(self):
        persona = Participant(name="sage", kind="persona", role_prompt="Be terse.")
        out = runtime.build_prompt(_conv([persona]), persona, self._history())
        assert out[0] == {"role": "system", "content": "Be terse."}
        assert out[1] == {"role": "user", "content": "[user]: hi"}
        assert out[2] == {"role": "assistant", "content": "hello"}

    def test_non_conversational_kinds_are_dropped(self):
        persona = Participant(name="sage", kind="persona")
        out = runtime.build_prompt(_conv([persona]), persona, self._history())
        assert all("scribe joined" not in m["content"] for m in out)

    def test_default_system_prompt_names_the_persona_and_room(self):
        persona = Participant(name="sage", kind="persona")
        out = runtime.build_prompt(_conv([persona]), persona, [])
        assert "sage" in out[0]["content"] and "room" in out[0]["content"]

    def test_history_is_capped(self):
        persona = Participant(name="sage", kind="persona")
        history = [
            Message(conversation_id="c", profile="personal", author="user", content=str(i))
            for i in range(runtime.HISTORY_LIMIT + 10)
        ]
        out = runtime.build_prompt(_conv([persona]), persona, history)
        assert len(out) == runtime.HISTORY_LIMIT + 1  # + the system turn


class TestChunking:
    def test_pieces_reassemble_exactly(self):
        text = "Hello there,   friend.\nNew line too."
        assert "".join(runtime._chunks(text)) == text

    def test_multiple_pieces_for_a_sentence(self):
        assert len(runtime._chunks("one two three")) == 3

    def test_empty_text_yields_nothing(self):
        assert runtime._chunks("") == []


class TestFanout:
    def test_publish_reaches_every_subscriber_of_that_room_only(self):
        a = runtime.subscribe("room-1")
        b = runtime.subscribe("room-1")
        other = runtime.subscribe("room-2")
        runtime.publish("room-1", runtime.EV_THINKING, {"author": "sage"})
        assert a.qsize() == 1 and b.qsize() == 1
        assert other.qsize() == 0

    def test_full_queue_drops_frames_instead_of_raising(self):
        q = runtime.subscribe("room-3")
        for _ in range(runtime.QUEUE_MAXSIZE + 5):
            runtime.publish("room-3", runtime.EV_THINKING, {"author": "sage"})
        assert q.qsize() == runtime.QUEUE_MAXSIZE

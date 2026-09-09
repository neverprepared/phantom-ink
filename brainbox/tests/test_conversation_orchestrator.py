"""Unit tests for the TurnOrchestrator (multi-agent Chat, PR2).

Everything here runs against fakes: the relevance gate is injected with
``set_gate`` and the reply driver with ``runtime.set_streamer``, so no model is
called and a round completes in microseconds. What is under test is the turn
POLICY — who is eligible, who passes the gate, how many get to speak, when the
room goes quiet — not the streaming/persist path (that is PR1's, covered in
test_conversations_api.py).

The store is real (Postgres, truncated per test) because cooldown and the
consecutive-agent-turn count are DERIVED from the persisted log; faking the
store would fake the very state being asserted.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from brainbox import conversation_orchestrator as orch
from brainbox import conversation_runtime as runtime
from brainbox import conversation_store as cs
from brainbox.config import settings

PROFILE = "personal"


@pytest.fixture(autouse=True)
def _reset_orchestrator():
    orch.reset_for_tests()
    yield
    orch.reset_for_tests()


@pytest.fixture(autouse=True)
def _tunables():
    """Pin the tunables so a config change cannot silently rewrite these tests."""
    conv_settings = settings.conversations
    before = (
        conv_settings.concurrency_cap,
        conv_settings.max_consecutive_agent_turns,
        conv_settings.default_cooldown_s,
    )
    conv_settings.concurrency_cap = 2
    conv_settings.max_consecutive_agent_turns = 4
    conv_settings.default_cooldown_s = 0.0  # cooldown off unless a test asks for it
    yield conv_settings
    (
        conv_settings.concurrency_cap,
        conv_settings.max_consecutive_agent_turns,
        conv_settings.default_cooldown_s,
    ) = before


def _persona(name: str, **kw) -> dict:
    return {"name": name, "kind": "persona", **kw}


def _room(*participants: dict, title: str = "room") -> cs.Conversation:
    return cs.create_conversation(
        profile=PROFILE,
        title=title,
        participants=[{"name": "user", "kind": "human"}, *participants],
    )


def _post(conv: cs.Conversation, author: str, content: str = "hi", **kw) -> cs.Message:
    return cs.add_message(
        conversation_id=conv.id, profile=PROFILE, author=author, content=content, **kw
    )


class FakeGate:
    """Gate stub. ``verdicts`` maps persona name → (respond, confidence)."""

    def __init__(self, verdicts: dict[str, tuple[bool, float]], default=(True, 0.5)):
        self.verdicts = verdicts
        self.default = default
        self.seen: list[str] = []

    async def __call__(self, conv, persona, history):
        self.seen.append(persona.name)
        respond, confidence = self.verdicts.get(persona.name, self.default)
        return orch.GateVerdict(respond, confidence)


def _fake_streamer(text="ok"):
    """Replace the reply driver so a 'reply' is one deterministic chunk."""

    async def streamer(messages, *, profile, persona):
        yield f"{text}:{persona.name}"

    return streamer


@pytest.fixture
def spoke():
    """Record who actually replied, in order."""
    said: list[str] = []

    async def streamer(messages, *, profile, persona):
        said.append(persona.name)
        yield f"reply from {persona.name}"

    runtime.set_streamer(streamer)
    yield said
    runtime.set_streamer(None)


def _quiet_frames(q) -> list[dict]:
    import json

    out = []
    while not q.empty():
        frame = json.loads(q.get_nowait())
        if frame["event"] == runtime.EV_QUIET:
            out.append(frame["data"])
    return out


# ---------------------------------------------------------------------------
# Relevance gate
# ---------------------------------------------------------------------------


class TestRelevanceGate:
    async def test_only_personas_that_pass_the_gate_reply(self, spoke):
        conv = _room(_persona("sage"), _persona("scribe"))
        orch.set_gate(FakeGate({"sage": (True, 0.9), "scribe": (False, 0.1)}))
        await orch.run_round(conv, _post(conv, "user"))
        assert spoke == ["sage"]

    async def test_no_one_passing_the_gate_goes_quiet(self, spoke):
        conv = _room(_persona("sage"))
        q = runtime.subscribe(conv.id)
        orch.set_gate(FakeGate({"sage": (False, 0.0)}))
        assert await orch.run_round(conv, _post(conv, "user")) == []
        assert spoke == []
        assert _quiet_frames(q) == [{"reason": orch.QUIET_NO_RESPONDER}]

    async def test_a_raising_gate_stays_silent_and_reports_an_outage(self, spoke):
        conv = _room(_persona("sage"))
        q = runtime.subscribe(conv.id)

        async def boom(conv_, persona, history):
            raise RuntimeError("model down")

        orch.set_gate(boom)
        assert await orch.run_round(conv, _post(conv, "user")) == []
        assert spoke == []
        import json

        events = [json.loads(q.get_nowait())["event"] for _ in range(q.qsize())]
        # An outage is an error, NOT a quiet room — the two must not look alike.
        assert runtime.EV_ERROR in events and runtime.EV_QUIET not in events

    async def test_gate_is_asked_once_per_eligible_persona(self, spoke):
        conv = _room(_persona("sage"), _persona("scribe"), _persona("echo"))
        gate = FakeGate({}, default=(False, 0.0))
        orch.set_gate(gate)
        await orch.run_round(conv, _post(conv, "user"))
        assert sorted(gate.seen) == ["echo", "sage", "scribe"]

    def test_verdict_parsing_fails_closed(self):
        assert orch.parse_gate_reply('{"respond": true, "confidence": 0.8}') == orch.GateVerdict(
            True, 0.8
        )
        assert orch.parse_gate_reply("sure, go ahead").respond is False
        assert orch.parse_gate_reply("").respond is False
        assert orch.parse_gate_reply("{oops").respond is False
        # Confidence is clamped, never trusted raw.
        assert orch.parse_gate_reply('{"respond": true, "confidence": 9}').confidence == 1.0
        assert orch.parse_gate_reply('{"respond": true, "confidence": "x"}').confidence == 0.0

    def test_gate_prompt_carries_the_persona_and_recent_history(self):
        conv = _room(_persona("sage", role_prompt="You are terse."))
        history = [
            cs.Message(conversation_id=conv.id, profile=PROFILE, author="user", content="hello")
        ]
        prompt = orch.build_gate_prompt(conv, conv.participants[1], history)
        assert "sage" in prompt[0]["content"]
        assert "You are terse." in prompt[1]["content"]
        assert "[user]: hello" in prompt[1]["content"]


# ---------------------------------------------------------------------------
# Concurrency cap
# ---------------------------------------------------------------------------


class TestConcurrencyCap:
    async def test_cap_limits_responders_to_the_most_confident(self, spoke, _tunables):
        _tunables.concurrency_cap = 2
        conv = _room(_persona("a"), _persona("b"), _persona("c"))
        orch.set_gate(FakeGate({"a": (True, 0.2), "b": (True, 0.9), "c": (True, 0.5)}))
        await orch.run_round(conv, _post(conv, "user"))
        assert spoke == ["b", "c"]

    async def test_cap_of_one_lets_exactly_one_speak(self, spoke, _tunables):
        _tunables.concurrency_cap = 1
        conv = _room(_persona("a"), _persona("b"))
        orch.set_gate(FakeGate({"a": (True, 0.4), "b": (True, 0.6)}))
        await orch.run_round(conv, _post(conv, "user"))
        assert spoke == ["b"]

    def test_ties_break_on_roster_order_not_completion_order(self):
        roster = [cs.Participant(name=n) for n in ("a", "b", "c")]
        ranked = [(p, orch.GateVerdict(True, 0.5)) for p in roster]
        assert [p.name for p in orch.select_responders(ranked, cap=2)] == ["a", "b"]

    def test_declined_verdicts_never_fill_the_cap(self):
        roster = [cs.Participant(name=n) for n in ("a", "b")]
        ranked = [
            (roster[0], orch.GateVerdict(False, 0.99)),
            (roster[1], orch.GateVerdict(True, 0.1)),
        ]
        assert [p.name for p in orch.select_responders(ranked, cap=2)] == ["b"]


# ---------------------------------------------------------------------------
# Cooldown
# ---------------------------------------------------------------------------


class TestCooldown:
    async def test_a_persona_that_just_spoke_is_ineligible(self, spoke):
        conv = _room(_persona("sage", cooldown_s=60), _persona("scribe", cooldown_s=0))
        _post(conv, "sage", "already said my piece")
        orch.set_gate(FakeGate({}, default=(True, 0.5)))
        await orch.run_round(conv, _post(conv, "user"))
        assert spoke == ["scribe"]

    def test_cooldown_expires(self):
        persona = cs.Participant(name="sage", cooldown_s=1)
        now = int(time.time() * 1000)
        old = cs.Message(
            conversation_id="c",
            profile=PROFILE,
            author="sage",
            content="x",
            created_at=now - 5_000,
        )
        recent = cs.Message(
            conversation_id="c", profile=PROFILE, author="sage", content="x", created_at=now - 200
        )
        assert orch.on_cooldown(persona, [old], now_ms=now) is False
        assert orch.on_cooldown(persona, [recent], now_ms=now) is True

    def test_zero_cooldown_never_blocks(self):
        persona = cs.Participant(name="sage", cooldown_s=0)
        now = int(time.time() * 1000)
        msg = cs.Message(
            conversation_id="c", profile=PROFILE, author="sage", content="x", created_at=now
        )
        assert orch.on_cooldown(persona, [msg], now_ms=now) is False

    def test_unset_cooldown_falls_back_to_the_configured_default(self, _tunables):
        _tunables.default_cooldown_s = 30.0
        persona = cs.Participant(name="sage")  # cooldown_s is None
        now = int(time.time() * 1000)
        msg = cs.Message(
            conversation_id="c", profile=PROFILE, author="sage", content="x", created_at=now - 1_000
        )
        assert orch.on_cooldown(persona, [msg], now_ms=now) is True

    async def test_everyone_on_cooldown_means_a_quiet_room(self, spoke):
        conv = _room(_persona("sage", cooldown_s=60))
        q = runtime.subscribe(conv.id)
        _post(conv, "sage", "spoke just now")
        orch.set_gate(FakeGate({}, default=(True, 1.0)))
        assert await orch.run_round(conv, _post(conv, "user")) == []
        assert spoke == []
        assert _quiet_frames(q) == [{"reason": orch.QUIET_NO_RESPONDER}]


# ---------------------------------------------------------------------------
# Quiet-detector
# ---------------------------------------------------------------------------


class TestQuietDetector:
    def test_streak_counts_trailing_persona_messages_only(self):
        conv = _room(_persona("sage"), _persona("scribe"))
        history = [
            cs.Message(conversation_id=conv.id, profile=PROFILE, author="user", content="a"),
            cs.Message(conversation_id=conv.id, profile=PROFILE, author="sage", content="b"),
            cs.Message(conversation_id=conv.id, profile=PROFILE, author="scribe", content="c"),
        ]
        assert orch.consecutive_agent_turns(conv, history) == 2

    def test_a_human_message_resets_the_streak(self):
        conv = _room(_persona("sage"))
        history = [
            cs.Message(conversation_id=conv.id, profile=PROFILE, author="sage", content="a"),
            cs.Message(conversation_id=conv.id, profile=PROFILE, author="user", content="b"),
        ]
        assert orch.consecutive_agent_turns(conv, history) == 0

    async def test_round_refuses_once_the_limit_is_reached(self, spoke, _tunables):
        _tunables.max_consecutive_agent_turns = 2
        conv = _room(_persona("sage", cooldown_s=0), _persona("scribe", cooldown_s=0))
        q = runtime.subscribe(conv.id)
        _post(conv, "user", "kick off")
        _post(conv, "sage", "one")
        trigger = _post(conv, "scribe", "two")
        orch.set_gate(FakeGate({}, default=(True, 1.0)))
        assert await orch.run_round(conv, trigger) == []
        assert spoke == []
        assert _quiet_frames(q) == [
            {"reason": orch.QUIET_AGENT_TURN_LIMIT, "consecutive_agent_turns": 2}
        ]

    async def test_chaining_stops_at_k_consecutive_agent_turns(self, spoke, _tunables):
        """The runaway-loop guard, end to end.

        Cooldowns are off and every gate says yes, so nothing but the
        quiet-detector can stop these two personas talking to each other.
        """
        _tunables.max_consecutive_agent_turns = 3
        _tunables.concurrency_cap = 1
        conv = _room(_persona("sage", cooldown_s=0), _persona("scribe", cooldown_s=0))
        orch.set_gate(FakeGate({"sage": (True, 0.9), "scribe": (True, 0.8)}))

        posted = await orch.on_message(conv, _post(conv, "user", "go"))

        assert len(posted) == 3
        assert len(spoke) == 3
        # And the log agrees: one human turn, then exactly K agent turns.
        authors = [m.author for m in cs.list_messages(conv.id, profile=PROFILE)]
        assert authors[0] == "user"
        assert len(authors) == 4

    async def test_zero_limit_disables_the_detector(self, spoke, _tunables):
        _tunables.max_consecutive_agent_turns = 0
        conv = _room(_persona("sage", cooldown_s=0))
        _post(conv, "user", "kick off")
        trigger = _post(conv, "sage", "one")
        orch.set_gate(FakeGate({}, default=(True, 1.0)))
        # sage authored the trigger, so it is ineligible; a second persona is
        # what proves the detector itself is out of the way.
        assert await orch.run_round(conv, trigger) == []
        conv2 = _room(_persona("a", cooldown_s=0), _persona("b", cooldown_s=0))
        _post(conv2, "a", "one")
        assert await orch.run_round(conv2, _post(conv2, "b", "two")) != []


# ---------------------------------------------------------------------------
# @address
# ---------------------------------------------------------------------------


class TestAddress:
    async def test_addressed_persona_bypasses_the_gate(self, spoke):
        conv = _room(_persona("sage"), _persona("scribe"))

        async def never(conv_, persona, history):
            raise AssertionError("the gate must not run for an addressed message")

        orch.set_gate(never)
        await orch.run_round(conv, _post(conv, "user", "you there?", addressed_to="sage"))
        assert spoke == ["sage"]

    async def test_non_addressed_personas_stay_silent(self, spoke):
        conv = _room(_persona("sage"), _persona("scribe"), _persona("echo"))
        orch.set_gate(FakeGate({}, default=(True, 1.0)))
        await orch.run_round(conv, _post(conv, "user", "hi", addressed_to="scribe"))
        assert spoke == ["scribe"]

    async def test_addressing_an_unknown_name_wakes_no_one(self, spoke):
        conv = _room(_persona("sage"))
        orch.set_gate(FakeGate({}, default=(True, 1.0)))
        assert await orch.run_round(conv, _post(conv, "user", "hi", addressed_to="ghost")) == []
        assert spoke == []

    async def test_address_is_matched_case_and_at_insensitively(self, spoke):
        conv = _room(_persona("Sage"))
        orch.set_gate(FakeGate({}, default=(False, 0.0)))
        await orch.run_round(conv, _post(conv, "user", "hi", addressed_to="@sage"))
        assert spoke == ["Sage"]

    async def test_a_human_address_lifts_the_cooldown(self, spoke):
        conv = _room(_persona("sage", cooldown_s=600))
        _post(conv, "sage", "just spoke")
        orch.set_gate(FakeGate({}, default=(False, 0.0)))
        await orch.run_round(conv, _post(conv, "user", "again please", addressed_to="sage"))
        assert spoke == ["sage"]

    async def test_an_agent_address_does_not_lift_the_cooldown(self, spoke):
        """Otherwise two personas could ping-pong past their own rate limits."""
        conv = _room(_persona("sage", cooldown_s=600), _persona("scribe", cooldown_s=0))
        _post(conv, "sage", "just spoke")
        orch.set_gate(FakeGate({}, default=(True, 1.0)))
        await orch.run_round(conv, _post(conv, "scribe", "sage?", addressed_to="sage"))
        assert spoke == []

    async def test_a_persona_never_answers_itself(self, spoke):
        conv = _room(_persona("sage", cooldown_s=0))
        orch.set_gate(FakeGate({}, default=(True, 1.0)))
        assert await orch.run_round(conv, _post(conv, "sage", "thinking out loud")) == []
        assert spoke == []


# ---------------------------------------------------------------------------
# Rounds and chaining
# ---------------------------------------------------------------------------


class TestRounds:
    async def test_replies_are_threaded_to_their_trigger(self, spoke):
        conv = _room(_persona("sage", cooldown_s=0))
        orch.set_gate(FakeGate({}, default=(True, 1.0)))
        trigger = _post(conv, "user", "hello")
        replies = await orch.run_round(conv, trigger)
        assert [r.in_reply_to for r in replies] == [trigger.id]
        assert replies[0].content == "reply from sage"

    async def test_an_archived_room_never_runs_a_round(self, spoke):
        conv = _room(_persona("sage", cooldown_s=0))
        orch.set_gate(FakeGate({}, default=(True, 1.0)))
        trigger = _post(conv, "user", "hello")
        cs.archive_conversation(conv.id, profile=PROFILE)
        conv.status = "archived"
        assert await orch.run_round(conv, trigger) == []
        assert spoke == []

    async def test_a_room_with_no_personas_goes_quiet(self, spoke):
        conv = cs.create_conversation(
            profile=PROFILE, title="empty", participants=[{"name": "user", "kind": "human"}]
        )
        assert await orch.on_message(conv, _post(conv, "user", "anyone?")) == []
        assert spoke == []

    async def test_cooldown_ends_the_chain_after_one_round(self, spoke, _tunables):
        _tunables.default_cooldown_s = 600.0
        conv = _room(_persona("sage"))
        orch.set_gate(FakeGate({}, default=(True, 1.0)))
        posted = await orch.on_message(conv, _post(conv, "user", "go"))
        assert [m.author for m in posted] == ["sage"]

    async def test_two_personas_can_reply_in_one_round(self, spoke, _tunables):
        _tunables.concurrency_cap = 2
        _tunables.default_cooldown_s = 600.0
        conv = _room(_persona("sage"), _persona("scribe"))
        orch.set_gate(FakeGate({"sage": (True, 0.9), "scribe": (True, 0.8)}))
        posted = await orch.run_round(conv, _post(conv, "user", "go"))
        assert sorted(m.author for m in posted) == ["sage", "scribe"]

    async def test_a_trigger_missing_from_the_log_is_still_honoured(self, spoke):
        """A synthetic trigger (not yet readable) must not be lost."""
        conv = _room(_persona("sage", cooldown_s=0))
        orch.set_gate(FakeGate({}, default=(True, 1.0)))
        ghost = cs.Message(
            conversation_id=conv.id, profile=PROFILE, author="user", content="unsaved"
        )
        assert await orch.run_round(conv, ghost) != []


class TestPersonaHelpers:
    def test_personas_excludes_humans_and_sessions(self):
        conv = _room(_persona("sage"), {"name": "box", "kind": "session"})
        assert [p.name for p in orch.personas(conv)] == ["sage"]

    def test_find_persona_ignores_case_and_at_prefix(self):
        conv = _room(_persona("Sage"))
        assert orch.find_persona(conv, "@SAGE").name == "Sage"
        assert orch.find_persona(conv, "user") is None  # a human is not a persona
        assert orch.find_persona(conv, None) is None


class TestDefaultGate:
    async def test_default_gate_calls_complete_and_parses_the_verdict(self, monkeypatch):
        seen: dict = {}

        async def fake_complete(messages, *, profile, policy=None, ctx=None, target=None):
            seen["policy"] = policy
            seen["caller"] = ctx.caller if ctx else None
            from brainbox.llm import Completion

            return Completion(
                text='{"respond": true, "confidence": 0.7}', backend="ollama", model="m"
            )

        monkeypatch.setattr("brainbox.llm.complete", fake_complete)
        conv = _room(_persona("sage"))
        verdict = await orch._default_gate(conv, conv.participants[1], [])
        assert verdict == orch.GateVerdict(True, 0.7)
        # The gate is deliberately the CHEAP call, not the persona's own target.
        assert seen["policy"].quality == "cheap"
        assert seen["caller"] == "conversations.gate"


class TestConcurrentGates:
    async def test_gates_run_concurrently_not_serially(self):
        conv = _room(*(_persona(n) for n in ("a", "b", "c", "d")))
        started = 0
        peak = 0

        async def slow(conv_, persona, history):
            nonlocal started, peak
            started += 1
            peak = max(peak, started)
            await asyncio.sleep(0.01)
            started -= 1
            return orch.GateVerdict(False, 0.0)

        orch.set_gate(slow)
        await orch.run_round(conv, _post(conv, "user"))
        assert peak == 4


class TestLiveRoster:
    async def test_a_chain_picks_up_a_roster_change_between_rounds(self, spoke, _tunables):
        """The snapshot the POST route hands us must not outlive the chain."""
        _tunables.concurrency_cap = 1
        conv = _room(_persona("sage", cooldown_s=0))
        orch.set_gate(FakeGate({}, default=(True, 1.0)))

        async def streamer(messages, *, profile, persona):
            if persona.name == "sage" and not spoke:
                # Mid-chain: a second persona joins the room.
                cs.add_participant(
                    conv.id,
                    profile=PROFILE,
                    participant={"name": "scribe", "kind": "persona", "cooldown_s": 0},
                )
            spoke.append(persona.name)
            yield f"reply from {persona.name}"

        runtime.set_streamer(streamer)
        await orch.on_message(conv, _post(conv, "user", "go"))
        assert "scribe" in spoke

    async def test_archiving_mid_chain_stops_the_room(self, spoke, _tunables):
        conv = _room(_persona("sage", cooldown_s=0))
        orch.set_gate(FakeGate({}, default=(True, 1.0)))

        async def streamer(messages, *, profile, persona):
            spoke.append(persona.name)
            cs.archive_conversation(conv.id, profile=PROFILE)
            yield "last words"

        runtime.set_streamer(streamer)
        await orch.on_message(conv, _post(conv, "user", "go"))
        assert spoke == ["sage"]


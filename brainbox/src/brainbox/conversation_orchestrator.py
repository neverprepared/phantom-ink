"""TurnOrchestrator — multi-persona, self-gated turn-taking (PR2).

Replaces PR1's ``maybe_reply`` (one persona, always answers) with the policy
from the design spec §5
(``docs/superpowers/specs/2026-09-09-multi-agent-chat-design.md``):

- **Event-driven** — a new message runs ONE evaluation round for its
  conversation. There is no poll and no background loop; the round is driven by
  the message that triggered it.
- **Eligibility** — a persona is eligible when it is not the trigger's author,
  is off cooldown, and is not excluded by an ``@address`` on the trigger.
- **Relevance gate** — each eligible persona runs a *cheap* ``should_respond``
  check through the ``complete()`` seam and returns a boolean + confidence.
  This is the self-gate; it is deliberately a different (cheaper) call than the
  reply.
- **Concurrency cap** — only the top *N* by confidence reply this round.
- **Cooldown** — a persona that replied is ineligible until its cooldown
  elapses.
- **Chaining** — each posted reply is a new message that triggers the next
  round, so personas build on each other.
- **Quiet-detector** — after *K* consecutive agent-only turns, or when no
  persona passes the gate, the room goes quiet and awaits the human. This is
  what stops runaway agent-to-agent loops.
- **``@address``** — an addressed persona bypasses the gate; everyone else is
  silent for that message.

**State is derived, not held.** Cooldown and the consecutive-agent-turn count
are computed from the persisted message log, not from an in-memory counter, so
a daemon restart, a second reader, or a replay all see the same turn state. The
only module-level mutable is the gate seam used by tests.

The streaming/emit/persist path is unchanged: this module decides *who* speaks
and calls ``conversation_runtime.stream_persona_reply`` to do the speaking, so
the SSE wire contract is PR1's, untouched.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .conversation_runtime import EV_ERROR, EV_QUIET, publish, stream_persona_reply
from .conversation_store import (
    Conversation,
    Message,
    Participant,
    async_get_conversation,
    async_list_messages,
)
from .log import get_logger

log = get_logger()

# How many prior messages the (cheap) relevance gate sees. Much smaller than
# the reply's HISTORY_LIMIT — the gate answers "is this my lane?", which the
# recent turns settle, and every extra token here is paid once per persona per
# round.
GATE_HISTORY_LIMIT = 8

# Reasons published on the `quiet` frame. The UI distinguishes "nobody had
# anything to add" from "the agents have been talking to each other too long".
QUIET_NO_RESPONDER = "no_persona_passed_gate"
QUIET_AGENT_TURN_LIMIT = "max_consecutive_agent_turns"

# Marks a verdict that came from a gate that RAISED, as opposed to one that
# decided to stay quiet. The two look the same to the round (nobody speaks) but
# mean opposite things to the user, so they are reported differently.
GATE_ERROR = "gate_error"


# ---------------------------------------------------------------------------
# Relevance gate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GateVerdict:
    """One persona's self-assessment for one round."""

    respond: bool
    confidence: float = 0.0
    reason: str = ""


GATE_SYSTEM = (
    "You decide whether one participant in a group chat should speak next. "
    "Answer with JSON only: {{\"respond\": true|false, \"confidence\": 0.0-1.0}}. "
    "Respond true only when '{name}' has something genuinely useful to add that "
    "nobody has said yet. Prefer silence over piling on."
)


def _render_history(history: list[Message]) -> str:
    lines = [
        f"[{m.author}]: {m.content}"
        for m in history[-GATE_HISTORY_LIMIT:]
        if m.kind in ("message", "session")
    ]
    return "\n".join(lines)


def build_gate_prompt(
    conv: Conversation, persona: Participant, history: list[Message]
) -> list[dict]:
    """The cheap ``should_respond`` prompt for one persona."""
    role = persona.role_prompt or f"a participant named '{persona.name}'"
    user = (
        f"Room: {conv.title}\n"
        f"Participant: {persona.name} — {role}\n\n"
        f"Recent messages:\n{_render_history(history)}\n\n"
        f"Should {persona.name} reply now?"
    )
    return [
        {"role": "system", "content": GATE_SYSTEM.format(name=persona.name)},
        {"role": "user", "content": user},
    ]


def parse_gate_reply(text: str) -> GateVerdict:
    """Read a verdict out of the gate model's text.

    Fails **closed**: anything unparseable is a "stay silent". A gate that
    cannot be understood must not become a licence to talk — silence is the
    recoverable failure in a chat room, a pile-on is not.
    """
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        return GateVerdict(False, 0.0, "unparseable")
    try:
        data = json.loads(match.group(0))
    except (ValueError, TypeError):
        return GateVerdict(False, 0.0, "unparseable")
    if not isinstance(data, dict):
        return GateVerdict(False, 0.0, "unparseable")
    respond = bool(data.get("respond"))
    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return GateVerdict(respond, max(0.0, min(1.0, confidence)), str(data.get("reason", "")))


async def _default_gate(
    conv: Conversation, persona: Participant, history: list[Message]
) -> GateVerdict:
    """Default gate: one small ``complete()`` call on the cheap chain.

    Deliberately ignores the persona's ``model_target`` — the gate is a yes/no
    question and runs once per persona per round, so it takes the cheap
    (local-first) chain even when the persona itself replies on an expensive
    one.
    """
    from .llm import CallCtx, CompletionPolicy, complete

    comp = await complete(
        build_gate_prompt(conv, persona, history),
        profile=conv.profile,
        policy=CompletionPolicy(quality="cheap"),
        ctx=CallCtx(caller="conversations.gate"),
    )
    return parse_gate_reply(comp.text)


Gate = Callable[..., Awaitable[GateVerdict]]
_gate: Gate = _default_gate


def set_gate(fn: Gate | None) -> None:
    """Swap the relevance gate (tests, and any future policy engine)."""
    global _gate
    _gate = fn or _default_gate


def reset_for_tests() -> None:
    set_gate(None)


# ---------------------------------------------------------------------------
# Turn state — all derived from the persisted log
# ---------------------------------------------------------------------------


def _now_ms() -> int:
    return int(time.time() * 1000)


def _norm(name: str | None) -> str:
    return (name or "").strip().lstrip("@").casefold()


def personas(conv: Conversation) -> list[Participant]:
    return [p for p in conv.participants if p.kind == "persona"]


def find_persona(conv: Conversation, name: str | None) -> Participant | None:
    target = _norm(name)
    if not target:
        return None
    return next((p for p in personas(conv) if _norm(p.name) == target), None)


def _cooldown_s(persona: Participant) -> float:
    from .config import settings

    if persona.cooldown_s is None:
        return float(settings.conversations.default_cooldown_s)
    return max(0.0, float(persona.cooldown_s))


def on_cooldown(persona: Participant, history: list[Message], *, now_ms: int | None = None) -> bool:
    """True while ``persona``'s last message is newer than its cooldown."""
    window = _cooldown_s(persona)
    if window <= 0:
        return False
    last = next(
        (m for m in reversed(history) if _norm(m.author) == _norm(persona.name)),
        None,
    )
    if last is None:
        return False
    return (now_ms or _now_ms()) - last.created_at < window * 1000


def consecutive_agent_turns(conv: Conversation, history: list[Message]) -> int:
    """How many messages at the tail of the log were authored by personas.

    Reset by any message from someone who is not a persona in this room — a
    human, or an external poster. This is the counter the quiet-detector trips
    on, and deriving it from the log means it cannot drift from what the room
    actually shows.
    """
    names = {_norm(p.name) for p in personas(conv)}
    count = 0
    for m in reversed(history):
        if m.kind not in ("message", "session"):
            continue
        if _norm(m.author) not in names:
            break
        count += 1
    return count


def eligible_personas(
    conv: Conversation,
    trigger: Message,
    history: list[Message],
    *,
    now_ms: int | None = None,
) -> list[Participant]:
    """Personas that may be considered this round (pre-gate).

    An ``@address`` narrows the field to exactly that persona, and — when the
    addressing message came from a human — also lifts its cooldown: an explicit
    human instruction outranks a rate limiter. An agent addressing another agent
    does NOT lift it, or two personas could ping-pong past their cooldowns.
    """
    now_ms = now_ms if now_ms is not None else _now_ms()
    roster = personas(conv)
    addressed = find_persona(conv, trigger.addressed_to)

    if trigger.addressed_to:
        if addressed is None or _norm(addressed.name) == _norm(trigger.author):
            return []
        from_agent = find_persona(conv, trigger.author) is not None
        if from_agent and on_cooldown(addressed, history, now_ms=now_ms):
            return []
        return [addressed]

    return [
        p
        for p in roster
        if _norm(p.name) != _norm(trigger.author)
        and not on_cooldown(p, history, now_ms=now_ms)
    ]


# ---------------------------------------------------------------------------
# Rounds
# ---------------------------------------------------------------------------


async def _gate_all(
    conv: Conversation, candidates: list[Participant], history: list[Message]
) -> list[tuple[Participant, GateVerdict]]:
    """Run every candidate's gate concurrently; a failed gate stays silent."""

    async def one(p: Participant) -> tuple[Participant, GateVerdict]:
        try:
            return p, await _gate(conv, p, history)
        except Exception as exc:  # a gate failure must not take down the round
            log.warning(
                "conversation.gate_failed",
                metadata={"conversation_id": conv.id, "persona": p.name, "reason": str(exc)},
            )
            return p, GateVerdict(False, 0.0, GATE_ERROR)

    return list(await asyncio.gather(*(one(p) for p in candidates)))


def select_responders(
    ranked: list[tuple[Participant, GateVerdict]], *, cap: int
) -> list[Participant]:
    """Top ``cap`` personas by confidence; roster order breaks ties.

    ``sorted`` is stable, so equal confidences keep the order the gates were
    launched in (roster order) — a tie is resolved deterministically instead of
    by whichever completion happened to land first.
    """
    passed = [(p, v) for p, v in ranked if v.respond]
    passed.sort(key=lambda pair: pair[1].confidence, reverse=True)
    return [p for p, _ in passed[: max(0, cap)]]


def _quiet(conv: Conversation, reason: str, **extra: Any) -> list[Message]:
    publish(conv.id, EV_QUIET, {"reason": reason, **extra})
    return []


async def run_round(conv: Conversation, trigger: Message) -> list[Message]:
    """One evaluation round: gate → cap → reply. Returns the posted messages."""
    from .config import settings

    if conv.status != "active":
        return []

    history = await async_list_messages(conv.id, profile=conv.profile)
    if not any(m.id == trigger.id for m in history):
        # The trigger is normally already persisted; tolerate a caller that
        # hands us a message the read missed (replica lag, or a synthetic one).
        history = [*history, trigger]

    limit = int(settings.conversations.max_consecutive_agent_turns)
    streak = consecutive_agent_turns(conv, history)
    if limit > 0 and streak >= limit:
        log.info(
            "conversation.quiet",
            metadata={"conversation_id": conv.id, "reason": QUIET_AGENT_TURN_LIMIT},
        )
        return _quiet(conv, QUIET_AGENT_TURN_LIMIT, consecutive_agent_turns=streak)

    candidates = eligible_personas(conv, trigger, history)
    if not candidates:
        return _quiet(conv, QUIET_NO_RESPONDER)

    if trigger.addressed_to:
        # An addressed persona always answers — no gate.
        chosen = candidates
    else:
        verdicts = await _gate_all(conv, candidates, history)
        if all(v.reason == GATE_ERROR for _, v in verdicts):
            # Every gate raised — that is an outage, not a room with nothing to
            # say. Surface it instead of letting the UI read it as "quiet".
            publish(
                conv.id,
                EV_ERROR,
                {"author": "system", "reason": "relevance gate unavailable"},
            )
            return []
        chosen = select_responders(
            verdicts, cap=int(settings.conversations.concurrency_cap)
        )
    if not chosen:
        return _quiet(conv, QUIET_NO_RESPONDER)

    replies = await asyncio.gather(
        *(
            stream_persona_reply(
                conv.id,
                profile=conv.profile,
                persona=p,
                in_reply_to=trigger.id,
            )
            for p in chosen
        )
    )
    return [m for m in replies if m is not None]


async def on_message(conv: Conversation, trigger: Message) -> list[Message]:
    """Entry point from the message POST path: run rounds until the room quiets.

    Each round's last reply chains into the next round, so personas build on
    each other; the loop ends when a round posts nothing (nobody passed the
    gate, everyone is on cooldown, or the quiet-detector tripped). The round
    budget is a belt-and-braces stop — the quiet-detector is the real bound, but
    a misbehaving gate must never spin this coroutine forever.
    """
    from .config import settings

    posted: list[Message] = []
    current = trigger
    room = conv
    budget = max(1, int(settings.conversations.max_consecutive_agent_turns) + 1)
    for _ in range(budget):
        replies = await run_round(room, current)
        if not replies:
            break
        posted.extend(replies)
        current = max(replies, key=lambda m: m.id)
        # Re-read between rounds: a chain can outlive the snapshot the caller
        # handed us, and a persona added — or the room archived — mid-chain must
        # take effect on the next round, not the next message.
        room = await async_get_conversation(conv.id, profile=conv.profile)
        if room is None:
            break
    return posted

"""Conversation runtime — per-conversation SSE fanout and the persona reply path.

Two responsibilities, both deliberately small:

1. **Fanout** — a per-conversation pub/sub of SSE frames. Subscribers (the
   ``/api/conversations/{id}/stream`` endpoint) get a bounded queue; publishers
   (the message POST path and the persona runner) push typed frames onto every
   subscriber of that conversation.

2. **Persona reply** — turn a conversation's history plus one persona into a
   streamed reply through the ``complete()`` seam
   (``brainbox.llm``), emitting deltas as they arrive and persisting the
   finalized message at the end.

The wire contract, in the order a client sees it for one reply::

    thinking         {"author": "<persona>"}
    message.created  {"message": {...}}   # content is "" — the shell
    message.delta    {"id": ..., "delta": "..."}   # zero or more
    message.done     {"message": {...}}   # the persisted record

``message.created`` carries the final ULID, so a client can allocate its bubble
once and append deltas into it — no placeholder reconciliation. A human message
takes the same path with no deltas between created and done.

Who speaks, and when, is NOT decided here — that is
``conversation_orchestrator`` (relevance gate, cooldown, concurrency cap,
quiet-detector, ``@address``; design spec §5). This module is the mechanism the
orchestrator drives: hand it a persona and it produces one streamed, persisted
reply. ``_stream_completion`` stays the swap point for a natively streaming
completion seam.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, AsyncIterator, Callable

from .conversation_store import (
    Conversation,
    Message,
    Participant,
    async_add_message,
    async_get_conversation,
    async_list_messages,
)
from .log import get_logger

log = get_logger()

# Event names — the SSE contract. Never inline these strings at a call site.
EV_MESSAGE_CREATED = "message.created"
EV_MESSAGE_DELTA = "message.delta"
EV_MESSAGE_DONE = "message.done"
EV_THINKING = "thinking"
EV_ERROR = "error"
# The room has nothing more to add and is waiting for a human (design spec §5
# quiet-detector). Emitted by the orchestrator; defined here because this module
# owns the wire contract.
EV_QUIET = "quiet"

# How many frames a slow subscriber may fall behind before frames are dropped
# for it. Bounded so one stalled client cannot grow memory without limit.
QUEUE_MAXSIZE = 256


# ---------------------------------------------------------------------------
# Per-conversation fanout
# ---------------------------------------------------------------------------

_queues: dict[str, set[asyncio.Queue]] = {}


def subscribe(conversation_id: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
    _queues.setdefault(conversation_id, set()).add(q)
    return q


def unsubscribe(conversation_id: str, q: asyncio.Queue) -> None:
    subs = _queues.get(conversation_id)
    if not subs:
        return
    subs.discard(q)
    if not subs:
        _queues.pop(conversation_id, None)


def subscriber_count(conversation_id: str) -> int:
    return len(_queues.get(conversation_id, ()))


def publish(conversation_id: str, event: str, data: dict[str, Any]) -> None:
    """Fan one frame out to every subscriber of ``conversation_id``.

    Never raises and never blocks: a full subscriber queue drops the frame for
    that subscriber only. Delivery is best-effort by construction — the store
    is the source of truth, and a client that missed deltas can re-read the
    finalized messages.
    """
    frame = json.dumps({"event": event, "conversation_id": conversation_id, "data": data})
    for q in list(_queues.get(conversation_id, ())):
        try:
            q.put_nowait(frame)
        except asyncio.QueueFull:
            log.warning(
                "conversation.sse_queue_full",
                metadata={"conversation_id": conversation_id, "event": event},
            )


def reset_for_tests() -> None:
    """Drop all subscribers and restore the default streamer (called from the
    conftest autouse reset)."""
    global _streamer
    _queues.clear()
    _streamer = _stream_completion


def publish_message(msg: Message, *, created: bool = True) -> None:
    """Announce an already-persisted message (created + done, no deltas)."""
    payload = {"message": msg.model_dump()}
    if created:
        publish(msg.conversation_id, EV_MESSAGE_CREATED, payload)
    publish(msg.conversation_id, EV_MESSAGE_DONE, payload)


# ---------------------------------------------------------------------------
# Persona reply
# ---------------------------------------------------------------------------

# Context window control: how many prior messages the persona prompt carries.
HISTORY_LIMIT = 30


def build_prompt(conv: Conversation, persona: Participant, history: list[Message]) -> list[dict]:
    """Conversation history as chat messages for ``complete()``.

    The persona's own messages become ``assistant`` turns and everyone else's
    become ``user`` turns prefixed with the author, which is how a single-stream
    chat model is told who said what in a multi-party room.
    """
    system = persona.role_prompt or (
        f"You are '{persona.name}', a participant in the conversation '{conv.title}'. "
        "Reply conversationally and concisely."
    )
    out: list[dict] = [{"role": "system", "content": system}]
    for m in history[-HISTORY_LIMIT:]:
        if m.kind not in ("message", "session"):
            continue
        if m.author == persona.name:
            out.append({"role": "assistant", "content": m.content})
        else:
            out.append({"role": "user", "content": f"[{m.author}]: {m.content}"})
    return out


def _chunks(text: str) -> list[str]:
    """Split a completion into delta-sized pieces (words, whitespace kept).

    ``complete()`` is a request/response seam today — it returns the whole
    string — so the persona's reply is re-chunked here to preserve the
    ``created → delta* → done`` wire contract end to end. When the seam grows a
    native streaming API, ``_stream_completion`` swaps over and the contract,
    the API and the frontend are all unchanged.
    """
    return re.findall(r"\S+\s*", text) or ([text] if text else [])


async def _stream_completion(
    messages: list[dict], *, profile: str, persona: Participant
) -> AsyncIterator[str]:
    """Default streamer: one ``complete()`` call, yielded in pieces."""
    from .llm import CallCtx, complete
    from .models import ModelTarget

    target = ModelTarget(**persona.model_target) if persona.model_target else None
    comp = await complete(
        messages,
        profile=profile,
        target=target,
        ctx=CallCtx(caller="conversations"),
    )
    for piece in _chunks(comp.text):
        yield piece


# Injection point for tests and for PR2's orchestrator: replace the streamer
# without touching the emit/persist logic around it.
Streamer = Callable[..., AsyncIterator[str]]
_streamer: Streamer = _stream_completion


def set_streamer(fn: Streamer | None) -> None:
    global _streamer
    _streamer = fn or _stream_completion


async def stream_persona_reply(
    conversation_id: str,
    *,
    profile: str,
    persona: Participant,
    in_reply_to: str | None = None,
) -> Message | None:
    """Drive one persona's reply: emit the shell, stream deltas, persist, done.

    Returns the persisted message, or ``None`` if the conversation vanished or
    the completion failed (an ``error`` frame is published in that case — the
    UI must not be left with a bubble that never finishes).
    """
    from .node_identity import ulid

    conv = await async_get_conversation(conversation_id, profile=profile)
    if conv is None or conv.status != "active":
        return None

    history = await async_list_messages(conversation_id, profile=profile)
    prompt = build_prompt(conv, persona, history)

    message_id = ulid()
    shell = Message(
        id=message_id,
        conversation_id=conversation_id,
        profile=profile,
        author=persona.name,
        content="",
        in_reply_to=in_reply_to,
    )

    publish(conversation_id, EV_THINKING, {"author": persona.name})
    publish(conversation_id, EV_MESSAGE_CREATED, {"message": shell.model_dump()})

    parts: list[str] = []
    try:
        async for piece in _streamer(prompt, profile=profile, persona=persona):
            parts.append(piece)
            publish(
                conversation_id,
                EV_MESSAGE_DELTA,
                {"id": message_id, "delta": piece},
            )
    except Exception as exc:
        log.warning(
            "conversation.persona_reply_failed",
            metadata={
                "conversation_id": conversation_id,
                "persona": persona.name,
                "reason": str(exc),
            },
        )
        publish(
            conversation_id,
            EV_ERROR,
            {"id": message_id, "author": persona.name, "reason": str(exc)},
        )
        return None

    msg = await async_add_message(
        conversation_id=conversation_id,
        profile=profile,
        author=persona.name,
        content="".join(parts),
        in_reply_to=in_reply_to,
        message_id=message_id,
    )
    publish(conversation_id, EV_MESSAGE_DONE, {"message": msg.model_dump()})
    return msg

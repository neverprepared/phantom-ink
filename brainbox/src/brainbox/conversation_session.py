"""Promote a conversation message into a real container session (design spec §6).

A persona in a room can think, but it cannot touch a repo. Real tool and file
work is the *promotion path*: a human picks one message and turns it into a
full brainbox session, seeded with the room's context and a task derived from
that message. The session then participates in the room as a first-class
``kind='session'`` participant, and its progress and results come back as
``kind='session'`` messages on the same finalize/bus path every other turn
takes.

**Explicit, never automatic.** The engine this replaces bootstrapped a Docker
session for every session-typed participant the moment a channel was created —
silently, in a fire-and-forget task whose failure nobody ever saw. That is the
bug §6 of the design spec calls out. Here a session exists only because a human
promoted a specific message, the submission failure surfaces as a 4xx on that
click, and the participant can be dismissed again.

**Nothing new is invented.** The session is an ordinary hub task
(``router.submit_task`` → the scheduler → ``lifecycle`` → a container), exactly
as the ``task`` promote target already submits one. The only addition is the
*link*: task id ↔ (conversation, profile, participant), which is what lets task
lifecycle events land back in the room they came from.

The session talks back through two paths, both of which end in
``conversation_runtime.finalize_message``:

1. **Its own turns** — the container calls the ``channel_*`` MCP tools, which
   PR4 repoints at ``/api/conversations/...``. Those posts are authored by the
   session and stored with ``kind='session'``.
2. **Lifecycle** — completion, failure and cancellation are posted here, from
   the router event bridge, so a room never ends on a session that just went
   quiet.
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel

from .log import get_logger

log = get_logger()

# How much of the room travels with the session. The brief is a prompt, not an
# archive: enough recent turns to know what is being asked and why, not the
# whole history (which the session can read back with ``channel_read``).
SEED_HISTORY_LIMIT = 30

# Task lifecycle events that become a message in the room. ``task.queued`` is
# deliberately absent — the promotion itself already announced the session, and
# a second "queued" line the same second is noise.
REPORTED_TASK_EVENTS: dict[str, str] = {
    "task.completed": "completed",
    "task.failed": "failed",
    "task.cancelled": "cancelled",
    "task.suspended": "suspended",
    "task.resumed": "resumed",
}


class SessionLink(BaseModel):
    """The binding between a hub task and the room that promoted it.

    Kept in memory alongside the router's own task table (which is also in
    memory): a link is only meaningful while the task it names is live, and a
    restart that loses the tasks has nothing to link to. The durable record of
    what happened is the room's message log, which IS persisted.
    """

    task_id: str
    conversation_id: str
    profile: str
    participant: str
    agent_name: str


_links: dict[str, SessionLink] = {}


def reset_for_tests() -> None:
    _links.clear()


# ---------------------------------------------------------------------------
# Link registry
# ---------------------------------------------------------------------------


def register_link(link: SessionLink) -> SessionLink:
    _links[link.task_id] = link
    return link


def link_for_task(task_id: str) -> SessionLink | None:
    return _links.get(task_id)


def links_for_conversation(conversation_id: str) -> list[SessionLink]:
    return [ln for ln in _links.values() if ln.conversation_id == conversation_id]


def unlink_participant(conversation_id: str, name: str) -> list[SessionLink]:
    """Drop every link for one participant of one room — what "dismiss" means.

    The task is NOT cancelled: dismissing a session participant removes it from
    the conversation, and killing work a human may still want is a different
    (and destructive) decision. The dropped links are returned so the caller can
    say what was detached.
    """
    key = name.strip().casefold()
    dropped = [
        ln
        for ln in _links.values()
        if ln.conversation_id == conversation_id and ln.participant.strip().casefold() == key
    ]
    for ln in dropped:
        _links.pop(ln.task_id, None)
    return dropped


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------


def participant_name(agent_name: str) -> str:
    """A short, unique participant name for a promoted session.

    The name is the addressing key in a room (``@mentions``, the orchestrator's
    cooldown bookkeeping), so it has to be unique per room and typeable — hence
    a suffix rather than a pasted UUID. It is minted BEFORE the task is
    submitted because the seed brief has to tell the session what to call
    itself, and a task is dispatchable the instant ``submit_task`` returns:
    editing its description afterwards would race the scheduler.
    """
    from .node_identity import ulid

    return f"{agent_name}-{ulid()[-6:].lower()}"


# ---------------------------------------------------------------------------
# The seed brief
# ---------------------------------------------------------------------------


def build_seed_brief(
    *,
    conv: Any,
    msg: Any,
    history: list[Any],
    participant: str,
    note: str | None = None,
) -> str:
    """The task description a promoted session wakes up holding.

    Three parts, in the order the session needs them: the task (the promoted
    message), the room it came from (recent history, for context), and how to
    talk back (the ``channel_*`` tools, pointed at this conversation id).
    """
    task_text = (msg.content or "").strip()
    if note and note.strip():
        task_text = f"{task_text}\n\n{note.strip()}"

    lines = [
        f"# Task (promoted from conversation '{conv.title}')",
        "",
        task_text,
        "",
        f"Promoted from message {msg.id} by {msg.author}.",
        "",
        "## Conversation context",
        "",
    ]
    for m in history[-SEED_HISTORY_LIMIT:]:
        if m.kind not in ("message", "session"):
            continue
        lines.append(f"[{m.author}]: {(m.content or '').strip()}")

    lines += [
        "",
        "## Participating in the room",
        "",
        f"You are participant '{participant}' in conversation `{conv.id}`.",
        "Use the channel_* tools to take part — they are pointed at this",
        "conversation, and the conversation id is the `channel_id` argument:",
        "",
        f'- `channel_join(channel_id="{conv.id}")` — register yourself (idempotent)',
        f'- `channel_read(channel_id="{conv.id}", since_id=<last id>)` — catch up',
        f'- `channel_send(channel_id="{conv.id}", from_participant="{participant}", '
        "content=<msg>)` — post progress and results",
        f'- `channel_complete(channel_id="{conv.id}", by="{participant}", reason=<why>)`'
        " — close the room when the work is done",
        "",
        "Post your findings back to the room as you go; the humans there are",
        "watching this conversation, not your terminal.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Promotion
# ---------------------------------------------------------------------------


async def promote_to_session(
    *, profile: str, conv: Any, msg: Any, body: Any
) -> SessionLink:
    """Spin a session for ``msg`` and wire it into ``conv``.

    Raises ``ValueError`` when the hub refuses the submission (unknown agent,
    policy denial) — the caller maps that to a 4xx, which is the whole point of
    making promotion explicit: the human who clicked sees the failure.
    """
    from . import conversation_store, router

    history = await conversation_store.async_list_messages(conv.id, profile=profile)
    name = participant_name(body.agent_name)
    brief = build_seed_brief(
        conv=conv, msg=msg, history=history, participant=name, note=body.note
    )

    task = await router.submit_task(
        brief,
        body.agent_name,
        repo_url=body.repo_url,
        workspace_profile=profile,
    )

    await conversation_store.async_add_participant(
        conv.id,
        profile=profile,
        participant={"name": name, "kind": "session"},
    )
    link = register_link(
        SessionLink(
            task_id=task.id,
            conversation_id=conv.id,
            profile=profile,
            participant=name,
            agent_name=body.agent_name,
        )
    )

    await post_session_message(
        link,
        f"Session '{name}' promoted from message {msg.id} "
        f"(agent '{body.agent_name}', task `{task.id}`). Starting work.",
    )
    log.info(
        "conversation.session_promoted",
        metadata={
            "conversation_id": conv.id,
            "message_id": msg.id,
            "task_id": task.id,
            "participant": name,
            "profile": profile,
        },
    )
    return link


# ---------------------------------------------------------------------------
# Talking back
# ---------------------------------------------------------------------------


async def post_session_message(link: SessionLink, content: str) -> Any | None:
    """Append one ``kind='session'`` message to the linked room.

    Goes through ``finalize_message`` — the same choke point a human post and a
    persona reply use — so the room's SSE subscribers and the
    ``conversation:<id>`` bus envelope see it exactly like any other turn.
    Returns ``None`` (never raises) when the room is gone: a task outliving its
    conversation must not take the router's event loop down with it.
    """
    from . import conversation_runtime, conversation_store

    try:
        conv = await conversation_store.async_get_conversation(
            link.conversation_id, profile=link.profile
        )
        if conv is None:
            return None
        message = await conversation_store.async_add_message(
            conversation_id=link.conversation_id,
            profile=link.profile,
            author=link.participant,
            content=content,
            kind="session",
        )
        await conversation_runtime.finalize_message(message, conv)
        return message
    except Exception as exc:
        log.warning(
            "conversation.session_message_failed",
            metadata={
                "conversation_id": link.conversation_id,
                "task_id": link.task_id,
                "reason": str(exc),
            },
        )
        return None


def describe_task_event(event: str, task: Any) -> str:
    """One line of room-readable prose for a task lifecycle event."""
    label = REPORTED_TASK_EVENTS.get(event, event)
    detail = ""
    if event == "task.failed":
        detail = str(getattr(task, "error", "") or getattr(task, "last_error", "") or "")
    elif event == "task.completed":
        result = getattr(task, "result", None)
        detail = "" if result is None else str(result)
    if detail:
        return f"Session {label}: {detail.strip()}"
    return f"Session {label}."


async def on_task_event_async(event: str, task: Any) -> None:
    link = link_for_task(getattr(task, "id", ""))
    if link is None or event not in REPORTED_TASK_EVENTS:
        return
    await post_session_message(link, describe_task_event(event, task))
    if event in ("task.completed", "task.failed", "task.cancelled"):
        # The work is over; the participant stays in the roster (its history is
        # part of the room) but nothing more will arrive under this task.
        _links.pop(link.task_id, None)


def on_task_event(event: str, task: Any) -> None:
    """Router listener: bridge a task lifecycle event into its room.

    ``router._emit`` is synchronous and is called from async request handlers,
    so the actual post is scheduled on the running loop. Outside a loop (a
    synchronous unit test driving the router directly) there is nothing to
    schedule onto and the event is dropped rather than raised — the router must
    never fail because a room is unreachable.
    """
    if link_for_task(getattr(task, "id", "")) is None or event not in REPORTED_TASK_EVENTS:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        log.debug(
            "conversation.session_event_no_loop",
            metadata={"event": event, "task_id": getattr(task, "id", None)},
        )
        return
    loop.create_task(on_task_event_async(event, task))

"""ConversationStore — local-first persistence for the multi-agent Chat engine.

The new conversation system (see ``docs/superpowers/specs/2026-09-09-multi-agent-chat-design.md``)
replaces the in-memory ``channels`` hub with real records in the local-first
store. This module owns the two record types and all CRUD over them:

- ``Conversation`` — a room: ULID id, profile, title, status, participants.
- ``Message``      — an append-only entry in a room: ULID id, author, kind,
  content.

Both carry the local-first / P2P substrate the rest of the store uses (see
``node_identity``): a ULID primary key (globally unique, time-sortable, no
central counter), a ``node_id`` stamp naming the writing node, and a
``deleted_at`` tombstone so a removal survives a peer merge instead of being
resurrected.

**Profile scoping is a store-level invariant, not a caller courtesy.** Every
read and write takes a ``profile`` and filters on it, so a conversation created
under ``personal`` is invisible to — and unwritable from — a request scoped to
``work``. The API layer never has to remember to filter; it cannot bypass it.

All functions are synchronous (matching ``store.py``) and are called from async
contexts via ``asyncio.to_thread`` (see the ``async_*`` wrappers at the bottom).

This is PR1 of the phased rollout: the old ``channels`` engine is untouched and
still serves the old endpoints. PR4 retires it.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Literal

from pydantic import BaseModel, Field

from .node_identity import node_id, ulid
from .store import _conn

# ---------------------------------------------------------------------------
# Record models
# ---------------------------------------------------------------------------

ConversationStatus = Literal["active", "archived"]
MessageKind = Literal["message", "system", "join", "tool", "session"]
ParticipantKind = Literal["human", "persona", "session"]


def _now_ms() -> int:
    return int(time.time() * 1000)


class Participant(BaseModel):
    """A member of a conversation.

    ``human`` participants post through the API; ``persona`` participants are
    lightweight LLM personas driven through the ``complete()`` seam (see
    ``conversation_runtime``); ``session`` is the PR4 promotion path and is
    accepted in the data model now so records written today stay valid.
    """

    name: str
    kind: ParticipantKind = "persona"
    model_target: dict[str, Any] | None = None
    role_prompt: str | None = None
    cooldown_s: float | None = None
    joined_at: int = Field(default_factory=_now_ms)


class Conversation(BaseModel):
    id: str = Field(default_factory=ulid)
    profile: str
    title: str
    status: ConversationStatus = "active"
    participants: list[Participant] = Field(default_factory=list)
    created_at: int = Field(default_factory=_now_ms)
    updated_at: int = Field(default_factory=_now_ms)
    node_id: str = Field(default_factory=node_id)
    deleted_at: int | None = None


class Message(BaseModel):
    id: str = Field(default_factory=ulid)
    conversation_id: str
    profile: str
    author: str
    kind: MessageKind = "message"
    content: str = ""
    addressed_to: str | None = None
    in_reply_to: str | None = None
    created_at: int = Field(default_factory=_now_ms)
    node_id: str = Field(default_factory=node_id)
    deleted_at: int | None = None


class ProfileScopeError(PermissionError):
    """Raised when a write targets a conversation outside the caller's profile.

    Distinct from "not found": the API maps both to 404 (never confirming that
    a row exists in another profile), but the store keeps them apart so tests
    and logs can tell a scoping violation from a genuine miss.
    """


# ---------------------------------------------------------------------------
# Row <-> model
# ---------------------------------------------------------------------------


def _row_to_conversation(row: Any) -> Conversation:
    d = dict(row)
    participants = json.loads(d.pop("participants_json") or "[]")
    return Conversation(participants=participants, **d)


def _row_to_message(row: Any) -> Message:
    return Message(**dict(row))


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------


def create_conversation(
    *,
    profile: str,
    title: str,
    participants: list[Participant] | list[dict[str, Any]] | None = None,
) -> Conversation:
    """Insert a new conversation owned by ``profile``."""
    parsed = [p if isinstance(p, Participant) else Participant(**p) for p in (participants or [])]
    conv = Conversation(profile=profile, title=title, participants=parsed)
    with _conn() as c:
        c.execute(
            """
            INSERT INTO conversations
                (id, profile, title, status, participants_json,
                 created_at, updated_at, node_id, deleted_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NULL)
            """,
            (
                conv.id,
                conv.profile,
                conv.title,
                conv.status,
                json.dumps([p.model_dump() for p in conv.participants]),
                conv.created_at,
                conv.updated_at,
                conv.node_id,
            ),
        )
    return conv


def get_conversation(conversation_id: str, *, profile: str) -> Conversation | None:
    """Fetch one conversation, or None if it is missing, tombstoned, or owned
    by a different profile. The profile filter is in the SQL — a caller in the
    wrong profile gets a miss, not a leak."""
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM conversations "
            "WHERE id = %s AND profile = %s AND deleted_at IS NULL",
            (conversation_id, profile),
        ).fetchone()
    return _row_to_conversation(row) if row else None


def list_conversations(
    *, profile: str, include_archived: bool = True, limit: int = 200
) -> list[Conversation]:
    clauses = ["profile = %s", "deleted_at IS NULL"]
    params: list[Any] = [profile]
    if not include_archived:
        clauses.append("status <> 'archived'")
    with _conn() as c:
        rows = c.execute(
            f"SELECT * FROM conversations WHERE {' AND '.join(clauses)} "
            f"ORDER BY updated_at DESC LIMIT %s",
            (*params, limit),
        ).fetchall()
    return [_row_to_conversation(r) for r in rows]


def archive_conversation(conversation_id: str, *, profile: str) -> Conversation:
    """Flip a conversation to ``archived``. Raises ``ProfileScopeError`` when
    the row is absent or belongs to another profile."""
    now = _now_ms()
    with _conn() as c:
        row = c.execute(
            "UPDATE conversations SET status = 'archived', updated_at = %s "
            "WHERE id = %s AND profile = %s AND deleted_at IS NULL RETURNING *",
            (now, conversation_id, profile),
        ).fetchone()
    if row is None:
        raise ProfileScopeError(f"conversation '{conversation_id}' not found in profile '{profile}'")
    return _row_to_conversation(row)


def touch_conversation(conversation_id: str, *, profile: str) -> None:
    """Bump ``updated_at`` so the list view sorts by recent activity."""
    with _conn() as c:
        c.execute(
            "UPDATE conversations SET updated_at = %s WHERE id = %s AND profile = %s",
            (_now_ms(), conversation_id, profile),
        )


def delete_conversation(conversation_id: str, *, profile: str) -> None:
    """Tombstone a conversation (and its messages) rather than removing rows,
    so the delete survives a P2P merge."""
    now = _now_ms()
    with _conn() as c, c.transaction():
        row = c.execute(
            "UPDATE conversations SET deleted_at = %s, updated_at = %s "
            "WHERE id = %s AND profile = %s AND deleted_at IS NULL RETURNING id",
            (now, now, conversation_id, profile),
        ).fetchone()
        if row is None:
            raise ProfileScopeError(
                f"conversation '{conversation_id}' not found in profile '{profile}'"
            )
        c.execute(
            "UPDATE conversation_messages SET deleted_at = %s "
            "WHERE conversation_id = %s AND deleted_at IS NULL",
            (now, conversation_id),
        )


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


def add_message(
    *,
    conversation_id: str,
    profile: str,
    author: str,
    content: str,
    kind: MessageKind = "message",
    addressed_to: str | None = None,
    in_reply_to: str | None = None,
    message_id: str | None = None,
) -> Message:
    """Append a message. ``message_id`` lets a caller reserve the ULID before
    the content exists — the streaming path emits ``message.created`` and its
    deltas under the final id, then persists with that same id, so the client
    never has to reconcile a placeholder.

    Raises ``ProfileScopeError`` if the conversation is not visible in
    ``profile``, so a cross-profile post cannot land.
    """
    conv = get_conversation(conversation_id, profile=profile)
    if conv is None:
        raise ProfileScopeError(f"conversation '{conversation_id}' not found in profile '{profile}'")

    msg = Message(
        id=message_id or ulid(),
        conversation_id=conversation_id,
        profile=profile,
        author=author,
        kind=kind,
        content=content,
        addressed_to=addressed_to,
        in_reply_to=in_reply_to,
    )
    with _conn() as c:
        c.execute(
            """
            INSERT INTO conversation_messages
                (id, conversation_id, profile, author, kind, content,
                 addressed_to, in_reply_to, created_at, node_id, deleted_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL)
            """,
            (
                msg.id,
                msg.conversation_id,
                msg.profile,
                msg.author,
                msg.kind,
                msg.content,
                msg.addressed_to,
                msg.in_reply_to,
                msg.created_at,
                msg.node_id,
            ),
        )
    touch_conversation(conversation_id, profile=profile)
    return msg


def list_messages(
    conversation_id: str,
    *,
    profile: str,
    since_id: str | None = None,
    limit: int = 500,
) -> list[Message]:
    """Messages in ULID (creation) order. ``since_id`` returns only messages
    created after that id — a plain string comparison, which is exactly why the
    ids are ULIDs."""
    clauses = ["conversation_id = %s", "profile = %s", "deleted_at IS NULL"]
    params: list[Any] = [conversation_id, profile]
    if since_id:
        clauses.append("id > %s")
        params.append(since_id)
    with _conn() as c:
        rows = c.execute(
            f"SELECT * FROM conversation_messages WHERE {' AND '.join(clauses)} "
            f"ORDER BY id ASC LIMIT %s",
            (*params, limit),
        ).fetchall()
    return [_row_to_message(r) for r in rows]


# ---------------------------------------------------------------------------
# Async wrappers — the API layer is async; the store is sync (see store.py).
# ---------------------------------------------------------------------------


async def async_create_conversation(**kwargs: Any) -> Conversation:
    return await asyncio.to_thread(lambda: create_conversation(**kwargs))


async def async_get_conversation(conversation_id: str, *, profile: str) -> Conversation | None:
    return await asyncio.to_thread(get_conversation, conversation_id, profile=profile)


async def async_list_conversations(**kwargs: Any) -> list[Conversation]:
    return await asyncio.to_thread(lambda: list_conversations(**kwargs))


async def async_archive_conversation(conversation_id: str, *, profile: str) -> Conversation:
    return await asyncio.to_thread(archive_conversation, conversation_id, profile=profile)


async def async_add_message(**kwargs: Any) -> Message:
    return await asyncio.to_thread(lambda: add_message(**kwargs))


async def async_list_messages(conversation_id: str, **kwargs: Any) -> list[Message]:
    return await asyncio.to_thread(lambda: list_messages(conversation_id, **kwargs))

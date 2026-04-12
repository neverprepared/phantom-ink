"""Group chat channels — append-only message log with Ollama watcher."""

from __future__ import annotations

import asyncio
from typing import Callable

from .log import get_logger
from .models import Channel, ChannelMessage, ChannelParticipant

log = get_logger()

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

_channels: dict[str, Channel] = {}
_messages: dict[str, list[ChannelMessage]] = {}  # channel_id -> ordered list
_listeners: list[Callable] = []

# Tracks the last message ID each Ollama participant has responded to
_ollama_last_read: dict[str, dict[str, str]] = {}  # channel_id -> {participant_name -> msg_id}


# ---------------------------------------------------------------------------
# Event emission
# ---------------------------------------------------------------------------


def _emit(event: str, data: object) -> None:
    for fn in _listeners:
        try:
            fn(event, data)
        except Exception as exc:
            log.warning("channels.event_listener_error", metadata={"event": event, "reason": str(exc)})


def on_event(fn: Callable) -> None:
    """Register a listener for channel events (used to bridge to SSE)."""
    _listeners.append(fn)


# ---------------------------------------------------------------------------
# Channel management
# ---------------------------------------------------------------------------


def create_channel(name: str, participants: list[ChannelParticipant]) -> Channel:
    channel = Channel(name=name, participants=participants)
    _channels[channel.id] = channel
    _messages[channel.id] = []
    _emit("channel.created", channel)
    log.info("channel.created", metadata={"channel_id": channel.id, "name": name})
    return channel


def get_channel(channel_id: str) -> Channel | None:
    return _channels.get(channel_id)


def list_channels() -> list[Channel]:
    return list(_channels.values())


# ---------------------------------------------------------------------------
# Message management
# ---------------------------------------------------------------------------


def post_message(
    channel_id: str,
    from_participant: str,
    content: str,
    summary: str | None = None,
    addressed_to: str | None = None,
    msg_type: str = "message",
) -> ChannelMessage:
    channel = _channels.get(channel_id)
    if not channel:
        raise ValueError(f"Channel '{channel_id}' not found")
    if channel.status == "completed":
        raise ValueError(f"Channel '{channel_id}' is already completed")

    msg = ChannelMessage(
        channel_id=channel_id,
        from_participant=from_participant,
        content=content,
        summary=summary,
        addressed_to=addressed_to,
        type=msg_type,  # type: ignore[arg-type]
    )
    _messages[channel_id].append(msg)
    _emit("channel.message", {"channel_id": channel_id, "message": msg})
    return msg


def get_messages(channel_id: str, since_id: str | None = None) -> list[ChannelMessage]:
    msgs = _messages.get(channel_id, [])
    if since_id is None:
        return list(msgs)
    idx = next((i for i, m in enumerate(msgs) if m.id == since_id), None)
    if idx is None:
        return list(msgs)  # since_id not found — return all
    return list(msgs[idx + 1:])


def complete_channel(channel_id: str, by: str, reason: str | None = None) -> Channel:
    channel = _channels.get(channel_id)
    if not channel:
        raise ValueError(f"Channel '{channel_id}' not found")
    from .models import _now_ms
    post_message(
        channel_id,
        from_participant=by,
        content=reason or "Channel completed.",
        msg_type="completion",
    )
    channel.status = "completed"
    channel.completed_at = _now_ms()
    channel.completed_by = by
    _emit("channel.completed", channel)
    log.info("channel.completed", metadata={"channel_id": channel_id, "by": by})
    return channel


# ---------------------------------------------------------------------------
# Hub state persistence
# ---------------------------------------------------------------------------


def get_state() -> dict:
    return {
        "channels": [(c.id, c.model_dump()) for c in _channels.values()],
        "messages": {
            cid: [m.model_dump() for m in msgs]
            for cid, msgs in _messages.items()
        },
    }


def restore_state(state: dict | None) -> None:
    if not state:
        return
    for cid, cdata in state.get("channels", []):
        try:
            _channels[cid] = Channel(**cdata)
        except Exception as exc:
            log.warning("channels.restore_channel_failed", metadata={"id": cid, "reason": str(exc)})
    for cid, msgs in state.get("messages", {}).items():
        restored = []
        for mdata in msgs:
            try:
                restored.append(ChannelMessage(**mdata))
            except Exception as exc:
                log.warning("channels.restore_message_failed", metadata={"reason": str(exc)})
        _messages[cid] = restored


# ---------------------------------------------------------------------------
# Ollama watcher (background task)
# ---------------------------------------------------------------------------


def _build_ollama_messages(
    channel: Channel,
    participant: ChannelParticipant,
) -> list[dict]:
    """Build Ollama messages array from channel history.

    Uses full content for the last 10 messages; summary for older ones to
    manage context window pressure. The sending agent authors their own summary.
    """
    msgs = _messages.get(channel.id, [])
    system_content = participant.system_prompt or (
        f"You are '{participant.name}' participating in a group discussion called '{channel.name}'. "
        "Respond thoughtfully and concisely. When sending, summarise your key point in 1-2 sentences."
    )
    result: list[dict] = [{"role": "system", "content": system_content}]

    recent, older = msgs[-10:], msgs[:-10]

    # Older messages: use summary if available, otherwise truncate
    for m in older:
        if m.type != "message":
            continue
        text = m.summary or m.content[:200]
        role = "assistant" if m.from_participant == participant.name else "user"
        result.append({"role": role, "content": f"[{m.from_participant}]: {text}"})

    # Recent messages: full content
    for m in recent:
        if m.type != "message":
            continue
        role = "assistant" if m.from_participant == participant.name else "user"
        prefix = f"[{m.from_participant}]" if m.from_participant != participant.name else ""
        addressed = f" (@{m.addressed_to})" if m.addressed_to else ""
        result.append({"role": role, "content": f"{prefix}{addressed}: {m.content}".strip(": ")})

    return result


def _auto_summary(content: str) -> str:
    """Extract first 1-2 sentences as a summary."""
    sentences = content.replace("\n", " ").split(". ")
    brief = ". ".join(sentences[:2])
    return brief[:300] if brief else content[:300]


async def _ollama_respond(
    channel: Channel,
    participant: ChannelParticipant,
) -> None:
    from .ollama import chat as ollama_chat, OllamaError

    messages = _build_ollama_messages(channel, participant)
    try:
        result = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: ollama_chat(messages, model=participant.ollama_model),
        )
        content = result.message.content
        post_message(
            channel.id,
            from_participant=participant.name,
            content=content,
            summary=_auto_summary(content),
        )
    except OllamaError as exc:
        log.warning(
            "channels.ollama_respond_failed",
            metadata={"channel": channel.id, "participant": participant.name, "reason": str(exc)},
        )


async def ollama_watcher() -> None:
    """Background task: watch channels and drive Ollama participant responses."""
    try:
        while True:
            try:
                for channel in list(_channels.values()):
                    if channel.status != "active":
                        continue
                    for p in channel.participants:
                        if p.type != "ollama":
                            continue
                        last_id = _ollama_last_read.get(channel.id, {}).get(p.name)
                        new_msgs = get_messages(channel.id, since_id=last_id)
                        relevant = [
                            m for m in new_msgs
                            if m.from_participant != p.name
                            and (m.addressed_to is None or m.addressed_to == p.name)
                            and m.type == "message"
                        ]
                        if relevant:
                            await _ollama_respond(channel, p)
                        if new_msgs:
                            _ollama_last_read.setdefault(channel.id, {})[p.name] = new_msgs[-1].id
            except Exception as exc:
                log.warning("channels.ollama_watcher_error", metadata={"reason": str(exc)})
            await asyncio.sleep(3)
    except asyncio.CancelledError:
        pass

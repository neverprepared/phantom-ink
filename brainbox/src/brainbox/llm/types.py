"""Data types for the ``brainbox.llm`` seam — the stateless LLM-request plane.

These are deliberately small and transport-agnostic. ``complete()`` (see
``core.py``) takes a prompt + ``ModelTarget`` + ``CompletionPolicy`` and returns
a ``Completion``; every call also emits an ``LlmCompletionRecord`` for metering.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

# A prompt is either a single user string or an explicit chat message list.
# Message dicts use the OpenAI/Ollama shape: {"role": "system|user|assistant",
# "content": "..."}.
Messages = list[dict]


class LlmError(RuntimeError):
    """Raised when no backend in the resolved chain could produce a completion.

    Carries the per-backend ``attempts`` so callers/logs can see exactly why each
    link fell through.
    """

    def __init__(self, reason: str, attempts: "list[Attempt] | None" = None) -> None:
        self.reason = reason
        self.attempts = attempts or []
        super().__init__(reason)


class CompletionPolicy(BaseModel):
    """How to route a completion.

    - ``quality`` — ``cheap`` prefers Ollama; ``high`` prefers Claude.
    - ``allow_paid`` — gates the paid ``claude_api`` backend. HARD default off so
      a stray call can never silently incur per-token spend. The platform default
      comes from ``settings.llm.allow_paid_default``.
    """

    quality: Literal["cheap", "high"] = "cheap"
    allow_paid: bool = False


class Usage(BaseModel):
    tokens_in: int = 0
    tokens_out: int = 0
    cost_estimate_usd: float = 0.0


class Attempt(BaseModel):
    """One backend try in the fallback chain."""

    backend: str
    ok: bool
    reason: str = ""  # why it fell through; empty when ok


class Completion(BaseModel):
    text: str
    backend: str            # which backend answered
    model: str
    usage: Usage = Field(default_factory=Usage)
    attempts: list[Attempt] = Field(default_factory=list)


@dataclass
class CallCtx:
    """Per-call context threaded to backends. Kept out of ``CompletionPolicy`` so
    routing stays a pure function of target+policy while transport knobs (timeout,
    session routing) live here.
    """

    caller: str                        # e.g. "playbooks", "loop_assist" — for metering
    trace_id: str = ""                 # auto-filled by complete() if empty
    timeout: float = 300.0
    max_tokens: int = 0                # 0 → backend/setting default
    # --- session-backed (claude_oauth) knobs; ignored by stateless backends ---
    runner: str | None = None          # route the ephemeral session to a runner
    workspace_home: str | None = None  # mount context for the ephemeral session
    session_prefix: str = "llm"        # ephemeral session-name prefix (when session_name unset)
    session_name: str | None = None    # explicit ephemeral session name (verbatim); callers that
                                       # track the name (e.g. playbooks) pass it so it stays stable


class LlmCompletionRecord(BaseModel):
    """Metering record emitted for every ``complete()`` call (success or failure).

    Bridged to the agent-event bus as a ``kind='metric'`` envelope by the API
    layer; the seam itself only emits this to registered listeners (no DB
    coupling), mirroring the playbook/channel event bridges.
    """

    profile: str
    caller: str
    backend: str = ""      # backend that answered; "" if all failed
    model: str = ""
    quality: str = "cheap"
    allow_paid: bool = False
    tokens_in: int = 0
    tokens_out: int = 0
    cost_estimate_usd: float = 0.0
    ok: bool = True
    trace_id: str = ""
    attempts: list[Attempt] = Field(default_factory=list)

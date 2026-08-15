"""``brainbox.llm`` — the LLM-request plane (the ``complete()`` seam).

The stateless completion plane, distinct from the OAuth session plane. Callers
hand a prompt to ``complete()``; a strategy resolves a backend chain
(Ollama → Claude-API / Claude-OAuth) with fallback, and every call emits a
metering record. API keys live only here; the seam never reads ``.claude.json``.

Public surface::

    from brainbox.llm import complete, CompletionPolicy, CallCtx, Completion

    result = await complete(
        "Summarize this changelog.",
        profile="personal",
        policy=CompletionPolicy(quality="cheap"),
        ctx=CallCtx(caller="playbooks"),
    )
"""

from __future__ import annotations

from .backends import (
    Backend,
    ClaudeApiBackend,
    ClaudeOAuthBackend,
    OllamaBackend,
)
from .core import (
    complete,
    get_registry,
    on_completion,
    reset_for_tests,
    set_registry,
)
from .strategy import chain_names, resolve_chain
from .types import (
    Attempt,
    CallCtx,
    Completion,
    CompletionPolicy,
    LlmCompletionRecord,
    LlmError,
    Messages,
    Usage,
)

__all__ = [
    "complete",
    "on_completion",
    "get_registry",
    "set_registry",
    "reset_for_tests",
    "resolve_chain",
    "chain_names",
    "Backend",
    "OllamaBackend",
    "ClaudeApiBackend",
    "ClaudeOAuthBackend",
    "CompletionPolicy",
    "Completion",
    "CallCtx",
    "Usage",
    "Attempt",
    "LlmCompletionRecord",
    "LlmError",
    "Messages",
]

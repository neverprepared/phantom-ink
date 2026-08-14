"""``complete()`` — the entry point of the LLM-request plane.

One prompt in, one ``Completion`` out. Resolves a backend chain from
``(target, policy)``, tries each with fallback, records every attempt, and emits
one ``LlmCompletionRecord`` per call (success or failure) to registered metering
listeners.

The seam holds NO OAuth credentials and does not touch the DB directly — metering
is a listener hook (mirroring the playbook/channel bridges), wired to the agent
event bus by the API layer.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from typing import Callable

from ..log import get_logger
from . import backends as _backends
from .strategy import resolve_chain
from .types import (
    Attempt,
    CallCtx,
    Completion,
    CompletionPolicy,
    LlmCompletionRecord,
    LlmError,
    Messages,
)

log = get_logger()

# --- metering listeners (sync, like playbooks.on_event) --------------------- #
_listeners: list[Callable[[LlmCompletionRecord], None]] = []


def on_completion(fn: Callable[[LlmCompletionRecord], None]) -> None:
    """Register a metering listener. The API layer bridges this to the agent bus."""
    _listeners.append(fn)


def reset_for_tests() -> None:
    """Clear listeners + backend registry (called by the conftest autouse reset)."""
    global _registry
    _listeners.clear()
    _registry = {}


def _emit(record: LlmCompletionRecord) -> None:
    for fn in list(_listeners):
        try:
            fn(record)
        except Exception as exc:  # a bad listener must never break a completion
            log.warning("llm.metering_listener_error", metadata={"reason": str(exc)})


# --- backend registry ------------------------------------------------------- #
_registry: dict[str, "_backends.Backend"] = {}


def _default_registry() -> dict[str, "_backends.Backend"]:
    return {
        "ollama": _backends.OllamaBackend(),
        "claude_api": _backends.ClaudeApiBackend(),
        "claude_oauth": _backends.ClaudeOAuthBackend(),
    }


def get_registry() -> dict[str, "_backends.Backend"]:
    global _registry
    if not _registry:
        _registry = _default_registry()
    return _registry


def set_registry(registry: dict[str, "_backends.Backend"]) -> None:
    """Override the backend registry (tests inject fakes)."""
    global _registry
    _registry = registry


def _normalize(prompt: "str | Messages") -> Messages:
    if isinstance(prompt, str):
        return [{"role": "user", "content": prompt}]
    return prompt


async def complete(
    prompt: "str | Messages",
    *,
    profile: str,
    target: "object | None" = None,   # ModelTarget | None (duck-typed on .provider/.model)
    policy: CompletionPolicy | None = None,
    ctx: CallCtx | None = None,
) -> Completion:
    """Produce a stateless completion.

    Args:
        prompt: a user string or an explicit message list.
        profile: workspace profile — scopes credentials and metering.
        target: optional ``ModelTarget`` (provider/model/effort). ``None`` uses
            the policy default chain.
        policy: routing policy; defaults to ``CompletionPolicy`` with
            ``allow_paid = settings.llm.allow_paid_default``.
        ctx: per-call transport context (caller label, timeout, session knobs).

    Raises:
        LlmError: if no backend in the resolved chain produced a completion.
    """
    from ..config import settings

    if policy is None:
        policy = CompletionPolicy(allow_paid=settings.llm.allow_paid_default)
    if ctx is None:
        ctx = CallCtx(caller="unknown")
    if not ctx.trace_id:
        ctx = replace(ctx, trace_id=uuid.uuid4().hex)

    messages = _normalize(prompt)
    model = getattr(target, "model", None)
    chain = resolve_chain(target, policy, get_registry())
    attempts: list[Attempt] = []

    if not chain:
        _emit(_record(profile, ctx, policy, backend="", ok=False, attempts=attempts))
        raise LlmError("no backend available for the requested target/policy", attempts)

    for backend in chain:
        # Belt-and-suspenders paid gate (the strategy already omits claude_api
        # unless allow_paid, but a custom registry / explicit pin could reach here).
        if backend.estimates_cost() and not policy.allow_paid:
            attempts.append(
                Attempt(backend=backend.name, ok=False, reason="paid backend skipped (allow_paid=false)")
            )
            continue
        try:
            comp = await backend.complete(messages, model=model, ctx=ctx, profile=profile)
        except LlmError as exc:
            attempts.append(Attempt(backend=backend.name, ok=False, reason=exc.reason))
            continue
        except Exception as exc:  # a backend crash falls through to the next link
            attempts.append(Attempt(backend=backend.name, ok=False, reason=str(exc)))
            log.warning("llm.backend_error", metadata={"backend": backend.name, "reason": str(exc)})
            continue
        attempts.append(Attempt(backend=backend.name, ok=True))
        comp.attempts = attempts
        _emit(
            _record(
                profile, ctx, policy,
                backend=comp.backend, model=comp.model,
                tokens_in=comp.usage.tokens_in, tokens_out=comp.usage.tokens_out,
                cost=comp.usage.cost_estimate_usd, ok=True, attempts=attempts,
            )
        )
        return comp

    _emit(_record(profile, ctx, policy, backend="", ok=False, attempts=attempts))
    reasons = [a.reason for a in attempts if not a.ok]
    raise LlmError(f"all backends failed: {reasons}", attempts)


def _record(
    profile: str,
    ctx: CallCtx,
    policy: CompletionPolicy,
    *,
    backend: str,
    ok: bool,
    attempts: list[Attempt],
    model: str = "",
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost: float = 0.0,
) -> LlmCompletionRecord:
    return LlmCompletionRecord(
        profile=profile,
        caller=ctx.caller,
        backend=backend,
        model=model,
        quality=policy.quality,
        allow_paid=policy.allow_paid,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_estimate_usd=cost,
        ok=ok,
        trace_id=ctx.trace_id,
        attempts=list(attempts),
    )

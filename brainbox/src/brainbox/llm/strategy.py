"""Backend chain resolution for the ``brainbox.llm`` seam.

Pure function of ``(target, policy)`` — no I/O, so it's trivially testable. The
default (``target.provider is None``) follows the quality×allow_paid table; an
explicit provider pin overrides the family.

    | quality | allow_paid | chain                        |
    |---------|-----------|------------------------------|
    | cheap   | false     | [ollama]                     |
    | cheap   | true      | [ollama, claude_api]         |
    | high    | false     | [claude_oauth]               |
    | high    | true      | [claude_oauth, claude_api]   |

Provider pins:
    ollama → [ollama]
    claude → [claude_oauth] (+ claude_api if allow_paid)   # quality never
             downgrades a claude pin to ollama
    codex  → [codex]        # not implemented yet → empty registry → LlmError
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid an import cycle at runtime (models imports many things)
    from ..models import ModelTarget
    from .backends import Backend
    from .types import CompletionPolicy


def chain_names(provider: str | None, policy: "CompletionPolicy") -> list[str]:
    if provider == "ollama":
        return ["ollama"]
    if provider == "codex":
        return ["codex"]
    if provider == "claude":
        return ["claude_oauth", "claude_api"] if policy.allow_paid else ["claude_oauth"]
    # provider is None → the quality×allow_paid default table
    if policy.quality == "cheap":
        return ["ollama", "claude_api"] if policy.allow_paid else ["ollama"]
    return ["claude_oauth", "claude_api"] if policy.allow_paid else ["claude_oauth"]


def resolve_chain(
    target: "ModelTarget | None",
    policy: "CompletionPolicy",
    registry: "dict[str, Backend]",
) -> list["Backend"]:
    provider = target.provider if target is not None else None
    names = chain_names(provider, policy)
    return [registry[n] for n in names if n in registry]

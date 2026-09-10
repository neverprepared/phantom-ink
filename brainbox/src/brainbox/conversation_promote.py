"""Promote one conversation message into the rest of the platform (design spec §8).

A room is where thinking happens; the platform is where it becomes durable
work. This module is the bridge, and it is deliberately thin — every target is
an EXISTING platform surface, reached the same way the rest of the product
reaches it:

- ``memory`` → the profile's phantom-brain **memory** vault, via the brain
  daemon's ``POST /api/brain/learn`` (the same endpoint and bearer-token shape
  the desktop app's vault browser uses).
- ``todo``   → the profile's phantom-brain **todo** vault, same endpoint, that
  vault's own token. Verbatim: the todo is stored exactly as authored.
- ``task``   → a hub task via ``router.submit_task`` — in-process, no HTTP hop.

**Credentials are resolved server-side, per profile, and never taken from the
request.** The brain API + per-vault bearer tokens come out of the encrypted
per-profile env store (``gateway_secrets``, read through the same
``gateway_pool`` helper the MCP gateway spawns servers with), so a caller can
only ever promote into the vaults of the profile it is scoped to. A missing
token is an error, never a fallback to another vault's token — misfiling a
memory into the todo list (or vice versa) would be worse than failing.

Promotion is profile-scoped end to end: the caller's profile is derived from
its auth context (see ``api._scoped_profile``), the message is loaded with that
profile in the SQL filter, and the credentials are that profile's.
"""

from __future__ import annotations

import hashlib
from typing import Any, Literal

import httpx
from pydantic import BaseModel

from .log import get_logger
from .models_api import PromoteMessageRequest

log = get_logger()

PromoteTarget = Literal["memory", "todo", "task"]

# Which phantom-brain vault each vault-backed target writes to.
VAULT_FOR_TARGET: dict[str, str] = {"memory": "memory", "todo": "todo"}

# The per-profile env key holding each vault's bearer token. The unified-token
# convention: one token per (profile, vault), the same value the workspace's
# own .env carries. NO cross-vault fallback — see the module docstring.
TOKEN_ENV_FOR_VAULT: dict[str, str] = {
    "memory": "CL_BRAIN_API_TOKEN",
    "todo": "CL_TODO_API_TOKEN",
}

# The brain daemon's base URL for a profile, and the learn endpoint on it.
BRAIN_API_ENV = "CL_BRAIN_API"
LEARN_PATH = "/api/brain/learn"

LEARN_TIMEOUT_S = 15.0


class PromoteError(Exception):
    """A promotion could not be performed (missing credentials, downstream
    rejection). The API maps this to a 4xx/502 rather than a 500 — it is an
    expected operational condition, not a bug."""


# The wire model lives with the other request models (``models_api``); it is
# aliased here so this module reads as the owner of the promotion domain.
PromoteRequest = PromoteMessageRequest


class PromoteResult(BaseModel):
    ok: bool
    target: PromoteTarget
    # Vault targets return the record's SHA; task returns the hub task id.
    sha: str | None = None
    task_id: str | None = None
    detail: str = ""


# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------

# Provenance footer appended to every promoted record/description. A promoted
# note that cannot be traced back to the turn it came from is a dead end.
_PROVENANCE = (
    "\n\n---\nPromoted from conversation '{title}' ({conversation_id})\n"
    "message {message_id} by {author}"
)


def build_title(conv: Any, msg: Any, override: str | None = None) -> str:
    if override and override.strip():
        return override.strip()
    first_line = (msg.content or "").strip().splitlines()[0] if msg.content else ""
    stem = first_line[:80] or f"{conv.title} — {msg.author}"
    return stem


def build_body(conv: Any, msg: Any, note: str | None = None) -> str:
    parts = [(msg.content or "").strip()]
    if note and note.strip():
        parts.append(note.strip())
    body = "\n\n".join(p for p in parts if p)
    return body + _PROVENANCE.format(
        title=conv.title,
        conversation_id=conv.id,
        message_id=msg.id,
        author=msg.author,
    )


# ---------------------------------------------------------------------------
# Credentials (server-side, per profile)
# ---------------------------------------------------------------------------


def _profile_env(profile: str) -> dict[str, str]:
    """The profile's encrypted env — the same resolution the MCP gateway uses
    to hand a server its credentials. Returns {} when the store is locked or
    the profile has no env, which surfaces as a clear "not configured" error
    rather than a stack trace."""
    from .gateway_pool import _profile_env as pool_profile_env

    return pool_profile_env(profile)


def brain_credentials(profile: str, vault: str) -> tuple[str, str]:
    """(api_base, bearer_token) for one (profile, vault). Raises
    ``PromoteError`` when either is unconfigured."""
    env = _profile_env(profile)
    api = (env.get(BRAIN_API_ENV) or "").strip().rstrip("/")
    token_env = TOKEN_ENV_FOR_VAULT[vault]
    token = (env.get(token_env) or "").strip()
    if not api:
        raise PromoteError(
            f"profile '{profile}' has no {BRAIN_API_ENV} configured — "
            "initialize its memory binding first"
        )
    if not token:
        raise PromoteError(
            f"profile '{profile}' has no {token_env} configured — "
            f"the {vault} vault has no token for this profile"
        )
    return api, token


# ---------------------------------------------------------------------------
# Downstream calls
# ---------------------------------------------------------------------------


async def brain_learn(
    *,
    api: str,
    token: str,
    title: str,
    body: str,
    tags: list[str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    """Write one record into the vault the token binds to; returns its SHA.

    The daemon re-derives the canonical SHA itself (the client's is advisory),
    so the content hash is sent to keep the request well-formed and to make a
    verbatim re-learn dedupe to the same identity — exactly what the desktop
    app's vault copy path does.
    """
    payload: dict[str, Any] = {
        "sha": hashlib.sha256(body.encode()).hexdigest(),
        "title": title,
        "body": body,
    }
    if tags:
        payload["tags"] = tags

    async with httpx.AsyncClient(timeout=LEARN_TIMEOUT_S, transport=transport) as client:
        try:
            resp = await client.post(
                api + LEARN_PATH,
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            raise PromoteError(f"brain daemon unreachable at {api}: {exc}") from exc

    if resp.status_code == 401:
        raise PromoteError("brain daemon rejected the vault token (401)")
    if resp.status_code >= 300:
        raise PromoteError(f"brain learn returned HTTP {resp.status_code}")

    try:
        data = resp.json()
    except ValueError:
        data = {}
    return str(data.get("sha") or payload["sha"])


async def _promote_to_vault(
    vault: str, *, profile: str, conv: Any, msg: Any, body: PromoteRequest
) -> PromoteResult:
    api, token = brain_credentials(profile, vault)
    tags = list(dict.fromkeys([*body.tags, "conversation", vault]))
    sha = await brain_learn(
        api=api,
        token=token,
        title=build_title(conv, msg, body.title),
        body=build_body(conv, msg, body.note),
        tags=tags,
    )
    return PromoteResult(
        ok=True,
        target=body.target,
        sha=sha,
        detail=f"written to the {vault} vault of profile '{profile}'",
    )


async def _promote_to_task(
    *, profile: str, conv: Any, msg: Any, body: PromoteRequest
) -> PromoteResult:
    from .router import submit_task

    description = build_body(conv, msg, body.note)
    try:
        task = await submit_task(
            description,
            body.agent_name,
            repo_url=body.repo_url,
            workspace_profile=profile,
        )
    except ValueError as exc:
        # Unknown agent / empty description — the caller's problem, not a bug.
        raise PromoteError(str(exc)) from exc
    return PromoteResult(
        ok=True,
        target=body.target,
        task_id=task.id,
        detail=f"submitted to agent '{body.agent_name}' in profile '{profile}'",
    )


async def promote_message(
    *, profile: str, conv: Any, msg: Any, body: PromoteRequest
) -> PromoteResult:
    """Dispatch one promotion. ``conv``/``msg`` MUST already have been loaded
    with ``profile`` in the store's filter — this function trusts that and uses
    ``profile`` for the downstream credentials and tenancy."""
    vault = VAULT_FOR_TARGET.get(body.target)
    if vault:
        result = await _promote_to_vault(vault, profile=profile, conv=conv, msg=msg, body=body)
    elif body.target == "task":
        result = await _promote_to_task(profile=profile, conv=conv, msg=msg, body=body)
    else:  # pragma: no cover - PromoteTarget is a closed Literal
        raise PromoteError(f"unknown promote target '{body.target}'")

    log.info(
        "conversation.promoted",
        metadata={
            "conversation_id": conv.id,
            "message_id": msg.id,
            "target": body.target,
            "profile": profile,
        },
    )
    return result

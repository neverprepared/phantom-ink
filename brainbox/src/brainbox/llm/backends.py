"""Backends for the ``brainbox.llm`` seam.

Each backend is one way to turn messages into a ``Completion``. The seam picks an
ordered chain (see ``strategy.py``) and tries them in turn.

Phase 1 backends:
  - ``OllamaBackend``     — free, self-hosted; wraps the existing ``ollama_pool``.
  - ``ClaudeApiBackend``  — Claude via API key; PAID, gated by ``allow_paid``.
  - ``ClaudeOAuthBackend``— session-backed completion under OAuth; free
                            (subscription). Relocates the ``create→query→stop``
                            flow behind the seam. Holds NO OAuth credential — it
                            delegates to the launcher plane's local HTTP API,
                            which owns ``.claude.json``.

Boundary invariant: this module never reads ``.claude.json``. Keyed backends hold
API keys; the OAuth backend holds nothing and calls the launcher API.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Callable, Protocol, runtime_checkable

from ..log import get_logger
from .types import CallCtx, Completion, LlmError, Messages, Usage

log = get_logger()


@runtime_checkable
class Backend(Protocol):
    name: str

    def estimates_cost(self) -> bool:
        """True for paid backends (per-token billing) — gated by ``allow_paid``."""
        ...

    async def complete(
        self, messages: Messages, *, model: str | None, ctx: CallCtx, profile: str
    ) -> Completion:
        ...


# --------------------------------------------------------------------------- #
# Ollama — free, self-hosted, default cheap tier                              #
# --------------------------------------------------------------------------- #


class OllamaBackend:
    name = "ollama"

    def estimates_cost(self) -> bool:
        return False

    async def complete(self, messages, *, model, ctx, profile):
        from ..ollama import OllamaError, achat
        from ..ollama_pool import get_pool

        pool = get_pool()
        inst = pool.pick(runner_name=ctx.runner)
        if inst is None:
            raise LlmError(
                "ollama: no healthy instance"
                + (f" for runner '{ctx.runner}'" if ctx.runner else "")
            )
        pool.acquire(inst)
        try:
            res = await achat(
                messages,
                model=model,
                base_url=inst.url,
                headers=inst.request_headers(),
                verify=inst.verify_tls,
            )
        except OllamaError as exc:
            raise LlmError(f"ollama: {exc}")
        finally:
            pool.release(inst)
        return Completion(
            text=res.message.content,
            backend=self.name,
            model=res.model,
            usage=Usage(tokens_out=res.eval_count),
        )


# --------------------------------------------------------------------------- #
# Claude via API key — PAID, gated by allow_paid                              #
# --------------------------------------------------------------------------- #


def _default_key_provider(profile: str) -> str:
    """Phase-1 key source: the ``CL_LLM__ANTHROPIC_API_KEY`` setting.

    Per-profile broker sourcing is the intended production refinement — inject a
    custom ``key_provider`` into ``ClaudeApiBackend`` that resolves the key from
    phantom-credentials for ``profile``. This default keeps the boundary honest
    (keys come from the request-plane config, never a session).
    """

    from ..config import settings

    return settings.llm.anthropic_api_key


def _estimate_cost(tokens_in: int, tokens_out: int) -> float:
    from ..config import settings

    rin = settings.llm.cost_per_mtok_in
    rout = settings.llm.cost_per_mtok_out
    if rin <= 0 and rout <= 0:
        return 0.0
    return (tokens_in / 1_000_000.0) * rin + (tokens_out / 1_000_000.0) * rout


class ClaudeApiBackend:
    """Claude via the Anthropic Messages API. Paid; only reached when
    ``allow_paid`` is set (both the strategy chain and ``complete()`` gate it).
    """

    name = "claude_api"

    def __init__(self, key_provider: Callable[[str], str] | None = None) -> None:
        self._key_provider = key_provider or _default_key_provider

    def estimates_cost(self) -> bool:
        return True

    async def complete(self, messages, *, model, ctx, profile):
        import httpx

        from ..config import settings

        key = self._key_provider(profile)
        if not key:
            raise LlmError("claude_api: no API key configured (CL_LLM__ANTHROPIC_API_KEY)")

        mdl = model or settings.llm.claude_api_model
        base = settings.llm.anthropic_base_url.rstrip("/")
        max_tokens = ctx.max_tokens or settings.llm.claude_api_max_tokens

        # Anthropic wants system prompts hoisted out of the turn list.
        system_parts = [m["content"] for m in messages if m.get("role") == "system"]
        turns = [
            {"role": m["role"], "content": m["content"]}
            for m in messages
            if m.get("role") != "system"
        ]
        body: dict = {"model": mdl, "max_tokens": max_tokens, "messages": turns}
        if system_parts:
            body["system"] = "\n\n".join(system_parts)

        headers = {
            "x-api-key": key,
            "anthropic-version": settings.llm.anthropic_version,
            "content-type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=ctx.timeout) as client:
                resp = await client.post(f"{base}/v1/messages", json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise LlmError(f"claude_api: transport error: {exc}")
        if resp.status_code != 200:
            raise LlmError(f"claude_api: HTTP {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        text = "".join(
            block.get("text", "")
            for block in data.get("content", [])
            if block.get("type") == "text"
        )
        usage = data.get("usage") or {}
        tin = int(usage.get("input_tokens", 0))
        tout = int(usage.get("output_tokens", 0))
        return Completion(
            text=text,
            backend=self.name,
            model=data.get("model", mdl),
            usage=Usage(
                tokens_in=tin,
                tokens_out=tout,
                cost_estimate_usd=_estimate_cost(tin, tout),
            ),
        )


# --------------------------------------------------------------------------- #
# Claude under OAuth — session-backed completion, free (subscription)         #
# --------------------------------------------------------------------------- #


def _load_local_api_key() -> str:
    from ..config import settings

    try:
        return settings.api_key_file.read_text().strip()
    except FileNotFoundError:
        return ""


class ClaudeOAuthBackend:
    """A single Claude Code query in an ephemeral OAuth session.

    Phase-1 impl = the relocated ``create → (wait) → query → stop → delete`` flow,
    driven through the local launcher API (``http://localhost:{api_port}``) with
    the brainbox API key. The *session* runs under OAuth inside its container;
    this backend never touches ``.claude.json``.

    Phase-2 will swap these internals for a warm pooled session — same backend,
    same seam, zero caller change.

    Because it drives a real Claude Code turn, the session may use tools — so this
    is also the faithful home for playbook-style tasks (not only bare prose).
    """

    name = "claude_oauth"

    def estimates_cost(self) -> bool:
        return False  # subscription, not per-token

    async def complete(self, messages, *, model, ctx, profile):
        import httpx

        from ..config import settings

        api_key = _load_local_api_key()
        base_url = f"http://localhost:{settings.api_port}"
        session_name = f"{ctx.session_prefix}-{uuid.uuid4().hex[:8]}"
        headers = {"X-API-Key": api_key}
        prompt = _compose_prompt(messages)

        try:
            async with httpx.AsyncClient(base_url=base_url, timeout=ctx.timeout + 60) as client:
                create_body: dict = {"name": session_name}
                if profile and profile != "global":
                    create_body["workspace_profile"] = profile
                    if ctx.workspace_home:
                        create_body["workspace_home"] = ctx.workspace_home
                if ctx.runner:
                    create_body["runner"] = ctx.runner
                resp = await client.post("/api/create", json=create_body, headers=headers)
                resp.raise_for_status()

                try:
                    # Runner-backed sessions signal readiness via exec; container
                    # tmux sessions need the readiness wait first.
                    if not ctx.runner:
                        await _wait_for_session(client, session_name, headers)
                    resp = await client.post(
                        f"/api/sessions/{session_name}/query",
                        json={"prompt": prompt, "timeout": int(ctx.timeout)},
                        headers=headers,
                    )
                    resp.raise_for_status()
                    payload = resp.json()
                    text = payload.get("output") or payload.get("response", "")
                finally:
                    try:
                        await client.post("/api/stop", json={"name": session_name}, headers=headers)
                        await client.post("/api/delete", json={"name": session_name}, headers=headers)
                    except Exception as cleanup_exc:  # cleanup is best-effort
                        log.warning(
                            "llm.claude_oauth.cleanup_failed",
                            metadata={"session": session_name, "reason": str(cleanup_exc)},
                        )
        except httpx.HTTPError as exc:
            raise LlmError(f"claude_oauth: session error: {exc}")

        return Completion(
            text=text,
            backend=self.name,
            model=model or "claude-code-oauth",
            usage=Usage(),  # the session /query API does not surface token usage
        )


def _compose_prompt(messages: Messages) -> str:
    """Flatten a message list into a single prompt for the session /query API.

    System content is prefaced; user/assistant turns follow. The common case
    (one user string) round-trips to just that string.
    """

    if len(messages) == 1 and messages[0].get("role") == "user":
        return messages[0].get("content", "")
    parts: list[str] = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "system":
            parts.append(content)
        elif role == "user":
            parts.append(content)
        else:  # assistant / other — label so context is preserved
            parts.append(f"[{role}] {content}")
    return "\n\n".join(p for p in parts if p)


async def _wait_for_session(client, session_name: str, headers: dict, max_wait: int = 120) -> None:
    """Poll until Claude Code's tmux session is ready inside the container.

    Mirrors ``playbooks._wait_for_session`` / ``loop_assist._wait_for_session`` —
    same fixture, same readiness contract.
    """

    deadline = asyncio.get_event_loop().time() + max_wait
    tmux_started = False
    while asyncio.get_event_loop().time() < deadline:
        try:
            resp = await client.post(
                f"/api/sessions/{session_name}/exec",
                json={"command": "echo alive"},
                headers=headers,
            )
            if resp.status_code != 200:
                await asyncio.sleep(3)
                continue
            if not tmux_started:
                await client.post(
                    f"/api/sessions/{session_name}/exec",
                    json={
                        "command": "tmux has-session -t main 2>/dev/null || "
                        "tmux new-session -d -s main 'claude --dangerously-skip-permissions'"
                    },
                    headers=headers,
                )
                tmux_started = True
                await asyncio.sleep(5)
            resp = await client.post(
                f"/api/sessions/{session_name}/exec",
                json={
                    "command": "tmux has-session -t main 2>/dev/null && echo claude_ready || echo waiting"
                },
                headers=headers,
            )
            if resp.status_code == 200 and "claude_ready" in resp.json().get("output", ""):
                return
        except Exception:
            pass
        await asyncio.sleep(3)
    raise LlmError(f"claude_oauth: session '{session_name}' not ready within {max_wait}s")

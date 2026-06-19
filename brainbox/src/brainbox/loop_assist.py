"""AI Assist for Loop template authoring — markdown edition.

Three operator-facing modes:

    Generate   — natural language → full markdown loop template.
                 Replaces the editor doc after operator confirms.
    Refine     — operator highlights a range, types an instruction,
                 AI returns a replacement. We assemble head + replacement
                 + tail and validate the WHOLE thing via ``loop_md.parse``
                 before returning.
    Explain    — operator highlights a range, asks a question, AI returns
                 a natural-language answer. No edit; popover UI.

Generate and Refine run a validate-and-retry loop. The output must
parse as a valid LoopMarkdown; on failure we feed the error back to the
model up to 3 times. Even an exhausted retry budget returns whatever
markdown the AI produced plus a warnings list — the operator can hand-fix.

**No API keys.** Per project convention (see top-level CLAUDE.md), this
module dispatches to an ephemeral brainbox session, not the Anthropic
API. The session is registered as a real hub task with ``role=worker``
+ ``task=<prompt>`` — visible in the Tasks panel, lifecycled through
``complete_task`` so the result flows back through the standard
worker-completion path. The structure mirrors the ratchet-worker
pattern; all retries within a single request share the one session.
"""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx

from .config import settings
from .log import get_logger
from .loop_md import LoopMarkdownError, parse as parse_loop_md

log = get_logger()


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class AssistError(RuntimeError):
    """Raised when the assist flow cannot complete — session provisioning
    failed, the upstream session errored, or any non-recoverable issue.
    The route handler surfaces these as HTTP 502."""


@dataclass
class AssistWarning:
    field: str | None
    message: str


@dataclass
class AssistResult:
    yaml: str = ""
    explanation: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    warnings: list[AssistWarning] = field(default_factory=list)
    retries: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "yaml": self.yaml,
            "explanation": self.explanation,
            "model": self.model,
            "tokens": {"input": self.input_tokens, "output": self.output_tokens},
            "cost_usd": round(self.cost_usd, 6),
            "warnings": [{"field": w.field, "message": w.message} for w in self.warnings],
            "retries": self.retries,
        }


# Session runs under the operator's OAuth credentials. Token usage isn't
# surfaced by the /query endpoint, so cost reporting is a no-op. Keep the
# field around for shape compatibility with the frontend.
def _cost(_model: str, _input_tokens: int, _output_tokens: int) -> float:
    return 0.0


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------


_HARD_RULES = """\
Hard rules — non-negotiable:

1. Output a COMPLETE markdown loop template. Frontmatter fenced with
   '---' lines, then named '# ' sections. No prose outside this shape.
2. Frontmatter MUST include: name (slug), trigger (free-form string),
   max_iterations (positive integer).
3. Frontmatter MAY include: agent (defaults to name), permissions
   (inherit|default|strict; default: "default"), budget_usd (positive
   number), objective (mapping of envelope path → expected value),
   required_refs (list of {name, type: int|string|sha, required?}).
4. Body MUST include these top-level sections, in this order:
       # Role
       # When to stop
       # When to escalate
   Body MAY include: # Tools, # Notes.
5. The "When to stop" and "When to escalate" sections are PROSE
   checklists evaluated each iteration by a separate judge agent. Be
   concrete and verifiable: "CI is green on the head commit", not
   "the code is good". The judge defaults to NOT firing on ambiguity.
6. The "objective" frontmatter block holds CHEAP DETERMINISTIC checks
   that short-circuit the judge. Each entry is an envelope path → an
   expected literal (equality), a truthiness flag (true|false), or an
   operator dict ({"<=": N}, {">=": N}, {"in": [...]}, {"not_empty": true}).
   If every objective check passes, the loop converges without paying
   for a judge call. Use objective for the cheap stuff (CI status,
   simple counters); use prose for the qualitative stuff.
7. Permissions default to "default". Use "strict" only when the loop
   runs against attacker-controllable input.
"""


def _canonical_example() -> str:
    """Read the bundled pr-review-loop template as an in-context example.
    Falls back to empty if the file isn't there."""
    try:
        from .loop_template import _builtin_templates_dir  # type: ignore[attr-defined]

        path = _builtin_templates_dir() / "pr-review-loop.md"
        if path.is_file():
            return path.read_text()
    except Exception:
        pass
    return ""


def build_system_prompt(mode: str) -> str:
    """Compose the model's system prompt for the given mode."""
    example = _canonical_example()

    if mode == "explain":
        return (
            "You are a documentation assistant for phantom-ink loop templates.\n"
            "A loop template is a markdown file with YAML frontmatter plus "
            "named prose sections. Each iteration, a separate judge agent "
            "reads the 'When to stop' / 'When to escalate' sections against "
            "the latest envelope. Respond with a clear, brief natural-language "
            "explanation of whatever the operator highlighted or asked about. "
            "No markdown fences, no template syntax — just the explanation."
        )

    return (
        "You are an authoring assistant for phantom-ink loop templates.\n"
        "A loop template is a markdown file with YAML frontmatter plus "
        "named prose sections. Each iteration, a judge agent reads the "
        "prose sections against the envelope to decide stop / escalate.\n\n"
        f"{_HARD_RULES}\n"
        "Canonical example — the bundled pr-review-loop template:\n\n"
        f"{example}\n\n"
        "Output ONLY the full markdown template for the requested loop. "
        "No wrapping fences, no commentary."
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_markdown(raw: str) -> tuple[bool, list[AssistWarning]]:
    """Check whether the raw text parses as a valid LoopMarkdown."""
    warnings: list[AssistWarning] = []
    try:
        parse_loop_md(raw)
    except LoopMarkdownError as exc:
        warnings.append(AssistWarning(field=None, message=str(exc)))
        return False, warnings
    return True, warnings


# Kept as an alias for the existing test fixtures during the cutover.
_validate_yaml = _validate_markdown


# ---------------------------------------------------------------------------
# Session-backed LLM call
# ---------------------------------------------------------------------------


_SESSION_LABEL = "brainbox-session"
_QUERY_TIMEOUT = 300


def _load_api_key() -> str:
    try:
        return settings.api_key_file.read_text().strip()
    except FileNotFoundError:
        return ""


async def _wait_for_session(client: httpx.AsyncClient, session_name: str, max_wait: int = 120) -> None:
    """Poll until Claude Code's tmux session is ready inside the container.
    Mirrors playbooks._wait_for_session — same fixture, same readiness
    semantics."""
    deadline = asyncio.get_event_loop().time() + max_wait
    tmux_started = False

    while asyncio.get_event_loop().time() < deadline:
        try:
            resp = await client.post(
                f"/api/sessions/{session_name}/exec",
                json={"command": "echo alive"},
            )
            if resp.status_code != 200:
                await asyncio.sleep(3)
                continue

            if not tmux_started:
                await client.post(
                    f"/api/sessions/{session_name}/exec",
                    json={"command": "tmux has-session -t main 2>/dev/null || tmux new-session -d -s main 'claude --dangerously-skip-permissions'"},
                )
                tmux_started = True
                await asyncio.sleep(5)

            resp = await client.post(
                f"/api/sessions/{session_name}/exec",
                json={"command": "tmux has-session -t main 2>/dev/null && echo claude_ready || echo waiting"},
            )
            if resp.status_code == 200:
                output = resp.json().get("output", "")
                if "claude_ready" in output:
                    return

        except Exception:
            pass

        await asyncio.sleep(3)

    raise AssistError(f"assist session '{session_name}' did not become ready within {max_wait}s")


async def _call_session(
    client: httpx.AsyncClient,
    session_name: str,
    *,
    system: str,
    user: str,
) -> dict[str, Any]:
    """Send one prompt to a ready brainbox session and return its
    response. The session /query endpoint has no system-role concept,
    so we concatenate system + user with a separator. Returns
    {text, input_tokens, output_tokens} — token counts are 0 because
    the session API doesn't surface them."""
    full_prompt = f"{system}\n\n--- operator request ---\n\n{user}"
    try:
        resp = await client.post(
            f"/api/sessions/{session_name}/query",
            json={"prompt": full_prompt, "timeout": _QUERY_TIMEOUT},
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise AssistError(f"upstream session call failed: {exc}") from exc

    body = resp.json()
    text = body.get("response") or body.get("output") or ""
    return {
        "text": text.strip(),
        "input_tokens": 0,
        "output_tokens": 0,
    }


async def _with_assist_session(
    fn: Callable[[httpx.AsyncClient, str], Awaitable[AssistResult]],
    *,
    operator_prompt: str,
) -> AssistResult:
    """Create a worker-role brainbox session registered as a hub task,
    run ``fn(client, session_name)``, mark the hub task completed with
    the assist result, then clean up.

    Worker-pattern shape (mirrors ratchet workers):
      - role=worker, task=<short description> so the session appears in
        the Tasks panel as a real task.
      - When ``fn`` returns, the hub task is finalized via
        ``complete_task(task_id, result.yaml or result.explanation)`` so
        the output flows through the standard task-completion path. On
        failure the task is marked FAILED with the exception message.
      - All retries within one assist request share this one session;
        the session sees its own earlier output when iterating on
        corrections.
    """
    from . import router

    api_key = _load_api_key()
    if not api_key:
        raise AssistError("brainbox api_key not available — cannot dispatch assist session")

    base_url = f"http://localhost:{settings.api_port}"
    session_name = f"loop-assist-{secrets.token_hex(3)}"
    headers = {"X-API-Key": api_key}

    # Truncate the operator prompt for the Tasks-panel description so the
    # row reads at a glance.
    short_desc = operator_prompt.strip().splitlines()[0] if operator_prompt.strip() else "loop AI Assist"
    if len(short_desc) > 120:
        short_desc = short_desc[:117] + "…"

    async with httpx.AsyncClient(base_url=base_url, timeout=600.0, headers=headers) as client:
        try:
            create_body = {
                "name": session_name,
                "role": "worker",
                "task": f"loop AI Assist: {short_desc}",
            }
            resp = await client.post("/api/create", json=create_body)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise AssistError(f"could not create assist session: {exc}") from exc

        # The /api/create handler registers a hub task whose session_name
        # matches our session. Find it so we can complete it structurally.
        hub_task_id = _find_hub_task_for_session(session_name)

        result: AssistResult | None = None
        failure: BaseException | None = None
        try:
            await _wait_for_session(client, session_name)
            result = await fn(client, session_name)
            return result
        except BaseException as exc:
            failure = exc
            raise
        finally:
            # Finalize the hub task before tearing the session down so the
            # row in the Tasks panel reflects the assist outcome.
            if hub_task_id is not None:
                try:
                    if result is not None and failure is None:
                        payload = result.yaml or result.explanation or ""
                        await router.complete_task(hub_task_id, payload)
                    else:
                        await router.fail_task(
                            hub_task_id,
                            error=str(failure) if failure else "assist failed",
                        )
                except Exception as task_exc:
                    log.warning(
                        "loop_assist.task_finalize_failed",
                        metadata={"task_id": hub_task_id, "reason": str(task_exc)},
                    )

            try:
                await client.post("/api/stop", json={"name": session_name})
                await client.post("/api/delete", json={"name": session_name})
            except Exception as cleanup_exc:
                log.warning(
                    "loop_assist.session_cleanup_failed",
                    metadata={"session": session_name, "reason": str(cleanup_exc)},
                )


def _find_hub_task_for_session(session_name: str) -> str | None:
    """Look up the hub task /api/create registered for our session. The
    task is created with ``session_name=session_name`` so a single
    linear scan over the in-memory task store finds it. Returns the
    task_id or None if not found (e.g. caller did not pass a ``task``
    in the create body)."""
    from . import router

    for tid, task in router._tasks.items():
        if getattr(task, "session_name", None) == session_name:
            return tid
    return None


def _strip_fences(text: str) -> str:
    """Strip code fences that wrap the markdown output. Models sometimes
    pad despite the system prompt; cheaper to clean than to retry."""
    t = text.strip()
    if t.startswith("```"):
        lines = t.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        t = "\n".join(lines)
    return t.strip()


# ---------------------------------------------------------------------------
# Validate-and-retry loop
# ---------------------------------------------------------------------------


_MAX_RETRIES = 3


async def _generate(prompt: str, current_yaml: str | None) -> AssistResult:
    system = build_system_prompt("generate")
    user = _generate_user_prompt(prompt, current_yaml)
    return await _with_assist_session(
        lambda client, name: _retry_loop(client, name, system=system, user=user),
        operator_prompt=f"generate — {prompt}",
    )


async def _refine(prompt: str, current_yaml: str, selection: dict[str, int]) -> AssistResult:
    lines = current_yaml.splitlines()
    start = max(0, selection.get("start_line", 1) - 1)
    end = min(len(lines), selection.get("end_line", start + 1))
    head_lines = lines[:start]
    selected_lines = lines[start:end]
    tail_lines = lines[end:]
    selected = "\n".join(selected_lines)

    system = build_system_prompt("refine")
    user = _refine_user_prompt(prompt, head_lines, selected, tail_lines)

    def _assemble(replacement: str) -> str:
        return "\n".join([*head_lines, replacement, *tail_lines])

    return await _with_assist_session(
        lambda client, name: _retry_loop(client, name, system=system, user=user, assemble=_assemble),
        operator_prompt=f"refine — {prompt}",
    )


async def _explain(prompt: str, current_yaml: str, selection: dict[str, int] | None) -> AssistResult:
    system = build_system_prompt("explain")
    excerpt = ""
    if selection and current_yaml:
        lines = current_yaml.splitlines()
        start = max(0, selection.get("start_line", 1) - 1)
        end = min(len(lines), selection.get("end_line", start + 1))
        excerpt = "\n".join(lines[start:end])
    user = _explain_user_prompt(prompt, excerpt)

    async def _run(client: httpx.AsyncClient, name: str) -> AssistResult:
        resp = await _call_session(client, name, system=system, user=user)
        return AssistResult(
            explanation=resp["text"],
            model=_SESSION_LABEL,
            input_tokens=resp["input_tokens"],
            output_tokens=resp["output_tokens"],
            cost_usd=0.0,
        )

    return await _with_assist_session(_run, operator_prompt=f"explain — {prompt}")


async def _retry_loop(
    client: httpx.AsyncClient,
    session_name: str,
    *,
    system: str,
    user: str,
    assemble: Callable[[str], str] | None = None,
) -> AssistResult:
    """Validate-and-retry. Calls the session, validates the output (or
    the assembled output if ``assemble`` is passed for Refine), feeds
    errors back on failure. Returns the latest YAML even when retries
    exhaust — operator can hand-fix."""
    aggregate_input = 0
    aggregate_output = 0
    last_yaml = ""
    last_warnings: list[AssistWarning] = []

    current_user = user
    for attempt in range(_MAX_RETRIES + 1):
        resp = await _call_session(client, session_name, system=system, user=current_user)
        aggregate_input += resp["input_tokens"]
        aggregate_output += resp["output_tokens"]
        raw = _strip_fences(resp["text"])
        last_yaml = assemble(raw) if assemble else raw

        ok, warnings = _validate_yaml(last_yaml)
        if ok:
            return AssistResult(
                yaml=last_yaml,
                model=_SESSION_LABEL,
                input_tokens=aggregate_input,
                output_tokens=aggregate_output,
                cost_usd=0.0,
                retries=attempt,
            )
        last_warnings = warnings
        if attempt >= _MAX_RETRIES:
            break
        error_summary = "\n".join(
            f"- {(w.field + ': ') if w.field else ''}{w.message}" for w in warnings
        )
        current_user = (
            f"Your previous output had these errors:\n{error_summary}\n\n"
            "Produce a corrected version. Same hard rules apply."
        )

    return AssistResult(
        yaml=last_yaml,
        model=_SESSION_LABEL,
        input_tokens=aggregate_input,
        output_tokens=aggregate_output,
        cost_usd=0.0,
        warnings=last_warnings,
        retries=_MAX_RETRIES,
    )


def _generate_user_prompt(prompt: str, current_yaml: str | None) -> str:
    if current_yaml:
        return (
            f"Operator request: {prompt}\n\n"
            "The editor currently contains this template — produce a "
            "replacement informed by it but matching the new request:\n\n"
            f"{current_yaml}"
        )
    return f"Operator request: {prompt}\n\nProduce a complete LoopSpec YAML."


def _refine_user_prompt(
    prompt: str,
    head_lines: list[str],
    selected: str,
    tail_lines: list[str],
) -> str:
    head_excerpt = "\n".join(head_lines[-10:]) if head_lines else "(no preceding content)"
    tail_excerpt = "\n".join(tail_lines[:10]) if tail_lines else "(no following content)"
    return (
        f"Operator instruction: {prompt}\n\n"
        "Selected region (REPLACE this exact range with your output):\n"
        "```\n"
        f"{selected}\n"
        "```\n\n"
        "Preceding context (do NOT include in your output):\n"
        "```\n"
        f"{head_excerpt}\n"
        "```\n\n"
        "Following context (do NOT include in your output):\n"
        "```\n"
        f"{tail_excerpt}\n"
        "```\n\n"
        "Output ONLY the replacement for the selected region. The caller "
        "will stitch your output between the preceding and following "
        "context and re-validate the whole document."
    )


def _explain_user_prompt(prompt: str, excerpt: str) -> str:
    if excerpt:
        return f"Operator question: {prompt}\n\nHighlighted YAML:\n```\n{excerpt}\n```"
    return f"Operator question: {prompt}"


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


async def assist(
    *,
    mode: str,
    prompt: str,
    current_yaml: str | None = None,
    selection: dict[str, int] | None = None,
) -> AssistResult:
    """Route a single AI Assist request to the right mode handler.

    Modes: 'generate' | 'refine' | 'explain'.
    Refine requires both ``current_yaml`` and ``selection``.
    """
    if not prompt or not prompt.strip():
        raise AssistError("prompt is required")

    if mode == "generate":
        return await _generate(prompt, current_yaml)
    if mode == "refine":
        if not current_yaml or not selection:
            raise AssistError("refine requires current_yaml and selection")
        return await _refine(prompt, current_yaml, selection)
    if mode == "explain":
        return await _explain(prompt, current_yaml or "", selection)
    raise AssistError(f"unknown mode: {mode!r}")

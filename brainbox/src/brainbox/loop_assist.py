"""AI Assist for Loop template authoring (loop-spec PR 6).

Three operator-facing modes drive one server-side path:

    Generate   — natural language → full LoopSpec YAML. The result
                 replaces the editor doc after operator confirms.
    Refine     — operator highlights a YAML range, types an instruction,
                 AI returns a replacement for the range. We assemble the
                 full doc (head + replacement + tail) and validate it
                 against LoopSpec before returning, so the editor never
                 sees malformed output.
    Explain    — operator highlights a range, asks a question, AI returns
                 a natural-language answer. No edit; popover UI.

Generate and Refine run a validate-and-retry loop server-side. The
output must parse as YAML AND validate against LoopSpec; if either
fails, we feed the error back to the model up to 3 times before giving
up. Even an exhausted retry budget returns whatever YAML the AI produced
plus a warnings list — the operator can hand-fix; we never waste the
call.

Explain skips validation (it's prose).

**No API keys.** Per project convention (see top-level CLAUDE.md), this
module does NOT call the Anthropic API directly. Every LLM round-trip
goes through an ephemeral brainbox session — same pattern as
``playbooks._run_task``. The session runs Claude Code under the
operator's existing OAuth credentials. All retries within a single
assist request share the same session for latency and so the model
sees prior context.
"""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx
import yaml as yaml_module
from pydantic import ValidationError

from .config import settings
from .log import get_logger
from .loops import LoopSpec

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

1. Output ONLY raw YAML for the LoopSpec frontmatter. No prose explanation.
2. Do NOT include the surrounding '---' fences. The caller adds them.
3. The output MUST include either intent.convergence (a JMESPath bool
   expression) OR a top-level convergence_predicate. A template without
   convergence cannot load and will be rejected.
4. The output MUST include max_iterations (integer >= 1) and body.nodes
   (non-empty list). Day 1 the runner only executes body.nodes[0].
5. Every Node MUST declare an executor ("agent" | "playbook" | "join" |
   "human" | "schedule") and either an agent_id or a role.
6. Use only valid JMESPath in convergence_predicate, convergence_metric,
   edge predicates, stop_conditions[].predicate. Common shapes:
     length(findings.blockers) == `0`
     observations.ci_status == 'green'
     length(findings.blockers) == `0` && observations.ci_status == 'green'
     observations.diff_lines > `500`
7. Permission tier defaults to "default" — use "strict" only when the
   loop runs against attacker-controllable input. Use "inherit" only
   for trusted internal loops.
"""


def _canonical_example_yaml() -> str:
    """Read the bundled pr-review-loop template as an in-context example.
    Falls back to a small inline example if the file isn't there."""
    try:
        from .loop_template import _builtin_templates_dir  # type: ignore[attr-defined]

        path = _builtin_templates_dir() / "pr-review-loop.md"
        if path.is_file():
            text = path.read_text()
            if text.startswith("---"):
                rest = text[3:].lstrip("\n")
                end = rest.find("\n---")
                if end != -1:
                    return rest[:end]
    except Exception:
        pass
    return ""


def build_system_prompt(mode: str) -> str:
    """Compose the model's system prompt for the given mode."""
    schema = LoopSpec.model_json_schema()
    schema_summary = yaml_module.safe_dump(_simplify_schema(schema), sort_keys=False)
    example = _canonical_example_yaml()

    if mode == "explain":
        return (
            "You are a code review assistant for phantom-ink Loop templates.\n"
            "A Loop template is a YAML document that drives a phantom-ink "
            "loop-engineering runtime. Convergence is a JMESPath predicate "
            "evaluated against a HandoffEnvelope after each iteration. "
            "Respond with a clear, brief natural-language explanation of "
            "whatever the operator highlighted or asked about. No YAML, no "
            "markdown fences, just the explanation."
        )

    return (
        "You are an authoring assistant for phantom-ink Loop templates.\n"
        "A Loop template is a YAML document that drives the loop-engineering "
        "runtime. Convergence is a JMESPath predicate evaluated against a "
        "HandoffEnvelope after each iteration.\n\n"
        f"{_HARD_RULES}\n"
        "LoopSpec schema (summarized):\n"
        f"{schema_summary}\n\n"
        "Canonical example — pr-review-loop:\n"
        f"---\n{example}\n---\n\n"
        "Output ONLY the YAML frontmatter for the requested template, no "
        "wrapping fences, no commentary."
    )


def _simplify_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Strip the LoopSpec JSON Schema down to the fields a model needs."""
    out: dict[str, Any] = {}
    for key in ("type", "required"):
        if key in schema:
            out[key] = schema[key]
    if "properties" in schema:
        out["properties"] = {}
        for name, prop in schema["properties"].items():
            slim: dict[str, Any] = {}
            for k in ("type", "description", "default", "enum"):
                if k in prop:
                    slim[k] = prop[k]
            out["properties"][name] = slim
    return out


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_yaml(raw: str) -> tuple[bool, list[AssistWarning]]:
    """Check whether raw YAML parses as a LoopSpec."""
    warnings: list[AssistWarning] = []
    try:
        data = yaml_module.safe_load(raw) or {}
    except yaml_module.YAMLError as exc:
        warnings.append(AssistWarning(field=None, message=f"YAML parse error: {exc}"))
        return False, warnings
    if not isinstance(data, dict):
        warnings.append(AssistWarning(field=None, message="output must be a YAML mapping"))
        return False, warnings
    try:
        LoopSpec.model_validate(data)
    except ValidationError as exc:
        for err in exc.errors():
            field_path = ".".join(str(p) for p in err.get("loc", ()))
            warnings.append(AssistWarning(field=field_path or None, message=err.get("msg", "validation error")))
        return False, warnings
    except ValueError as exc:
        warnings.append(AssistWarning(field=None, message=str(exc)))
        return False, warnings
    return True, warnings


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
) -> AssistResult:
    """Create an ephemeral session, run fn(client, session_name), clean up.

    All LLM round-trips for a single assist request share this one session
    — cheaper than re-provisioning per retry, and the session sees its own
    earlier output when iterating on corrections."""
    api_key = _load_api_key()
    if not api_key:
        raise AssistError("brainbox api_key not available — cannot dispatch assist session")

    base_url = f"http://localhost:{settings.api_port}"
    session_name = f"loop-assist-{secrets.token_hex(3)}"
    headers = {"X-API-Key": api_key}

    async with httpx.AsyncClient(base_url=base_url, timeout=600.0, headers=headers) as client:
        try:
            resp = await client.post("/api/create", json={"name": session_name})
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise AssistError(f"could not create assist session: {exc}") from exc

        try:
            await _wait_for_session(client, session_name)
            return await fn(client, session_name)
        finally:
            try:
                await client.post("/api/stop", json={"name": session_name})
                await client.post("/api/delete", json={"name": session_name})
            except Exception as cleanup_exc:
                log.warning(
                    "loop_assist.session_cleanup_failed",
                    metadata={"session": session_name, "reason": str(cleanup_exc)},
                )


def _strip_fences(text: str) -> str:
    """Strip code fences that wrap the YAML. Models sometimes add them
    despite the system prompt; cheaper to clean than to retry."""
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
        lambda client, name: _retry_loop(client, name, system=system, user=user)
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
        lambda client, name: _retry_loop(client, name, system=system, user=user, assemble=_assemble)
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

    return await _with_assist_session(_run)


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

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

Explain skips validation (it's prose) and runs on a cheaper haiku model
by default.

Configured via Settings.anthropic_api_key + loop_assist_model +
loop_assist_explain_model. Missing API key → AssistError, surfaced as
HTTP 503 in the route. The editor stays fully functional without AI;
operators can still author YAML by hand.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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
    """Raised when the assist flow cannot complete — missing API key, the
    LLM client errored, or any non-recoverable issue. The route handler
    surfaces these as HTTP 503 (config) or 502 (upstream)."""


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


# Approximate per-million-token USD pricing. Used for the operator-facing
# cost ticker — accuracy is not critical, ballpark is. Update if Anthropic
# changes prices.
_PRICE_PER_M: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-4-7": (3.00, 15.00),
    "claude-opus-4-7": (15.00, 75.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-haiku-4-5-20251001": (1.00, 5.00),
}


def _cost(model: str, input_tokens: int, output_tokens: int) -> float:
    in_per_m, out_per_m = _PRICE_PER_M.get(model, (3.00, 15.00))
    return (input_tokens * in_per_m + output_tokens * out_per_m) / 1_000_000


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
            # Extract frontmatter so the example is just YAML, not the markdown body.
            if text.startswith("---"):
                rest = text[3:].lstrip("\n")
                end = rest.find("\n---")
                if end != -1:
                    return rest[:end]
    except Exception:
        pass
    return ""


def build_system_prompt(mode: str) -> str:
    """Compose the model's system prompt for the given mode. Sonnet-class
    models get the full schema + example + JMESPath palette; Explain
    runs on haiku and skips the schema (it's a read task, not an authoring
    task)."""
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
    """Strip the LoopSpec JSON Schema down to the fields a model needs to
    author — properties, descriptions, types. Drops the $defs noise that
    would blow up the prompt.
    """
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
    """Check whether raw YAML parses as a LoopSpec. Returns (ok, warnings).
    Warnings carry field paths so the system prompt's retry can pinpoint
    the field that failed."""
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
# Anthropic client — thin wrapper, mockable
# ---------------------------------------------------------------------------


def _call_anthropic(*, model: str, system: str, user: str, max_tokens: int = 4096) -> dict[str, Any]:
    """Single API call to Anthropic Messages API. Returns
    {text, input_tokens, output_tokens}. Raises AssistError on missing
    API key or upstream failure.
    """
    if not settings.anthropic_api_key:
        raise AssistError("anthropic_api_key not configured — set CL_ANTHROPIC_API_KEY")

    try:
        from anthropic import Anthropic
    except ImportError as exc:  # pragma: no cover — dep is declared, present in installs
        raise AssistError(f"anthropic SDK not available: {exc}")

    client = Anthropic(api_key=settings.anthropic_api_key)
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
    except Exception as exc:
        raise AssistError(f"upstream LLM call failed: {exc}") from exc

    text_parts: list[str] = []
    for block in resp.content or []:
        text = getattr(block, "text", None)
        if text:
            text_parts.append(text)
    text = "".join(text_parts).strip()
    # Fence stripping happens in the retry loop so a test that mocks
    # _call_anthropic still benefits from the cleanup.

    usage = resp.usage
    return {
        "text": text,
        "input_tokens": getattr(usage, "input_tokens", 0),
        "output_tokens": getattr(usage, "output_tokens", 0),
    }


def _strip_fences(text: str) -> str:
    """Strip code fences that wrap the YAML. Models sometimes add them
    despite the system prompt; cheaper to clean than to retry."""
    t = text.strip()
    if t.startswith("```"):
        # Find end fence
        lines = t.split("\n")
        # Drop the first line (```yaml or ```) and the last fence line if present.
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


def _generate(prompt: str, current_yaml: str | None) -> AssistResult:
    """Mode: generate. The operator describes what they want; we ask the
    model for a full LoopSpec, validate, and retry on failure."""
    model = settings.loop_assist_model
    system = build_system_prompt("generate")
    user = _generate_user_prompt(prompt, current_yaml)
    return _retry_loop(model=model, system=system, user=user)


def _refine(prompt: str, current_yaml: str, selection: dict[str, int]) -> AssistResult:
    """Mode: refine. Operator highlighted a YAML range and gave an
    instruction. We ask the model for a replacement for the range, then
    stitch head + replacement + tail and validate the WHOLE thing."""
    head_lines: list[str] = []
    tail_lines: list[str] = []
    selected_lines: list[str] = []

    lines = current_yaml.splitlines()
    start = max(0, selection.get("start_line", 1) - 1)
    end = min(len(lines), selection.get("end_line", start + 1))
    head_lines = lines[:start]
    selected_lines = lines[start:end]
    tail_lines = lines[end:]
    selected = "\n".join(selected_lines)

    model = settings.loop_assist_model
    system = build_system_prompt("refine")
    user = _refine_user_prompt(prompt, head_lines, selected, tail_lines)

    # The retry loop reassembles the document with each model reply and
    # validates the full doc — never just the fragment.
    def _assemble(replacement: str) -> str:
        return "\n".join([*head_lines, replacement, *tail_lines])

    return _retry_loop(
        model=model,
        system=system,
        user=user,
        assemble=_assemble,
    )


def _explain(prompt: str, current_yaml: str, selection: dict[str, int] | None) -> AssistResult:
    """Mode: explain. No validation, cheaper model. Returns text in the
    ``explanation`` field instead of ``yaml``."""
    model = settings.loop_assist_explain_model
    system = build_system_prompt("explain")
    excerpt = ""
    if selection and current_yaml:
        lines = current_yaml.splitlines()
        start = max(0, selection.get("start_line", 1) - 1)
        end = min(len(lines), selection.get("end_line", start + 1))
        excerpt = "\n".join(lines[start:end])
    user = _explain_user_prompt(prompt, excerpt)

    resp = _call_anthropic(model=model, system=system, user=user)
    return AssistResult(
        explanation=resp["text"],
        model=model,
        input_tokens=resp["input_tokens"],
        output_tokens=resp["output_tokens"],
        cost_usd=_cost(model, resp["input_tokens"], resp["output_tokens"]),
    )


def _retry_loop(
    *,
    model: str,
    system: str,
    user: str,
    assemble: Any = None,
) -> AssistResult:
    """Generic validate-and-retry loop. Calls the model, validates the
    output (or the assembled output if ``assemble`` is passed for Refine),
    feeds errors back on failure. Returns the latest YAML even when retries
    exhaust — operator can hand-fix.
    """
    aggregate_input = 0
    aggregate_output = 0
    last_yaml = ""
    last_warnings: list[AssistWarning] = []

    current_user = user
    for attempt in range(_MAX_RETRIES + 1):
        resp = _call_anthropic(model=model, system=system, user=current_user)
        aggregate_input += resp["input_tokens"]
        aggregate_output += resp["output_tokens"]
        raw = _strip_fences(resp["text"])
        last_yaml = assemble(raw) if assemble else raw

        ok, warnings = _validate_yaml(last_yaml)
        if ok:
            return AssistResult(
                yaml=last_yaml,
                model=model,
                input_tokens=aggregate_input,
                output_tokens=aggregate_output,
                cost_usd=_cost(model, aggregate_input, aggregate_output),
                retries=attempt,
            )
        last_warnings = warnings
        if attempt >= _MAX_RETRIES:
            break
        # Feed the errors back into the next prompt
        error_summary = "\n".join(
            f"- {(w.field + ': ') if w.field else ''}{w.message}" for w in warnings
        )
        current_user = (
            f"Your previous output had these errors:\n{error_summary}\n\n"
            "Produce a corrected version. Same hard rules apply."
        )

    return AssistResult(
        yaml=last_yaml,
        model=model,
        input_tokens=aggregate_input,
        output_tokens=aggregate_output,
        cost_usd=_cost(model, aggregate_input, aggregate_output),
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


def assist(
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
        return _generate(prompt, current_yaml)
    if mode == "refine":
        if not current_yaml or not selection:
            raise AssistError("refine requires current_yaml and selection")
        return _refine(prompt, current_yaml, selection)
    if mode == "explain":
        return _explain(prompt, current_yaml or "", selection)
    raise AssistError(f"unknown mode: {mode!r}")

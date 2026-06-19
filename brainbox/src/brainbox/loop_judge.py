"""Judge agent — decides stop / escalate against the loop's prose
sections each iteration.

Two layers, evaluated in order:

  1. Objective: cheap, deterministic, runs in-process. Frontmatter
     ``objective:`` is a dict of envelope-path → expected-shape. If any
     entry is satisfied, the loop converges immediately — no LLM call.
     Supported shapes:

         key: value                     # equality (truthiness for True)
         key: {">=": 10}                # numeric comparison
         key: {"<=": 500}
         key: {">": 0}
         key: {"<": 100}
         key: {"in": ["a", "b"]}
         key: {"not_empty": true}       # length(value) > 0

     Paths are dotted into ``envelope`` (e.g. ``observations.ci_status``).
     Missing keys are silently false.

  2. Prose: the body's ``# When to stop`` and ``# When to escalate``
     sections are handed to a small judge agent (Haiku-class brainbox
     session) along with the envelope. The judge returns JSON
     ``{done: bool, reason: str}`` / ``{escalate: bool, reason: str}``.

No API keys. Same session dispatch path as ``loop_assist`` — see top-level
CLAUDE.md "No API Keys for Agents".
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import httpx

from .config import settings
from .log import get_logger

log = get_logger()


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass
class JudgeVerdict:
    fired: bool
    reason: str
    via: str   # "objective" | "judge" | "missing" — for telemetry


class JudgeError(RuntimeError):
    """Session-side failure. Caller decides whether to treat as keep-iterating
    or terminate."""


# ---------------------------------------------------------------------------
# Objective evaluation — fast path
# ---------------------------------------------------------------------------


def _walk(data: Any, path: str) -> Any:
    """Dotted lookup. Returns None for missing keys (no exception)."""
    cur = data
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _eval_objective_entry(value: Any, expected: Any) -> bool:
    """Compare one envelope value against one objective entry.
    Operator shapes documented in module docstring."""
    if isinstance(expected, dict) and expected:
        # Comparison shape — pick one operator.
        for op, target in expected.items():
            if op == ">=":
                return isinstance(value, (int, float)) and value >= target
            if op == "<=":
                return isinstance(value, (int, float)) and value <= target
            if op == ">":
                return isinstance(value, (int, float)) and value > target
            if op == "<":
                return isinstance(value, (int, float)) and value < target
            if op == "in":
                return value in (target or [])
            if op == "not_empty":
                if target is False:
                    return value in (None, "", [], {}, 0)
                return value not in (None, "", [], {}, 0)
        return False
    if expected is True:
        return bool(value)
    if expected is False:
        return not value
    return value == expected


def eval_objective(envelope: dict[str, Any], objective: dict[str, Any]) -> JudgeVerdict:
    """Run all objective checks. If ALL pass, the verdict fires. If any
    check is absent or fails, the verdict does NOT fire and the runner
    falls through to the prose judge.

    Why "all must pass": the operator wrote multiple checks because every
    one is necessary. Same semantics as a checklist."""
    if not objective:
        return JudgeVerdict(fired=False, reason="no objective checks", via="missing")

    failed = []
    for path, expected in objective.items():
        actual = _walk(envelope, path)
        if not _eval_objective_entry(actual, expected):
            failed.append(path)

    if failed:
        return JudgeVerdict(
            fired=False,
            reason=f"objective unmet: {', '.join(failed)}",
            via="objective",
        )
    return JudgeVerdict(
        fired=True,
        reason="all objective checks met",
        via="objective",
    )


# ---------------------------------------------------------------------------
# Prose judge — slow path, dispatched to a brainbox session
# ---------------------------------------------------------------------------


_SESSION_QUERY_TIMEOUT = 180

_STOP_SYSTEM = """\
You are a STOP judge for a phantom-ink loop. You are NOT the worker
agent — you read its output and decide if the loop's goal has been met.

You will be given:
  1. The loop's "When to stop" checklist (prose, written by the operator)
  2. The latest HandoffEnvelope as JSON

Your job: decide whether ALL stop conditions are satisfied. Default to
NOT done if uncertain — false positives end the loop early. False
negatives just cost one more iteration.

Output: a single JSON object on one line, nothing else:
  {"done": true|false, "reason": "<one short sentence>"}
"""

_ESCALATE_SYSTEM = """\
You are an ESCALATION judge for a phantom-ink loop. You are NOT the
worker agent — you read its output and decide if a human must be
paged.

You will be given:
  1. The loop's "When to escalate" checklist (prose, written by the operator)
  2. The latest HandoffEnvelope as JSON
  3. The current iteration count and accumulated cost

Your job: decide whether ANY escalation condition has been triggered.
Default to NOT escalate if uncertain — escalation pages a human; the
bar is high.

Output: a single JSON object on one line, nothing else:
  {"escalate": true|false, "reason": "<one short sentence>"}
"""


def _load_api_key() -> str:
    try:
        return settings.api_key_file.read_text().strip()
    except FileNotFoundError:
        return ""


async def _wait_for_session(client: httpx.AsyncClient, session_name: str, max_wait: int = 120) -> None:
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
                json={"command": "tmux has-session -t main 2>/dev/null && echo judge_ready || echo waiting"},
            )
            if resp.status_code == 200 and "judge_ready" in resp.json().get("output", ""):
                return
        except Exception:
            pass
        await asyncio.sleep(3)
    raise JudgeError(f"judge session '{session_name}' did not become ready within {max_wait}s")


async def _with_judge_session(
    fn: Callable[[httpx.AsyncClient, str], Awaitable[Any]],
) -> Any:
    api_key = _load_api_key()
    if not api_key:
        raise JudgeError("brainbox api_key not available — cannot dispatch judge session")

    base_url = f"http://localhost:{settings.api_port}"
    session_name = f"loop-judge-{secrets.token_hex(3)}"
    async with httpx.AsyncClient(
        base_url=base_url, timeout=600.0, headers={"X-API-Key": api_key}
    ) as client:
        try:
            resp = await client.post("/api/create", json={"name": session_name})
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise JudgeError(f"could not create judge session: {exc}") from exc
        try:
            await _wait_for_session(client, session_name)
            return await fn(client, session_name)
        finally:
            try:
                await client.post("/api/stop", json={"name": session_name})
                await client.post("/api/delete", json={"name": session_name})
            except Exception as cleanup_exc:
                log.warning(
                    "loop_judge.session_cleanup_failed",
                    metadata={"session": session_name, "reason": str(cleanup_exc)},
                )


_JSON_LINE_RE = re.compile(r"\{[^{}]*\}")


def _extract_verdict(text: str, keys: tuple[str, str]) -> dict[str, Any]:
    """Pull the first ``{...}`` JSON object out of the model's reply.
    Models often pad the JSON with prose despite the system prompt;
    don't fight it, just extract.

    ``keys`` is the (bool_key, fallback_reason) pair the caller expects."""
    bool_key, fallback_reason = keys
    for candidate in _JSON_LINE_RE.findall(text):
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and bool_key in obj:
            return obj
    return {bool_key: False, "reason": fallback_reason}


async def _query_session(client: httpx.AsyncClient, name: str, *, system: str, user: str) -> str:
    full_prompt = f"{system}\n\n--- input ---\n\n{user}"
    try:
        resp = await client.post(
            f"/api/sessions/{name}/query",
            json={"prompt": full_prompt, "timeout": _SESSION_QUERY_TIMEOUT},
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise JudgeError(f"upstream judge session call failed: {exc}") from exc
    body = resp.json()
    return (body.get("response") or body.get("output") or "").strip()


def _serialize_envelope(envelope: dict[str, Any]) -> str:
    """Compact JSON for prompt — keys ordered for diff-friendliness."""
    return json.dumps(envelope, sort_keys=True, default=str)


# ---------------------------------------------------------------------------
# Public entrypoints
# ---------------------------------------------------------------------------


async def evaluate_stop(
    *,
    envelope: dict[str, Any],
    objective: dict[str, Any],
    stop_prose: str,
) -> JudgeVerdict:
    """Decide whether the loop has reached its goal this iteration.

    Order: cheap objective checks first. If they all pass, return
    immediately — no LLM call needed. Otherwise dispatch the prose
    judge session and use its verdict.

    A judge-session failure surfaces as ``via='judge-error'`` with
    ``fired=False`` so the runner continues iterating rather than
    silently terminating on transient infra issues.
    """
    obj_verdict = eval_objective(envelope, objective)
    if obj_verdict.fired:
        return obj_verdict
    if not stop_prose.strip():
        return obj_verdict  # nothing else to check

    user = f"# When to stop\n{stop_prose}\n\n# Envelope\n{_serialize_envelope(envelope)}"

    try:
        text = await _with_judge_session(
            lambda client, name: _query_session(client, name, system=_STOP_SYSTEM, user=user)
        )
    except JudgeError as exc:
        log.warning("loop_judge.stop_failed", metadata={"reason": str(exc)})
        return JudgeVerdict(fired=False, reason=f"judge error: {exc}", via="judge-error")

    verdict = _extract_verdict(text, ("done", "judge replied without a parseable verdict"))
    return JudgeVerdict(
        fired=bool(verdict.get("done")),
        reason=str(verdict.get("reason") or ""),
        via="judge",
    )


async def evaluate_escalation(
    *,
    envelope: dict[str, Any],
    escalation_prose: str,
    iteration: int,
    max_iterations: int,
    cost_usd: float,
    budget_usd: float | None,
) -> JudgeVerdict:
    """Decide whether a human must be paged this iteration.

    Two structural triggers fire deterministically without the judge:
      - iteration >= max_iterations (hard cap)
      - cost_usd > budget_usd       (hard cap)
    Then the prose judge runs against the body's "# When to escalate"
    section for the qualitative cases.
    """
    if iteration >= max_iterations:
        return JudgeVerdict(
            fired=True,
            reason=f"iteration cap reached ({iteration}/{max_iterations})",
            via="objective",
        )
    if budget_usd is not None and cost_usd > budget_usd:
        return JudgeVerdict(
            fired=True,
            reason=f"cost budget exceeded ({cost_usd:.4f}/{budget_usd:.4f} USD)",
            via="objective",
        )
    if not escalation_prose.strip():
        return JudgeVerdict(fired=False, reason="no escalation clauses", via="missing")

    user = (
        f"# When to escalate\n{escalation_prose}\n\n"
        f"# Iteration\n{iteration}/{max_iterations}\n\n"
        f"# Cost\n{cost_usd:.4f} USD (budget: {budget_usd if budget_usd is not None else 'unset'})\n\n"
        f"# Envelope\n{_serialize_envelope(envelope)}"
    )

    try:
        text = await _with_judge_session(
            lambda client, name: _query_session(client, name, system=_ESCALATE_SYSTEM, user=user)
        )
    except JudgeError as exc:
        log.warning("loop_judge.escalate_failed", metadata={"reason": str(exc)})
        return JudgeVerdict(fired=False, reason=f"judge error: {exc}", via="judge-error")

    verdict = _extract_verdict(text, ("escalate", "judge replied without a parseable verdict"))
    return JudgeVerdict(
        fired=bool(verdict.get("escalate")),
        reason=str(verdict.get("reason") or ""),
        via="judge",
    )

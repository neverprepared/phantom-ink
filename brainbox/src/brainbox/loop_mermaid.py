"""Mermaid diagram generation for loop templates.

Every loop has the same architectural skeleton today (one worker per
iteration; judge decides next step), so the *graph topology* doesn't
change between templates. What changes — and what makes a useful
diagram — is the *content* the diagram surfaces:

  - Trigger labels carry the trigger string (``github:pull_request``,
    ``schedule:daily``, ``manual``, etc.) and pick a node shape per
    family (webhook/schedule/manual) so they're scannable.
  - Required artifact_refs land in their own node when present, so
    operators see the data plane the trigger has to deliver.
  - Worker carries the agent name, iteration cap, and budget.
  - Each ``objective`` entry becomes its own labeled edge off the
    Judge → Converged path (so a 3-rule objective draws 3 edges,
    a no-objective template draws zero).
  - Prose stop / escalate are surfaced as their own dashed edges.
  - The iterate-back-to-Worker edge carries the actual cap; the
    escalate-on-cap edge calls out budget when set.

Output is a mermaid string ready for the frontend to render via
mermaid.js. Persisted onto ``LoopInstance.mermaid`` at creation so the
diagram is frozen alongside the template snapshot — survives template
edits.
"""

from __future__ import annotations

from .loop_md import LoopMarkdown


_MAX_OBJECTIVE_EDGES = 4
_MAX_LABEL_CHARS = 60


def render(loop: LoopMarkdown) -> str:
    """Generate a mermaid ``graph TD`` for the parsed loop.

    Single skeleton, rich content. Two templates with different
    agents / triggers / objectives / required_refs will produce
    visibly different graphs because each of those becomes its own
    node or edge.
    """
    lines = ["graph TD"]

    # Trigger node — shape signals family
    trigger_family = _trigger_family(loop.trigger)
    trigger_label = _escape(loop.trigger or "(unset)")
    lines.append(_trigger_node(trigger_family, f"Trigger<br/>{trigger_label}"))

    # Required refs — only render when there are any; saves visual noise
    # for trigger-only loops.
    has_refs = bool(loop.required_refs)
    if has_refs:
        refs_label = _refs_label(loop.required_refs)
        lines.append(f'    R["Required refs<br/>{refs_label}"]')
        lines.append('    T --> R')
        lines.append('    R --> W')
    else:
        lines.append('    T --> W')

    # Worker — agent + iteration cap + budget
    worker_meta_parts = [f"agent: {_escape(loop.agent)}"]
    worker_meta_parts.append(f"max {loop.max_iterations} iter")
    if loop.budget_usd is not None:
        worker_meta_parts.append(f"budget ${loop.budget_usd:.2f}")
    worker_meta = "<br/>".join(worker_meta_parts)
    lines.append(f'    W["Worker<br/>{worker_meta}"]')
    lines.append('    W --> E')

    # Envelope
    lines.append('    E["Envelope<br/>artifact_refs · observations · findings"]')
    lines.append('    E --> J')
    lines.append('    J{"Judge"}')

    # Converged + Human terminals
    lines.append('    S(["✓ Converged"])')
    lines.append('    H(["🚨 Human attention"])')

    # Objective edges — one per rule (truncated). When no objective,
    # we only show the prose path so the diagram is honest about how
    # the judge will actually fire.
    if loop.objective:
        entries = list(loop.objective.items())
        shown = entries[:_MAX_OBJECTIVE_EDGES]
        for path, expected in shown:
            edge_label = _objective_edge_label(path, expected)
            lines.append(f'    J -- "{edge_label}" --> S')
        if len(entries) > _MAX_OBJECTIVE_EDGES:
            extra = len(entries) - _MAX_OBJECTIVE_EDGES
            lines.append(f'    J -- "+{extra} more objective rule(s)" --> S')

    # Prose stop — every loop has it (required section)
    stop_hint = _prose_hint(loop.stop_prose, "stop prose")
    lines.append(f'    J -. "{stop_hint}" .-> S')

    # Iterate back to Worker — labeled with the cap
    lines.append(f'    J -- "iter &lt; {loop.max_iterations}" --> W')

    # Prose escalate
    esc_hint = _prose_hint(loop.escalation_prose, "escalate prose")
    lines.append(f'    J -. "{esc_hint}" .-> H')

    # Iteration / budget cap escalation
    cap_label = f"iter ≥ {loop.max_iterations}"
    if loop.budget_usd is not None:
        cap_label += f" or > ${loop.budget_usd:.2f}"
    lines.append(f'    J -- "{cap_label}" --> H')

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _trigger_family(trigger: str) -> str:
    """Classify the trigger so we can pick a visually-distinct shape.
    Three families today: webhook (events), schedule (cron-like), manual."""
    t = (trigger or "").lower()
    if t.startswith(("github:", "webhook:", "slack:", "linear:", "http:")):
        return "webhook"
    if t.startswith(("schedule:", "cron:")):
        return "schedule"
    return "manual"


def _trigger_node(family: str, label: str) -> str:
    """Mermaid shape by family.
      - webhook → stadium ([...]), evokes "event"
      - schedule → cylinder [(...)], evokes "clock/timer"
      - manual → rectangle ([...]), evokes "button"
    """
    if family == "webhook":
        return f'    T([{label}])'
    if family == "schedule":
        return f'    T[("{label}")]'
    return f'    T["{label}"]'


def _refs_label(required_refs) -> str:
    """One ref per line, with type + required marker."""
    parts = []
    for ref in required_refs[:5]:
        marker = "" if ref.required else "?"
        parts.append(f"{_escape(ref.name)} ({ref.type.value}){marker}")
    if len(required_refs) > 5:
        parts.append(f"+{len(required_refs) - 5} more")
    return "<br/>".join(parts)


def _objective_edge_label(path: str, expected: object) -> str:
    """Turn an ``objective`` entry into a short, mermaid-safe edge label.

    Examples:
        observations.ci_status: green   →  "observations.ci_status == green"
        findings.approved: True         →  "findings.approved"
        diff_lines: {"<=": 500}         →  "diff_lines ≤ 500"
        status: {"in": [...]}           →  "status in [...]"
    """
    if isinstance(expected, dict) and expected:
        op, target = next(iter(expected.items()))
        symbol = {
            ">=": "≥",
            "<=": "≤",
            ">": ">",
            "<": "<",
            "in": "in",
            "not_empty": "not_empty",
        }.get(op, op)
        if op == "not_empty":
            label = f"{path} {symbol}" if target else f"{path} empty"
        else:
            label = f"{path} {symbol} {_compact(target)}"
    elif expected is True:
        label = path
    elif expected is False:
        label = f"!{path}"
    else:
        label = f"{path} == {_compact(expected)}"
    return _truncate(_escape(label))


def _prose_hint(prose: str, fallback: str) -> str:
    """First line of the prose section, truncated. Operator sees just
    enough to recognize what they wrote; the full text lives in the
    markdown.
    """
    if not prose.strip():
        return fallback
    # Skip bullet markers / leading whitespace
    first = ""
    for raw in prose.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.startswith(("-", "*", "•")):
            stripped = stripped.lstrip("-*• ").strip()
        if stripped:
            first = stripped
            break
    return _truncate(_escape(first or fallback))


def _compact(value: object) -> str:
    """One-line rendering for objective targets — lists collapse to
    ``[a, b, …]`` so a 10-item ``in`` clause doesn't blow the label up.
    """
    if isinstance(value, list):
        head = ", ".join(str(v) for v in value[:3])
        if len(value) > 3:
            head += ", …"
        return f"[{head}]"
    return str(value)


def _truncate(text: str, limit: int = _MAX_LABEL_CHARS) -> str:
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def _escape(text: str) -> str:
    """Mermaid label escaping. Quoted labels are robust to most special
    chars; we fix the quote, backslash, and newline. We don't try to
    fight mermaid's grammar — the regex above already keeps labels
    short and excludes structural punctuation by truncation."""
    if text is None:
        return ""
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace('"', "&quot;")
        .replace("\n", " ")
    )

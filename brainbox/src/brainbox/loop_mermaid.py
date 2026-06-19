"""Mermaid diagram generation for loop templates.

A loop's shape is the same five nodes regardless of body text:

    Trigger → Worker → Envelope → Judge → {Stop | Escalate | Iterate}

What varies between templates is the labels: the trigger name, the
role, the budget, the iteration cap, the objective shorthand. So the
generator takes a parsed ``LoopMarkdown`` and stamps those labels onto
a fixed ``graph TD`` skeleton.

Output is a mermaid string ready for the frontend to render via
mermaid.js. Persisted onto ``LoopInstance.mermaid`` at creation so the
diagram is frozen in time alongside the spec snapshot — survives
template edits.
"""

from __future__ import annotations

from .loop_md import LoopMarkdown


def render(loop: LoopMarkdown) -> str:
    """Generate a mermaid ``graph TD`` for the parsed loop."""
    objective_label = _objective_label(loop.objective)
    budget_label = (
        f"budget ${loop.budget_usd:.2f}" if loop.budget_usd is not None else "no budget cap"
    )

    # Mermaid is whitespace-tolerant but the rendered output is cleaner
    # if we keep edges grouped by source node.
    lines = [
        "graph TD",
        f'    T["Trigger<br/>{_escape(loop.trigger)}"] --> W',
        f'    W["Worker<br/>role: {_escape(_short_role(loop.role))}"] --> E',
        '    E["Envelope<br/>artifact_refs · observations · findings"] --> J',
        f'    J{{"Judge<br/>{_escape(objective_label)}"}}',
        '    J -- objective met --> S(["Converged"])',
        '    J -- stop prose true --> S',
        f'    J -- iter &lt; {loop.max_iterations} --> W',
        '    J -- escalate prose true --> H(["bus.attention<br/>human"])',
        f'    J -- iter ≥ {loop.max_iterations} or {budget_label} --> H',
    ]
    return "\n".join(lines)


def _objective_label(objective: dict[str, object]) -> str:
    """Compact human label for the objective dict.

    The diagram shouldn't try to render every operator shape — it's a
    visualization, not a spec. Truncate at 3 keys + " …" for the rest."""
    if not objective:
        return "prose only"
    keys = list(objective.keys())
    head = keys[:3]
    suffix = f" +{len(keys) - 3} more" if len(keys) > 3 else ""
    return "objective: " + ", ".join(head) + suffix


def _short_role(role_text: str) -> str:
    """First line of the role section, truncated. The full role lives
    in the markdown — the diagram just needs a one-line hint."""
    first = next((ln.strip() for ln in role_text.splitlines() if ln.strip()), "")
    if len(first) > 60:
        first = first[:57] + "…"
    return first or "(no role)"


def _escape(text: str) -> str:
    """Mermaid label escaping. Quoted labels are robust to most special
    chars; we only need to fix the quote itself plus a few HTML entities
    we already emit (``<br/>``)."""
    return text.replace('"', "&quot;").replace("\n", " ")

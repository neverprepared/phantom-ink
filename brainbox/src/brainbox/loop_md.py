"""Markdown loop definition — the new authoring format.

A loop is a single markdown file. Tiny YAML frontmatter for the four
things the runner cannot guess; named prose sections for everything
else. A judge agent reads the prose sections each iteration to decide
stop / escalate.

Frontmatter (required keys):
    name              — template identifier (slug)
    trigger           — what starts the loop (free-form string)
    max_iterations    — hard iteration cap

Frontmatter (optional):
    agent             — registered agent name that runs each iteration.
                        Must match a registered agent in
                        ``brainbox/agents/*.json`` (today: assistant,
                        reviewer, supervisor, worker). Defaults to
                        ``worker`` since that's the generic write-
                        capable role and the right starting point for
                        most loops. Override when the loop has stronger
                        constraints (e.g. ``reviewer`` for read-only).
    permissions       — "inherit" | "default" | "strict". Defaults to
                        "default". Same semantics as the prior YAML
                        format's PermissionTier.
    budget_usd        — hard cost cap; runner stops the loop if exceeded
    objective         — dict of cheap deterministic checks evaluated
                        BEFORE the prose judge each iteration. If any
                        succeeds, the loop converges immediately without
                        paying for an LLM call. Two shapes per entry:

                          ci_status: green                  # equality
                          no_blockers: true                  # truthiness
                          diff_lines: {"<=": 500}            # comparison
                          findings.approved: true

                        Keys are envelope paths (dotted). See
                        ``loop_judge._eval_objective`` for the full set.

    required_refs     — list of artifact_refs the operator must supply
                        before start_loop accepts the trigger.

Required body sections (case-insensitive, ``# Heading`` markers):
    # Role             — agent persona / system prompt
    # When to stop     — prose checklist; judge evaluates each iteration
    # When to escalate — prose checklist; judge evaluates each iteration

Optional sections:
    # Tools
    # Required artifacts
    # Notes

The parser is strict-but-friendly: missing required keys / sections
raise ``LoopMarkdownError`` with a one-line, operator-fixable message.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import yaml as yaml_module

from .loops import HandoffEnvelope, PermissionTier, RequiredRef, RequiredRefType  # noqa: F401 (re-export)


class LoopMarkdownError(ValueError):
    """Raised on parse / validation failure. The message is rendered
    directly to the operator — keep it actionable, single line."""


# ---------------------------------------------------------------------------
# Parsed shape
# ---------------------------------------------------------------------------


@dataclass
class LoopMarkdown:
    """Parsed loop template. Pure data — no runtime behavior. The runner
    and the judge each pull what they need.

    ``raw`` is the original markdown text. ``frontmatter`` is the raw
    YAML dict — we keep it so AI Assist can round-trip without losing
    unknown keys (forward-compat with new objective check shapes).
    ``sections`` keys are normalized lowercase heading names.
    """

    raw: str
    frontmatter: dict[str, Any]
    sections: dict[str, str]

    # Convenience pulled-out fields
    name: str
    trigger: str
    max_iterations: int
    agent: str
    permissions: PermissionTier
    budget_usd: float | None
    objective: dict[str, Any]
    required_refs: list[RequiredRef]

    role: str
    stop_prose: str
    escalation_prose: str
    tools_prose: str
    notes_prose: str

    @property
    def has_objective(self) -> bool:
        return bool(self.objective)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


_REQUIRED_FRONTMATTER = ("name", "trigger", "max_iterations")
_REQUIRED_SECTIONS = ("role", "when to stop", "when to escalate")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def parse(text: str) -> LoopMarkdown:
    """Parse a markdown loop template. Single source of truth — every
    other module that needs the parsed shape calls this."""
    frontmatter, body = _split_frontmatter(text)
    sections = _split_sections(body)

    _validate_frontmatter(frontmatter)
    _validate_sections(sections)

    refs_raw = frontmatter.get("required_refs", []) or []
    required_refs = [_parse_required_ref(r) for r in refs_raw]

    budget_raw = frontmatter.get("budget_usd")
    budget_usd = float(budget_raw) if budget_raw is not None else None

    name = str(frontmatter["name"])
    # 'worker' is the generic write-capable registered agent — sensible
    # default for a fresh template since the template name itself is
    # almost never the name of a registered agent.
    agent = str(frontmatter.get("agent") or "worker")
    perm_raw = (frontmatter.get("permissions") or "default").lower()
    try:
        permissions = PermissionTier(perm_raw)
    except ValueError as exc:
        raise LoopMarkdownError(
            f"frontmatter 'permissions' must be one of inherit|default|strict (got {perm_raw!r})"
        ) from exc

    return LoopMarkdown(
        raw=text,
        frontmatter=frontmatter,
        sections=sections,
        name=name,
        trigger=str(frontmatter["trigger"]),
        max_iterations=int(frontmatter["max_iterations"]),
        agent=agent,
        permissions=permissions,
        budget_usd=budget_usd,
        objective=dict(frontmatter.get("objective", {}) or {}),
        required_refs=required_refs,
        role=sections["role"].strip(),
        stop_prose=sections["when to stop"].strip(),
        escalation_prose=sections["when to escalate"].strip(),
        tools_prose=sections.get("tools", "").strip(),
        notes_prose=sections.get("notes", "").strip(),
    )


# ---------------------------------------------------------------------------
# Frontmatter split
# ---------------------------------------------------------------------------


_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(?P<fm>.*?)\n---\s*\n(?P<body>.*)\Z",
    re.DOTALL,
)


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        raise LoopMarkdownError(
            "missing frontmatter — every loop template must begin with a '---' fenced YAML block"
        )
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise LoopMarkdownError("malformed frontmatter — opening '---' has no closing '---'")
    try:
        fm = yaml_module.safe_load(match.group("fm")) or {}
    except yaml_module.YAMLError as exc:
        raise LoopMarkdownError(f"frontmatter is not valid YAML: {exc}") from exc
    if not isinstance(fm, dict):
        raise LoopMarkdownError("frontmatter must be a YAML mapping")
    return fm, match.group("body")


# ---------------------------------------------------------------------------
# Section split
# ---------------------------------------------------------------------------


_HEADING_RE = re.compile(r"^# +(?P<name>.+?)\s*$", re.MULTILINE)


def _split_sections(body: str) -> dict[str, str]:
    """Carve the body on top-level ``# `` headings. Lowercases section
    names so callers don't have to think about case."""
    sections: dict[str, str] = {}
    matches = list(_HEADING_RE.finditer(body))
    if not matches:
        return sections
    for i, m in enumerate(matches):
        name = m.group("name").strip().lower()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        sections[name] = body[start:end].lstrip("\n")
    return sections


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_frontmatter(fm: dict[str, Any]) -> None:
    missing = [k for k in _REQUIRED_FRONTMATTER if k not in fm]
    if missing:
        raise LoopMarkdownError(f"frontmatter is missing required keys: {', '.join(missing)}")

    name = fm.get("name")
    if not isinstance(name, str) or not _SLUG_RE.match(name):
        raise LoopMarkdownError(
            "frontmatter 'name' must be a slug (lowercase letters, digits, hyphens; "
            "must start with a letter or digit)"
        )

    trigger = fm.get("trigger")
    if not isinstance(trigger, str) or not trigger.strip():
        raise LoopMarkdownError("frontmatter 'trigger' must be a non-empty string")

    mi = fm.get("max_iterations")
    if not isinstance(mi, int) or mi < 1:
        raise LoopMarkdownError("frontmatter 'max_iterations' must be a positive integer")

    budget = fm.get("budget_usd")
    if budget is not None and (not isinstance(budget, (int, float)) or budget <= 0):
        raise LoopMarkdownError("frontmatter 'budget_usd' must be a positive number when set")

    obj = fm.get("objective")
    if obj is not None and not isinstance(obj, dict):
        raise LoopMarkdownError("frontmatter 'objective' must be a mapping when set")


def _validate_sections(sections: dict[str, str]) -> None:
    missing = [s for s in _REQUIRED_SECTIONS if s not in sections]
    if missing:
        pretty = ", ".join(f"# {s.title()}" for s in missing)
        raise LoopMarkdownError(f"body is missing required section(s): {pretty}")

    for s in _REQUIRED_SECTIONS:
        if not sections[s].strip():
            raise LoopMarkdownError(f"section '# {s.title()}' is empty")


def _parse_required_ref(raw: Any) -> RequiredRef:
    if not isinstance(raw, dict) or "name" not in raw:
        raise LoopMarkdownError("each required_refs entry must be a mapping with a 'name' field")
    type_str = (raw.get("type") or "string").lower()
    try:
        ref_type = RequiredRefType(type_str)
    except ValueError as exc:
        raise LoopMarkdownError(
            f"required_refs.{raw['name']}: type must be one of int|string|sha (got {type_str!r})"
        ) from exc
    return RequiredRef(
        name=str(raw["name"]),
        type=ref_type,
        description=str(raw.get("description") or ""),
        required=bool(raw.get("required", True)),
    )

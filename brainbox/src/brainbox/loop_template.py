"""Loop template loader — wraps loop_md.parse with disk I/O + CRUD.

Templates are markdown files with YAML frontmatter. ``loop_md.parse``
owns the parsing and validation contract; this module owns:

  - search-path resolution (built-in vs user dir, user wins)
  - CRUD with atomic writes
  - dry-run plan for the editor's "what would this do?" affordance
  - inline-lint shape for the AI Assist + editor validation surfaces

Built-in templates ship under ``brainbox/loop-templates/``. User-added
templates live under ``~/.config/phantom-ink/brainbox/loop-templates/``.
User path is searched first so an operator override shadows the bundled
template of the same name.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any

from .loop_md import LoopMarkdown, LoopMarkdownError, parse
from .loop_mermaid import render as render_mermaid

# ---------------------------------------------------------------------------
# Search paths
# ---------------------------------------------------------------------------


def _builtin_templates_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "loop-templates"


def _user_templates_dir() -> Path | None:
    try:
        from .config import settings
    except Exception:
        return None
    return settings.config_dir / "loop-templates"


def _search_paths() -> list[Path]:
    paths: list[Path] = []
    user = _user_templates_dir()
    if user is not None and user.is_dir():
        paths.append(user)
    builtin = _builtin_templates_dir()
    if builtin.is_dir():
        paths.append(builtin)
    return paths


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class TemplateError(ValueError):
    """Raised on a malformed or missing template. Wraps LoopMarkdownError
    so API handlers can map a single exception type to HTTP 400."""


def list_templates() -> list[str]:
    """Visible template names, deduped by name (user dir wins)."""
    seen: set[str] = set()
    out: list[str] = []
    for dirp in _search_paths():
        for f in sorted(dirp.glob("*.md")):
            name = f.stem
            if name in seen:
                continue
            seen.add(name)
            out.append(name)
    return out


def template_path(name: str) -> Path | None:
    for dirp in _search_paths():
        candidate = dirp / f"{name}.md"
        if candidate.is_file():
            return candidate
    return None


def load_template(name: str) -> LoopMarkdown:
    """Load and parse a template by name."""
    path = template_path(name)
    if path is None:
        raise TemplateError(f"loop template {name!r} not found")
    return parse_template(path.read_text())


def parse_template(content: str) -> LoopMarkdown:
    """Parse a template's full text. Single line of defense — the editor's
    inline lint (``validate_markdown``) returns a structured error report
    instead of raising; the runtime path uses this function and lets
    LoopMarkdownError bubble."""
    try:
        return parse(content)
    except LoopMarkdownError as exc:
        raise TemplateError(str(exc)) from exc


# ---------------------------------------------------------------------------
# Read / write — for the editor surface
# ---------------------------------------------------------------------------


def _origin_for(path: Path) -> str:
    user = _user_templates_dir()
    try:
        if user is not None and path.is_relative_to(user):
            return "user"
    except (ValueError, AttributeError):
        pass
    return "built-in"


def content_hash(text: str) -> str:
    """16-char hex digest of the raw text. Used as the snapshot fingerprint
    on LoopInstance and as the optimistic-concurrency hint for the editor."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def read_raw_template(name: str) -> dict[str, Any]:
    """Return raw markdown + metadata for the editor.

    Shape:
      {
        "name":     "...",
        "origin":   "built-in" | "user",
        "hash":     "...",
        "markdown": "<raw text>",
      }
    """
    path = template_path(name)
    if path is None:
        raise TemplateError(f"loop template {name!r} not found")
    text = path.read_text()
    return {
        "name": name,
        "origin": _origin_for(path),
        "hash": content_hash(text),
        "markdown": text,
    }


def _is_safe_name(name: str) -> bool:
    """Reject path traversal + shell metacharacters."""
    if not name:
        return False
    if name in (".", "..") or "/" in name or "\\" in name:
        return False
    return all(c.isalnum() or c in "-_." for c in name)


def write_user_template(
    name: str,
    raw_markdown: str,
    *,
    fork_from_builtin: bool = False,
    validate: bool = True,
) -> dict[str, Any]:
    """Write a template to the user dir. Atomic rename; refuses to
    overwrite a built-in of the same name unless ``fork_from_builtin``.

    ``validate=False`` skips the markdown parse check before writing.
    Use for draft saves where the operator wants the file persisted
    even when it doesn't yet pass LoopMarkdown's required-section
    rules — they can fix it in-editor afterwards. The Save button
    keeps the default (validate=True) so a manual save always lands
    parseable content.
    """
    if not _is_safe_name(name):
        raise TemplateError(f"invalid template name: {name!r}")

    if validate:
        parse_template(raw_markdown)  # validate before touching disk

    existing = template_path(name)
    if existing is not None and _origin_for(existing) == "built-in" and not fork_from_builtin:
        raise TemplateError(
            f"{name!r} is built-in; pass fork=true to create a user override"
        )

    user_dir = _user_templates_dir()
    if user_dir is None:
        raise TemplateError("user templates dir is unavailable")
    user_dir.mkdir(parents=True, exist_ok=True)

    target = user_dir / f"{name}.md"
    with tempfile.NamedTemporaryFile(
        mode="w",
        dir=str(user_dir),
        prefix=f".{name}.",
        suffix=".tmp",
        delete=False,
    ) as tmp:
        tmp.write(raw_markdown)
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, target)
    return read_raw_template(name)


def delete_user_template(name: str) -> None:
    if not _is_safe_name(name):
        raise TemplateError(f"invalid template name: {name!r}")
    user_dir = _user_templates_dir()
    if user_dir is None:
        raise TemplateError("user templates dir is unavailable")
    target = user_dir / f"{name}.md"
    if not target.is_file():
        raise TemplateError(f"user template {name!r} not found")
    target.unlink()


# ---------------------------------------------------------------------------
# Validate without saving — drives the editor's inline lint
# ---------------------------------------------------------------------------


def validate_markdown(raw_markdown: str) -> dict[str, Any]:
    """Validate text without saving. Returns:

      {
        "ok":       bool,
        "errors":   [{"line": int|null, "col": int|null,
                      "field":  str|null, "message": str}],
        "warnings": [...]
      }

    Single error per failure (the parser is fail-fast on the first
    problem) — operator fixes, re-validates, iterates. Line/col are
    null today because the parser raises a single message; the editor
    falls back to surfacing the message inline at the top of the file.
    """
    errors: list[dict[str, Any]] = []
    try:
        parse(raw_markdown)
    except LoopMarkdownError as exc:
        errors.append({"line": None, "col": None, "field": None, "message": str(exc)})
        return {"ok": False, "errors": errors, "warnings": []}
    return {"ok": True, "errors": [], "warnings": []}


# Back-compat alias for the old YAML editor endpoint shape. Deleted once
# the frontend stops calling /validate with the legacy yaml body key.
validate_yaml = validate_markdown


# ---------------------------------------------------------------------------
# Dry-run plan — what would iteration 1 actually do?
# ---------------------------------------------------------------------------


def build_dry_run_plan(loop: LoopMarkdown, envelope_data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Plan iteration 1 against a sample envelope without enqueueing.

    Returns:
      {
        "first_iteration": {iteration, agent_name, role_preview, ...},
        "objective":       {entries, would_fire, evaluated_against},
        "stop_prose":      "<text>",
        "escalation_prose": "<text>",
        "max_iterations":  int,
        "budget_usd":      float|null,
        "permissions":     str,
        "mermaid":         "<rendered diagram>",
      }
    """
    from .loop_judge import eval_objective

    envelope = envelope_data or {}
    obj_verdict = eval_objective(envelope, loop.objective)

    return {
        "first_iteration": {
            "iteration": 1,
            "agent_name": loop.agent,
            "role_preview": (loop.role[:240] + "…") if len(loop.role) > 240 else loop.role,
            "required_scopes": [],
            "task_description": f"loop {loop.name or 'ad-hoc'} iter 1: {loop.agent}",
        },
        "objective": {
            "entries": loop.objective,
            "would_fire": obj_verdict.fired,
            "reason": obj_verdict.reason,
        },
        "stop_prose": loop.stop_prose,
        "escalation_prose": loop.escalation_prose,
        "max_iterations": loop.max_iterations,
        "budget_usd": loop.budget_usd,
        "permissions": loop.permissions.value,
        "mermaid": render_mermaid(loop),
    }

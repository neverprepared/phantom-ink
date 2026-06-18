"""Loop template loader — parses markdown + YAML frontmatter into a LoopSpec.

The on-disk format mirrors the existing reflex workflow templates:
opening fence, YAML body, closing fence, then markdown documentation.

The YAML body deserializes directly into a LoopSpec via pydantic. That
means every validation guarantee from loops.py — convergence_predicate
required, body has nodes, permission tier defaults to 'default' — fires
at template-load time, not at start_loop time. A template that fails to
declare convergence cannot load. This is the forcing function for rigor
the plan describes (Kilo's "vague intent" is the root cause of thrashing).

Built-in templates ship under brainbox/loop-templates/ alongside the
agents/, pipelines/, ansible/ asset directories. User-added templates
under ~/.config/phantom-ink/brainbox/loop-templates/ are also picked
up; user takes precedence over built-in for the same name.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from .loops import LoopSpec


# ---------------------------------------------------------------------------
# Search paths
# ---------------------------------------------------------------------------


def _builtin_templates_dir() -> Path:
    """Path to the templates shipped with brainbox. Mirrors how agents/
    and pipelines/ resolve in config.py.
    """
    return Path(__file__).resolve().parent.parent.parent / "loop-templates"


def _user_templates_dir() -> Path | None:
    """Path under the user's config dir for operator-added templates.

    Returns None if config dir isn't writable / determinable; the loader
    falls back to built-ins only in that case.
    """
    try:
        from .config import settings
    except Exception:
        return None
    p = settings.config_dir / "loop-templates"
    return p


def _search_paths() -> list[Path]:
    """User-first search order so an operator override wins over the
    bundled template of the same name.
    """
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
    """Raised on a malformed template file. Wraps the specific reason
    so callers can surface a useful error to the operator without
    having to catch yaml.YAMLError / ValidationError separately.
    """


def list_templates() -> list[str]:
    """Return template names visible to this brainbox install, deduped
    by name (user overrides built-in). Names are the filename stem
    (e.g. 'pr-review-loop').
    """
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
    """Resolve the on-disk path for a template by name. User dir wins."""
    for dirp in _search_paths():
        candidate = dirp / f"{name}.md"
        if candidate.is_file():
            return candidate
    return None


def load_template(name: str) -> LoopSpec:
    """Load a template by name and return a validated LoopSpec.

    Raises TemplateError if the file is missing, frontmatter is
    malformed, or the LoopSpec validation fails.
    """
    path = template_path(name)
    if path is None:
        raise TemplateError(f"loop template {name!r} not found")
    return parse_template(path.read_text())


def parse_template(content: str) -> LoopSpec:
    """Parse the full template text (frontmatter + body) into a LoopSpec.

    The body markdown is currently dropped — it's operator-facing docs
    only. When variable substitution lands, the body may become a
    secondary prompt fragment.
    """
    fm, _body = _split_frontmatter(content)
    try:
        data = yaml.safe_load(fm) or {}
    except yaml.YAMLError as exc:
        raise TemplateError(f"frontmatter is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise TemplateError("frontmatter must be a YAML mapping")
    try:
        return LoopSpec.model_validate(data)
    except ValidationError as exc:
        raise TemplateError(f"loop spec validation failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------


def _split_frontmatter(content: str) -> tuple[str, str]:
    """Return (yaml_text, body_text).

    Matches the standard ``---\\n<yaml>\\n---\\n<body>`` shape. A template
    without frontmatter (no opening ``---`` on line 1) is rejected loud
    so an empty file doesn't silently produce a default LoopSpec.
    """
    if not content.startswith("---"):
        raise TemplateError("template must begin with '---' frontmatter fence")

    # Strip the opening fence + newline, then split on the closing fence.
    after_open = content[3:].lstrip("\n")
    parts = after_open.split("\n---", 1)
    if len(parts) != 2:
        raise TemplateError("frontmatter is missing closing '---' fence")

    yaml_text = parts[0]
    body_text = parts[1].lstrip("\n")
    return yaml_text, body_text

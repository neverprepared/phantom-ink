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

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any

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


# ---------------------------------------------------------------------------
# Read / write — for the editor surface
# ---------------------------------------------------------------------------


def _origin_for(path: Path) -> str:
    """Return ``"user"`` if the path is under the user templates dir,
    otherwise ``"built-in"``. Determines whether the editor allows
    in-place save or requires a fork.
    """
    user = _user_templates_dir()
    try:
        if user is not None and path.is_relative_to(user):
            return "user"
    except (ValueError, AttributeError):
        pass
    return "built-in"


def _content_hash(text: str) -> str:
    """Stable 16-char hex digest of the raw text. Surfaced to the editor
    so it can detect concurrent edits (compare on save)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def read_raw_template(name: str) -> dict[str, Any]:
    """Return the raw template text + metadata for the editor.

    Shape:
      {
        "name": "...",
        "origin": "built-in" | "user",
        "version": "...",       // from frontmatter; "" if not parseable
        "hash":    "...",        // content hash
        "yaml":    "<raw text>"
      }

    Raises TemplateError if the template isn't found.
    """
    path = template_path(name)
    if path is None:
        raise TemplateError(f"loop template {name!r} not found")
    text = path.read_text()
    version = ""
    try:
        fm, _ = _split_frontmatter(text)
        data = yaml.safe_load(fm) or {}
        if isinstance(data, dict):
            version = str(data.get("version", "") or "")
    except Exception:
        # Reading raw is best-effort about version; the editor will show
        # the YAML and the operator can fix whatever's wrong.
        pass
    return {
        "name": name,
        "origin": _origin_for(path),
        "version": version,
        "hash": _content_hash(text),
        "yaml": text,
    }


def _is_safe_name(name: str) -> bool:
    """Reject path traversal and shell metacharacters in template names.

    Allowed: alphanumerics, dash, underscore, dot (for extensions in the
    name itself, though we strip and re-add the .md ourselves). Reject
    everything else, including slashes and "..".
    """
    if not name:
        return False
    if name in (".", "..") or "/" in name or "\\" in name:
        return False
    return all(c.isalnum() or c in "-_." for c in name)


def write_user_template(
    name: str,
    raw_yaml: str,
    *,
    fork_from_builtin: bool = False,
) -> dict[str, Any]:
    """Write a template to the user templates dir. Atomic rename; rejects
    writes when the same name exists as a built-in unless
    ``fork_from_builtin`` is set.

    Returns the same shape as ``read_raw_template`` after the write.

    Raises:
      TemplateError: invalid name, content fails to parse, write to
        a built-in name without fork, or any IO error.
    """
    if not _is_safe_name(name):
        raise TemplateError(f"invalid template name: {name!r}")

    # Validate the YAML before touching disk — never persist garbage.
    # This re-parses what we're about to save and raises on schema failure.
    parse_template(raw_yaml)

    # Block in-place overwrite of a built-in unless the caller forks.
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
    # Atomic write: write to a temp file in the same dir, then rename. This
    # guarantees a partial write never leaves a half-template on disk that
    # a concurrent load_template would parse.
    with tempfile.NamedTemporaryFile(
        mode="w",
        dir=str(user_dir),
        prefix=f".{name}.",
        suffix=".tmp",
        delete=False,
    ) as tmp:
        tmp.write(raw_yaml)
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, target)
    return read_raw_template(name)


def delete_user_template(name: str) -> None:
    """Delete a user template by name. Rejects deletes against built-ins
    (operator can't remove a bundled template; they can only shadow it
    with a user copy and then delete that copy).
    """
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


def validate_yaml(raw_yaml: str) -> dict[str, Any]:
    """Run ``parse_template`` against raw YAML without saving, returning a
    structured error report the editor can render as inline lint.

    Shape:
      {
        "ok":       bool,
        "errors":   [{"line": int|null, "col": int|null,
                      "field":  str|null, "message": str}],
        "warnings": [...]    // reserved; same shape
      }

    YAML syntax errors carry line/col from the PyYAML parser. Pydantic
    schema errors carry a dotted ``field`` path but no line/col — the
    editor can highlight the field name instead.
    """
    errors: list[dict[str, Any]] = []

    # Step 1: frontmatter / YAML syntax. Surfaces line/col when the parser
    # gives us a position.
    try:
        fm, _body = _split_frontmatter(raw_yaml)
    except TemplateError as exc:
        errors.append({"line": None, "col": None, "field": None, "message": str(exc)})
        return {"ok": False, "errors": errors, "warnings": []}

    try:
        data = yaml.safe_load(fm) or {}
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        errors.append({
            "line": mark.line + 2 if mark else None,  # +2: opening fence + 1-index
            "col": mark.column + 1 if mark else None,
            "field": None,
            "message": str(exc),
        })
        return {"ok": False, "errors": errors, "warnings": []}

    if not isinstance(data, dict):
        errors.append({
            "line": None, "col": None, "field": None,
            "message": "frontmatter must be a YAML mapping",
        })
        return {"ok": False, "errors": errors, "warnings": []}

    # Step 2: pydantic schema validation.
    try:
        LoopSpec.model_validate(data)
    except ValidationError as exc:
        for err in exc.errors():
            field = ".".join(str(p) for p in err.get("loc", ()))
            errors.append({
                "line": None,
                "col": None,
                "field": field or None,
                "message": err.get("msg", "validation error"),
            })
        return {"ok": False, "errors": errors, "warnings": []}
    except ValueError as exc:
        # model_post_init raises ValueError for the convergence-required case
        errors.append({"line": None, "col": None, "field": None, "message": str(exc)})
        return {"ok": False, "errors": errors, "warnings": []}

    return {"ok": True, "errors": [], "warnings": []}


# ---------------------------------------------------------------------------
# Dry-run plan — what would iteration 1 actually do?
# ---------------------------------------------------------------------------


def build_dry_run_plan(spec: LoopSpec, envelope_data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Plan iteration 1 against a sample envelope without enqueueing.

    Returns:
      {
        "first_iteration": {... what the runner would do ...},
        "convergence_predicate": {expr, would_fire},
        "convergence_metric": {expr, value} | null,
        "stop_conditions": [...],
        "max_iterations": int,
        "permissions": str,
      }
    """
    from .loop_predicate import eval_metric, eval_predicate
    from .loops import HandoffEnvelope

    if not spec.body.nodes:
        raise TemplateError("body has no nodes; nothing to run")

    envelope = HandoffEnvelope.model_validate(envelope_data or {})
    first = spec.body.nodes[0]
    agent_name = first.agent_id or first.role

    # JMESPath can raise ValueError on functions like length() called against
    # null when the sample envelope doesn't carry the field the predicate
    # references. In dry-run we surface that as "couldn't evaluate" rather
    # than failing the whole plan — that diagnostic is itself useful (it
    # tells the operator their convergence predicate references a field
    # that won't be populated at iteration 1).
    def _safe_pred(expr: str) -> tuple[bool | None, str | None]:
        if not expr:
            return True, None
        try:
            return eval_predicate(envelope, expr), None
        except Exception as exc:
            return None, str(exc)

    def _safe_metric(expr: str) -> tuple[float | None, str | None]:
        if not expr:
            return None, None
        try:
            return eval_metric(envelope, expr), None
        except Exception as exc:
            return None, str(exc)

    conv_value, conv_err = _safe_pred(spec.convergence_predicate)
    metric_value, metric_err = _safe_metric(spec.convergence_metric)

    stop_evals = []
    for sc in spec.stop_conditions:
        fire, err = _safe_pred(sc.predicate)
        entry = {
            "reason": sc.reason or sc.predicate,
            "expr": sc.predicate,
            "would_fire": fire,
        }
        if err is not None:
            entry["error"] = err
        stop_evals.append(entry)

    return {
        "first_iteration": {
            "iteration": 1,
            "node_id": first.id,
            "node_kind": first.kind.value,
            "node_executor": first.executor.value,
            "agent_name": agent_name,
            "prompt_preview": first.prompt,
            "required_scopes": list(first.requires),
            "task_description": f"loop {spec.name or 'ad-hoc'} iter 1: "
                                f"{first.id} ({first.kind.value})",
        },
        "convergence_predicate": {
            "expr": spec.convergence_predicate,
            "would_fire": conv_value,
            **({"error": conv_err} if conv_err else {}),
        },
        "convergence_metric": ({
            "expr": spec.convergence_metric,
            "value": metric_value,
            **({"error": metric_err} if metric_err else {}),
        } if spec.convergence_metric else None),
        "stop_conditions": stop_evals,
        "max_iterations": spec.max_iterations,
        "permissions": spec.permissions.value,
    }


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

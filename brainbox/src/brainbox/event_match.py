"""EventBridge-style pattern matching over agent-bus event documents.

A pattern is a JSON object matched structurally against an event document
(an ``AgentEnvelope.model_dump()`` plus injected ``seq``/``ts`` fields):

- All keys in a pattern object must match (AND).
- A pattern value is normalized to a list; the field matches if ANY element
  matches (OR). ``"x"`` and ``["x"]`` are equivalent.
- If the event value is itself a list (e.g. ``tags``), the field matches if
  ANY event element matches ANY pattern element.
- A nested non-operator dict recurses into the event dict at that key.
- List elements are either JSON literals (strict equality, no type coercion)
  or single-key operator dicts:

    {"prefix": "task."}          string startswith
    {"suffix": ".failed"}        string endswith
    {"exists": true|false}       key present and non-null / absent-or-null.
                                 The ONLY matcher that can succeed on a
                                 missing field.
    {"anything-but": [...]}      value present and not equal to any listed
                                 literal (bare scalar also accepted)
    {"numeric": ["<op>", n, ...]} with ops < <= > >= = !=, one or two
                                 op/operand pairs (two = range). Event value
                                 must be int/float (bool excluded).

Pure module: no I/O, no brainbox imports. ``validate_pattern`` returns
human-readable errors for the API layer; ``matches`` assumes a pattern that
validated and is defensive (malformed operators simply don't match).
"""

from __future__ import annotations

from typing import Any

_OPERATORS = ("prefix", "suffix", "exists", "anything-but", "numeric")
_NUMERIC_OPS = ("<", "<=", ">", ">=", "=", "!=")

# Sentinel distinguishing "key absent" from "value is None".
_ABSENT = object()


def _is_operator(value: Any) -> bool:
    return isinstance(value, dict) and len(value) == 1 and next(iter(value)) in _OPERATORS


def _numeric_cmp(event_value: Any, spec: list[Any]) -> bool:
    if isinstance(event_value, bool) or not isinstance(event_value, (int, float)):
        return False
    if not isinstance(spec, list) or len(spec) not in (2, 4):
        return False
    pairs = [(spec[0], spec[1])] if len(spec) == 2 else [(spec[0], spec[1]), (spec[2], spec[3])]
    for op, operand in pairs:
        if op not in _NUMERIC_OPS or isinstance(operand, bool) or not isinstance(operand, (int, float)):
            return False
        ok = {
            "<": event_value < operand,
            "<=": event_value <= operand,
            ">": event_value > operand,
            ">=": event_value >= operand,
            "=": event_value == operand,
            "!=": event_value != operand,
        }[op]
        if not ok:
            return False
    return True


def _literal_eq(event_value: Any, literal: Any) -> bool:
    # Strict: bool is not int, 1 is not "1". None == None is allowed.
    if isinstance(literal, bool) or isinstance(event_value, bool):
        return type(event_value) is type(literal) and event_value == literal
    if isinstance(literal, (int, float)) and isinstance(event_value, (int, float)):
        return event_value == literal
    return type(event_value) is type(literal) and event_value == literal


def _element_matches(event_value: Any, element: Any, *, present: bool) -> bool:
    """Match one pattern-list element against one event value."""
    if _is_operator(element):
        op, arg = next(iter(element.items()))
        if op == "exists":
            found = present and event_value is not None
            return found if arg is True else (not found if arg is False else False)
        # Every other operator requires the field to be present.
        if not present:
            return False
        if op == "prefix":
            return isinstance(event_value, str) and isinstance(arg, str) and event_value.startswith(arg)
        if op == "suffix":
            return isinstance(event_value, str) and isinstance(arg, str) and event_value.endswith(arg)
        if op == "anything-but":
            excluded = arg if isinstance(arg, list) else [arg]
            return event_value is not None and not any(_literal_eq(event_value, x) for x in excluded)
        if op == "numeric":
            return _numeric_cmp(event_value, arg)
        return False
    # Nested pattern object → recurse into dicts (or ANY dict element of a list).
    if isinstance(element, dict):
        if not present:
            return False
        if isinstance(event_value, dict):
            return matches(element, event_value)
        if isinstance(event_value, list):
            return any(isinstance(v, dict) and matches(element, v) for v in event_value)
        return False
    # JSON literal.
    return present and _literal_eq(event_value, element)


def _field_matches(event_value: Any, pattern_value: Any, *, present: bool) -> bool:
    # Nested non-operator dict recurses without list normalization.
    if isinstance(pattern_value, dict) and not _is_operator(pattern_value):
        return _element_matches(event_value, pattern_value, present=present)
    elements = pattern_value if isinstance(pattern_value, list) else [pattern_value]
    # Event arrays: ANY event element may satisfy ANY pattern element —
    # except for operators that reason about the value as a whole (exists,
    # anything-but), which see the array itself.
    for element in elements:
        if _element_matches(event_value, element, present=present):
            return True
        if present and isinstance(event_value, list):
            elementwise = not _is_operator(element) or (
                next(iter(element)) in ("prefix", "suffix", "numeric")
            )
            if elementwise and any(
                _element_matches(v, element, present=True) for v in event_value
            ):
                return True
    return False


def matches(pattern: dict[str, Any], event: dict[str, Any]) -> bool:
    """Return True when ``event`` satisfies every key of ``pattern``."""
    if not isinstance(pattern, dict) or not isinstance(event, dict):
        return False
    for key, pattern_value in pattern.items():
        present = key in event
        event_value = event.get(key, _ABSENT)
        if event_value is _ABSENT:
            event_value = None
        if not _field_matches(event_value, pattern_value, present=present):
            return False
    return True


# ---------------------------------------------------------------------------
# Validation (API-facing)
# ---------------------------------------------------------------------------


def _validate_element(element: Any, path: str, errors: list[str]) -> None:
    if isinstance(element, dict):
        if len(element) == 1 and next(iter(element)) in _OPERATORS:
            op, arg = next(iter(element.items()))
            if op == "exists" and not isinstance(arg, bool):
                errors.append(f"{path}: 'exists' takes true or false")
            elif op in ("prefix", "suffix") and not isinstance(arg, str):
                errors.append(f"{path}: '{op}' takes a string")
            elif op == "anything-but":
                values = arg if isinstance(arg, list) else [arg]
                if any(isinstance(v, (dict, list)) for v in values):
                    errors.append(f"{path}: 'anything-but' takes scalars only")
            elif op == "numeric":
                if (
                    not isinstance(arg, list)
                    or len(arg) not in (2, 4)
                    or any(arg[i] not in _NUMERIC_OPS for i in range(0, len(arg), 2))
                    or any(
                        isinstance(arg[i], bool) or not isinstance(arg[i], (int, float))
                        for i in range(1, len(arg), 2)
                    )
                ):
                    errors.append(
                        f"{path}: 'numeric' takes [op, number] or [op, number, op, number] "
                        f"with ops {', '.join(_NUMERIC_OPS)}"
                    )
        else:
            # Nested pattern object.
            _validate_pattern_obj(element, path, errors)
    elif isinstance(element, list):
        errors.append(f"{path}: nested arrays are not valid pattern elements")


def _validate_pattern_obj(pattern: Any, path: str, errors: list[str]) -> None:
    if not isinstance(pattern, dict):
        errors.append(f"{path or 'pattern'}: must be an object")
        return
    if not pattern:
        errors.append(f"{path or 'pattern'}: must not be empty")
        return
    for key, value in pattern.items():
        child = f"{path}.{key}" if path else key
        if isinstance(value, dict) and not _is_operator(value):
            _validate_pattern_obj(value, child, errors)
        elif isinstance(value, list):
            if not value:
                errors.append(f"{child}: match list must not be empty")
            for element in value:
                _validate_element(element, child, errors)
        else:
            _validate_element(value, child, errors)


def validate_pattern(pattern: Any) -> list[str]:
    """Return a list of human-readable problems; empty list = valid."""
    errors: list[str] = []
    _validate_pattern_obj(pattern, "", errors)
    return errors

"""JMESPath helpers for the Loop runner — Python mirror of app/loop_predicate.go.

Five sites in the runner share this entry point: edge predicates, the
convergence predicate, stop conditions, escalation predicates, and join
conditions (all bool); plus the per-iteration convergence metric (number).
One language, one debugger, one trace line per evaluation across both Go and
Python sides.

Coercion rules MUST match the Go side exactly. The cross-language contract
is exercised by tests/test_loop_predicate.py which uses the same expressions
and envelopes as app/loop_predicate_test.go.
"""

from __future__ import annotations

from typing import Any

import jmespath


def eval_predicate(envelope: Any, expr: str) -> bool:
    """Evaluate ``expr`` against ``envelope`` and coerce to bool.

    Coercion table (matches Go side):
      - True / False             → as-is
      - None                     → False
      - numbers                  → != 0
      - strings                  → non-empty
      - lists / dicts            → non-empty
      - anything else            → raises ValueError

    Empty ``expr`` is always True — same semantics as an Edge with no
    Predicate set.
    """
    if not expr:
        return True
    value = _eval_jmespath(envelope, expr)
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value != ""
    if isinstance(value, (list, dict)):
        return len(value) > 0
    raise ValueError(f"predicate {expr!r} returned uncoercible type {type(value).__name__}")


def eval_metric(envelope: Any, expr: str) -> float:
    """Evaluate ``expr`` against ``envelope`` and coerce to float.

    Used for the per-iteration convergence metric and any future numeric stop
    conditions (cost_usd, diff_lines, etc.).

    Missing-field returns 0.0 with no error — an iteration that hasn't
    populated findings yet should report a metric of 0, not crash. The
    alternative (treat missing as error) would force every chart query to
    guard for "iteration 0 hasn't run yet."

    Empty ``expr`` is an error — unlike predicates, a metric has no
    sensible default.
    """
    if not expr:
        raise ValueError("empty metric expression")
    value = _eval_jmespath(envelope, expr)
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    raise ValueError(
        f"metric {expr!r} returned non-numeric type {type(value).__name__}"
    )


def _eval_jmespath(envelope: Any, expr: str) -> Any:
    """Run the expression against the envelope, normalizing through the
    pydantic .model_dump() / .__dict__ surface so json field names drive
    lookups (matching what predicates author against).
    """
    data = _normalize(envelope)
    try:
        return jmespath.search(expr, data)
    except jmespath.exceptions.JMESPathError as exc:
        raise ValueError(f"evaluate {expr!r}: {exc}") from exc


def _normalize(envelope: Any) -> Any:
    """Convert a pydantic model / dataclass / dict into a plain dict/list
    structure jmespath can walk. Leaves scalars and built-in containers alone.
    """
    if envelope is None or isinstance(envelope, (str, int, float, bool, list, dict)):
        return envelope
    if hasattr(envelope, "model_dump"):
        return envelope.model_dump(by_alias=True)
    if hasattr(envelope, "__dict__"):
        return dict(envelope.__dict__)
    return envelope

package main

import (
	"encoding/json"
	"fmt"

	"github.com/jmespath/go-jmespath"
)

// evalPredicate evaluates a JMESPath expression against an envelope (or any
// JSON-marshallable value) and coerces the result to a bool. Used for edge
// predicates, the convergence predicate, stop conditions, escalation
// predicates, and join conditions — every bool-valued predicate site in the
// Loop runner shares this one entry point.
//
// Coercion rules:
//
//   - true / false → as-is
//   - nil          → false
//   - numbers      → !=0 is true
//   - strings      → non-empty is true (matches operator intuition for things
//                    like `observations.ci_status` returning "green" or "")
//   - arrays/maps  → non-empty is true (matches `findings.blockers` style
//                    expressions where a blockers array existing-but-empty
//                    should still be false-y for "any blockers")
//
// An empty expression is treated as always-true; this matches the semantics
// of an Edge with no Predicate.
func evalPredicate(envelope any, expr string) (bool, error) {
	if expr == "" {
		return true, nil
	}
	v, err := evalJMESPath(envelope, expr)
	if err != nil {
		return false, err
	}
	switch x := v.(type) {
	case nil:
		return false, nil
	case bool:
		return x, nil
	case float64:
		return x != 0, nil
	case int:
		return x != 0, nil
	case string:
		return x != "", nil
	case []interface{}:
		return len(x) > 0, nil
	case map[string]interface{}:
		return len(x) > 0, nil
	default:
		return false, fmt.Errorf("predicate %q returned uncoercible type %T", expr, v)
	}
}

// evalMetric evaluates a JMESPath expression and coerces to float64. Used for
// the per-iteration convergence_metric and for any future numeric stop
// conditions (cost_usd, diff_lines, etc.).
//
// A missing field returns 0 with no error — a Loop iterating before any
// findings have populated should report a metric of 0, not crash. The
// alternative (treat missing as error) would make every chart require a
// guard for "iteration 0 hasn't run yet."
func evalMetric(envelope any, expr string) (float64, error) {
	if expr == "" {
		return 0, fmt.Errorf("empty metric expression")
	}
	v, err := evalJMESPath(envelope, expr)
	if err != nil {
		return 0, err
	}
	switch x := v.(type) {
	case nil:
		return 0, nil
	case float64:
		return x, nil
	case int:
		return float64(x), nil
	case bool:
		if x {
			return 1, nil
		}
		return 0, nil
	default:
		return 0, fmt.Errorf("metric %q returned non-numeric type %T", expr, v)
	}
}

// evalJMESPath runs the expression against the envelope. The envelope is
// first round-tripped through JSON so structs with json tags evaluate using
// the wire-format field names (matching what predicates author against).
func evalJMESPath(envelope any, expr string) (any, error) {
	data, err := normalizeForJMESPath(envelope)
	if err != nil {
		return nil, err
	}
	result, err := jmespath.Search(expr, data)
	if err != nil {
		return nil, fmt.Errorf("evaluate %q: %w", expr, err)
	}
	return result, nil
}

// normalizeForJMESPath ensures the input is a JSON-shaped map/slice/scalar
// rather than a Go struct. If it's already a map/slice/scalar, return as-is.
// Otherwise marshal+unmarshal so json tags drive field names.
func normalizeForJMESPath(envelope any) (any, error) {
	switch envelope.(type) {
	case map[string]interface{}, []interface{}, string, float64, int, bool, nil:
		return envelope, nil
	}
	b, err := json.Marshal(envelope)
	if err != nil {
		return nil, fmt.Errorf("marshal envelope: %w", err)
	}
	var out interface{}
	if err := json.Unmarshal(b, &out); err != nil {
		return nil, fmt.Errorf("unmarshal envelope: %w", err)
	}
	return out, nil
}

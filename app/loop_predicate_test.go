package main

import "testing"

func TestEvalPredicate_EmptyExpressionIsAlwaysTrue(t *testing.T) {
	ok, err := evalPredicate(map[string]interface{}{}, "")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !ok {
		t.Fatalf("empty predicate must be true")
	}
}

func TestEvalPredicate_BlockerCount(t *testing.T) {
	env := HandoffEnvelope{
		Findings: map[string]interface{}{
			"blockers": []interface{}{
				map[string]interface{}{"file": "a.go", "line": 1},
				map[string]interface{}{"file": "b.go", "line": 2},
			},
		},
	}
	cases := []struct {
		expr string
		want bool
	}{
		{"length(findings.blockers) > `0`", true},
		{"length(findings.blockers) == `0`", false},
		{"length(findings.blockers) >= `2`", true},
		{"length(findings.blockers) > `5`", false},
	}
	for _, c := range cases {
		got, err := evalPredicate(env, c.expr)
		if err != nil {
			t.Fatalf("expr %q: unexpected error: %v", c.expr, err)
		}
		if got != c.want {
			t.Fatalf("expr %q: got %v want %v", c.expr, got, c.want)
		}
	}
}

func TestEvalPredicate_MissingFieldIsFalse(t *testing.T) {
	env := HandoffEnvelope{}
	ok, err := evalPredicate(env, "findings.approved")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if ok {
		t.Fatalf("missing field must be false")
	}
}

func TestEvalPredicate_StringComparison(t *testing.T) {
	env := HandoffEnvelope{
		Observations: map[string]interface{}{"ci_status": "green"},
	}
	ok, err := evalPredicate(env, "observations.ci_status == 'green'")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !ok {
		t.Fatalf("ci_status=='green' must be true")
	}
}

func TestEvalPredicate_BooleanField(t *testing.T) {
	env := HandoffEnvelope{
		Findings: map[string]interface{}{"approved": true},
	}
	ok, err := evalPredicate(env, "findings.approved")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !ok {
		t.Fatalf("approved=true must be true")
	}
}

func TestEvalPredicate_MalformedExpressionErrors(t *testing.T) {
	_, err := evalPredicate(map[string]interface{}{}, "this is not jmespath !!")
	if err == nil {
		t.Fatalf("expected error for malformed expression")
	}
}

func TestEvalMetric_BlockerCount(t *testing.T) {
	env := HandoffEnvelope{
		Findings: map[string]interface{}{
			"blockers": []interface{}{
				map[string]interface{}{"file": "a.go"},
				map[string]interface{}{"file": "b.go"},
				map[string]interface{}{"file": "c.go"},
			},
		},
	}
	got, err := evalMetric(env, "length(findings.blockers)")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got != 3 {
		t.Fatalf("got %v want 3", got)
	}
}

func TestEvalMetric_MissingFieldIsZero(t *testing.T) {
	env := HandoffEnvelope{}
	got, err := evalMetric(env, "observations.diff_lines")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got != 0 {
		t.Fatalf("missing metric must be 0, got %v", got)
	}
}

func TestEvalMetric_EmptyExpressionErrors(t *testing.T) {
	_, err := evalMetric(HandoffEnvelope{}, "")
	if err == nil {
		t.Fatalf("expected error for empty metric expression")
	}
}

func TestEvalMetric_NonNumericErrors(t *testing.T) {
	env := HandoffEnvelope{
		Observations: map[string]interface{}{"ci_status": "green"},
	}
	_, err := evalMetric(env, "observations.ci_status")
	if err == nil {
		t.Fatalf("expected error for string-valued metric")
	}
}

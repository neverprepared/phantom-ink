package main

import (
	"encoding/json"
	"strings"
	"testing"
)

// The chart timeout must exceed the list's: GetCharts issues four aggregations
// over a wider window than a single session page.
func TestChartsTimeoutExceedsTheListTimeout(t *testing.T) {
	if chartsQueryTimeout <= agentSessionQueryTimeout {
		t.Errorf("chartsQueryTimeout %v should exceed agentSessionQueryTimeout %v",
			chartsQueryTimeout, agentSessionQueryTimeout)
	}
}

// The chart payload must marshal with empty arrays, never nulls: the panel
// iterates all three, and a null threw during render once already.
//
// Also pins the shape FLAT. Embedding a pointer to another package's chart
// struct makes Wails' TypeScript generator emit a class referencing that
// package's point and share types without defining either, which breaks
// type-checking of the generated models.ts. Keep the fields declared here.
func TestAgentWorkChartsMarshalsWithoutNulls(t *testing.T) {
	blob, err := json.Marshal(&AgentWorkCharts{Granularity: "day"})
	if err != nil {
		t.Fatal(err)
	}
	// These three are the whole payload: two cost trends plus the per-source
	// breakdown. The panel iterates each by name, so the names are contract.
	for _, field := range []string{"cost_series", "cost_by_model", "cost_by_source"} {
		if !strings.Contains(string(blob), `"`+field+`"`) {
			t.Errorf("%s missing from the payload: %s", field, blob)
		}
	}
	// A zero-value struct has nil slices, so this documents that callers must
	// populate them -- which GetAgentWorkCharts does via make().
	var out AgentWorkCharts
	if err := json.Unmarshal(blob, &out); err != nil {
		t.Fatalf("round trip: %v", err)
	}
	if out.Granularity != "day" {
		t.Errorf("granularity lost in round trip: %+v", out)
	}
}

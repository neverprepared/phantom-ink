package opensearch

import (
	"context"
	"strings"
	"testing"
)

// Shape of a real metrics-otel aggregation response, measured 2026-10-20.
const chartAggs = `{
 "aggregations":{
  "trend":{"buckets":[
    {"key_as_string":"2026-10-19T18:00:00.000Z","key":1792447200000,"usd":{"value":127.01}},
    {"key_as_string":"2026-10-19T21:00:00.000Z","key":1792458000000,"usd":{"value":109.60}}
  ]},
  "by_model":{"buckets":[
    {"key":"claude-opus-5[1m]","usd":{"value":342.71}},
    {"key":"claude-opus-5","usd":{"value":177.60}}
  ]},
  "by_source":{"buckets":[
    {"key":"main","usd":{"value":499.51}},
    {"key":"auxiliary","usd":{"value":25.16}},
    {"key":"subagent","usd":{"value":15.89}}
  ]}
 }
}`

func TestAgentChartsReadsCostFromMetrics(t *testing.T) {
	// Cost must come from the metrics index, which prices every model call
	// including delegated subagent work. Reading it from anywhere that only
	// sees the main thread understates spend while looking authoritative.
	c, caps := serveJSON(t, chartAggs)

	got, err := c.GetAgentCharts(context.Background(), "personal", "now-24h")
	if err != nil {
		t.Fatalf("GetAgentCharts: %v", err)
	}

	body := caps.get("metrics-otel-*")
	if !strings.Contains(body, "claude_code.cost.usage") {
		t.Errorf("not filtered to the cost metric: %s", body)
	}
	if !strings.Contains(body, "resource.attributes.workspace.keyword") {
		t.Errorf("not scoped to the workspace: %s", body)
	}
	if len(got.CostSeries) != 2 || got.CostSeries[0].Value != 127.01 {
		t.Errorf("CostSeries = %+v", got.CostSeries)
	}
}

func TestAgentChartsSplitsCostByModel(t *testing.T) {
	c, _ := serveJSON(t, chartAggs)

	got, _ := c.GetAgentCharts(context.Background(), "personal", "now-24h")

	if len(got.CostByModel) != 2 {
		t.Fatalf("want 2 model buckets, got %+v", got.CostByModel)
	}
	// [1m] is the 1M-context variant and prices differently, so it is a
	// genuinely separate bucket rather than noise to be folded in.
	if got.CostByModel[0].Name != "claude-opus-5[1m]" || got.CostByModel[0].Value != 342.71 {
		t.Errorf("first model bucket = %+v", got.CostByModel[0])
	}
}

func TestAgentChartsSplitsCostBySource(t *testing.T) {
	// The subagent bucket is the point of this split: delegated work has to be
	// attributable separately from the main thread.
	c, _ := serveJSON(t, chartAggs)

	got, _ := c.GetAgentCharts(context.Background(), "personal", "now-24h")

	names := make([]string, 0, len(got.CostBySource))
	for _, s := range got.CostBySource {
		names = append(names, s.Name)
	}
	if strings.Join(names, ",") != "main,auxiliary,subagent" {
		t.Errorf("CostBySource = %v", names)
	}
}

func TestAgentChartsBucketsByWindow(t *testing.T) {
	// A 15-minute window bucketed at 3h is one bar; a 3-day window bucketed at
	// a minute is 4320. The interval has to follow the range.
	for since, want := range map[string]string{
		"now-15m": "1m",
		"now-60m": "5m",
		"now-24h": "1h",
		"now-3d":  "3h",
	} {
		c, caps := serveJSON(t, chartAggs)
		got, err := c.GetAgentCharts(context.Background(), "personal", since)
		if err != nil {
			t.Fatalf("%s: %v", since, err)
		}
		if got.Granularity != want {
			t.Errorf("%s granularity = %q, want %q", since, got.Granularity, want)
		}
		if !strings.Contains(caps.get("metrics-otel-*"), `"`+want+`"`) {
			t.Errorf("%s: interval %q absent from query", since, want)
		}
	}
}

func TestAgentChartsNeverReturnsNilSeries(t *testing.T) {
	// The panel iterates these; a nil marshals as JSON null and breaks it.
	c, _ := serveJSON(t, `{"aggregations":{}}`)

	got, err := c.GetAgentCharts(context.Background(), "personal", "now-24h")
	if err != nil {
		t.Fatalf("GetAgentCharts: %v", err)
	}
	if got.CostSeries == nil || got.CostByModel == nil || got.CostBySource == nil {
		t.Errorf("nil series: %+v", got)
	}
}

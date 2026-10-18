package langfuse

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

// serveMetrics answers every metrics query from bodies chosen by what the query
// asks for, so one fake can satisfy the four aggregations GetCharts issues.
func serveMetrics(t *testing.T, bodies map[string]string) (*Client, *[]string) {
	t.Helper()
	var mu sync.Mutex
	var seen []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query().Get("query")
		mu.Lock()
		seen = append(seen, q)
		mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		if r.URL.Path == "/api/public/projects" {
			_, _ = io.WriteString(w, `{"data":[{"id":"proj-1","name":"personal"}]}`)
			return
		}
		for key, body := range bodies {
			if strings.Contains(q, key) {
				if body == "" {
					w.WriteHeader(http.StatusBadRequest)
					_, _ = io.WriteString(w, `{"message":"nope"}`)
					return
				}
				_, _ = io.WriteString(w, body)
				return
			}
		}
		_, _ = io.WriteString(w, `{"data":[]}`)
	}))
	t.Cleanup(srv.Close)
	return NewClient(srv.URL, "pk", "sk"), &seen
}

// Shapes copied from what the live API actually returned, null buckets included.
const costSeriesBody = `{"data":[
 {"time_dimension":"2026-10-11","sum_totalCost":null},
 {"time_dimension":"2026-10-13","sum_totalCost":12.5},
 {"time_dimension":"2026-10-12","sum_totalCost":3.25}
]}`

const obsSeriesBody = `{"data":[
 {"time_dimension":"2026-10-11T00:00:00Z","count_count":0},
 {"time_dimension":"2026-10-11T01:00:00Z","count_count":42}
]}`

const byModelBody = `{"data":[
 {"providedModelName":"claude-opus-5","sum_totalCost":527.48},
 {"providedModelName":null,"sum_totalCost":0},
 {"providedModelName":"m","sum_totalCost":0}
]}`

const byTypeBody = `{"data":[
 {"type":"GENERATION","count_count":150},
 {"type":"TOOL","count_count":143},
 {"type":"SPAN","count_count":7},
 {"type":"","count_count":0}
]}`

func TestGetChartsParsesSeriesAndBreakdowns(t *testing.T) {
	// Route by dimension/measure rather than by encoded fragments, which are
	// brittle: a cost series and a cost breakdown both mention totalCost.
	c, _ := serveMetrics(t, map[string]string{
		"timeDimension":     costSeriesBody, // both series hit this first
		"providedModelName": byModelBody,
		`"field":"type"`:    byTypeBody,
	})
	got, err := c.GetCharts(context.Background(), "personal", "now-7d")
	if err != nil {
		t.Fatalf("GetCharts: %v", err)
	}
	if got.Granularity != "day" {
		t.Errorf("granularity = %q, want day for a 7d window", got.Granularity)
	}
	if !got.CostUnderstated {
		t.Error("CostUnderstated must be true for this source")
	}

	// Buckets are sorted by time, and a null bucket becomes 0 rather than
	// being dropped -- dropping it makes the line skip the gap.
	if len(got.CostSeries) != 3 {
		t.Fatalf("cost series = %d points, want 3 (the null bucket is kept)", len(got.CostSeries))
	}
	for i := 1; i < len(got.CostSeries); i++ {
		if got.CostSeries[i].TS < got.CostSeries[i-1].TS {
			t.Error("series is not sorted by time")
		}
	}
	if got.CostSeries[0].Value != 0 {
		t.Errorf("null bucket = %v, want 0", got.CostSeries[0].Value)
	}
}

func TestGetChartsDropsEmptyAndNullCategories(t *testing.T) {
	c, _ := serveMetrics(t, map[string]string{
		"timeDimension":     costSeriesBody,
		"providedModelName": byModelBody,
		`"field":"type"`:    byTypeBody,
	})
	got, err := c.GetCharts(context.Background(), "personal", "now-7d")
	if err != nil {
		t.Fatalf("GetCharts: %v", err)
	}
	// A null model and a zero-cost model are not categories: tool and span
	// rows carry no model, and a zero wedge renders as a label with no area.
	if len(got.CostByModel) != 1 || got.CostByModel[0].Name != "claude-opus-5" {
		t.Errorf("cost by model = %+v, want only claude-opus-5", got.CostByModel)
	}
	// The empty-string type with a zero count goes the same way.
	if len(got.ObsByType) != 3 {
		t.Fatalf("obs by type = %+v, want 3", got.ObsByType)
	}
	// Sorted descending, so the chart legend reads largest first.
	if got.ObsByType[0].Name != "GENERATION" || got.ObsByType[0].Value != 150 {
		t.Errorf("first slice = %+v, want GENERATION 150", got.ObsByType[0])
	}
}

func TestGranularityBoundaries(t *testing.T) {
	now := time.Date(2026, 10, 18, 12, 0, 0, 0, time.UTC)
	// Asserted on BOTH sides of each threshold. A table of typical ranges
	// passes against an off-by-one comparison; these do not.
	cases := []struct {
		d    time.Duration
		want string
	}{
		{time.Minute, "minute"},
		{61 * time.Minute, "minute"}, // inclusive upper bound
		{61*time.Minute + time.Second, "hour"},
		{24 * time.Hour, "hour"},
		{73 * time.Hour, "hour"}, // inclusive upper bound
		{73*time.Hour + time.Second, "day"},
		{7 * 24 * time.Hour, "day"},
	}
	for _, tc := range cases {
		if got := granularityFor(now.Add(-tc.d), now); got != tc.want {
			t.Errorf("granularityFor(-%v) = %q, want %q", tc.d, got, tc.want)
		}
	}
}

func TestParseBucketHandlesBothShapes(t *testing.T) {
	// Both observed live: a bare date at day granularity, an instant at hour.
	for _, s := range []string{"2026-10-11", "2026-10-11T00:00:00Z", "2026-10-11T00:00:00.000Z"} {
		if _, ok := parseBucket(s); !ok {
			t.Errorf("parseBucket(%q) failed", s)
		}
	}
	if _, ok := parseBucket("garbage"); ok {
		t.Error("parseBucket accepted garbage")
	}
}

// One failing aggregation must not lose the other three.
func TestGetChartsSurvivesOneFailedAggregation(t *testing.T) {
	c, _ := serveMetrics(t, map[string]string{
		"timeDimension":     costSeriesBody,
		"providedModelName": "", // 400
		`"field":"type"`:    byTypeBody,
	})
	got, err := c.GetCharts(context.Background(), "personal", "now-7d")
	if err != nil {
		t.Fatalf("one failure must not fail the call: %v", err)
	}
	if len(got.CostByModel) != 0 {
		t.Errorf("failed breakdown should be empty, got %+v", got.CostByModel)
	}
	if len(got.ObsByType) == 0 {
		t.Error("the other aggregations must still be present")
	}
	if got.CostByModel == nil {
		t.Error("CostByModel is nil; it marshals as null and the panel iterates it")
	}
}

// All four failing means the endpoint or credentials are wrong, and that IS an
// error -- silently rendering four empty charts would hide a 401.
func TestGetChartsErrorsWhenEverythingFails(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnauthorized)
		_, _ = io.WriteString(w, `{"message":"Invalid credentials"}`)
	}))
	t.Cleanup(srv.Close)
	if _, err := NewClient(srv.URL, "pk", "sk").
		GetCharts(context.Background(), "personal", "now-7d"); err == nil {
		t.Fatal("expected an error when every aggregation fails")
	}
}

func TestChartsSlicesAreNeverNil(t *testing.T) {
	c, _ := serveMetrics(t, map[string]string{"anything-unmatched": `{"data":[]}`})
	got, err := c.GetCharts(context.Background(), "personal", "now-24h")
	if err != nil {
		t.Fatalf("GetCharts: %v", err)
	}
	blob, err := json.Marshal(got)
	if err != nil {
		t.Fatal(err)
	}
	for _, field := range []string{"cost_series", "obs_series", "cost_by_model", "obs_by_type"} {
		if strings.Contains(string(blob), `"`+field+`":null`) {
			t.Errorf("%s marshalled as null; the panel iterates it and a null threw "+
				"during render once already — %s", field, blob)
		}
	}
}

func TestProjectIDAndUILink(t *testing.T) {
	c, _ := serveMetrics(t, nil)
	id, err := c.ProjectID(context.Background())
	if err != nil {
		t.Fatalf("ProjectID: %v", err)
	}
	if id != "proj-1" {
		t.Errorf("ProjectID = %q, want proj-1", id)
	}
	link := c.UILink(id, "sessions")
	if !strings.HasSuffix(link, "/project/proj-1/sessions") {
		t.Errorf("UILink = %q", link)
	}
	// A leading slash in the path must not double up.
	if c.UILink(id, "/traces") != c.UILink(id, "traces") {
		t.Error("UILink should tolerate a leading slash")
	}
	// No project id means no link, rather than a URL that 404s.
	if c.UILink("", "sessions") != "" {
		t.Error("UILink with no project id should be empty")
	}
}

func TestGranularityCoversTheShortRanges(t *testing.T) {
	now := time.Date(2026, 10, 18, 12, 0, 0, 0, time.UTC)
	// The panel's own range set. Minute buckets for the short ones or a 15m
	// trend chart is a single point; hour up to 3 days, because daily buckets
	// over 3 days is three points.
	cases := map[string]string{
		"now-15m": "minute",
		"now-30m": "minute",
		"now-60m": "minute",
		"now-24h": "hour",
		"now-3d":  "hour",
		"now-7d":  "day",
		"now-30d": "day",
	}
	for since, want := range cases {
		if got := granularityFor(sinceTime(since, now), now); got != want {
			t.Errorf("granularityFor(%s) = %q, want %q", since, got, want)
		}
	}
}

// Point counts per range, so a change to the thresholds cannot quietly produce
// a chart with two points or two hundred.
func TestGranularityYieldsAReadablePointCount(t *testing.T) {
	now := time.Date(2026, 10, 18, 12, 0, 0, 0, time.UTC)
	per := map[string]time.Duration{
		"minute": time.Minute, "hour": time.Hour, "day": 24 * time.Hour,
	}
	for _, since := range []string{"now-15m", "now-30m", "now-60m", "now-24h", "now-3d"} {
		from := sinceTime(since, now)
		g := granularityFor(from, now)
		pts := int(now.Sub(from) / per[g])
		if pts < 10 || pts > 100 {
			t.Errorf("%s at %q granularity gives %d points; want 10..100 for a "+
				"chart a few hundred pixels wide", since, g, pts)
		}
	}
}

func TestFreshnessReportsTheNewestObservation(t *testing.T) {
	const body = `{"data":[{"id":"o1","startTime":"2026-10-18T12:00:00Z",
	  "type":"GENERATION","sessionId":"s1"}]}`
	c, caps := serve(t, body, `{"data":[]}`)
	got, err := c.Freshness(context.Background())
	if err != nil {
		t.Fatalf("Freshness: %v", err)
	}
	want := time.Date(2026, 10, 18, 12, 0, 0, 0, time.UTC)
	if !got.Equal(want) {
		t.Errorf("Freshness = %v, want %v", got, want)
	}
	// startTime lives in the `basic` group; `core` alone omits it, which is the
	// same trap that made the session list return zero rows.
	if q := caps.get(obsPath); !strings.Contains(q, "basic") {
		t.Errorf("Freshness must request the basic field group: %s", q)
	}
}

// An empty project is not an error: it means nothing has been traced yet, and
// the caller renders that differently from a failure.
func TestFreshnessOnAnEmptyProject(t *testing.T) {
	c, _ := serve(t, `{"data":[]}`, `{"data":[]}`)
	got, err := c.Freshness(context.Background())
	if err != nil {
		t.Fatalf("Freshness: %v", err)
	}
	if !got.IsZero() {
		t.Errorf("Freshness = %v, want the zero time", got)
	}
}

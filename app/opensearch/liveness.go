package opensearch

import "time"

// Liveness states. There is no session-end event anywhere in Claude Code
// telemetry — checked across all 23 distinct event names the index has ever
// held — so "finished" is not derivable from this data. These three states are
// deliberately about SILENCE, not completion, and the UI must not imply
// otherwise: LivenessDormant covers finished, crashed and waiting-on-a-human
// identically.
const (
	LivenessActive  = "active"
	LivenessIdle    = "idle"
	LivenessDormant = "dormant"
	// LivenessUnknown is returned when there is no activity timestamp at all,
	// which is distinct from "silent for a long time".
	LivenessUnknown = "unknown"
)

// Liveness thresholds. CHOSEN, not measured — the measurements they are chosen
// against are recorded here so a future reader can see which numbers are
// assumptions and re-derive them.
//
// activeWithin: an actively working session's own event gaps were measured at
// median 0.22s but p90 5.7s and max 80.3s — it goes quiet while it thinks. 120s
// is that 80s worst case plus the 10s OTEL_LOGS_EXPORT_INTERVAL plus margin.
// Anything tighter marks a thinking session dead, which is the bug this
// replaces: the previous 150s gate was applied to the METRIC heartbeat, whose
// gaps are p90 180s, so it reported ~10% of live intervals as dead and did so
// systematically during subagent fan-out.
//
// idleWithin: a judgement about when an operator stops caring, not a
// measurement. There is no signal that distinguishes "idle" from "gone".
const (
	activeWithin = 120 * time.Second
	idleWithin   = 900 * time.Second
)

// ageMillis returns how long ago ts was, in milliseconds, relative to now.
//
// ts is an OpenSearch `strict_date_time` value. Returns ok=false when ts is
// absent or unparseable, so the caller can distinguish "no data" from "old
// data" — collapsing those two is what makes a liveness indicator lie.
//
// A negative age is clamped to zero: the app's clock and the ingest path's
// clock are independent, and a slightly-future timestamp is skew, not an event
// that has not happened yet.
func ageMillis(ts string, now time.Time) (int64, bool) {
	if ts == "" {
		return 0, false
	}
	t, err := time.Parse(time.RFC3339Nano, ts)
	if err != nil {
		return 0, false
	}
	ms := now.Sub(t).Milliseconds()
	if ms < 0 {
		ms = 0
	}
	return ms, true
}

// classifyLiveness turns an activity timestamp into one of the four states.
//
// Only log RECENCY is thresholded. The metric heartbeat is reported alongside
// but never gates the state: it is main-thread only (0 of 26,566 docs carry a
// query_source), so it STOPS during subagent fan-out while the session is
// working hard. Measured during fan-out: log recency lagged 40s, the heartbeat
// 264s. Fusing the two would destroy the one genuinely useful statement this
// data can make — fresh activity with a stale heartbeat means subagents are
// running while the main thread waits.
func classifyLiveness(lastActivity string, now time.Time) (string, int64) {
	age, ok := ageMillis(lastActivity, now)
	if !ok {
		return LivenessUnknown, 0
	}
	switch d := time.Duration(age) * time.Millisecond; {
	case d < activeWithin:
		return LivenessActive, age
	case d < idleWithin:
		return LivenessIdle, age
	default:
		return LivenessDormant, age
	}
}

// clock returns the current time, tolerating a zero-value Client so a test can
// construct one without going through NewClient.
func (c *Client) clock() time.Time {
	if c.now == nil {
		return time.Now()
	}
	return c.now()
}

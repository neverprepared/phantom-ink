package opensearch

import (
	"context"
	"fmt"
	"strconv"
)

// redactedSentinel is the literal value Claude Code's OTEL exporter writes into
// the prompt/response attributes when content logging is disabled. The fields
// are always present in the schema, so an absent flag looks identical to
// captured content unless we test for this. We surface it as Redacted rather
// than letting "<REDACTED>" reach the UI as if it were a message.
const redactedSentinel = "<REDACTED>"

// activityEvents are the event@name values that represent real agent work.
//
// Deliberately excludes tool_decision: Claude Code emits TWO documents per tool
// call — a tool_decision (permission outcome, no success/duration) and a
// tool_result (the outcome) — and BOTH carry tool_name. Counting all docs with
// a tool_name double-counts every call. tool_result is the one that carries the
// result, so it is the one we count.
//
// Also excludes hook_execution_* (the local hook plumbing, not agent work) —
// those are the single highest-volume event pair in the index.
var activityEvents = []string{"tool_result", "user_prompt", "assistant_response", "api_request"}

// AgentSession is one row of the session list: enough to answer "what is
// running, what is it doing, and should I look at it" without opening it.
type AgentSession struct {
	SessionID string `json:"session_id"`
	Workspace string `json:"workspace"`
	// FirstSeen/LastSeen bound the session's activity in this index. A session
	// older than the retention window reports only the part still stored.
	FirstSeen string `json:"first_seen"`
	LastSeen  string `json:"last_seen"`
	// Turns counts distinct prompt@id values — user turns, not tool calls.
	Turns int64 `json:"turns"`
	// ToolCalls counts tool_result docs only (see activityEvents).
	ToolCalls int64 `json:"tool_calls"`
	Failures  int64 `json:"failures"`
	// LastTool is the activity line: the most recent tool this session ran.
	LastTool string   `json:"last_tool"`
	TopTools []string `json:"top_tools"`
	Models   []string `json:"models"`
	CostUSD  float64  `json:"cost_usd"`
	// TokensIn counts input_tokens only, which EXCLUDES cache reads and cache
	// writes — on a long session the cached portion dominates by orders of
	// magnitude (118k cache-read against 2 input on a single observed request).
	// Reporting TokensIn alone beside CostUSD reads as a contradiction, so the
	// cache counters are first-class here rather than folded in or dropped.
	TokensIn       int64 `json:"tokens_in"`
	TokensOut      int64 `json:"tokens_out"`
	TokensCacheIn  int64 `json:"tokens_cache_in"`
	TokensCacheNew int64 `json:"tokens_cache_new"`
}

// ListAgentSessions returns recent agent sessions, most-recently-active first.
//
// since is an OpenSearch date-math expression ("now-24h"); workspace ""
// means unfiltered. One aggregation produces every column — the list does not
// fan out a query per session.
func (c *Client) ListAgentSessions(ctx context.Context, workspace, since string, limit int) ([]AgentSession, error) {
	if limit <= 0 || limit > 200 {
		limit = 50
	}
	if since == "" {
		since = "now-24h"
	}
	must := []any{
		map[string]any{"terms": map[string]any{"log.attributes.event@name.keyword": activityEvents}},
		map[string]any{"range": map[string]any{"time": map[string]any{"gte": since}}},
	}
	must = append(must, workspaceFilter(workspace)...)

	// A filter sub-agg reused for the tool-only rollups.
	toolResultOnly := map[string]any{
		"term": map[string]any{"log.attributes.event@name.keyword": "tool_result"},
	}

	body := map[string]any{
		"size":             0,
		"track_total_hits": false,
		"query":            map[string]any{"bool": map[string]any{"must": must}},
		"aggs": map[string]any{
			"sessions": map[string]any{
				"terms": map[string]any{
					"field": "log.attributes.session@id.keyword",
					"size":  limit,
					"order": map[string]any{"last": "desc"},
				},
				"aggs": map[string]any{
					"last":  map[string]any{"max": map[string]any{"field": "time"}},
					"first": map[string]any{"min": map[string]any{"field": "time"}},
					"turns": map[string]any{"cardinality": map[string]any{"field": "log.attributes.prompt@id.keyword"}},
					"ws": map[string]any{"terms": map[string]any{
						"field": "resource.attributes.workspace.keyword", "size": 3}},
					"models": map[string]any{"terms": map[string]any{
						"field": "log.attributes.model.keyword", "size": 4}},
					"cost":       map[string]any{"sum": map[string]any{"field": "log.attributes.cost_usd"}},
					"tokens_in":  map[string]any{"sum": map[string]any{"field": "log.attributes.input_tokens"}},
					"tokens_out": map[string]any{"sum": map[string]any{"field": "log.attributes.output_tokens"}},
					"cache_in":   map[string]any{"sum": map[string]any{"field": "log.attributes.cache_read_tokens"}},
					"cache_new":  map[string]any{"sum": map[string]any{"field": "log.attributes.cache_creation_tokens"}},
					"calls": map[string]any{
						"filter": toolResultOnly,
						"aggs": map[string]any{
							"tools": map[string]any{"terms": map[string]any{
								"field": "log.attributes.tool_name.keyword", "size": 4}},
							"latest": map[string]any{"top_hits": map[string]any{
								"size":    1,
								"sort":    []any{map[string]any{"time": map[string]any{"order": "desc"}}},
								"_source": []string{"log.attributes.tool_name"},
							}},
						},
					},
					// success is mapped as `text` in this index, not boolean.
					// A boolean term happens to coerce today; the string form
					// is what the mapping actually stores.
					"fails": map[string]any{"filter": map[string]any{
						"term": map[string]any{"log.attributes.success": "false"}}},
				},
			},
		},
	}

	var resp struct {
		Aggregations struct {
			Sessions struct {
				Buckets []struct {
					Key   string `json:"key"`
					Last  valAgg `json:"last"`
					First valAgg `json:"first"`
					Turns struct {
						Value int64 `json:"value"`
					} `json:"turns"`
					WS        termsAgg `json:"ws"`
					Models    termsAgg `json:"models"`
					Cost      sumAgg   `json:"cost"`
					TokensIn  sumAgg   `json:"tokens_in"`
					TokensOut sumAgg   `json:"tokens_out"`
					CacheIn   sumAgg   `json:"cache_in"`
					CacheNew  sumAgg   `json:"cache_new"`
					Calls     struct {
						DocCount int64    `json:"doc_count"`
						Tools    termsAgg `json:"tools"`
						Latest   struct {
							Hits struct {
								Hits []struct {
									Source map[string]any `json:"_source"`
								} `json:"hits"`
							} `json:"hits"`
						} `json:"latest"`
					} `json:"calls"`
					Fails struct {
						DocCount int64 `json:"doc_count"`
					} `json:"fails"`
				} `json:"buckets"`
			} `json:"sessions"`
		} `json:"aggregations"`
	}
	if err := c.search(ctx, "logs-otel-*", body, &resp); err != nil {
		return nil, fmt.Errorf("list agent sessions: %w", err)
	}

	out := make([]AgentSession, 0, len(resp.Aggregations.Sessions.Buckets))
	for _, b := range resp.Aggregations.Sessions.Buckets {
		s := AgentSession{
			SessionID:      b.Key,
			Workspace:      b.WS.first(),
			FirstSeen:      b.First.String,
			LastSeen:       b.Last.String,
			Turns:          b.Turns.Value,
			ToolCalls:      b.Calls.DocCount,
			Failures:       b.Fails.DocCount,
			TopTools:       b.Calls.Tools.keys(),
			Models:         b.Models.keys(),
			CostUSD:        b.Cost.Value,
			TokensIn:       int64(b.TokensIn.Value),
			TokensOut:      int64(b.TokensOut.Value),
			TokensCacheIn:  int64(b.CacheIn.Value),
			TokensCacheNew: int64(b.CacheNew.Value),
		}
		if h := b.Calls.Latest.Hits.Hits; len(h) > 0 {
			s.LastTool = strOf(h[0].Source["log.attributes.tool_name"])
		}
		out = append(out, s)
	}
	return out, nil
}

// SessionEvent is one entry in a session's timeline.
type SessionEvent struct {
	Sequence int64  `json:"sequence"`
	Time     string `json:"time"`
	// Kind is the event@name: user_prompt, assistant_response, tool_result,
	// api_request.
	Kind string `json:"kind"`
	// TurnID is prompt@id — the join key that groups events into a turn.
	TurnID string `json:"turn_id"`

	ToolName   string `json:"tool_name,omitempty"`
	ToolUseID  string `json:"tool_use_id,omitempty"`
	Success    *bool  `json:"success,omitempty"`
	DurationMs int64  `json:"duration_ms,omitempty"`
	InputSize  int64  `json:"input_size,omitempty"`
	ResultSize int64  `json:"result_size,omitempty"`

	Model     string  `json:"model,omitempty"`
	TokensIn  int64   `json:"tokens_in,omitempty"`
	TokensOut int64   `json:"tokens_out,omitempty"`
	CostUSD   float64 `json:"cost_usd,omitempty"`

	// Content holds the prompt or response text when content capture is
	// enabled. Redacted reports that the exporter suppressed it — the
	// distinction matters, because a suppressed field is present-but-sentinel,
	// not absent. ContentLength is emitted regardless, so the UI can show the
	// size of a message it cannot show the text of.
	Content       string `json:"content,omitempty"`
	Redacted      bool   `json:"redacted,omitempty"`
	ContentLength int64  `json:"content_length,omitempty"`
}

// SessionTimeline is a session's full drill-down payload.
type SessionTimeline struct {
	SessionID string         `json:"session_id"`
	Workspace string         `json:"workspace"`
	Events    []SessionEvent `json:"events"`
	// Truncated reports that the session has more events than were returned,
	// so the UI says so instead of implying the session ended.
	Truncated bool `json:"truncated"`
	// ContentAvailable is true when at least one event carried real text.
	// False with events present means capture is off — an actionable state,
	// distinct from "nothing happened".
	ContentAvailable bool `json:"content_available"`
}

// GetSessionTimeline returns one session's events in execution order.
//
// Ordered by event@sequence, which is monotonic within a session and stable
// under equal timestamps — two events in the same millisecond are common
// (a tool_decision and its tool_result), and sorting by time alone interleaves
// them unpredictably.
func (c *Client) GetSessionTimeline(ctx context.Context, sessionID string, limit int) (*SessionTimeline, error) {
	if sessionID == "" {
		return nil, fmt.Errorf("session id is required")
	}
	if limit <= 0 || limit > 2000 {
		limit = 500
	}
	body := map[string]any{
		"size":             limit,
		"track_total_hits": true,
		"sort":             []any{map[string]any{"log.attributes.event@sequence": map[string]any{"order": "asc"}}},
		"query": map[string]any{"bool": map[string]any{"must": []any{
			map[string]any{"term": map[string]any{"log.attributes.session@id.keyword": sessionID}},
			map[string]any{"terms": map[string]any{"log.attributes.event@name.keyword": activityEvents}},
		}}},
		"_source": []string{
			"time",
			"log.attributes.event@name", "log.attributes.event@sequence",
			"log.attributes.prompt@id", "resource.attributes.workspace",
			"log.attributes.tool_name", "log.attributes.tool_use_id",
			"log.attributes.success", "log.attributes.duration_ms",
			"log.attributes.tool_input_size_bytes", "log.attributes.tool_result_size_bytes",
			"log.attributes.model", "log.attributes.input_tokens",
			"log.attributes.output_tokens", "log.attributes.cost_usd",
			"log.attributes.prompt", "log.attributes.prompt_length",
			"log.attributes.response", "log.attributes.response_length",
		},
	}
	var resp struct {
		Hits struct {
			Total struct {
				Value int64 `json:"value"`
			} `json:"total"`
			Hits []struct {
				Source map[string]any `json:"_source"`
			} `json:"hits"`
		} `json:"hits"`
	}
	if err := c.search(ctx, "logs-otel-*", body, &resp); err != nil {
		return nil, fmt.Errorf("session timeline: %w", err)
	}

	tl := &SessionTimeline{
		SessionID: sessionID,
		Events:    make([]SessionEvent, 0, len(resp.Hits.Hits)),
		Truncated: resp.Hits.Total.Value > int64(len(resp.Hits.Hits)),
	}
	for _, h := range resp.Hits.Hits {
		s := h.Source
		if tl.Workspace == "" {
			tl.Workspace = strOf(s["resource.attributes.workspace"])
		}
		ev := SessionEvent{
			Sequence:   numOf(s["log.attributes.event@sequence"]),
			Time:       strOf(s["time"]),
			Kind:       strOf(s["log.attributes.event@name"]),
			TurnID:     strOf(s["log.attributes.prompt@id"]),
			ToolName:   strOf(s["log.attributes.tool_name"]),
			ToolUseID:  strOf(s["log.attributes.tool_use_id"]),
			DurationMs: numOf(s["log.attributes.duration_ms"]),
			InputSize:  numOf(s["log.attributes.tool_input_size_bytes"]),
			ResultSize: numOf(s["log.attributes.tool_result_size_bytes"]),
			Model:      strOf(s["log.attributes.model"]),
			TokensIn:   numOf(s["log.attributes.input_tokens"]),
			TokensOut:  numOf(s["log.attributes.output_tokens"]),
			CostUSD:    floatOf(s["log.attributes.cost_usd"]),
		}
		if b, ok := boolOf(s["log.attributes.success"]); ok {
			ev.Success = &b
		}
		// A turn carries its text in prompt (user) or response (assistant);
		// only one is ever set on a given document.
		for _, pair := range [][2]string{
			{"log.attributes.prompt", "log.attributes.prompt_length"},
			{"log.attributes.response", "log.attributes.response_length"},
		} {
			raw, present := s[pair[0]]
			if !present {
				continue
			}
			text := strOf(raw)
			ev.ContentLength = numOf(s[pair[1]])
			if text == redactedSentinel {
				ev.Redacted = true
			} else if text != "" {
				ev.Content = text
				tl.ContentAvailable = true
			}
		}
		tl.Events = append(tl.Events, ev)
	}
	return tl, nil
}

// --- small shared agg shapes -------------------------------------------------

type valAgg struct {
	Value  float64 `json:"value"`
	String string  `json:"value_as_string"`
}

type sumAgg struct {
	Value float64 `json:"value"`
}

type termsAgg struct {
	Buckets []struct {
		Key string `json:"key"`
	} `json:"buckets"`
}

// keys returns an empty slice, never nil, for an empty bucket list. A nil slice
// marshals to JSON `null` rather than `[]`, and these fields reach the frontend
// typed as string[] — so `null.join(...)` throws inside the template and blanks
// the pane. A prompt-only session (no tool_result docs) hits exactly this.
func (t termsAgg) keys() []string {
	out := make([]string, 0, len(t.Buckets))
	for _, b := range t.Buckets {
		out = append(out, b.Key)
	}
	return out
}

func (t termsAgg) first() string {
	if len(t.Buckets) == 0 {
		return ""
	}
	return t.Buckets[0].Key
}

func floatOf(v any) float64 {
	switch x := v.(type) {
	case float64:
		return x
	case string:
		f, err := strconv.ParseFloat(x, 64)
		if err == nil {
			return f
		}
	}
	return 0
}

// numOf reads an integer attribute that may arrive as a JSON number or as a
// string. This index maps duration_ms, event@sequence and the token counts as
// `long` but tool_input_size_bytes, tool_result_size_bytes, prompt_length and
// response_length as `text` — so a number-only reader silently returns 0 for
// every size we display.
func numOf(v any) int64 {
	switch x := v.(type) {
	case float64:
		return int64(x)
	case int64:
		return x
	case string:
		n, err := strconv.ParseInt(x, 10, 64)
		if err == nil {
			return n
		}
	}
	return 0
}

// boolOf reports a boolean attribute and whether it was present at all. OTEL
// attributes arrive as either a real bool or its string form depending on the
// producer, and absence is meaningful here (a tool_decision has no success).
func boolOf(v any) (bool, bool) {
	switch x := v.(type) {
	case bool:
		return x, true
	case string:
		switch x {
		case "true":
			return true, true
		case "false":
			return false, true
		}
	}
	return false, false
}

package opensearch

import (
	"context"
	"fmt"
	"strconv"
	"strings"
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
//
// subagent_completed is included: it is the one event that NAMES a child agent
// (agent_type, agent@source) and carries its duration, token total and tool
// count, attached to the parent turn via prompt@id. The design doc concluded no
// such record existed.
var activityEvents = []string{
	"tool_result", "user_prompt", "assistant_response", "api_request", "subagent_completed",
}

// shardReport is the _shards block every search returns. The client checks only
// the HTTP status, so a query that failed on some shards comes back 200 with a
// short result — for a session timeline that means silently missing events
// presented as a complete history. Two August indices have no event@sequence
// mapping and fail the timeline sort on every call today.
type shardReport struct {
	Total      int `json:"total"`
	Successful int `json:"successful"`
	Failed     int `json:"failed"`
	Failures   []struct {
		Index  string `json:"index"`
		Reason struct {
			Type   string `json:"type"`
			Reason string `json:"reason"`
		} `json:"reason"`
	} `json:"failures"`
}

// describe renders a one-line operator-facing summary, or "" when nothing failed.
func (s shardReport) describe() string {
	if s.Failed == 0 {
		return ""
	}
	msg := fmt.Sprintf("%d of %d shards failed", s.Failed, s.Total)
	if len(s.Failures) > 0 {
		f := s.Failures[0]
		msg += fmt.Sprintf(" (%s: %s)", f.Index, f.Reason.Reason)
	}
	return msg
}

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
	// LastBeat is the newest claude_code.active_time.total heartbeat for this
	// session, from metrics-otel-*. That metric is a delta sum on a strict 60s
	// cadence and STOPS when the session stops, which makes it the only real
	// liveness signal here — log recency cannot do the job, because an active
	// session's own event gaps reach 80s while it is thinking.
	//
	// Empty means no heartbeat in the window: either the session ended, or it
	// is open but idle. The metric beats only on ACTIVITY, so an idle session
	// emits nothing and is indistinguishable from a finished one.
	LastBeat string `json:"last_beat,omitempty"`

	CostUSD float64 `json:"cost_usd"`
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

	// Liveness is a best-effort overlay: it lives in a different index, and a
	// list that works is worth more than one that fails because the heartbeat
	// query did. On error every row simply reports no heartbeat.
	if beats, err := c.sessionHeartbeats(ctx, workspace, since); err == nil {
		for i := range out {
			out[i].LastBeat = beats[out[i].SessionID]
		}
	}
	return out, nil
}

// sessionHeartbeats maps session id to its newest claude_code.active_time.total
// timestamp.
//
// Note the .keyword subfields: in metrics-otel-* these attributes are mapped
// `text` with a keyword subfield, and some older daily indices carry the text
// mapping WITHOUT the subfield — aggregating the bare field fails 7 shards.
func (c *Client) sessionHeartbeats(ctx context.Context, workspace, since string) (map[string]string, error) {
	if since == "" {
		since = "now-24h"
	}
	must := []any{
		map[string]any{"term": map[string]any{"name": "claude_code.active_time.total"}},
		map[string]any{"range": map[string]any{"time": map[string]any{"gte": since}}},
	}
	if workspace != "" {
		must = append(must, map[string]any{"term": map[string]any{
			"metric.attributes.workspace.keyword": workspace,
		}})
	}
	body := map[string]any{
		"size":             0,
		"track_total_hits": false,
		"query":            map[string]any{"bool": map[string]any{"must": must}},
		"aggs": map[string]any{
			"s": map[string]any{
				"terms": map[string]any{
					"field": "metric.attributes.session@id.keyword",
					"size":  200,
					"order": map[string]any{"last": "desc"},
				},
				"aggs": map[string]any{
					"last": map[string]any{"max": map[string]any{"field": "time"}},
				},
			},
		},
	}
	var resp struct {
		Aggregations struct {
			S struct {
				Buckets []struct {
					Key  string `json:"key"`
					Last valAgg `json:"last"`
				} `json:"buckets"`
			} `json:"s"`
		} `json:"aggregations"`
	}
	if err := c.search(ctx, "metrics-otel-*", body, &resp); err != nil {
		return nil, fmt.Errorf("session heartbeats: %w", err)
	}
	out := make(map[string]string, len(resp.Aggregations.S.Buckets))
	for _, b := range resp.Aggregations.S.Buckets {
		out[b.Key] = b.Last.String
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

	// Agent is the query_source: "repl_main_thread" for the session's own
	// thread, "agent:custom" or "agent:builtin:<Type>" for a subagent, and
	// values like "away_summary"/"prompt_suggestion"/"compact" for harness
	// overhead that still costs money. Present ONLY on api_request and
	// assistant_response — tool_result carries no query_source at all, so a
	// tool call cannot be attributed to a subagent from this index.
	Agent string `json:"agent,omitempty"`
	// AgentType/AgentSource/ToolUses/TotalTokens come from subagent_completed,
	// the child's own summary row.
	AgentType   string `json:"agent_type,omitempty"`
	AgentSource string `json:"agent_source,omitempty"`
	ToolUses    int64  `json:"tool_uses,omitempty"`
	TotalTokens int64  `json:"total_tokens,omitempty"`

	Model     string  `json:"model,omitempty"`
	TokensIn  int64   `json:"tokens_in,omitempty"`
	TokensOut int64   `json:"tokens_out,omitempty"`
	CostUSD   float64 `json:"cost_usd,omitempty"`

	// ToolInput is the serialized tool input, and ToolParams the structured
	// detail the exporter breaks out of it (bash_command, full_command,
	// mcp_tool_name, mcp_server_name, description, timeout). Both are emitted
	// only under OTEL_LOG_TOOL_DETAILS, which carries NO prompt or completion
	// text — so it answers "what did this task touch" at a materially lower
	// privacy cost than the prompt flags.
	//
	// No document in the index carries these yet (the flag is off), so their
	// exact nesting is unverified; they are collected by key prefix rather
	// than by a guessed path.
	ToolInput  string            `json:"tool_input,omitempty"`
	ToolParams map[string]string `json:"tool_params,omitempty"`

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
	// Truncated reports that the session has more events than were returned.
	// The query takes the NEWEST events and this type presents them oldest-
	// first, so what a truncated timeline omits is the EARLIEST part of the
	// session — the opposite of what an ascending query would drop. For a
	// running session the recent end is the useful end.
	Truncated bool `json:"truncated"`
	// TotalEvents is the session's full event count, so the UI can say how
	// much it is not showing.
	TotalEvents int64 `json:"total_events"`
	// Warning is non-empty when the search succeeded only partially (shard
	// failures). Events may be missing, and a drill-down that silently drops
	// events is worse than one that admits it.
	Warning string `json:"warning,omitempty"`
	// ContentAvailable is true when at least one event carried real prompt or
	// completion text. False with events present means the prompt flags are
	// off — an actionable state, distinct from "nothing happened".
	ContentAvailable bool `json:"content_available"`
	// Totals are the session's TRUE rollups, unclipped by any time window.
	// The list's figures are scoped to the selected range, so the same session
	// reports different costs at 6h, 24h and 30d (measured: $144 / $151 /
	// $273). The drill-down is the audit surface and must not inherit that.
	Totals SessionTotals `json:"totals"`
	// ToolDetailsAvailable is the same signal for tool inputs, which are
	// governed by a SEPARATE flag carrying no prompt text. The two are
	// reported independently because an operator may well want one and not
	// the other.
	ToolDetailsAvailable bool `json:"tool_details_available"`
}

// SessionTotals are a session's whole-life rollups, independent of any range.
type SessionTotals struct {
	// AgentSpawns counts Agent tool calls; SubagentReports counts
	// subagent_completed events. They SHOULD match and frequently do not:
	// coverage is bimodal across real sessions (measured: 64 spawns/70
	// reports, but also 38/0 and 45/0). A shortfall means the child detail is
	// missing for those spawns, which the UI must say rather than implying no
	// subagents ran.
	AgentSpawns     int64 `json:"agent_spawns"`
	SubagentReports int64 `json:"subagent_reports"`

	Turns     int64   `json:"turns"`
	ToolCalls int64   `json:"tool_calls"`
	Failures  int64   `json:"failures"`
	CostUSD   float64 `json:"cost_usd"`
	TokensOut int64   `json:"tokens_out"`
	FirstSeen string  `json:"first_seen"`
	LastSeen  string  `json:"last_seen"`
}

// GetSessionTimeline returns one session's events in execution order.
//
// Ordered by event@sequence, which is monotonic within a session and stable
// under equal timestamps — two events in the same millisecond are common
// (a tool_decision and its tool_result), and sorting by time alone interleaves
// them unpredictably.
// workspace, when non-empty, additionally constrains the query to that profile.
// A session id is already unique, so this is defence in depth rather than the
// primary scoping — but it means a caller cannot read another profile's session
// by id alone.
func (c *Client) GetSessionTimeline(ctx context.Context, sessionID, workspace string, limit int) (*SessionTimeline, error) {
	if sessionID == "" {
		return nil, fmt.Errorf("session id is required")
	}
	if limit <= 0 || limit > 2000 {
		limit = 500
	}
	must := []any{
		map[string]any{"term": map[string]any{"log.attributes.session@id.keyword": sessionID}},
		map[string]any{"terms": map[string]any{"log.attributes.event@name.keyword": activityEvents}},
	}
	must = append(must, workspaceFilter(workspace)...)
	body := map[string]any{
		"size":             limit,
		"track_total_hits": true,
		// DESC so a capped query keeps the most recent events; reversed below
		// for display. unmapped_type is required, not optional: two August
		// indices have no event@sequence mapping and fail this sort on every
		// call, and OpenSearch reports that as a 200 with failed shards.
		"sort": []any{map[string]any{"log.attributes.event@sequence": map[string]any{
			"order": "desc", "unmapped_type": "long",
		}}},
		"query": map[string]any{"bool": map[string]any{"must": must}},
		// Computed over the whole matched set, not the `size`-capped page, so
		// these stay correct for a truncated timeline.
		"aggs": map[string]any{
			"turns":      map[string]any{"cardinality": map[string]any{"field": "log.attributes.prompt@id.keyword"}},
			"cost":       map[string]any{"sum": map[string]any{"field": "log.attributes.cost_usd"}},
			"tokens_out": map[string]any{"sum": map[string]any{"field": "log.attributes.output_tokens"}},
			"first":      map[string]any{"min": map[string]any{"field": "time"}},
			"last":       map[string]any{"max": map[string]any{"field": "time"}},
			"calls": map[string]any{"filter": map[string]any{
				"term": map[string]any{"log.attributes.event@name.keyword": "tool_result"}}},
			"fails": map[string]any{"filter": map[string]any{
				"term": map[string]any{"log.attributes.success": "false"}}},
			"spawns": map[string]any{"filter": map[string]any{"bool": map[string]any{"must": []any{
				map[string]any{"term": map[string]any{"log.attributes.event@name.keyword": "tool_result"}},
				map[string]any{"term": map[string]any{"log.attributes.tool_name.keyword": "Agent"}},
			}}}},
			"reports": map[string]any{"filter": map[string]any{
				"term": map[string]any{"log.attributes.event@name.keyword": "subagent_completed"}}},
		},
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
			"log.attributes.tool_input", "log.attributes.tool_parameters*",
			"log.attributes.query_source",
			"log.attributes.agent_type", "log.attributes.agent@source",
			"log.attributes.total_tool_uses", "log.attributes.total_tokens",
		},
	}
	var resp struct {
		Shards       shardReport `json:"_shards"`
		Aggregations struct {
			Turns struct {
				Value int64 `json:"value"`
			} `json:"turns"`
			Cost      sumAgg `json:"cost"`
			TokensOut sumAgg `json:"tokens_out"`
			First     valAgg `json:"first"`
			Last      valAgg `json:"last"`
			Calls     struct {
				DocCount int64 `json:"doc_count"`
			} `json:"calls"`
			Fails struct {
				DocCount int64 `json:"doc_count"`
			} `json:"fails"`
			Spawns struct {
				DocCount int64 `json:"doc_count"`
			} `json:"spawns"`
			Reports struct {
				DocCount int64 `json:"doc_count"`
			} `json:"reports"`
		} `json:"aggregations"`
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
		SessionID:   sessionID,
		Workspace:   workspace,
		Events:      make([]SessionEvent, 0, len(resp.Hits.Hits)),
		TotalEvents: resp.Hits.Total.Value,
		Truncated:   resp.Hits.Total.Value > int64(len(resp.Hits.Hits)),
		Warning:     resp.Shards.describe(),
		Totals: SessionTotals{
			AgentSpawns:     resp.Aggregations.Spawns.DocCount,
			SubagentReports: resp.Aggregations.Reports.DocCount,
			Turns:           resp.Aggregations.Turns.Value,
			ToolCalls:       resp.Aggregations.Calls.DocCount,
			Failures:        resp.Aggregations.Fails.DocCount,
			CostUSD:         resp.Aggregations.Cost.Value,
			TokensOut:       int64(resp.Aggregations.TokensOut.Value),
			FirstSeen:       resp.Aggregations.First.String,
			LastSeen:        resp.Aggregations.Last.String,
		},
	}
	for _, h := range resp.Hits.Hits {
		s := h.Source
		if tl.Workspace == "" {
			tl.Workspace = strOf(s["resource.attributes.workspace"])
		}
		ev := SessionEvent{
			Sequence:    numOf(s["log.attributes.event@sequence"]),
			Time:        strOf(s["time"]),
			Kind:        strOf(s["log.attributes.event@name"]),
			TurnID:      strOf(s["log.attributes.prompt@id"]),
			ToolName:    strOf(s["log.attributes.tool_name"]),
			ToolUseID:   strOf(s["log.attributes.tool_use_id"]),
			DurationMs:  numOf(s["log.attributes.duration_ms"]),
			InputSize:   numOf(s["log.attributes.tool_input_size_bytes"]),
			ResultSize:  numOf(s["log.attributes.tool_result_size_bytes"]),
			Model:       strOf(s["log.attributes.model"]),
			TokensIn:    numOf(s["log.attributes.input_tokens"]),
			TokensOut:   numOf(s["log.attributes.output_tokens"]),
			CostUSD:     floatOf(s["log.attributes.cost_usd"]),
			Agent:       strOf(s["log.attributes.query_source"]),
			AgentType:   strOf(s["log.attributes.agent_type"]),
			AgentSource: strOf(s["log.attributes.agent@source"]),
			ToolUses:    numOf(s["log.attributes.total_tool_uses"]),
			TotalTokens: numOf(s["log.attributes.total_tokens"]),
		}
		if b, ok := boolOf(s["log.attributes.success"]); ok {
			ev.Success = &b
		}
		ev.ToolInput = strOf(s["log.attributes.tool_input"])
		// Collected by prefix: the exporter may deliver these flattened
		// (log.attributes.tool_parameters.bash_command) or nested, and with no
		// documents to read we do not guess which.
		const paramPrefix = "log.attributes.tool_parameters"
		for k, v := range s {
			if !strings.HasPrefix(k, paramPrefix) {
				continue
			}
			name := strings.TrimPrefix(strings.TrimPrefix(k, paramPrefix), ".")
			if name == "" {
				name = "input"
			}
			if text := strOf(v); text != "" {
				if ev.ToolParams == nil {
					ev.ToolParams = map[string]string{}
				}
				ev.ToolParams[name] = text
			}
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
		if ev.ToolInput != "" || len(ev.ToolParams) > 0 {
			tl.ToolDetailsAvailable = true
		}
		tl.Events = append(tl.Events, ev)
	}
	// The query returned newest-first so that capping keeps the recent end;
	// present oldest-first, which is how a session is read.
	for i, j := 0, len(tl.Events)-1; i < j; i, j = i+1, j-1 {
		tl.Events[i], tl.Events[j] = tl.Events[j], tl.Events[i]
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

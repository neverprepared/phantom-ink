package main

import (
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/wailsapp/wails/v2/pkg/runtime"

	"phantom-ink/brainbox"
)

// AttentionItem is one card in the Stream panel's "Attention" tab. It comes
// from a variety of backend sources and gets a stable composite ID so
// dismissals and resolutions persist across app restarts.
type AttentionItem struct {
	ID          string   `json:"id"`        // "<source>:<sourceID>"
	Source      string   `json:"source"`    // "task" | "chain" | "entry" | "hub" | "bus"
	SourceID    string   `json:"source_id"` // raw id within the source
	Status      string   `json:"status"`    // "failed" | "blocked" | "needs_action" — drives badge
	Title       string   `json:"title"`
	Subtitle    string   `json:"subtitle"`
	Reason      string   `json:"reason"`    // why this needs attention (often the error message)
	Workspace   string   `json:"workspace"` // for profile filter
	Time        int64    `json:"time"`      // epoch ms (sort key, newest first)
	URL         string   `json:"url,omitempty"`
	Actions     []string `json:"actions"`   // ["retry","open","respond","dismiss"]
	UserReply   string   `json:"user_reply,omitempty"`
	// Owning resources extracted from envelope metadata so the UI can show
	// session / runner chips without forcing an extra round-trip per row.
	SessionName string   `json:"session_name,omitempty"`
	RunnerName  string   `json:"runner_name,omitempty"`
}

// OpenTarget tells the frontend which panel to navigate to for an attention item.
type OpenTarget struct {
	Panel string `json:"panel"` // "chains" | "jobs" | "sessions"
	Ref   string `json:"ref"`   // run id or task id
}

// ListAttention returns items requiring user focus, filtered by workspace
// (empty string = all). Bus-only as of P5: queries brainbox /api/agent_state
// for envelopes whose status is attention-eligible, then overlays local
// dismissals and user replies.
func (a *App) ListAttention(workspace string) ([]AttentionItem, error) {
	if a.db == nil {
		return nil, fmt.Errorf("database not available")
	}
	if a.client == nil {
		return nil, nil // brainbox unreachable — no bus, no attention
	}

	dismissed, err := a.db.DismissedAttentionSet()
	if err != nil {
		return nil, fmt.Errorf("dismissed set: %w", err)
	}
	replies, _ := a.db.AttentionReplies()

	busItems, err := a.client.ListAgentState(brainbox.ListAgentStateOptions{
		Status:    "failed,blocked,needs_action",
		Workspace: workspace,
		Limit:     200,
	})
	if err != nil {
		return nil, fmt.Errorf("list agent_state: %w", err)
	}

	items := make([]AttentionItem, 0, len(busItems))
	for _, it := range busItems {
		if dismissed[it.ID] {
			continue
		}
		items = append(items, AttentionItem{
			ID:          it.ID,
			Source:      "bus",
			SourceID:    it.ID,
			Status:      it.Status,
			Title:       it.Title,
			Subtitle:    it.Subtitle,
			Reason:      busReason(it),
			Workspace:   it.Workspace,
			Time:        it.UpdatedAt,
			URL:         it.URL,
			Actions:     busActions(it),
			UserReply:   replies[it.ID],
			SessionName: metaString(it.Metadata, "session_name", "session", "session_id"),
			RunnerName:  metaString(it.Metadata, "runner_name", "runner", "runner_id"),
		})
	}

	sort.Slice(items, func(i, j int) bool { return items[i].Time > items[j].Time })
	return items, nil
}

// metaString returns the first non-empty string value found under any of the
// supplied keys in m. Envelope producers are inconsistent about naming
// (session vs session_name vs session_id), so we tolerate several shapes.
func metaString(m map[string]interface{}, keys ...string) string {
	if m == nil {
		return ""
	}
	for _, k := range keys {
		if v, ok := m[k].(string); ok && v != "" {
			return v
		}
	}
	return ""
}

// busReason extracts the human-readable error from a bus envelope's metadata.
// Returns "" when no error is present — the status badge already conveys the
// lifecycle state, so we don't echo it as the reason text.
func busReason(it brainbox.AgentStateItem) string {
	if it.Metadata != nil {
		if v, ok := it.Metadata["last_error"].(string); ok && v != "" {
			return truncate(v, 200)
		}
		if v, ok := it.Metadata["error"].(string); ok && v != "" {
			return truncate(v, 200)
		}
		if v, ok := it.Metadata["reason"].(string); ok && v != "" {
			return truncate(v, 200)
		}
	}
	return ""
}

// entryStatusToAttention maps a collected_entries status string into one of
// the three attention-eligible statuses. Anything we don't recognise is
// surfaced as "needs_action" (the most neutral catch-all for entries with
// pending actions).
func entryStatusToAttention(s string) string {
	switch strings.ToLower(s) {
	case "failed", "error":
		return "failed"
	case "blocked":
		return "blocked"
	case "action_needed", "action-needed":
		return "needs_action"
	}
	return "needs_action"
}

// busActions returns the action slugs surfaced on a bus card. We keep the
// existing four (retry/open/respond/dismiss) and let producers narrow via
// the envelope's `actions[]` field in a later phase.
func busActions(it brainbox.AgentStateItem) []string {
	if len(it.Actions) > 0 {
		out := make([]string, 0, len(it.Actions))
		for _, a := range it.Actions {
			if lbl, ok := a["kind"].(string); ok && lbl != "" {
				out = append(out, lbl)
			}
		}
		if len(out) > 0 {
			out = append(out, "dismiss")
			return out
		}
	}
	return []string{"open", "dismiss"}
}

// DismissAttention records the envelope id in the local dismissed_attention
// table so the bus aggregator filters it out on the next ListAttention call.
// Per P5 the bus is the single attention source; the legacy attention_items
// resolve path is gone.
func (a *App) DismissAttention(id string) error {
	return a.recordAction(id, "dismiss", ActorUser, func() error {
		if a.db == nil {
			return fmt.Errorf("database not available")
		}
		return a.db.DismissAttentionRow(id)
	})
}

// RestoreAttention removes a dismissal — used by an "undo" UI.
func (a *App) RestoreAttention(id string) error {
	return a.recordAction(id, "restore", ActorUser, func() error {
		if a.db == nil {
			return fmt.Errorf("database not available")
		}
		return a.db.UndismissAttentionRow(id)
	})
}

// AttentionRetry re-dispatches the work that caused the bus item and dismisses
// it. The retry path is driven entirely by the envelope id prefix and the
// envelope's metadata:
//   - task:*  → local queue task; calls RetryTask with the stripped id
//   - chain:* → reads chain_id/input/cwd from metadata and re-enqueues
//   - other   → returns an error (the source doesn't support retry today)
//
// On success the row is dismissed so it falls out of the attention list. The
// underlying job will surface a fresh bus envelope as it runs.
func (a *App) AttentionRetry(id string) error {
	return a.recordAction(id, "retry", ActorUser, func() error {
		if a.db == nil || a.client == nil {
			return fmt.Errorf("database or brainbox not available")
		}

		switch {
		case strings.HasPrefix(id, "task:"):
			taskID := strings.TrimPrefix(id, "task:")
			if err := a.RetryTask(taskID); err != nil {
				return fmt.Errorf("retry task: %w", err)
			}

		case strings.HasPrefix(id, "chain:"):
			env, ok, err := a.client.GetAgentState(id)
			if err != nil {
				return fmt.Errorf("fetch chain envelope: %w", err)
			}
			if !ok {
				return fmt.Errorf("chain envelope %q not found", id)
			}
			chainID, _ := env.Metadata["chain_id"].(string)
			input, _ := env.Metadata["input"].(string)
			cwd, _ := env.Metadata["cwd"].(string)
			if chainID == "" {
				return fmt.Errorf("chain envelope missing chain_id metadata")
			}
			if _, err := a.EnqueueTask(EnqueueTaskRequest{
				ChainID:          chainID,
				Input:            input,
				Cwd:              cwd,
				Trigger:          TriggerManual,
				WorkspaceProfile: env.Workspace,
			}); err != nil {
				return fmt.Errorf("re-enqueue chain: %w", err)
			}

		default:
			return fmt.Errorf("envelope %q does not support retry", id)
		}

		// Hide the card now that we've dispatched a follow-up.
		return a.db.DismissAttentionRow(id)
	})
}

// AttentionRespond stores the user's reply against the envelope id and emits
// an event so automations can hook into it. The item is not auto-dismissed —
// the user dismisses explicitly after following up.
//
// P5 note: replies live in attention_replies (local overlay), keyed by
// envelope id; the bus envelope itself is not mutated.
func (a *App) AttentionRespond(id, text string) error {
	return a.recordAction(id, "respond", ActorUser, func() error {
		if a.db == nil {
			return fmt.Errorf("database not available")
		}
		if err := a.db.SetAttentionReply(id, text); err != nil {
			return err
		}
		if a.ctx != nil {
			runtime.EventsEmit(a.ctx, "attention:responded", map[string]string{"id": id, "text": text})
		}
		return nil
	})
}

// AttentionOpenTarget returns the panel and ref the frontend should navigate to
// for a given attention item.
func (a *App) AttentionOpenTarget(id string) (OpenTarget, error) {
	parts := strings.SplitN(id, ":", 2)
	if len(parts) != 2 {
		return OpenTarget{}, fmt.Errorf("invalid attention item id: %s", id)
	}
	source, sourceID := parts[0], parts[1]
	switch source {
	case "task":
		return OpenTarget{Panel: "jobs", Ref: sourceID}, nil
	case "chain":
		return OpenTarget{Panel: "chains", Ref: sourceID}, nil
	case "hub":
		return OpenTarget{Panel: "sessions", Ref: sourceID}, nil
	case "entry":
		return OpenTarget{Panel: "timeline", Ref: sourceID}, nil
	default:
		return OpenTarget{Panel: "jobs", Ref: sourceID}, nil
	}
}

// ── helpers ────────────────────────────────────────────────────────────────────

func truncate(s string, n int) string {
	s = strings.TrimSpace(s)
	if len(s) <= n {
		return s
	}
	return s[:n-1] + "…"
}

func firstNonEmpty(vals ...string) string {
	for _, v := range vals {
		if v != "" {
			return v
		}
	}
	return ""
}

// errString turns a brainbox Task.Error (interface{}, could be string or
// {message, code, ...}) into a flat human string.
func errString(v any) string {
	switch x := v.(type) {
	case nil:
		return ""
	case string:
		return truncate(x, 200)
	case map[string]any:
		if msg, ok := x["message"].(string); ok {
			return truncate(msg, 200)
		}
		if b, err := json.Marshal(x); err == nil {
			return truncate(string(b), 200)
		}
	}
	return ""
}

// coerceMillis accepts the int/float/string variants brainbox returns for
// CreatedAt/UpdatedAt and returns epoch milliseconds, or 0 on failure.
func coerceMillis(v any) int64 {
	switch x := v.(type) {
	case float64:
		if x < 1e12 { // seconds
			return int64(x * 1000)
		}
		return int64(x)
	case int64:
		if x < 1e12 {
			return x * 1000
		}
		return x
	case string:
		if t, err := time.Parse(time.RFC3339, x); err == nil {
			return t.UnixMilli()
		}
	}
	return 0
}

// hasActions returns true when an entry's Actions JSON is a non-empty array.
// Used by the bus bridge in app_envelope.go to decide whether a collected
// entry warrants an envelope.
func hasActions(raw json.RawMessage) bool {
	if len(raw) == 0 {
		return false
	}
	var arr []any
	if err := json.Unmarshal(raw, &arr); err != nil {
		return false
	}
	return len(arr) > 0
}

// chainNameOrID returns the chain's human-readable name, falling back to the
// id when the chain is not found.
func chainNameOrID(db *DB, chainID string) string {
	if db == nil {
		return chainID
	}
	row, ok := db.GetChain(chainID)
	if !ok || row.Name == "" {
		return chainID
	}
	return row.Name
}

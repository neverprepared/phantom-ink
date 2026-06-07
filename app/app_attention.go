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
	ID        string   `json:"id"`        // "<source>:<sourceID>"
	Source    string   `json:"source"`    // "task" | "chain" | "entry" | "hub"
	SourceID  string   `json:"source_id"` // raw id within the source
	Title     string   `json:"title"`
	Subtitle  string   `json:"subtitle"`
	Reason    string   `json:"reason"`    // why this needs attention
	Workspace string   `json:"workspace"` // for profile filter
	Time      int64    `json:"time"`      // epoch ms (sort key, newest first)
	URL       string   `json:"url,omitempty"`
	Actions   []string `json:"actions"`   // ["retry","open","respond","dismiss"]
	UserReply string   `json:"user_reply,omitempty"`
}

// OpenTarget tells the frontend which panel to navigate to for an attention item.
type OpenTarget struct {
	Panel string `json:"panel"` // "chains" | "jobs" | "sessions"
	Ref   string `json:"ref"`   // run id or task id
}

// ListAttention returns items requiring user focus, filtered by workspace
// (empty string = all). Active producer-driven items are unioned with the two
// legacy scraped sources; dismissed items are excluded.
func (a *App) ListAttention(workspace string) ([]AttentionItem, error) {
	if a.db == nil {
		return nil, fmt.Errorf("database not available")
	}
	dismissed, err := a.db.DismissedAttentionSet()
	if err != nil {
		return nil, fmt.Errorf("dismissed set: %w", err)
	}

	var items []AttentionItem

	// ── Source 1: producer-driven attention_items ─────────────────────────────
	rows, err := a.db.ListActiveAttention(workspace)
	if err == nil {
		for _, r := range rows {
			items = append(items, AttentionItem{
				ID:        r.ID,
				Source:    r.Source,
				SourceID:  r.SourceID,
				Title:     r.Title,
				Subtitle:  r.Subtitle,
				Reason:    r.Reason,
				Workspace: r.Workspace,
				Time:      r.CreatedAt,
				URL:       r.URL,
				Actions:   r.Actions,
				UserReply: r.UserReply,
			})
		}
	}

	// ── Source 2: failed hub tasks (legacy scrape) ────────────────────────────
	if a.client != nil {
		tasks, err := a.client.ListTasks("failed", workspace)
		if err == nil {
			for _, t := range tasks {
				id := "hub:" + t.ID
				if dismissed[id] {
					continue
				}
				actions := []string{"open", "dismiss"}
				items = append(items, AttentionItem{
					ID:       id,
					Source:   "hub",
					SourceID: t.ID,
					Title:    truncate(t.Description, 80),
					Subtitle: fmt.Sprintf("%s · %s", t.AgentName, t.SessionName),
					Reason:   errString(t.Error),
					Workspace: t.WorkspaceProfile,
					Time:     coerceMillis(t.UpdatedAt),
					Actions:  actions,
				})
			}
		}
	}

	// ── Source 3: collected entries with actions[] (legacy scrape) ────────────
	entries, err := a.db.ListCollectedEntries(workspace, "", "")
	if err == nil {
		for _, e := range entries {
			if !hasActions(e.Actions) {
				continue
			}
			id := "entry:" + e.JobID + "/" + e.EntryID
			if dismissed[id] {
				continue
			}
			actions := []string{"dismiss"}
			if e.URL != "" {
				actions = []string{"open", "dismiss"}
			}
			items = append(items, AttentionItem{
				ID:       id,
				Source:   "entry",
				SourceID: e.EntryID,
				Title:    firstNonEmpty(e.Title, e.EntryID),
				Subtitle: fmt.Sprintf("%s · %s", e.Kind, e.JobID),
				Reason:   statusReason(e.Status),
				Workspace: e.Profile,
				Time:     timeFromEntry(e),
				URL:      e.URL,
				Actions:  actions,
			})
		}
	}

	// ── Source 4: brainbox agent_state (P2 — agent event bus) ────────────────
	// Pulls every envelope whose status is attention-eligible. We dedup by
	// the local composite id so legacy and bus-sourced rows don't double-up
	// during the transition window.
	seen := make(map[string]bool, len(items))
	for _, it := range items {
		seen[it.ID] = true
	}
	if a.client != nil {
		busItems, err := a.client.ListAgentState(brainbox.ListAgentStateOptions{
			Status:    "failed,blocked,needs_action",
			Workspace: workspace,
			Limit:     200,
		})
		if err == nil {
			for _, it := range busItems {
				if dismissed[it.ID] {
					continue
				}
				if seen[it.ID] {
					continue
				}
				items = append(items, AttentionItem{
					ID:        it.ID,
					Source:    "bus",
					SourceID:  it.ID,
					Title:     it.Title,
					Subtitle:  it.Subtitle,
					Reason:    busReason(it),
					Workspace: it.Workspace,
					Time:      it.UpdatedAt,
					URL:       it.URL,
					Actions:   busActions(it),
				})
			}
		}
	}

	sort.Slice(items, func(i, j int) bool { return items[i].Time > items[j].Time })
	return items, nil
}

// busReason extracts a short human reason from a bus envelope. Falls back to
// the status when no error metadata is present.
func busReason(it brainbox.AgentStateItem) string {
	if it.Metadata != nil {
		if v, ok := it.Metadata["last_error"].(string); ok && v != "" {
			return truncate(v, 200)
		}
		if v, ok := it.Metadata["error"].(string); ok && v != "" {
			return truncate(v, 200)
		}
	}
	return statusReason(it.Status)
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

// DismissAttention resolves producer-driven items (removes from active queue)
// or persists a legacy dismissal for scraped items.
func (a *App) DismissAttention(id string) error {
	return a.recordAction(id, "dismiss", ActorUser, func() error {
		if a.db == nil {
			return fmt.Errorf("database not available")
		}
		if resolved, err := a.db.ResolveAttentionItem(id); err != nil {
			return err
		} else if resolved {
			return nil
		}
		return a.db.DismissAttentionRow(id)
	})
}

// RestoreAttention removes a legacy dismissal — used by an "undo" UI.
func (a *App) RestoreAttention(id string) error {
	return a.recordAction(id, "restore", ActorUser, func() error {
		if a.db == nil {
			return fmt.Errorf("database not available")
		}
		return a.db.UndismissAttentionRow(id)
	})
}

// AttentionRetry re-dispatches the work that caused the attention item and
// resolves it. task:* items retry the local queue task; chain:* items
// re-enqueue a new task with the original chain input.
func (a *App) AttentionRetry(id string) error {
	return a.recordAction(id, "retry", ActorUser, func() error {
		if a.db == nil {
			return fmt.Errorf("database not available")
		}
		row, ok := a.db.GetAttentionItem(id)
		if !ok {
			return fmt.Errorf("attention item %q not found", id)
		}
		switch row.Source {
		case "task":
			if err := a.RetryTask(row.SourceID); err != nil {
				return fmt.Errorf("retry task: %w", err)
			}
		case "chain":
			var ctx map[string]any
			_ = json.Unmarshal([]byte(row.ContextJSON), &ctx)
			chainID, _ := ctx["chain_id"].(string)
			input, _ := ctx["input"].(string)
			cwd, _ := ctx["cwd"].(string)
			profile, _ := ctx["workspace_profile"].(string)
			if chainID == "" {
				return fmt.Errorf("chain attention item missing context")
			}
			if _, err := a.EnqueueTask(EnqueueTaskRequest{
				ChainID:          chainID,
				Input:            input,
				Cwd:              cwd,
				Trigger:          TriggerManual,
				WorkspaceProfile: profile,
			}); err != nil {
				return fmt.Errorf("re-enqueue chain: %w", err)
			}
		default:
			return fmt.Errorf("source %q does not support retry", row.Source)
		}
		_, err := a.db.ResolveAttentionItem(id)
		return err
	})
}

// AttentionRespond stores the user's reply on the item and emits an event so
// automations can hook into it. The item is not auto-resolved — the user
// dismisses explicitly after following up.
func (a *App) AttentionRespond(id, text string) error {
	return a.recordAction(id, "respond", ActorUser, func() error {
		if a.db == nil {
			return fmt.Errorf("database not available")
		}
		if err := a.db.SetAttentionUserReply(id, text); err != nil {
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

func timeFromEntry(e CollectedEntry) int64 {
	if e.StartAt != nil {
		return *e.StartAt * 1000
	}
	return e.CollectedAt * 1000
}

// hasActions returns true when the entry's Actions JSON is a non-empty array.
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

func statusReason(status string) string {
	switch strings.ToLower(status) {
	case "action_needed", "action-needed":
		return "needs action"
	case "blocked":
		return "blocked"
	case "failed", "error":
		return "failed"
	case "":
		return "has pending actions"
	default:
		return status
	}
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

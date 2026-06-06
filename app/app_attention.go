package main

import (
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"time"
)

// AttentionItem is one card in the Stream panel's "Attention" tab. It comes
// from a variety of backend sources (failed hub tasks, collected entries with
// actions, etc.) and gets a stable composite ID so dismissals persist across
// app restarts.
type AttentionItem struct {
	ID        string          `json:"id"`        // "<source>:<sourceID>"
	Source    string          `json:"source"`    // "task" | "entry"
	Title     string          `json:"title"`
	Subtitle  string          `json:"subtitle"`
	Reason    string          `json:"reason"`    // why this needs attention
	Workspace string          `json:"workspace"` // for profile filter
	Time      int64           `json:"time"`      // epoch ms (sort key, newest first)
	URL       string          `json:"url,omitempty"`
	Actions   json.RawMessage `json:"actions,omitempty"` // passthrough for entry actions
}

// ListAttention returns items requiring user focus, filtered by workspace
// (empty string = all). Dismissed items are excluded.
func (a *App) ListAttention(workspace string) ([]AttentionItem, error) {
	if a.db == nil {
		return nil, fmt.Errorf("database not available")
	}
	dismissed, err := a.db.DismissedAttentionSet()
	if err != nil {
		return nil, fmt.Errorf("dismissed set: %w", err)
	}

	var items []AttentionItem

	// ── Source 1: failed hub tasks ────────────────────────────────────────────
	if a.client != nil {
		tasks, err := a.client.ListTasks("failed", workspace)
		if err == nil {
			for _, t := range tasks {
				id := "task:" + t.ID
				if dismissed[id] {
					continue
				}
				items = append(items, AttentionItem{
					ID:        id,
					Source:    "task",
					Title:     truncate(t.Description, 80),
					Subtitle:  fmt.Sprintf("%s · %s", t.AgentName, t.SessionName),
					Reason:    errString(t.Error),
					Workspace: t.WorkspaceProfile,
					Time:      coerceMillis(t.UpdatedAt),
				})
			}
		}
		// Errors here are non-fatal — brainbox may be down; we still want
		// collected entries to render. Surface via empty list rather than
		// erroring the whole call.
	}

	// ── Source 2: collected entries with actions[] populated ──────────────────
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
			items = append(items, AttentionItem{
				ID:        id,
				Source:    "entry",
				Title:     firstNonEmpty(e.Title, e.EntryID),
				Subtitle:  fmt.Sprintf("%s · %s", e.Kind, e.JobID),
				Reason:    statusReason(e.Status),
				Workspace: e.Profile,
				Time:      timeFromEntry(e),
				URL:       e.URL,
				Actions:   e.Actions,
			})
		}
	}

	sort.Slice(items, func(i, j int) bool { return items[i].Time > items[j].Time })
	return items, nil
}

// DismissAttention persists a dismissal so the item won't reappear.
func (a *App) DismissAttention(id string) error {
	if a.db == nil {
		return fmt.Errorf("database not available")
	}
	return a.db.DismissAttentionRow(id)
}

// RestoreAttention removes a dismissal — used by an "undo" UI.
func (a *App) RestoreAttention(id string) error {
	if a.db == nil {
		return fmt.Errorf("database not available")
	}
	return a.db.UndismissAttentionRow(id)
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

package brainbox

import (
	"encoding/json"
	"fmt"
)

// AgentEventBatch is the wire shape accepted by POST /api/agent_events.
// Envelopes are passed through as raw JSON so callers can use the canonical
// outbox.Envelope type without importing it here (avoiding a cyclic dep).
type AgentEventBatch struct {
	Events []json.RawMessage `json:"events"`
}

// AgentEventIngestResult mirrors the brainbox response.
type AgentEventIngestResult struct {
	Ingested int      `json:"ingested"`
	IDs      []string `json:"ids"`
}

// IngestAgentEvents POSTs a batch of envelopes (already JSON-encoded) to
// brainbox. Returns the number ingested and their ids on success.
func (c *Client) IngestAgentEvents(envelopes []json.RawMessage) (AgentEventIngestResult, error) {
	if len(envelopes) == 0 {
		return AgentEventIngestResult{}, nil
	}
	var result AgentEventIngestResult
	if err := c.post("/api/agent_events", AgentEventBatch{Events: envelopes}, &result); err != nil {
		return AgentEventIngestResult{}, fmt.Errorf("ingest agent events: %w", err)
	}
	return result, nil
}

// AgentStateItem is one row from GET /api/agent_state.
// Fields mirror brainbox/src/brainbox/agent_store.py:_row_to_state.
type AgentStateItem struct {
	ID         string                 `json:"id"`
	Kind       string                 `json:"kind"`
	Source     string                 `json:"source"`
	Type       string                 `json:"type"`
	Status     string                 `json:"status"`
	Title      string                 `json:"title"`
	Subtitle   string                 `json:"subtitle"`
	Workspace  string                 `json:"workspace"`
	ParentID   string                 `json:"parent_id"`
	URL        string                 `json:"url"`
	StartAt    *int64                 `json:"start_at"`
	EndAt      *int64                 `json:"end_at"`
	Tags       []string               `json:"tags"`
	Metadata   map[string]interface{} `json:"metadata"`
	Actions    []map[string]any       `json:"actions"`
	Outcome    map[string]any         `json:"outcome"`
	CreatedAt  int64                  `json:"created_at"`
	UpdatedAt  int64                  `json:"updated_at"`
}

// ListAgentStateOptions narrows the brainbox /api/agent_state query.
type ListAgentStateOptions struct {
	Status    string // comma-separated, e.g. "failed,blocked,needs_action"
	Workspace string
	Source    string
	ParentID  string
	Limit     int
}

type agentStateResp struct {
	Items []AgentStateItem `json:"items"`
	Count int              `json:"count"`
}

// ListAgentState fetches current-state envelopes from brainbox, filtered.
func (c *Client) ListAgentState(opts ListAgentStateOptions) ([]AgentStateItem, error) {
	q := "?"
	add := func(k, v string) {
		if v == "" {
			return
		}
		if len(q) > 1 {
			q += "&"
		}
		q += k + "=" + v
	}
	add("status", opts.Status)
	add("workspace", opts.Workspace)
	add("source", opts.Source)
	add("parent_id", opts.ParentID)
	if opts.Limit > 0 {
		add("limit", fmt.Sprintf("%d", opts.Limit))
	}
	path := "/api/agent_state" + q
	var resp agentStateResp
	if err := c.get(path, &resp); err != nil {
		return nil, fmt.Errorf("list agent state: %w", err)
	}
	return resp.Items, nil
}

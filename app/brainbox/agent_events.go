package brainbox

import (
	"encoding/json"
	"fmt"
	"net/url"
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
	ID          string                 `json:"id"`
	Kind        string                 `json:"kind"`
	Source      string                 `json:"source"`
	Type        string                 `json:"type"`
	Status      string                 `json:"status"`
	Title       string                 `json:"title"`
	Subtitle    string                 `json:"subtitle"`
	Description string                 `json:"description"`
	Workspace   string                 `json:"workspace"`
	ParentID    string                 `json:"parent_id"`
	URL         string                 `json:"url"`
	StartAt     *int64                 `json:"start_at"`
	EndAt       *int64                 `json:"end_at"`
	Tags        []string               `json:"tags"`
	Metadata    map[string]interface{} `json:"metadata"`
	Actions     []map[string]any       `json:"actions"`
	Outcome     map[string]any         `json:"outcome"`
	CreatedAt   int64                  `json:"created_at"`
	UpdatedAt   int64                  `json:"updated_at"`
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

// AgentEventEntry is one row from GET /api/agent_events — an append-only
// audit-log entry. `Envelope` is the full envelope as it was received at the
// time `seq` was assigned, so consumers can replay history without consulting
// agent_state separately.
type AgentEventEntry struct {
	Seq      int64                  `json:"seq"`
	ID       string                 `json:"id"`
	Source   string                 `json:"source"`
	Type     string                 `json:"type"`
	Status   string                 `json:"status"`
	ParentID string                 `json:"parent_id"`
	Ts       int64                  `json:"ts"`
	Envelope map[string]interface{} `json:"envelope"`
}

type agentEventsResp struct {
	Events []AgentEventEntry `json:"events"`
	Count  int               `json:"count"`
}

// ListAgentEvents fetches the audit log filtered by envelope id and/or
// parent_id. Pass empty strings to skip a filter. Returns events ordered by
// seq ascending (oldest first), matching the brainbox query contract.
func (c *Client) ListAgentEvents(envelopeID, parentID string, limit int) ([]AgentEventEntry, error) {
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
	add("id", envelopeID)
	add("parent_id", parentID)
	if limit > 0 {
		add("limit", fmt.Sprintf("%d", limit))
	}
	var resp agentEventsResp
	if err := c.get("/api/agent_events"+q, &resp); err != nil {
		return nil, fmt.Errorf("list agent events: %w", err)
	}
	return resp.Events, nil
}

// GetAgentState fetches one envelope by id. Returns ok=false when brainbox
// returned 404 (envelope unknown to the bus).
func (c *Client) GetAgentState(id string) (AgentStateItem, bool, error) {
	if id == "" {
		return AgentStateItem{}, false, fmt.Errorf("empty envelope id")
	}
	var item AgentStateItem
	err := c.get("/api/agent_state/"+id, &item)
	if err != nil {
		// Detect a 404 — Client.do returns a *http error with the status code
		// in its message. The simplest signal is a string contains check.
		if err.Error() != "" && contains404(err.Error()) {
			return AgentStateItem{}, false, nil
		}
		return AgentStateItem{}, false, fmt.Errorf("get agent state: %w", err)
	}
	return item, true, nil
}

func contains404(s string) bool {
	// brainbox returns 404 as "HTTP 404: ..." from the do helper
	for i := 0; i+3 <= len(s); i++ {
		if s[i] == '4' && s[i+1] == '0' && s[i+2] == '4' {
			return true
		}
	}
	return false
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

// SearchAgentEventsOptions narrows GET /api/agent_events/search. Zero
// values skip the corresponding filter.
type SearchAgentEventsOptions struct {
	Q         string // full-text (title/description/envelope)
	Type      string // event type prefix, e.g. "task." or "rule."
	Workspace string
	Status    string
	Source    string
	SinceMs   int64
	UntilMs   int64
	Limit     int
}

// SearchAgentEventsResult carries the hits plus which backend answered —
// "opensearch" when the daemon's sink is configured, "postgres" otherwise.
type SearchAgentEventsResult struct {
	Items   []AgentEventEntry `json:"items"`
	Backend string            `json:"backend"`
	Total   *int64            `json:"total"` // null on the postgres path
}

// SearchAgentEvents queries event history through the daemon (which owns
// the OpenSearch-vs-Postgres decision). Results are newest-first.
func (c *Client) SearchAgentEvents(opts SearchAgentEventsOptions) (SearchAgentEventsResult, error) {
	q := url.Values{}
	if opts.Q != "" {
		q.Set("q", opts.Q)
	}
	if opts.Type != "" {
		q.Set("type", opts.Type)
	}
	if opts.Workspace != "" {
		q.Set("workspace", opts.Workspace)
	}
	if opts.Status != "" {
		q.Set("status", opts.Status)
	}
	if opts.Source != "" {
		q.Set("source", opts.Source)
	}
	if opts.SinceMs > 0 {
		q.Set("since_ms", fmt.Sprintf("%d", opts.SinceMs))
	}
	if opts.UntilMs > 0 {
		q.Set("until_ms", fmt.Sprintf("%d", opts.UntilMs))
	}
	if opts.Limit > 0 {
		q.Set("limit", fmt.Sprintf("%d", opts.Limit))
	}
	path := "/api/agent_events/search"
	if enc := q.Encode(); enc != "" {
		path += "?" + enc
	}
	var result SearchAgentEventsResult
	if err := c.get(path, &result); err != nil {
		return SearchAgentEventsResult{}, fmt.Errorf("search agent events: %w", err)
	}
	return result, nil
}

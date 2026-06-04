package brainbox

import (
	"net/http"
	"net/url"
	"time"
)

// Runner describes a registered remote runner returned by GET /api/runners.
type Runner struct {
	Name          string          `json:"name"`
	Capabilities  map[string]bool `json:"capabilities"`
	Tags          []string        `json:"tags"`
	Version       string          `json:"version"`
	RegisteredAt  int64           `json:"registered_at"`
	LastSeen      int64           `json:"last_seen"`
	QueueDepth    int             `json:"queue_depth"`
	InFlight      int             `json:"in_flight"`
	MaxConcurrent int             `json:"max_concurrent"`
	Host          string          `json:"host"`
}

// PairingTicket is the response from POST /api/runners/pair/start.
type PairingTicket struct {
	Token     string  `json:"token"`
	ExpiresAt float64 `json:"expires_at"`
	APIURL    string  `json:"api_url"`
}

// ListRunners returns all runners currently registered with the API.
func (c *Client) ListRunners() ([]Runner, error) {
	var runners []Runner
	if err := c.do("GET", "/api/runners", nil, &runners); err != nil {
		return nil, err
	}
	return runners, nil
}

// DeleteRunner deregisters a runner. Pending work for it is cancelled.
// Names with spaces / unicode are URL-encoded so we don't accidentally
// craft invalid request lines for the "Curtis's MacBook Pro (2)" case.
func (c *Client) DeleteRunner(name string) error {
	return c.do("DELETE", "/api/runners/"+url.PathEscape(name), nil, nil)
}

// RegisterRunnerRequest is the body sent to POST /api/runners/register.
type RegisterRunnerRequest struct {
	Name          string          `json:"name"`
	Capabilities  map[string]bool `json:"capabilities"`
	Host          string          `json:"host,omitempty"`
	MachineID     string          `json:"machine_id,omitempty"`
	MaxConcurrent int             `json:"max_concurrent,omitempty"`
}

// RunnerWorkItem is a single unit of work returned by GET /api/runners/{name}/pending.
type RunnerWorkItem struct {
	ID      string         `json:"id"`
	Kind    string         `json:"kind"`
	Payload map[string]any `json:"payload"`
}

// RunnerResult is posted back to POST /api/runners/{name}/result/{id}.
type RunnerResult struct {
	OK    bool           `json:"ok"`
	Data  map[string]any `json:"data,omitempty"`
	Error string         `json:"error,omitempty"`
}

// RegisterRunner registers (or re-registers) this runner with the API.
func (c *Client) RegisterRunner(req RegisterRunnerRequest) error {
	return c.post("/api/runners/register", req, nil)
}

// GetPendingWork long-polls for the next work item for the given runner.
// The server holds the connection open for up to ~30 s; use a 35 s client.
// Returns nil (no error) on HTTP 204 (no work available).
func (c *Client) GetPendingWork(name string, longPollClient *http.Client) (*RunnerWorkItem, error) {
	var item RunnerWorkItem
	err := c.doWith(longPollClient, "GET", "/api/runners/"+url.PathEscape(name)+"/pending", nil, &item)
	if err != nil {
		// 204 No Content comes back as an unmarshal error on empty body — treat as no work.
		if item.ID == "" {
			return nil, nil
		}
		return nil, err
	}
	if item.ID == "" {
		return nil, nil
	}
	return &item, nil
}

// PostRunnerResult posts the result for a completed work item.
func (c *Client) PostRunnerResult(name, workID string, result RunnerResult) error {
	return c.post("/api/runners/"+url.PathEscape(name)+"/result/"+url.PathEscape(workID), result, nil)
}

// PostRunnerHeartbeat sends a heartbeat to keep the runner registration alive.
func (c *Client) PostRunnerHeartbeat(name string) error {
	return c.post("/api/runners/"+url.PathEscape(name)+"/heartbeat", nil, nil)
}

// PostRunnerEvent sends an incremental event (e.g. stdout chunk) for a work item.
func (c *Client) PostRunnerEvent(name, workID string, event map[string]any) error {
	return c.post("/api/runners/"+url.PathEscape(name)+"/event", event, nil)
}

// longPollHTTPClient returns an HTTP client with a 35-second timeout — slightly
// longer than the server's 30-second long-poll hold so we don't race it.
func LongPollHTTPClient() *http.Client {
	return &http.Client{Timeout: 35 * time.Second}
}

// StartRunnerPairing issues a one-time pairing token. networkAPIURL is the
// URL the remote runner should use to reach the API (e.g. http://192.168.1.42:9999).
// If empty, the client's own baseURL is used (fine for same-host setups).
// ttlSeconds <= 0 uses the server default (300).
func (c *Client) StartRunnerPairing(runnerNameSuggestion string, ttlSeconds int, networkAPIURL string) (PairingTicket, error) {
	baseURL, apiKey := c.snapshot()
	if networkAPIURL == "" {
		networkAPIURL = baseURL
	}
	body := map[string]interface{}{
		"api_url":                networkAPIURL,
		"api_key":                apiKey,
		"runner_name_suggestion": runnerNameSuggestion,
	}
	if ttlSeconds > 0 {
		body["ttl"] = ttlSeconds
	}
	var ticket PairingTicket
	if err := c.do("POST", "/api/runners/pair/start", body, &ticket); err != nil {
		return PairingTicket{}, err
	}
	return ticket, nil
}

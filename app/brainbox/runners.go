package brainbox

import "net/url"

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

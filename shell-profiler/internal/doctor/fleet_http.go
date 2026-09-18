package doctor

// The real FleetClient: a thin typed wrapper over the orchestration API
// endpoints that already exist. It adds no endpoint and no behaviour — every
// call here has a prouterctl equivalent (`session-create`, `delete-session`,
// `runners`) and a Go precedent in app/brainbox/sessions.go.

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"time"
)

// apiKeyHeader is the orchestration API's auth header.
const apiKeyHeader = "X-API-Key"

// Timeouts. Session creation is in a class of its own: provisioning a
// container on a cold remote node pulls an image and can take minutes, so it
// gets its own budget rather than the general one.
const (
	fleetRequestTimeout = 30 * time.Second
	fleetCreateTimeout  = 5 * time.Minute
)

// HTTPFleetClient talks to the orchestration API over HTTP.
type HTTPFleetClient struct {
	baseURL string
	apiKey  string
	client  *http.Client
	// createClient carries the longer create budget.
	createClient *http.Client
}

// NewHTTPFleetClient builds a client for the orchestration API at baseURL.
func NewHTTPFleetClient(baseURL, apiKey string) *HTTPFleetClient {
	return &HTTPFleetClient{
		baseURL:      trimTrailingSlash(baseURL),
		apiKey:       apiKey,
		client:       &http.Client{Timeout: fleetRequestTimeout},
		createClient: &http.Client{Timeout: fleetCreateTimeout},
	}
}

func trimTrailingSlash(s string) string {
	for len(s) > 0 && s[len(s)-1] == '/' {
		s = s[:len(s)-1]
	}
	return s
}

// ListRunners implements FleetClient via GET /api/runners.
func (c *HTTPFleetClient) ListRunners() ([]FleetRunner, error) {
	var runners []FleetRunner
	if err := c.do(c.client, http.MethodGet, "/api/runners", nil, &runners); err != nil {
		return nil, err
	}
	return runners, nil
}

// createSessionBody is the POST /api/create payload. Only the fields a
// delivery probe needs are sent; everything else keeps its server default.
//
// workspace_profile is what makes this work at all: it selects which profile's
// credentials the broker delivers into the container.
type createSessionBody struct {
	Name             string `json:"name"`
	Role             string `json:"role,omitempty"`
	Runner           string `json:"runner,omitempty"`
	WorkspaceProfile string `json:"workspace_profile,omitempty"`
}

// sessionActionResponse is the shared start/stop/delete/create reply shape.
type sessionActionResponse struct {
	Success bool   `json:"success"`
	Error   string `json:"error"`
}

// CreateSession implements FleetClient via POST /api/create.
func (c *HTTPFleetClient) CreateSession(spec FleetSessionSpec) error {
	body := createSessionBody{
		Name:             spec.Name,
		Role:             spec.Role,
		Runner:           spec.Runner,
		WorkspaceProfile: spec.Profile,
	}
	var resp sessionActionResponse
	if err := c.do(c.createClient, http.MethodPost, "/api/create", body, &resp); err != nil {
		return err
	}
	if !resp.Success && resp.Error != "" {
		return fmt.Errorf("%s", resp.Error)
	}
	return nil
}

// Exec implements FleetClient via POST /api/sessions/{name}/exec. For a
// runner-hosted session the API dispatches this to the node as session.exec;
// that dispatch is the existing path this check rides on.
func (c *HTTPFleetClient) Exec(sessionName, command string) (FleetExecResult, error) {
	var out FleetExecResult
	path := "/api/sessions/" + url.PathEscape(sessionName) + "/exec"
	err := c.do(c.client, http.MethodPost, path, map[string]string{"command": command}, &out)
	return out, err
}

// DeleteSession implements FleetClient via POST /api/delete.
func (c *HTTPFleetClient) DeleteSession(sessionName string) error {
	var resp sessionActionResponse
	return c.do(c.client, http.MethodPost, "/api/delete", map[string]string{"name": sessionName}, &resp)
}

// do performs one request and decodes the JSON reply into result.
func (c *HTTPFleetClient) do(hc *http.Client, method, path string, body, result any) error {
	var reader io.Reader
	if body != nil {
		data, err := json.Marshal(body)
		if err != nil {
			return fmt.Errorf("marshal body: %w", err)
		}
		reader = bytes.NewReader(data)
	}

	req, err := http.NewRequest(method, c.baseURL+path, reader)
	if err != nil {
		return fmt.Errorf("create request: %w", err)
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	if c.apiKey != "" {
		req.Header.Set(apiKeyHeader, c.apiKey)
	}

	resp, err := hc.Do(req)
	if err != nil {
		return fmt.Errorf("request failed: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()

	// Cap the body: an error page from a reverse proxy is enormous and none of
	// it is useful in a doctor line.
	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if err != nil {
		return fmt.Errorf("read response: %w", err)
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("HTTP %d: %s", resp.StatusCode, firstLine(string(respBody)))
	}
	if result != nil {
		if err := json.Unmarshal(respBody, result); err != nil {
			return fmt.Errorf("unmarshal response: %w", err)
		}
	}
	return nil
}

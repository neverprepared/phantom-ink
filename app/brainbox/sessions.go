package brainbox

import (
	"fmt"
	"net/http"
	"time"
)

// Session represents a brainbox container session.
// Port and SSHPort use interface{} because the API returns them as strings
// or null depending on the backend (docker vs utm).
type Session struct {
	Name             string      `json:"name"`
	SessionName      string      `json:"session_name"`
	Active           bool        `json:"active"`
	Role             string      `json:"role"`
	URL              string      `json:"url"`
	Port             interface{} `json:"port"`
	Volume           string      `json:"volume"`
	LLMProvider      string      `json:"llm_provider"`
	LLMModel         string      `json:"llm_model"`
	WorkspaceProfile string      `json:"workspace_profile"`
	Backend          string      `json:"backend"`
	SSHPort          interface{} `json:"ssh_port"`
	VMState          string      `json:"vm_state,omitempty"`
}

// CreateSessionRequest mirrors the POST /api/create payload.
type CreateSessionRequest struct {
	Name             string            `json:"name"`
	Role             string            `json:"role,omitempty"`
	Volume           string            `json:"volume,omitempty"`
	Volumes          []string          `json:"volumes,omitempty"`
	LLMProvider      string            `json:"llm_provider,omitempty"`
	LLMModel         string            `json:"llm_model,omitempty"`
	OllamaHost       string            `json:"ollama_host,omitempty"`
	CodexApiKey      string            `json:"codex_api_key,omitempty"`
	Backend          string            `json:"backend,omitempty"`
	VMTemplate       string            `json:"vm_template,omitempty"`
	GuestOS          string            `json:"guest_os,omitempty"`
	WorkspaceProfile string            `json:"workspace_profile,omitempty"`
	WorkspaceHome    string            `json:"workspace_home,omitempty"`
	Task             string            `json:"task,omitempty"`
	Ports            map[string]int    `json:"ports,omitempty"`
	DockerHost       string            `json:"docker_host,omitempty"`
	Runner           string            `json:"runner,omitempty"`
}

// QuerySessionRequest mirrors the POST /api/sessions/{name}/query payload.
type QuerySessionRequest struct {
	Prompt      string `json:"prompt"`
	WorkingDir  string `json:"working_dir,omitempty"`
	Timeout     int    `json:"timeout,omitempty"`
	ForkSession bool   `json:"fork_session,omitempty"`
}

// SessionActionResponse is the generic response for start/stop/delete.
type SessionActionResponse struct {
	Success bool   `json:"success"`
	Error   string `json:"error"`
	URL     string `json:"url"`
}

// ListSessions fetches all container sessions.
func (c *Client) ListSessions() ([]Session, error) {
	var sessions []Session
	if err := c.get("/api/sessions", &sessions); err != nil {
		return nil, err
	}
	return sessions, nil
}

// CreateSession creates a new container session.
// Uses a long timeout because Docker image pulls and container provisioning
// on remote hosts can take several minutes.
func (c *Client) CreateSession(req CreateSessionRequest) (SessionActionResponse, error) {
	longClient := &http.Client{Timeout: 10 * time.Minute}
	var resp SessionActionResponse
	if err := c.doWith(longClient, http.MethodPost, "/api/create", req, &resp); err != nil {
		return resp, err
	}
	return resp, nil
}

// StartSession starts a stopped session.
func (c *Client) StartSession(name string) (SessionActionResponse, error) {
	var resp SessionActionResponse
	if err := c.post("/api/start", map[string]string{"name": name}, &resp); err != nil {
		return resp, err
	}
	return resp, nil
}

// StopSession stops a running session.
func (c *Client) StopSession(name string) (SessionActionResponse, error) {
	var resp SessionActionResponse
	if err := c.post("/api/stop", map[string]string{"name": name}, &resp); err != nil {
		return resp, err
	}
	return resp, nil
}

// DeleteSession deletes a session.
func (c *Client) DeleteSession(name string) (SessionActionResponse, error) {
	var resp SessionActionResponse
	if err := c.post("/api/delete", map[string]string{"name": name}, &resp); err != nil {
		return resp, err
	}
	return resp, nil
}

// ExecSession executes a shell command in a session.
func (c *Client) ExecSession(name, command string) (map[string]interface{}, error) {
	var resp map[string]interface{}
	path := fmt.Sprintf("/api/sessions/%s/exec", name)
	if err := c.post(path, map[string]string{"command": command}, &resp); err != nil {
		return nil, err
	}
	return resp, nil
}

// QuerySession sends a prompt to Claude Code in a session.
func (c *Client) QuerySession(name string, req QuerySessionRequest) (map[string]interface{}, error) {
	var resp map[string]interface{}
	path := fmt.Sprintf("/api/sessions/%s/query", name)
	if err := c.post(path, req, &resp); err != nil {
		return nil, err
	}
	return resp, nil
}

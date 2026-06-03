package brainbox

import (
	"bufio"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strings"
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
	Delivery         string            `json:"delivery,omitempty"`
	Env              map[string]string `json:"env,omitempty"`
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

// hostOnlyEnvVars are stripped from profile env before forwarding to a remote
// brainbox host. These are host-specific and would be wrong or harmful inside
// a container. Must stay in sync with the Python and Go builder equivalents.
var hostOnlyEnvVars = map[string]bool{
	"SSH_AUTH_SOCK": true, "GIT_SSH_COMMAND": true, "TMPDIR": true,
	"SHELL": true, "TERM_PROGRAM": true, "TERM_SESSION_ID": true,
	"HOME": true, "USER": true, "LOGNAME": true,
	"PATH": true, "PWD": true, "OLDPWD": true, "SHLVL": true,
	"XDG_CONFIG_HOME": true, "CLAUDE_CONFIG_DIR": true, "GEMINI_CONFIG_DIR": true,
	"WORKSPACE_HOME": true, "WORKSPACE_PROFILE": true,
}

// ReadProfileEnv reads KEY=VALUE pairs from .env and .env.secrets files under
// workspaceHome and returns them as a map. Used to forward profile secrets to
// a remote brainbox host when creating sessions.
//
// Host-only vars (PATH, CLAUDE_CONFIG_DIR, etc.) are filtered. $WORKSPACE_HOME
// references in values are expanded to workspaceHome so downstream consumers
// receive resolved paths rather than unexpanded shell variables.
func ReadProfileEnv(workspaceHome string) map[string]string {
	env := make(map[string]string)
	if workspaceHome == "" {
		return env
	}
	for _, name := range []string{".env", ".env.secrets"} {
		path := filepath.Join(workspaceHome, name)
		f, err := os.Open(path)
		if err != nil {
			continue
		}
		scanner := bufio.NewScanner(f)
		for scanner.Scan() {
			line := strings.TrimSpace(scanner.Text())
			if line == "" || strings.HasPrefix(line, "#") {
				continue
			}
			if strings.HasPrefix(line, "export ") {
				line = line[7:]
			}
			k, v, ok := strings.Cut(line, "=")
			if !ok {
				continue
			}
			k = strings.TrimSpace(k)
			if hostOnlyEnvVars[k] {
				continue
			}
			v = strings.TrimSpace(v)
			// Strip surrounding quotes (single or double)
			if len(v) >= 2 && ((v[0] == '"' && v[len(v)-1] == '"') || (v[0] == '\'' && v[len(v)-1] == '\'')) {
				v = v[1 : len(v)-1]
			}
			// Expand $WORKSPACE_HOME / ${WORKSPACE_HOME} so callers receive
			// resolved paths rather than unexpanded shell variable references.
			v = strings.ReplaceAll(v, "${WORKSPACE_HOME}", workspaceHome)
			v = strings.ReplaceAll(v, "$WORKSPACE_HOME", workspaceHome)
			env[k] = v
		}
		f.Close()
	}
	return env
}

// SessionHistoryEntry mirrors a row from GET /api/sessions/history.
type SessionHistoryEntry struct {
	ID          int64   `json:"id"`
	SessionName string  `json:"session_name"`
	RunnerName  *string `json:"runner_name"`
	Backend     string  `json:"backend"`
	Role        *string `json:"role"`
	StateFinal  string  `json:"state_final"`
	CreatedAt   int64   `json:"created_at"`
	StoppedAt   int64   `json:"stopped_at"`
	TaskID      *string `json:"task_id"`
	JobID       *string `json:"job_id"`
	RepoURL     *string `json:"repo_url"`
	Reason      *string `json:"reason"`
}

// GetSessionHistory fetches stopped sessions from the history log.
func (c *Client) GetSessionHistory(limit, offset int) ([]SessionHistoryEntry, error) {
	var entries []SessionHistoryEntry
	path := fmt.Sprintf("/api/sessions/history?limit=%d&offset=%d", limit, offset)
	if err := c.get(path, &entries); err != nil {
		return nil, err
	}
	return entries, nil
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

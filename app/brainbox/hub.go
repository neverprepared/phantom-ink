package brainbox

import (
	"fmt"
	"time"
)

// Task represents a hub task.
type Task struct {
	ID               string      `json:"id"`
	Description      string      `json:"description"`
	AgentName        string      `json:"agent_name"`
	Status           string      `json:"status"`
	RepoURL          interface{} `json:"repo_url"`
	CreatedAt        interface{} `json:"created_at"`
	UpdatedAt        interface{} `json:"updated_at"`
	Result           interface{} `json:"result"`
	Error            interface{} `json:"error"`
	SessionName      string      `json:"session_name"`
	WorkspaceProfile string      `json:"workspace_profile"`
	JobID            string      `json:"job_id"`
	SpawnedBy        string      `json:"spawned_by"`       // task ID of the parent that spawned this one
	ChildTaskIDs     []string    `json:"child_task_ids"`   // task IDs of children spawned by this task
	ChannelIDs       []string    `json:"channel_ids"`      // channels spawned by this task
}

// AgentDefinition represents a registered agent definition.
type AgentDefinition struct {
	Name              string   `json:"name"`
	Image             string   `json:"image"`
	Description       string   `json:"description"`
	Category          string   `json:"category"`
	SpawnMode         string   `json:"spawn_mode"` // "container" | "subagent"
	Capabilities      []string `json:"capabilities"`
	Hardened          bool     `json:"hardened"`
	Persistent        bool     `json:"persistent"`
	RolePrompt        string   `json:"role_prompt,omitempty"`
	RolePromptContent string   `json:"role_prompt_content,omitempty"`
	ClaudeModel       string   `json:"claude_model,omitempty"`
	ClaudeEffort      string   `json:"claude_effort,omitempty"`
	CodexModel        string   `json:"codex_model,omitempty"`
	OllamaModel       string   `json:"ollama_model,omitempty"`
}

// CreateAgentRequest is the payload for POST /api/hub/agents.
type CreateAgentRequest struct {
	Name              string   `json:"name"`
	Image             string   `json:"image,omitempty"`
	Description       string   `json:"description,omitempty"`
	Category          string   `json:"category,omitempty"`
	SpawnMode         string   `json:"spawn_mode,omitempty"`
	Capabilities      []string `json:"capabilities,omitempty"`
	Hardened          bool     `json:"hardened,omitempty"`
	Persistent        bool     `json:"persistent,omitempty"`
	RolePromptContent string   `json:"role_prompt_content,omitempty"`
	ClaudeModel       string   `json:"claude_model,omitempty"`
	ClaudeEffort      string   `json:"claude_effort,omitempty"`
	CodexModel        string   `json:"codex_model,omitempty"`
	OllamaModel       string   `json:"ollama_model,omitempty"`
}

// UpdateAgentRequest is the payload for PATCH /api/hub/agents/{name}.
type UpdateAgentRequest struct {
	Image             *string  `json:"image,omitempty"`
	Description       *string  `json:"description,omitempty"`
	Category          *string  `json:"category,omitempty"`
	SpawnMode         *string  `json:"spawn_mode,omitempty"`
	Capabilities      []string `json:"capabilities,omitempty"`
	Hardened          *bool    `json:"hardened,omitempty"`
	Persistent        *bool    `json:"persistent,omitempty"`
	RolePromptContent *string  `json:"role_prompt_content,omitempty"`
	ClaudeModel       *string  `json:"claude_model,omitempty"`
	ClaudeEffort      *string  `json:"claude_effort,omitempty"`
	CodexModel        *string  `json:"codex_model,omitempty"`
	OllamaModel       *string  `json:"ollama_model,omitempty"`
}

// Agent is kept as an alias for backward compatibility with HubState.
type Agent = AgentDefinition

// HubState is the full hub state snapshot.
type HubState struct {
	Agents []Agent                  `json:"agents"`
	Tasks  []Task                   `json:"tasks"`
	Tokens []map[string]interface{} `json:"tokens"`
}

// Message represents an inter-agent message.
type Message struct {
	ID        string      `json:"id"`
	Sender    string      `json:"sender"`
	Recipient string      `json:"recipient"`
	Type      string      `json:"type"`
	Payload   interface{} `json:"payload"`
	Timestamp string      `json:"timestamp"`
}

// SubmitTaskRequest is the payload for POST /api/hub/tasks.
type SubmitTaskRequest struct {
	Description      string `json:"description"`
	AgentName        string `json:"agent_name"`
	RepoURL          string `json:"repo_url,omitempty"`
	WorkspaceProfile string `json:"workspace_profile,omitempty"`
	WorkspaceHome    string `json:"workspace_home,omitempty"`
	Pool             string `json:"pool,omitempty"` // optional machine-class routing pool
}

// GetHubState returns the full hub state.
func (c *Client) GetHubState() (HubState, error) {
	var state HubState
	if err := c.get("/api/hub/state", &state); err != nil {
		return state, err
	}
	return state, nil
}

// ListTasks returns hub tasks, optionally filtered by status and/or workspace profile.
func (c *Client) ListTasks(status, workspaceProfile string) ([]Task, error) {
	path := "/api/hub/tasks"
	sep := "?"
	if status != "" {
		path += sep + "status=" + status
		sep = "&"
	}
	if workspaceProfile != "" {
		path += sep + "workspace_profile=" + workspaceProfile
	}
	var tasks []Task
	if err := c.get(path, &tasks); err != nil {
		return nil, err
	}
	return tasks, nil
}

// ListTasksByJob returns all tasks with the given job_id (the full job tree).
func (c *Client) ListTasksByJob(jobID string) ([]Task, error) {
	path := fmt.Sprintf("/api/hub/tasks?job_id=%s&limit=200", jobID)
	var tasks []Task
	if err := c.get(path, &tasks); err != nil {
		return nil, err
	}
	return tasks, nil
}

// SubmitTask submits a new task to the hub.
func (c *Client) SubmitTask(req SubmitTaskRequest) (Task, error) {
	var task Task
	if err := c.post("/api/hub/tasks", req, &task); err != nil {
		return task, err
	}
	return task, nil
}

// CancelTask cancels a running task.
func (c *Client) CancelTask(taskID string) error {
	path := fmt.Sprintf("/api/hub/tasks/%s", taskID)
	return c.delete(path, nil)
}

// ListAgents returns all registered agent definitions.
func (c *Client) ListAgents() ([]AgentDefinition, error) {
	var agents []AgentDefinition
	if err := c.get("/api/hub/agents", &agents); err != nil {
		return nil, err
	}
	return agents, nil
}

// GetAgent returns a single agent definition including its role prompt content.
func (c *Client) GetAgent(name string) (AgentDefinition, error) {
	var agent AgentDefinition
	path := fmt.Sprintf("/api/hub/agents/%s", name)
	if err := c.get(path, &agent); err != nil {
		return agent, err
	}
	return agent, nil
}

// CreateAgent creates a new agent definition.
func (c *Client) CreateAgent(req CreateAgentRequest) (AgentDefinition, error) {
	var agent AgentDefinition
	if err := c.post("/api/hub/agents", req, &agent); err != nil {
		return agent, err
	}
	return agent, nil
}

// UpdateAgent updates an existing agent definition.
func (c *Client) UpdateAgent(name string, req UpdateAgentRequest) (AgentDefinition, error) {
	var agent AgentDefinition
	path := fmt.Sprintf("/api/hub/agents/%s", name)
	if err := c.patch(path, req, &agent); err != nil {
		return agent, err
	}
	return agent, nil
}

// DeleteAgent removes a custom agent definition.
func (c *Client) DeleteAgent(name string) error {
	path := fmt.Sprintf("/api/hub/agents/%s", name)
	return c.delete(path, nil)
}

// GetMessageLog returns the inter-agent message audit log.
func (c *Client) GetMessageLog() ([]Message, error) {
	var messages []Message
	if err := c.get("/api/hub/message-log", &messages); err != nil {
		return nil, err
	}
	return messages, nil
}

// WaitForTaskRequest is the input for SubmitTaskAndWait.
type WaitForTaskRequest struct {
	Description      string `json:"description"`
	AgentName        string `json:"agent_name"`
	RepoURL          string `json:"repo_url,omitempty"`
	WorkspaceProfile string `json:"workspace_profile,omitempty"`
	WorkspaceHome    string `json:"workspace_home,omitempty"`
	TimeoutSec       int    `json:"timeout_sec,omitempty"` // 0 → 300
}

// WaitForTaskResponse is the result from SubmitTaskAndWait.
type WaitForTaskResponse struct {
	TaskID string         `json:"task_id"`
	Status string         `json:"status"`           // "completed" | "failed" | "timeout"
	Result map[string]any `json:"result,omitempty"`
	Error  string         `json:"error,omitempty"`
}

// SubmitTaskAndWait submits a hub task and polls until it reaches a terminal
// state, then returns the outcome. Polling is client-side (3s interval) so no
// brainbox server changes are required. A future PR can replace polling with a
// server-push wait endpoint while keeping this call-site unchanged.
func (c *Client) SubmitTaskAndWait(req WaitForTaskRequest) (WaitForTaskResponse, error) {
	timeoutSec := req.TimeoutSec
	if timeoutSec <= 0 {
		timeoutSec = 300
	}

	submitted, err := c.SubmitTask(SubmitTaskRequest{
		Description:      req.Description,
		AgentName:        req.AgentName,
		RepoURL:          req.RepoURL,
		WorkspaceProfile: req.WorkspaceProfile,
		WorkspaceHome:    req.WorkspaceHome,
	})
	if err != nil {
		return WaitForTaskResponse{}, fmt.Errorf("submit task: %w", err)
	}

	deadline := time.Now().Add(time.Duration(timeoutSec) * time.Second)
	ticker := time.NewTicker(3 * time.Second)
	defer ticker.Stop()

	for {
		select {
		case <-ticker.C:
			var tasks []Task
			path := fmt.Sprintf("/api/hub/tasks/%s", submitted.ID)
			var single Task
			if err := c.get(path, &single); err != nil {
				// If individual fetch fails, fall back to list search.
				tasks, err = c.ListTasks("", "")
				if err != nil {
					if time.Now().After(deadline) {
						return WaitForTaskResponse{TaskID: submitted.ID, Status: "timeout"}, nil
					}
					continue
				}
				for _, t := range tasks {
					if t.ID == submitted.ID {
						single = t
						break
					}
				}
			}
			switch single.Status {
			case "completed":
				var result map[string]any
				if m, ok := single.Result.(map[string]any); ok {
					result = m
				}
				return WaitForTaskResponse{
					TaskID: submitted.ID,
					Status: "completed",
					Result: result,
				}, nil
			case "failed":
				return WaitForTaskResponse{
					TaskID: submitted.ID,
					Status: "failed",
					Error:  errString(single.Error),
				}, nil
			case "cancelled":
				return WaitForTaskResponse{
					TaskID: submitted.ID,
					Status: "failed",
					Error:  "task cancelled",
				}, nil
			}
			if time.Now().After(deadline) {
				return WaitForTaskResponse{TaskID: submitted.ID, Status: "timeout"}, nil
			}
		}
	}
}

// errString flattens the brainbox Task.Error (interface{}) to a plain string.
func errString(v any) string {
	if v == nil {
		return ""
	}
	if s, ok := v.(string); ok {
		return s
	}
	if m, ok := v.(map[string]any); ok {
		if msg, ok := m["message"].(string); ok {
			return msg
		}
	}
	return fmt.Sprint(v)
}

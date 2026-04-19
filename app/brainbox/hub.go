package brainbox

import "fmt"

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
	Agents  []Agent                  `json:"agents"`
	Tasks   []Task                   `json:"tasks"`
	Tokens  []map[string]interface{} `json:"tokens"`
	Repos   []Repo                   `json:"repos"`
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

// Repo represents a tracked GitHub repository.
type Repo struct {
	Name               string `json:"name"`
	URL                string `json:"url"`
	MergeQueueEnabled  bool   `json:"merge_queue_enabled"`
	PRShepherdEnabled  bool   `json:"pr_shepherd_enabled"`
	TargetBranch       string `json:"target_branch"`
	IsFork             bool   `json:"is_fork"`
	UpstreamURL        string `json:"upstream_url"`
	WorkspaceProfile   string `json:"workspace_profile"`
	WorkspaceHome      string `json:"workspace_home"`
}

// SubmitTaskRequest is the payload for POST /api/hub/tasks.
type SubmitTaskRequest struct {
	Description      string `json:"description"`
	AgentName        string `json:"agent_name"`
	RepoURL          string `json:"repo_url,omitempty"`
	WorkspaceProfile string `json:"workspace_profile,omitempty"`
	WorkspaceHome    string `json:"workspace_home,omitempty"`
}

// AddRepoRequest is the payload for POST /api/hub/repos.
type AddRepoRequest struct {
	URL              string `json:"url"`
	Name             string `json:"name,omitempty"`
	MergeQueue       bool   `json:"merge_queue,omitempty"`
	PRShepherd       bool   `json:"pr_shepherd,omitempty"`
	TargetBranch     string `json:"target_branch,omitempty"`
	IsFork           bool   `json:"is_fork,omitempty"`
	UpstreamURL      string `json:"upstream_url,omitempty"`
	WorkspaceProfile string `json:"workspace_profile,omitempty"`
	WorkspaceHome    string `json:"workspace_home,omitempty"`
}

// UpdateRepoRequest is the payload for PATCH /api/hub/repos/{name}.
type UpdateRepoRequest struct {
	MergeQueue   *bool  `json:"merge_queue,omitempty"`
	PRShepherd   *bool  `json:"pr_shepherd,omitempty"`
	TargetBranch string `json:"target_branch,omitempty"`
}

// GetHubState returns the full hub state.
func (c *Client) GetHubState() (HubState, error) {
	var state HubState
	if err := c.get("/api/hub/state", &state); err != nil {
		return state, err
	}
	return state, nil
}

// ListTasks returns all hub tasks, optionally filtered by status.
func (c *Client) ListTasks(status string) ([]Task, error) {
	path := "/api/hub/tasks"
	if status != "" {
		path += "?status=" + status
	}
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

// ListRepos returns all tracked repositories.
func (c *Client) ListRepos() ([]Repo, error) {
	var repos []Repo
	if err := c.get("/api/hub/repos", &repos); err != nil {
		return nil, err
	}
	return repos, nil
}

// addRepoResponse wraps the POST /api/hub/repos response.
type addRepoResponse struct {
	Repo         Repo   `json:"repo"`
	LaunchedTasks []interface{} `json:"launched_tasks"`
}

// AddRepo registers a new repository.
func (c *Client) AddRepo(req AddRepoRequest) (Repo, error) {
	var resp addRepoResponse
	if err := c.post("/api/hub/repos", req, &resp); err != nil {
		return Repo{}, err
	}
	return resp.Repo, nil
}

// UpdateRepo updates a repository's settings.
func (c *Client) UpdateRepo(name string, req UpdateRepoRequest) (Repo, error) {
	var repo Repo
	path := fmt.Sprintf("/api/hub/repos/%s", name)
	if err := c.patch(path, req, &repo); err != nil {
		return repo, err
	}
	return repo, nil
}

// DeleteRepo removes a tracked repository.
func (c *Client) DeleteRepo(name string) error {
	path := fmt.Sprintf("/api/hub/repos/%s", name)
	return c.delete(path, nil)
}

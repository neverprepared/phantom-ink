package brainbox

import "fmt"

// PlaybookTask is one checklist item in a playbook.
type PlaybookTask struct {
	ID          string  `json:"id"`
	Index       int     `json:"index"`
	Content     string  `json:"content"`
	Status      string  `json:"status"` // "pending", "running", "completed", "failed"
	SessionName string  `json:"session_name,omitempty"`
	Output      string  `json:"output,omitempty"`
	Error       string  `json:"error,omitempty"`
	StartedAt   *int64  `json:"started_at,omitempty"`
	FinishedAt  *int64  `json:"finished_at,omitempty"`
}

// Playbook is a markdown checklist execution plan.
type Playbook struct {
	ID               string         `json:"id"`
	Name             string         `json:"name"`
	Markdown         string         `json:"markdown"`
	Tasks            []PlaybookTask `json:"tasks"`
	Status           string         `json:"status"` // "idle", "running", "completed", "failed", "cancelled"
	WorkspaceProfile string         `json:"workspace_profile"`
	Runner           string         `json:"runner,omitempty"` // runner name; empty = in-process API host
	CreatedAt        int64          `json:"created_at"`
	StartedAt        *int64         `json:"started_at,omitempty"`
	FinishedAt       *int64         `json:"finished_at,omitempty"`
}

// CreatePlaybookRequest is the payload for POST /api/hub/playbooks.
type CreatePlaybookRequest struct {
	Name             string `json:"name"`
	Markdown         string `json:"markdown"`
	WorkspaceProfile string `json:"workspace_profile,omitempty"`
	Runner           string `json:"runner,omitempty"`
}

// ListPlaybooks returns playbooks, optionally filtered by profile.
func (c *Client) ListPlaybooks(profile string) ([]Playbook, error) {
	path := "/api/hub/playbooks"
	if profile != "" {
		path += "?profile=" + profile
	}
	var playbooks []Playbook
	if err := c.get(path, &playbooks); err != nil {
		return nil, err
	}
	return playbooks, nil
}

// GetPlaybook returns a single playbook by ID.
func (c *Client) GetPlaybook(id string) (Playbook, error) {
	var pb Playbook
	if err := c.get(fmt.Sprintf("/api/hub/playbooks/%s", id), &pb); err != nil {
		return pb, err
	}
	return pb, nil
}

// CreatePlaybook creates a new playbook from markdown.
func (c *Client) CreatePlaybook(req CreatePlaybookRequest) (Playbook, error) {
	var pb Playbook
	if err := c.post("/api/hub/playbooks", req, &pb); err != nil {
		return pb, err
	}
	return pb, nil
}

// UpdatePlaybookRequest is the payload for PATCH /api/hub/playbooks/{id}.
type UpdatePlaybookRequest struct {
	Name     *string `json:"name,omitempty"`
	Markdown *string `json:"markdown,omitempty"`
	Runner   *string `json:"runner,omitempty"` // nil = don't change; ptr to "" = clear runner
}

// UpdatePlaybook updates a playbook's name and/or markdown instructions.
func (c *Client) UpdatePlaybook(id string, req UpdatePlaybookRequest) (Playbook, error) {
	var pb Playbook
	if err := c.patch(fmt.Sprintf("/api/hub/playbooks/%s", id), req, &pb); err != nil {
		return pb, err
	}
	return pb, nil
}

// DeletePlaybook deletes a playbook (cancels it first if running).
func (c *Client) DeletePlaybook(id string) error {
	return c.delete(fmt.Sprintf("/api/hub/playbooks/%s", id), nil)
}

// RunPlaybookRequest is the optional body for POST /api/hub/playbooks/{id}/run.
type RunPlaybookRequest struct {
	WorkspaceProfile string `json:"workspace_profile,omitempty"`
	Runner           string `json:"runner,omitempty"` // overrides playbook's saved runner for this run
}

// RunPlaybook starts sequential execution of a playbook.
// workspaceProfile and runner override the playbook's saved values for this run.
func (c *Client) RunPlaybook(id, workspaceProfile, runner string) (Playbook, error) {
	var pb Playbook
	body := RunPlaybookRequest{WorkspaceProfile: workspaceProfile, Runner: runner}
	if err := c.post(fmt.Sprintf("/api/hub/playbooks/%s/run", id), body, &pb); err != nil {
		return pb, err
	}
	return pb, nil
}

// CancelPlaybook cancels a running playbook.
func (c *Client) CancelPlaybook(id string) error {
	return c.post(fmt.Sprintf("/api/hub/playbooks/%s/cancel", id), nil, nil)
}

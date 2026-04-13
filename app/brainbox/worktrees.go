package brainbox

import "fmt"

// Worktree is a persistent git worktree for a registered repository.
type Worktree struct {
	ID            string  `json:"id"`
	RepoName      string  `json:"repo_name"`
	Branch        string  `json:"branch"`
	WorktreePath  string  `json:"worktree_path"`
	SessionName   string  `json:"session_name,omitempty"`
	Status        string  `json:"status"` // "ready", "in_use", "error"
	CreatedAt     int64   `json:"created_at"`
	Error         string  `json:"error,omitempty"`
}

// CreateWorktreeRequest is the payload for POST /api/hub/worktrees.
type CreateWorktreeRequest struct {
	RepoName string `json:"repo_name"`
	Branch   string `json:"branch"`
}

// WorktreeSessionResponse is returned by POST /api/hub/worktrees/{id}/session.
type WorktreeSessionResponse struct {
	WorktreeID string `json:"worktree_id"`
	Session    string `json:"session"`
}

// ListWorktrees returns all worktrees, optionally filtered by repo name.
func (c *Client) ListWorktrees(repo string) ([]Worktree, error) {
	path := "/api/hub/worktrees"
	if repo != "" {
		path += "?repo=" + repo
	}
	var worktrees []Worktree
	if err := c.get(path, &worktrees); err != nil {
		return nil, err
	}
	return worktrees, nil
}

// GetWorktree returns a single worktree by ID.
func (c *Client) GetWorktree(id string) (Worktree, error) {
	var wt Worktree
	if err := c.get(fmt.Sprintf("/api/hub/worktrees/%s", id), &wt); err != nil {
		return wt, err
	}
	return wt, nil
}

// CreateWorktree creates a new worktree for a repo branch.
func (c *Client) CreateWorktree(req CreateWorktreeRequest) (Worktree, error) {
	var wt Worktree
	if err := c.post("/api/hub/worktrees", req, &wt); err != nil {
		return wt, err
	}
	return wt, nil
}

// DeleteWorktree removes the worktree from disk and deregisters it.
func (c *Client) DeleteWorktree(id string) error {
	return c.delete(fmt.Sprintf("/api/hub/worktrees/%s", id), nil)
}

// CreateWorktreeSession starts a brainbox session mounted on the given worktree.
func (c *Client) CreateWorktreeSession(id string) (WorktreeSessionResponse, error) {
	var resp WorktreeSessionResponse
	if err := c.post(fmt.Sprintf("/api/hub/worktrees/%s/session", id), nil, &resp); err != nil {
		return resp, err
	}
	return resp, nil
}

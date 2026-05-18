package brainbox

// DispatchPreviewRequest is the shape of POST /api/sessions/preview.
type DispatchPreviewRequest struct {
	Backend string   `json:"backend,omitempty"`
	Runner  string   `json:"runner,omitempty"`
	Tags    []string `json:"tags,omitempty"`
}

// DispatchCandidate describes one eligible runner from the preview response.
type DispatchCandidate struct {
	Name            string   `json:"name"`
	Version         string   `json:"version"`
	Tags            []string `json:"tags"`
	Online          bool     `json:"online"`
	SupportsBackend bool     `json:"supports_backend"`
	TagScore        *int     `json:"tag_score"`
}

// DispatchPreview is the response from POST /api/sessions/preview.
type DispatchPreview struct {
	SelectedRunner *string             `json:"selected_runner"`
	InProcess      bool                `json:"in_process"`
	Reason         string              `json:"reason"`
	Candidates     []DispatchCandidate `json:"candidates"`
	Error          string              `json:"error,omitempty"`
}

// PreviewDispatch asks the API where a session with these parameters would land.
// No state change — safe to call reactively from the UI as the user edits the
// session-create form.
func (c *Client) PreviewDispatch(req DispatchPreviewRequest) (DispatchPreview, error) {
	var p DispatchPreview
	if err := c.do("POST", "/api/sessions/preview", req, &p); err != nil {
		return DispatchPreview{}, err
	}
	return p, nil
}

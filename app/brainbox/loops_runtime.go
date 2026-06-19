package brainbox

import (
	"fmt"
	"net/http"
)

// LiveLoopSummary is the slim projection returned by GET /api/loops. It drops
// the full template snapshot to keep list payloads small; use GetLiveLoop to
// fetch the full instance including the spec snapshot.
type LiveLoopSummary struct {
	ID               string    `json:"id"`
	Name             string    `json:"name"`
	Status           string    `json:"status"`
	Iteration        int       `json:"iteration"`
	MaxIterations    int       `json:"max_iterations"`
	ParentTaskID     string    `json:"parent_task_id"`
	CurrentChildID   string    `json:"current_child_id,omitempty"`
	MetricHistory    []float64 `json:"metric_history"`
	StopReason       string    `json:"stop_reason,omitempty"`
	Error            string    `json:"error,omitempty"`
	WorkspaceProfile string    `json:"workspace_profile,omitempty"`
	CreatedAt        int64     `json:"created_at"`
	UpdatedAt        int64     `json:"updated_at"`
}

// LiveLoop is the full LoopInstance with the pinned template snapshot.
// Only used by GET /api/loops/{id} when drilling in.
type LiveLoop struct {
	ID               string                 `json:"id"`
	SpecSnapshot     map[string]interface{} `json:"spec_snapshot"`
	ParentTaskID     string                 `json:"parent_task_id"`
	Status           string                 `json:"status"`
	Iteration        int                    `json:"iteration"`
	Envelope         map[string]interface{} `json:"envelope"`
	MetricHistory    []float64              `json:"metric_history"`
	CurrentChildID   string                 `json:"current_child_id,omitempty"`
	WorkspaceProfile string                 `json:"workspace_profile,omitempty"`
	CreatedAt        int64                  `json:"created_at"`
	UpdatedAt        int64                  `json:"updated_at"`
	Error            string                 `json:"error,omitempty"`
	StopReason       string                 `json:"stop_reason,omitempty"`
}

// LiveLoopIteration is one row from the loop_iteration_metric table.
// Feeds the per-loop convergence trend chart and the cost-per-iteration view.
type LiveLoopIteration struct {
	LoopID                 string  `json:"loop_id"`
	Iteration              int     `json:"iteration"`
	ConvergenceMetricValue float64 `json:"convergence_metric_value"`
	DurationMs             int64   `json:"duration_ms"`
	CostUsd                float64 `json:"cost_usd"`
	Tokens                 int64   `json:"tokens"`
	Model                  string  `json:"model,omitempty"`
	StateAtEnd             string  `json:"state_at_end,omitempty"`
	Timestamp              int64   `json:"timestamp"`
}

// ListLiveLoops returns the slim view of every loop the brainbox runtime
// knows about, optionally filtered by status. Empty status = no filter.
func (c *Client) ListLiveLoops(status string) ([]LiveLoopSummary, error) {
	path := "/api/loops"
	if status != "" {
		path += "?status=" + status
	}
	var resp struct {
		Loops []LiveLoopSummary `json:"loops"`
	}
	if err := c.get(path, &resp); err != nil {
		return nil, err
	}
	return resp.Loops, nil
}

// GetLiveLoop returns the full LoopInstance for the given id, including the
// pinned template snapshot. Falls back to a DB lookup for terminal loops.
func (c *Client) GetLiveLoop(id string) (LiveLoop, error) {
	var loop LiveLoop
	if err := c.get("/api/loops/"+id, &loop); err != nil {
		return LiveLoop{}, err
	}
	return loop, nil
}

// GetLiveLoopIterations returns the per-iteration metric rows in iteration
// order. Feeds the convergence trend chart.
func (c *Client) GetLiveLoopIterations(id string) ([]LiveLoopIteration, error) {
	var resp struct {
		LoopID     string              `json:"loop_id"`
		Iterations []LiveLoopIteration `json:"iterations"`
	}
	if err := c.get("/api/loops/"+id+"/iterations", &resp); err != nil {
		return nil, err
	}
	return resp.Iterations, nil
}

// CancelLiveLoop terminates an in-flight loop. The reason is recorded on
// the LoopInstance and shown in the panel. Idempotent — cancelling an
// already-terminal loop returns it unchanged.
func (c *Client) CancelLiveLoop(id, reason string) (LiveLoop, error) {
	body := map[string]string{}
	if reason != "" {
		body["reason"] = reason
	}
	var loop LiveLoop
	if err := c.doWith(c.httpClient, http.MethodPost, "/api/loops/"+id+"/cancel", body, &loop); err != nil {
		return LiveLoop{}, err
	}
	return loop, nil
}

// ListLoopTemplates returns the names of every loop template visible to
// the brainbox install. Used by the Templates tab + Trigger tab dropdown.
func (c *Client) ListLoopTemplates() ([]string, error) {
	var resp struct {
		Templates []string `json:"templates"`
	}
	if err := c.get("/api/loops/templates", &resp); err != nil {
		return nil, err
	}
	return resp.Templates, nil
}

// LoopTemplate is the raw text + metadata for one template, returned by
// GET /api/loops/templates/{name}. The Templates tab renders `yaml` in the
// editor; `origin` controls whether Save is enabled vs. requiring fork.
type LoopTemplate struct {
	Name    string `json:"name"`
	Origin  string `json:"origin"` // "built-in" | "user"
	Version string `json:"version"`
	Hash    string `json:"hash"`
	YAML    string `json:"yaml"`
}

// GetLoopTemplate fetches one template by name.
func (c *Client) GetLoopTemplate(name string) (LoopTemplate, error) {
	var t LoopTemplate
	if err := c.get("/api/loops/templates/"+name, &t); err != nil {
		return LoopTemplate{}, err
	}
	return t, nil
}

// LoopTemplateValidation is the result shape of POST /validate. Editor
// renders errors as inline lint annotations; line/col is supplied for
// YAML syntax errors, field paths for pydantic schema errors.
type LoopTemplateValidation struct {
	OK       bool                          `json:"ok"`
	Errors   []LoopTemplateValidationEntry `json:"errors"`
	Warnings []LoopTemplateValidationEntry `json:"warnings"`
}

type LoopTemplateValidationEntry struct {
	Line    *int   `json:"line,omitempty"`
	Col     *int   `json:"col,omitempty"`
	Field   string `json:"field,omitempty"`
	Message string `json:"message"`
}

// ValidateLoopTemplate runs server-side validation on raw template YAML
// without saving. Editor calls this on debounced keystrokes.
func (c *Client) ValidateLoopTemplate(rawYAML string) (LoopTemplateValidation, error) {
	body := map[string]string{"yaml": rawYAML}
	var resp LoopTemplateValidation
	if err := c.doWith(c.httpClient, http.MethodPost, "/api/loops/templates/validate", body, &resp); err != nil {
		return LoopTemplateValidation{}, err
	}
	return resp, nil
}

// DryRunLoopTemplate plans iteration 1 of the template against an optional
// sample envelope without enqueueing. Returns the structured plan as a
// loosely-typed map — the operator-facing JSON shape is rich and not yet
// stable.
func (c *Client) DryRunLoopTemplate(name string, envelope map[string]interface{}) (map[string]interface{}, error) {
	body := map[string]interface{}{}
	if envelope != nil {
		body["envelope"] = envelope
	}
	var resp map[string]interface{}
	if err := c.doWith(c.httpClient, http.MethodPost, "/api/loops/templates/"+name+"/dry-run", body, &resp); err != nil {
		return nil, err
	}
	return resp, nil
}

// PutLoopTemplate writes raw YAML to the user templates dir. Pass fork=true
// to create a user override of a built-in. Returns the freshly-read
// template metadata.
func (c *Client) PutLoopTemplate(name, rawYAML string, fork bool) (LoopTemplate, error) {
	path := "/api/loops/templates/" + name
	if fork {
		path += "?fork=true"
	}
	body := map[string]string{"yaml": rawYAML}
	var resp LoopTemplate
	if err := c.doWith(c.httpClient, http.MethodPut, path, body, &resp); err != nil {
		return LoopTemplate{}, err
	}
	return resp, nil
}

// DeleteLoopTemplate removes a user template by name. 403 on built-ins.
func (c *Client) DeleteLoopTemplate(name string) error {
	return c.doWith(c.httpClient, http.MethodDelete, "/api/loops/templates/"+name, nil, nil)
}

// GetLoopTemplateSchema returns the LoopSpec JSON Schema. Editor consumes
// this to drive Intellisense and inline validation hints.
func (c *Client) GetLoopTemplateSchema() (map[string]interface{}, error) {
	var resp map[string]interface{}
	if err := c.get("/api/loops/templates/schema", &resp); err != nil {
		return nil, err
	}
	return resp, nil
}

// LoopAssistRequest is the body shape for POST /api/loops/templates/assist.
type LoopAssistRequest struct {
	Mode        string                 `json:"mode"` // "generate" | "refine" | "explain"
	Prompt      string                 `json:"prompt"`
	CurrentYAML string                 `json:"current_yaml,omitempty"`
	Selection   map[string]interface{} `json:"selection,omitempty"`
}

// LoopAssistResult is the response shape — yaml for generate/refine,
// explanation for explain mode. Tokens + cost feed the operator-facing
// session cost ticker.
type LoopAssistResult struct {
	YAML        string                 `json:"yaml"`
	Explanation string                 `json:"explanation"`
	Model       string                 `json:"model"`
	Tokens      map[string]int         `json:"tokens"`
	CostUSD     float64                `json:"cost_usd"`
	Warnings    []map[string]interface{} `json:"warnings"`
	Retries     int                    `json:"retries"`
}

// AssistLoopTemplate runs a single AI Assist round. The brainbox endpoint
// handles validation + retry server-side; this is a thin pass-through.
func (c *Client) AssistLoopTemplate(req LoopAssistRequest) (LoopAssistResult, error) {
	var resp LoopAssistResult
	if err := c.doWith(c.httpClient, http.MethodPost, "/api/loops/templates/assist", req, &resp); err != nil {
		return LoopAssistResult{}, err
	}
	return resp, nil
}

// StartLiveLoop kicks off a Loop by template name with the given initial
// artifact_refs. The full envelope is built server-side around the refs.
func (c *Client) StartLiveLoop(templateName string, artifactRefs map[string]interface{}) (LiveLoop, error) {
	body := map[string]interface{}{
		"template_name": templateName,
		"envelope": map[string]interface{}{
			"artifact_refs": artifactRefs,
		},
	}
	var loop LiveLoop
	if err := c.doWith(c.httpClient, http.MethodPost, "/api/loops/start", body, &loop); err != nil {
		return LiveLoop{}, fmt.Errorf("start loop: %w", err)
	}
	return loop, nil
}

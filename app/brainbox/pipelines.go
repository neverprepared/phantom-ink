package brainbox

import "fmt"

// Pipeline represents a pipeline definition.
type Pipeline struct {
	Name        string      `json:"name"`
	Description string      `json:"description"`
	Steps       interface{} `json:"steps"`
}

// PipelineRun represents a pipeline run.
type PipelineRun struct {
	ID         string `json:"id"`
	Pipeline   string `json:"pipeline"`
	Status     string `json:"status"`
	StartedAt  string `json:"started_at"`
	FinishedAt string `json:"finished_at"`
	Error      string `json:"error"`
}

// ListPipelines returns all available pipeline definitions.
func (c *Client) ListPipelines() ([]Pipeline, error) {
	var pipelines []Pipeline
	if err := c.get("/api/pipelines", &pipelines); err != nil {
		return nil, err
	}
	return pipelines, nil
}

// ListPipelineRuns returns all pipeline run records.
func (c *Client) ListPipelineRuns() ([]PipelineRun, error) {
	var runs []PipelineRun
	if err := c.get("/api/pipelines/runs", &runs); err != nil {
		return nil, err
	}
	return runs, nil
}

// GetPipelineRun returns the status of a specific run.
func (c *Client) GetPipelineRun(runID string) (PipelineRun, error) {
	var run PipelineRun
	path := fmt.Sprintf("/api/pipelines/runs/%s", runID)
	if err := c.get(path, &run); err != nil {
		return run, err
	}
	return run, nil
}

// StartPipelineRun starts a pipeline by name.
func (c *Client) StartPipelineRun(name string, params map[string]interface{}) (PipelineRun, error) {
	var run PipelineRun
	path := fmt.Sprintf("/api/pipelines/%s/run", name)
	body := map[string]interface{}{"params": params}
	if err := c.post(path, body, &run); err != nil {
		return run, err
	}
	return run, nil
}

// CancelPipelineRun cancels a running pipeline.
func (c *Client) CancelPipelineRun(runID string) error {
	path := fmt.Sprintf("/api/pipelines/runs/%s/cancel", runID)
	return c.post(path, nil, nil)
}

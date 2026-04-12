package brainbox

import "fmt"

// OllamaModel represents a model available on the Ollama server.
type OllamaModel struct {
	Name       string `json:"name"`
	Size       int64  `json:"size"`
	ModifiedAt string `json:"modified_at"`
	Digest     string `json:"digest"`
}

// ollamaModelsResponse mirrors GET /api/ollama/models
type ollamaModelsResponse struct {
	Models []OllamaModel `json:"models"`
}

// ollamaActionResponse mirrors POST /api/ollama/pull and DELETE /api/ollama/models/{name}
type ollamaActionResponse struct {
	Status string `json:"status"`
	Model  string `json:"model"`
}

// ListOllamaModels returns the list of models on the Ollama server.
func (c *Client) ListOllamaModels() ([]OllamaModel, error) {
	var resp ollamaModelsResponse
	if err := c.get("/api/ollama/models", &resp); err != nil {
		return nil, err
	}
	return resp.Models, nil
}

// PullOllamaModel pulls a model from the Ollama registry.
func (c *Client) PullOllamaModel(name string) (string, error) {
	var resp ollamaActionResponse
	if err := c.post("/api/ollama/pull", map[string]string{"name": name}, &resp); err != nil {
		return "", err
	}
	return resp.Status, nil
}

// DeleteOllamaModel deletes a model from the Ollama server.
func (c *Client) DeleteOllamaModel(name string) error {
	path := fmt.Sprintf("/api/ollama/models/%s", name)
	return c.delete(path, nil)
}

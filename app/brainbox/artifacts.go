package brainbox

import (
	"bytes"
	"fmt"
	"io"
	"net/http"
	"net/url"
)

// Artifact represents an object stored in artifact storage.
type Artifact struct {
	Key          string `json:"key"`
	Size         int64  `json:"size"`
	LastModified string `json:"last_modified"`
	ContentType  string `json:"content_type"`
}

// ArtifactHealth is the health response for artifact storage.
type ArtifactHealth struct {
	Status  string `json:"status"`
	Message string `json:"message"`
}

// ListArtifacts returns all artifacts, optionally filtered by prefix.
func (c *Client) ListArtifacts(prefix string) ([]Artifact, error) {
	path := "/api/artifacts"
	if prefix != "" {
		path += "?prefix=" + url.QueryEscape(prefix)
	}
	var artifacts []Artifact
	if err := c.get(path, &artifacts); err != nil {
		return nil, err
	}
	return artifacts, nil
}

// DownloadArtifact fetches the raw bytes of an artifact.
func (c *Client) DownloadArtifact(key string) ([]byte, error) {
	path := fmt.Sprintf("/api/artifacts/%s", url.PathEscape(key))
	req, err := http.NewRequest(http.MethodGet, c.baseURL+path, nil)
	if err != nil {
		return nil, fmt.Errorf("create request: %w", err)
	}
	if c.apiKey != "" {
		req.Header.Set("X-API-Key", c.apiKey)
	}
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("request failed: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		body, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("HTTP %d: %s", resp.StatusCode, string(body))
	}
	return io.ReadAll(resp.Body)
}

// UploadArtifact uploads bytes to artifact storage at the given key.
func (c *Client) UploadArtifact(key string, data []byte, contentType string) error {
	path := fmt.Sprintf("/api/artifacts/%s", url.PathEscape(key))
	req, err := http.NewRequest(http.MethodPost, c.baseURL+path, bytes.NewReader(data))
	if err != nil {
		return fmt.Errorf("create request: %w", err)
	}
	if contentType != "" {
		req.Header.Set("Content-Type", contentType)
	} else {
		req.Header.Set("Content-Type", "application/octet-stream")
	}
	if c.apiKey != "" {
		req.Header.Set("X-API-Key", c.apiKey)
	}
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("request failed: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		body, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("HTTP %d: %s", resp.StatusCode, string(body))
	}
	return nil
}

// DeleteArtifact removes an artifact from storage.
func (c *Client) DeleteArtifact(key string) error {
	path := fmt.Sprintf("/api/artifacts/%s", url.PathEscape(key))
	return c.delete(path, nil)
}

// GetArtifactHealth checks the artifact storage health.
func (c *Client) GetArtifactHealth() (ArtifactHealth, error) {
	var h ArtifactHealth
	if err := c.get("/api/artifacts/health", &h); err != nil {
		return h, err
	}
	return h, nil
}

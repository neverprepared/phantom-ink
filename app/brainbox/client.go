package brainbox

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

// Client is a typed HTTP client for the brainbox REST API.
type Client struct {
	baseURL    string
	apiKey     string
	httpClient *http.Client
}

// NewClient creates a new brainbox API client.
func NewClient(baseURL, apiKey string) *Client {
	return &Client{
		baseURL: baseURL,
		apiKey:  apiKey,
		httpClient: &http.Client{
			Timeout: 15 * time.Second,
		},
	}
}

// Update reconfigures the client's baseURL and apiKey.
func (c *Client) Update(baseURL, apiKey string) {
	c.baseURL = baseURL
	c.apiKey = apiKey
}

// BaseURL returns the current base URL.
func (c *Client) BaseURL() string {
	return c.baseURL
}

// do performs an HTTP request and unmarshals the JSON response into result.
// If result is nil, the response body is discarded.
func (c *Client) do(method, path string, body interface{}, result interface{}) error {
	var bodyReader io.Reader
	if body != nil {
		data, err := json.Marshal(body)
		if err != nil {
			return fmt.Errorf("marshal body: %w", err)
		}
		bodyReader = bytes.NewReader(data)
	}

	req, err := http.NewRequest(method, c.baseURL+path, bodyReader)
	if err != nil {
		return fmt.Errorf("create request: %w", err)
	}

	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	if c.apiKey != "" {
		req.Header.Set("X-API-Key", c.apiKey)
	}

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("request failed: %w", err)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return fmt.Errorf("read response: %w", err)
	}

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("HTTP %d: %s", resp.StatusCode, string(respBody))
	}

	if result != nil {
		if err := json.Unmarshal(respBody, result); err != nil {
			return fmt.Errorf("unmarshal response: %w", err)
		}
	}

	return nil
}

// doWith is like do but uses a custom HTTP client (e.g. longer timeout).
func (c *Client) doWith(httpClient *http.Client, method, path string, body interface{}, result interface{}) error {
	var bodyReader io.Reader
	if body != nil {
		data, err := json.Marshal(body)
		if err != nil {
			return fmt.Errorf("marshal body: %w", err)
		}
		bodyReader = bytes.NewReader(data)
	}

	req, err := http.NewRequest(method, c.baseURL+path, bodyReader)
	if err != nil {
		return fmt.Errorf("create request: %w", err)
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	if c.apiKey != "" {
		req.Header.Set("X-API-Key", c.apiKey)
	}

	resp, err := httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("request failed: %w", err)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return fmt.Errorf("read response: %w", err)
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("HTTP %d: %s", resp.StatusCode, string(respBody))
	}
	if result != nil {
		if err := json.Unmarshal(respBody, result); err != nil {
			return fmt.Errorf("unmarshal response: %w", err)
		}
	}
	return nil
}

// get is a convenience wrapper for GET requests.
func (c *Client) get(path string, result interface{}) error {
	return c.do(http.MethodGet, path, nil, result)
}

// post is a convenience wrapper for POST requests.
func (c *Client) post(path string, body interface{}, result interface{}) error {
	return c.do(http.MethodPost, path, body, result)
}

// patch is a convenience wrapper for PATCH requests.
func (c *Client) patch(path string, body interface{}, result interface{}) error {
	return c.do(http.MethodPatch, path, body, result)
}

// delete is a convenience wrapper for DELETE requests.
func (c *Client) delete(path string, result interface{}) error {
	return c.do(http.MethodDelete, path, nil, result)
}

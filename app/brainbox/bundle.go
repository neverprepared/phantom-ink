package brainbox

// Credential-bundle endpoints (gateway_bundle.py). The PUT body is a raw
// tar.gz — not JSON — so it gets its own doRaw helper.

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
)

// BundleMeta mirrors GET /api/gateway/profiles/{p}/bundle.
type BundleMeta struct {
	Profile        string `json:"profile"`
	Etag           string `json:"etag"`
	Size           int64  `json:"size"`
	LastModifiedMs int64  `json:"last_modified_ms"`
	CapturedAt     string `json:"captured_at"`
	AppVersion     string `json:"app_version"`
}

// BundlePutResult mirrors the PUT response.
type BundlePutResult struct {
	Profile    string   `json:"profile"`
	Saved      bool     `json:"saved"`
	Etag       string   `json:"etag"`
	CapturedAt string   `json:"captured_at"`
	Sources    []string `json:"sources"`
	Size       int64    `json:"size"`
}

// doRaw sends a non-JSON body and decodes a JSON response into result.
func (c *Client) doRaw(method, path, contentType string, body []byte, headers map[string]string, result interface{}) error {
	baseURL, apiKey := c.snapshot()
	req, err := http.NewRequest(method, baseURL+path, bytes.NewReader(body))
	if err != nil {
		return fmt.Errorf("create request: %w", err)
	}
	if contentType != "" {
		req.Header.Set("Content-Type", contentType)
	}
	if apiKey != "" {
		req.Header.Set("X-API-Key", apiKey)
	}
	for k, v := range headers {
		req.Header.Set(k, v)
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
		capped := respBody
		if len(capped) > 500 {
			capped = append(capped[:500], []byte("…")...)
		}
		return fmt.Errorf("HTTP %d: %s", resp.StatusCode, string(capped))
	}
	if result != nil {
		if err := json.Unmarshal(respBody, result); err != nil {
			return fmt.Errorf("unmarshal response: %w", err)
		}
	}
	return nil
}

// PutProfileBundle uploads a plaintext bundle tar.gz for a profile; the
// daemon encrypts it before it touches MinIO.
func (c *Client) PutProfileBundle(profile string, tarGz []byte, appVersion string) (BundlePutResult, error) {
	var out BundlePutResult
	path := fmt.Sprintf("/api/gateway/profiles/%s/bundle", url.PathEscape(profile))
	err := c.doRaw(http.MethodPut, path, "application/gzip", tarGz,
		map[string]string{"X-App-Version": appVersion}, &out)
	return out, err
}

// GetProfileBundleMeta returns bundle metadata, or (nil, nil) when no
// bundle is stored for the profile.
func (c *Client) GetProfileBundleMeta(profile string) (*BundleMeta, error) {
	var out BundleMeta
	path := fmt.Sprintf("/api/gateway/profiles/%s/bundle", url.PathEscape(profile))
	if err := c.get(path, &out); err != nil {
		if strings.Contains(err.Error(), "HTTP 404") {
			return nil, nil
		}
		return nil, err
	}
	return &out, nil
}

// DeleteProfileBundle removes a profile's stored bundle.
func (c *Client) DeleteProfileBundle(profile string) error {
	path := fmt.Sprintf("/api/gateway/profiles/%s/bundle", url.PathEscape(profile))
	return c.delete(path, nil)
}

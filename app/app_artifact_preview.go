package main

// Preview + download for the Files panel. Content flows through Go
// (presign → http.Get here) — the webview never fetches MinIO itself,
// keeping the no-fetch-from-JS convention. The existing presigned
// window.open stays available in the panel as the fallback "Open".

import (
	"encoding/base64"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/wailsapp/wails/v2/pkg/runtime"
)

// ArtifactPreview is what the Files panel's detail pane renders.
type ArtifactPreview struct {
	Kind        string `json:"kind"` // text | image | unsupported
	ContentType string `json:"content_type"`
	Size        int64  `json:"size"`
	Text        string `json:"text,omitempty"`
	DataURI     string `json:"data_uri,omitempty"`
	Truncated   bool   `json:"truncated"`
	Reason      string `json:"reason,omitempty"` // why unsupported
}

const (
	previewTextCap  = 2 << 20 // 2 MiB
	previewImageCap = 5 << 20 // 5 MiB
)

var textualTypes = map[string]bool{
	"application/json":         true,
	"application/x-yaml":       true,
	"application/yaml":         true,
	"application/toml":         true,
	"application/xml":          true,
	"application/javascript":   true,
	"application/x-sh":         true,
	"application/x-ndjson":     true,
	"application/octet-stream": false, // decided by extension
}

var textualExts = map[string]bool{
	".md": true, ".txt": true, ".json": true, ".yaml": true, ".yml": true,
	".toml": true, ".xml": true, ".csv": true, ".log": true, ".sh": true,
	".py": true, ".go": true, ".ts": true, ".js": true, ".svelte": true,
	".env": true, ".ini": true, ".cfg": true, ".conf": true, ".cast": true,
}

var imageTypes = map[string]bool{
	"image/png": true, "image/jpeg": true, "image/gif": true,
	"image/webp": true, "image/svg+xml": true,
}

// classifyPreview decides how (whether) to preview an object. Pure —
// unit-tested against the type/size matrix.
func classifyPreview(contentType, name string, size int64) (kind, reason string) {
	ct := strings.ToLower(strings.TrimSpace(strings.SplitN(contentType, ";", 2)[0]))
	ext := strings.ToLower(filepath.Ext(name))
	switch {
	case imageTypes[ct]:
		if size > previewImageCap {
			return "unsupported", fmt.Sprintf("image too large to preview (%d bytes)", size)
		}
		return "image", ""
	case strings.HasPrefix(ct, "text/"), textualTypes[ct], (ct == "" || ct == "application/octet-stream") && textualExts[ext]:
		if size > previewTextCap {
			return "unsupported", fmt.Sprintf("file too large to preview (%d bytes)", size)
		}
		return "text", ""
	default:
		return "unsupported", "no preview for this type"
	}
}

// fetchPresigned downloads an object body via a fresh presigned GET,
// signed against this app's resolved MinIO address (the fetch happens
// from the app's machine, not the daemon's).
func (a *App) fetchPresigned(bucketKey, key string, maxBytes int64) ([]byte, error) {
	presigned, err := a.client.PresignArtifactURL(bucketKey, key, "get", 300, a.resolveMinioAddress())
	if err != nil {
		return nil, fmt.Errorf("presign: %w", err)
	}
	httpc := &http.Client{Timeout: 60 * time.Second}
	resp, err := httpc.Get(presigned.URL)
	if err != nil {
		return nil, fmt.Errorf("fetch: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("fetch: HTTP %d", resp.StatusCode)
	}
	return io.ReadAll(io.LimitReader(resp.Body, maxBytes))
}

// GetArtifactPreview returns renderable preview content for an object.
func (a *App) GetArtifactPreview(bucketKey, key string) (ArtifactPreview, error) {
	head, err := a.client.HeadArtifactObject(bucketKey, key)
	if err != nil {
		return ArtifactPreview{}, err
	}
	kind, reason := classifyPreview(head.ContentType, head.Key, head.Size)
	out := ArtifactPreview{Kind: kind, ContentType: head.ContentType, Size: head.Size, Reason: reason}
	switch kind {
	case "text":
		data, err := a.fetchPresigned(bucketKey, key, previewTextCap)
		if err != nil {
			return ArtifactPreview{}, err
		}
		out.Text = string(data)
		out.Truncated = int64(len(data)) < head.Size
	case "image":
		data, err := a.fetchPresigned(bucketKey, key, previewImageCap)
		if err != nil {
			return ArtifactPreview{}, err
		}
		ct := strings.SplitN(head.ContentType, ";", 2)[0]
		out.DataURI = "data:" + ct + ";base64," + base64.StdEncoding.EncodeToString(data)
	}
	return out, nil
}

// SearchArtifactsFiles is the Wails binding for the Files search box.
func (a *App) SearchArtifactsFiles(bucketKey, query string) (interface{}, error) {
	return a.client.SearchArtifacts(bucketKey, query)
}

// DownloadArtifactObject saves an object to a user-chosen local path via
// the native save dialog. Returns the saved path, or "" when the user
// cancelled.
func (a *App) DownloadArtifactObject(bucketKey, key, suggestedName string) (string, error) {
	if suggestedName == "" {
		suggestedName = filepath.Base(key)
	}
	dest, err := runtime.SaveFileDialog(a.ctx, runtime.SaveDialogOptions{
		Title:           "Save file",
		DefaultFilename: suggestedName,
	})
	if err != nil || dest == "" {
		return "", err
	}
	presigned, err := a.client.PresignArtifactURL(bucketKey, key, "get", 300, a.resolveMinioAddress())
	if err != nil {
		return "", fmt.Errorf("presign: %w", err)
	}
	httpc := &http.Client{Timeout: 10 * time.Minute}
	resp, err := httpc.Get(presigned.URL)
	if err != nil {
		return "", fmt.Errorf("download: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("download: HTTP %d", resp.StatusCode)
	}
	f, err := os.Create(dest)
	if err != nil {
		return "", err
	}
	defer f.Close()
	if _, err := io.Copy(f, resp.Body); err != nil {
		return "", fmt.Errorf("write %s: %w", dest, err)
	}
	return dest, nil
}

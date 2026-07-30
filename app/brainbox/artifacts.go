package brainbox

import (
	"fmt"
	"net/url"
)

// ArtifactsHealth is the cheap reachability probe the file browser
// uses on mount to decide whether to render content or a "MinIO is
// not reachable" empty state.
type ArtifactsHealth struct {
	OK            bool              `json:"ok"`
	Reason        string            `json:"reason,omitempty"`
	Endpoint      string            `json:"endpoint,omitempty"`
	Buckets       map[string]string `json:"buckets,omitempty"`
	ProfilePrefix string            `json:"profile_prefix,omitempty"`
	// ProtectedBucket + ProtectedDirs describe the immutable vault folders
	// (memory/artifacts/tasks/skills under the shared brain bucket). The
	// Files panel renders <ProtectedBucket>/<profile>/<dir>/ read-only.
	ProtectedBucket string   `json:"protected_bucket,omitempty"`
	ProtectedDirs   []string `json:"protected_dirs,omitempty"`
}

// ArtifactBucket is one entry in the live bucket catalog.
type ArtifactBucket struct {
	Key         string `json:"key"`          // bucket identifier the API expects
	Name        string `json:"name"`         // real MinIO bucket name
	Label       string `json:"label"`        // human-facing label in the sidebar
	ScopePrefix string `json:"scope_prefix"` // browser root for the active profile ("" = bucket root)
}

// ArtifactFolder is a directory entry (S3 CommonPrefix).
type ArtifactFolder struct {
	Name   string `json:"name"`
	Prefix string `json:"prefix"`
}

// ArtifactFile is one object at the current folder level.
type ArtifactFile struct {
	Name           string `json:"name"`
	Key            string `json:"key"`
	Size           int64  `json:"size"`
	ETag           string `json:"etag"`
	LastModifiedMs int64  `json:"last_modified_ms"`
}

// ArtifactListing is what /api/artifacts/{bucket}/list returns —
// folders + files at one level.
type ArtifactListing struct {
	Bucket    string           `json:"bucket"`
	Prefix    string           `json:"prefix"`
	Truncated bool             `json:"truncated"`
	Folders   []ArtifactFolder `json:"folders"`
	Files     []ArtifactFile   `json:"files"`
}

// ArtifactObjectHead is the metadata response for the file detail pane.
type ArtifactObjectHead struct {
	Bucket         string            `json:"bucket"`
	Key            string            `json:"key"`
	Size           int64             `json:"size"`
	ETag           string            `json:"etag"`
	ContentType    string            `json:"content_type"`
	LastModifiedMs int64             `json:"last_modified_ms"`
	Metadata       map[string]string `json:"metadata"`
}

// ArtifactPresignedURL is the response from /api/artifacts/{bucket}/presign.
type ArtifactPresignedURL struct {
	URL       string `json:"url"`
	ExpiresIn int    `json:"expires_in"`
}

// GetArtifactsHealth probes MinIO. Frontend calls this on Files panel
// mount and on the periodic refresh. 503 (integration disabled) and
// 200 with ok=false (integration enabled but unreachable) are
// different states — the panel renders distinct empty states for each.
func (c *Client) GetArtifactsHealth() (ArtifactsHealth, error) {
	var h ArtifactsHealth
	if err := c.get("/api/artifacts/health", &h); err != nil {
		return ArtifactsHealth{}, err
	}
	return h, nil
}

// ListArtifactsBuckets returns the live bucket catalog, scoped to the
// app's active profile ("" = all buckets, unscoped).
func (c *Client) ListArtifactsBuckets(profile string) ([]ArtifactBucket, error) {
	path := "/api/artifacts/buckets"
	if profile != "" {
		path += "?profile=" + url.QueryEscape(profile)
	}
	var resp struct {
		Buckets []ArtifactBucket `json:"buckets"`
	}
	if err := c.get(path, &resp); err != nil {
		return nil, err
	}
	return resp.Buckets, nil
}

// ListArtifactsFolder lists folders + files at one prefix in a bucket.
// ``bucketKey`` is "vault" or "artifacts" (the logical name); the
// profile prefix is applied server-side. ``prefix`` is the path the
// operator navigated to within their profile namespace.
func (c *Client) ListArtifactsFolder(bucketKey, prefix string) (ArtifactListing, error) {
	path := fmt.Sprintf("/api/artifacts/%s/list", url.PathEscape(bucketKey))
	if prefix != "" {
		path += "?prefix=" + url.QueryEscape(prefix)
	}
	var listing ArtifactListing
	if err := c.get(path, &listing); err != nil {
		return ArtifactListing{}, err
	}
	return listing, nil
}

// HeadArtifactObject fetches object metadata without downloading the body.
func (c *Client) HeadArtifactObject(bucketKey, key string) (ArtifactObjectHead, error) {
	path := fmt.Sprintf(
		"/api/artifacts/%s/head?key=%s",
		url.PathEscape(bucketKey),
		url.QueryEscape(key),
	)
	var head ArtifactObjectHead
	if err := c.get(path, &head); err != nil {
		return ArtifactObjectHead{}, err
	}
	return head, nil
}

// DeleteArtifactObject removes an object.
func (c *Client) DeleteArtifactObject(bucketKey, key string) error {
	path := fmt.Sprintf(
		"/api/artifacts/%s/object?key=%s",
		url.PathEscape(bucketKey),
		url.QueryEscape(key),
	)
	return c.doWith(c.httpClient, "DELETE", path, nil, nil)
}

// ArtifactSearchResult is what /api/artifacts/{bucket}/search returns.
type ArtifactSearchResult struct {
	Bucket    string         `json:"bucket"`
	Query     string         `json:"query"`
	Truncated bool           `json:"truncated"`
	Scanned   int            `json:"scanned"`
	Files     []ArtifactFile `json:"files"`
}

// SearchArtifacts runs a substring search over object keys under
// ``prefix`` (the bucket's scope_prefix; "" = whole bucket).
func (c *Client) SearchArtifacts(bucketKey, query, prefix string) (ArtifactSearchResult, error) {
	path := fmt.Sprintf(
		"/api/artifacts/%s/search?q=%s&prefix=%s",
		url.PathEscape(bucketKey),
		url.QueryEscape(query),
		url.QueryEscape(prefix),
	)
	var res ArtifactSearchResult
	if err := c.get(path, &res); err != nil {
		return ArtifactSearchResult{}, err
	}
	return res, nil
}

// PresignArtifactURL mints a presigned URL. ``op`` is "get" (for
// operator file opens) or "put" (reserved for the Phase 4 assist
// worker writes). ``host`` is the base URL the caller will fetch from —
// SigV4 signs the Host, so it must be supplied at signing time; empty
// defers to the daemon's configured public endpoint.
func (c *Client) PresignArtifactURL(bucketKey, key, op string, ttlSeconds int, host string) (ArtifactPresignedURL, error) {
	path := fmt.Sprintf(
		"/api/artifacts/%s/presign?key=%s&op=%s&ttl=%d",
		url.PathEscape(bucketKey),
		url.QueryEscape(key),
		url.QueryEscape(op),
		ttlSeconds,
	)
	if host != "" {
		path += "&host=" + url.QueryEscape(host)
	}
	var resp ArtifactPresignedURL
	if err := c.get(path, &resp); err != nil {
		return ArtifactPresignedURL{}, err
	}
	return resp, nil
}

// PutArtifactObject creates or overwrites an object with raw bytes. Used
// for uploads, empty-file creation, and folder creation (a key ending in
// "/" with a nil body). The daemon refuses (403) writes to protected
// vault folders.
func (c *Client) PutArtifactObject(bucketKey, key string, data []byte, contentType string) error {
	path := fmt.Sprintf(
		"/api/artifacts/%s/object?key=%s",
		url.PathEscape(bucketKey), url.QueryEscape(key),
	)
	if contentType != "" {
		path += "&content_type=" + url.QueryEscape(contentType)
	}
	ct := contentType
	if ct == "" {
		ct = "application/octet-stream"
	}
	return c.doRaw("PUT", path, ct, data, nil, nil)
}

// CopyArtifactObject copies src→dst within a bucket; move=true renames
// (deletes the source). The daemon refuses (403) when the destination —
// or, for a move, the source — is inside a protected vault folder.
func (c *Client) CopyArtifactObject(bucketKey, src, dst string, move bool) error {
	path := fmt.Sprintf(
		"/api/artifacts/%s/copy?src=%s&dst=%s&move=%t",
		url.PathEscape(bucketKey), url.QueryEscape(src), url.QueryEscape(dst), move,
	)
	return c.post(path, nil, nil)
}

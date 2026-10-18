package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"time"
)

// Wails-bound surface for the p2p phantom-brain mesh. The mesh daemon exposes
// an UNAUTHENTICATED GET {url}/admin/mesh/status describing the local node plus
// each peer's sync state. This backend is DISTINCT from the brainbox API — its
// URL is its own integration ("phantom-brain-mesh"), default http://127.0.0.1:9998.

const meshServiceName = "phantom-brain-mesh"
const meshDefaultURL = "http://127.0.0.1:9998"

// MeshPeer is one remote node in the mesh plus this node's sync lag against it.
// The lag fields are short daemon-formatted strings ("0", ">=100", "off", "err",
// "n/a", "never", "4s", "12m", "3h", "2d") — displayed verbatim.
type MeshPeer struct {
	ID        string `json:"id"`
	BaseURL   string `json:"base_url"`
	Profile   string `json:"profile"`
	Live      bool   `json:"live"`      // peer reachable this tick
	LiveNote  string `json:"live_note"` // reason when down
	DBLag     string `json:"db_lag"`
	LinksLag  string `json:"links_lag"`
	CursorAge string `json:"cursor_age"`
}

// MeshMetrics are the pb_sync_* counters/gauges. The daemon emits them as
// strings; kept as strings here so the frontend controls parsing/formatting.
type MeshMetrics struct {
	RoundsTotal        string `json:"pb_sync_rounds_total"`
	RowsMergedTotal    string `json:"pb_sync_rows_merged_total"`
	BlobsFetchedTotal  string `json:"pb_sync_blobs_fetched_total"`
	OrphanBlobsGCTotal string `json:"pb_sync_orphan_blobs_gc_total"`
	ErrorsTotal        string `json:"pb_sync_errors_total"`
	LastTickMS         string `json:"pb_sync_last_tick_ms"` // epoch-ms, "0" = never
}

// MeshStatus is the full mesh snapshot: the local node id, whether sync is on,
// each peer, and the aggregate sync metrics.
type MeshStatus struct {
	NodeID      string      `json:"node_id"`
	SyncEnabled bool        `json:"sync_enabled"`
	Peers       []MeshPeer  `json:"peers"`
	Metrics     MeshMetrics `json:"metrics"`
}

// meshURL resolves the configured mesh daemon URL, falling back to the default.
func (a *App) meshURL() string {
	cfg := a.getIntegrationConfig(meshServiceName)
	if url := cfg.ActiveURL(meshDefaultURL); url != "" {
		return url
	}
	return meshDefaultURL
}

// GetMeshStatus fetches the mesh snapshot from the configured daemon. Returns a
// clear error if the daemon is unreachable so the panel can surface the URL +
// an "check the integration" hint.
func (a *App) GetMeshStatus() (MeshStatus, error) {
	url := a.meshURL()
	endpoint := url + "/admin/mesh/status"

	req, err := http.NewRequest(http.MethodGet, endpoint, nil)
	if err != nil {
		return MeshStatus{}, fmt.Errorf("build mesh request: %w", err)
	}

	httpClient := &http.Client{Timeout: 10 * time.Second}
	resp, err := httpClient.Do(req)
	if err != nil {
		return MeshStatus{}, fmt.Errorf("mesh daemon unreachable at %s: %w", url, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return MeshStatus{}, fmt.Errorf("mesh daemon at %s returned HTTP %d", url, resp.StatusCode)
	}

	var status MeshStatus
	if err := json.NewDecoder(resp.Body).Decode(&status); err != nil {
		return MeshStatus{}, fmt.Errorf("decode mesh status from %s: %w", url, err)
	}
	return status, nil
}

// SyncError mirrors nodesync.SyncError in phantom-brain — one retained sync
// failure, returned by the retry action so an "it worked" 200 can still carry
// a failed pull.
type SyncError struct {
	At      string `json:"at"`
	Peer    string `json:"peer"`
	Profile string `json:"profile"`
	Phase   string `json:"phase"`
	Class   string `json:"class"`
	Err     string `json:"err"`
}

// MeshActionResult is the union of the three remediation responses — each
// endpoint fills the fields it owns and leaves the rest zero. Errors is
// populated only by retry, and only when at least one pull/push failed.
type MeshActionResult struct {
	RowsMerged   int         `json:"rows_merged"`
	LinksMerged  int         `json:"links_merged"`
	BlobsFetched int         `json:"blobs_fetched"`
	Cleared      int         `json:"cleared"`
	Reset        bool        `json:"reset"`
	Errors       []SyncError `json:"errors,omitempty"`
}

// postMeshAdmin POSTs one mesh remediation action. Free function over its
// dependencies so it is testable without an App.
//
// A missing key short-circuits before the request: the daemon's 403 for an
// unset PB_ADMIN_KEY and for a wrong key are indistinguishable, and "you never
// set a key" is the more useful of the two diagnoses.
func postMeshAdmin(base, key, path string, hc *http.Client) (MeshActionResult, error) {
	var out MeshActionResult
	if strings.TrimSpace(key) == "" {
		return out, fmt.Errorf("no mesh admin key configured — set one in Settings")
	}
	req, err := http.NewRequest(http.MethodPost, strings.TrimRight(base, "/")+path, nil)
	if err != nil {
		return out, fmt.Errorf("build mesh action request: %w", err)
	}
	req.Header.Set("X-API-Key", key)
	resp, err := hc.Do(req)
	if err != nil {
		return out, fmt.Errorf("mesh daemon unreachable at %s: %w", base, err)
	}
	defer resp.Body.Close()

	switch {
	case resp.StatusCode == http.StatusForbidden:
		return out, fmt.Errorf("mesh admin key rejected by the daemon")
	case resp.StatusCode == http.StatusNotFound:
		return out, fmt.Errorf("unknown peer — the peer list is stale, refresh and retry")
	case resp.StatusCode == http.StatusServiceUnavailable:
		return out, fmt.Errorf("sync is disabled on this node")
	case resp.StatusCode < 200 || resp.StatusCode >= 300:
		return out, fmt.Errorf("mesh action failed: HTTP %d", resp.StatusCode)
	}
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return out, fmt.Errorf("decode mesh action result: %w", err)
	}
	return out, nil
}

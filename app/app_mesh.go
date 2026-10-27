package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
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

	LastError *SyncError `json:"last_error,omitempty"`
	Errors24h int        `json:"errors_24h"`
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

	RecentErrors []SyncError `json:"recent_errors,omitempty"`
	Errors15m    int         `json:"errors_15m"`
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
		return MeshStatus{}, fmt.Errorf("%s: %w", meshReach(err, url), err)
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
// meshReach distinguishes a timeout from an unreachable daemon. Reporting a
// slow-but-alive daemon as "unreachable" sent the operator off to restart a
// healthy container; a timed-out action may also still be running server-side,
// which the operator needs to know before retrying it.
func meshReach(err error, target string) string {
	if os.IsTimeout(err) || errors.Is(err, context.DeadlineExceeded) {
		return fmt.Sprintf("mesh daemon at %s did not respond in time — it may still be working on this action", target)
	}
	return fmt.Sprintf("mesh daemon unreachable at %s", target)
}

// meshDetail renders the daemon's own error body as a trailing clause. The
// daemon sends a useful envelope on every non-2xx ("no configured peer with id
// X", "no database for profile Y" plus a cause); discarding it left the
// operator with a bare status code and nothing to act on. Returns "" when the
// body is empty or unreadable, so the caller's sentence still stands alone.
func meshDetail(resp *http.Response) string {
	body, err := io.ReadAll(io.LimitReader(resp.Body, 4096))
	if err != nil {
		return ""
	}
	msg := strings.TrimSpace(string(body))
	if msg == "" {
		return ""
	}
	// Unwrap {"error":{"code":...,"message":"..."}} to the message alone;
	// fall back to the raw body for any other shape.
	var env struct {
		Error struct {
			Code    string `json:"code"`
			Message string `json:"message"`
		} `json:"error"`
	}
	if json.Unmarshal(body, &env) == nil && strings.TrimSpace(env.Error.Message) != "" {
		msg = strings.TrimSpace(env.Error.Message)
	}
	if len(msg) > 300 {
		msg = msg[:300] + "…"
	}
	return ": " + msg
}

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
		return out, fmt.Errorf("%s: %w", meshReach(err, base), err)
	}
	defer resp.Body.Close()

	switch {
	case resp.StatusCode == http.StatusForbidden:
		return out, fmt.Errorf("mesh admin key rejected by the daemon (403)%s", meshDetail(resp))
	case resp.StatusCode == http.StatusNotFound:
		return out, fmt.Errorf("unknown peer — the peer list is stale, refresh and retry%s", meshDetail(resp))
	case resp.StatusCode == http.StatusServiceUnavailable:
		return out, fmt.Errorf("sync is disabled on this node%s", meshDetail(resp))
	case resp.StatusCode < 200 || resp.StatusCode >= 300:
		return out, fmt.Errorf("mesh action failed: HTTP %d%s", resp.StatusCode, meshDetail(resp))
	}
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return out, fmt.Errorf("decode mesh action result: %w", err)
	}
	return out, nil
}

const meshClearErrorsPath = "/api/brain/admin/mesh/errors/clear"

// Peer ids come from the daemon but round-trip through the frontend, so escape
// them rather than trusting the shape.
func meshRetryPath(id string) string {
	return "/api/brain/admin/mesh/peers/" + url.PathEscape(id) + "/retry"
}

func meshResetCursorPath(id string) string {
	return "/api/brain/admin/mesh/peers/" + url.PathEscape(id) + "/reset-cursor"
}

// meshAdminKey reads the stored operator key, falling back to an ambient
// PB_ADMIN_KEY so a developer running the daemon locally needs no setup.
func (a *App) meshAdminKey() string {
	if a.db != nil {
		if k := strings.TrimSpace(a.db.GetSetting(settingMeshAdminKey, "")); k != "" {
			return k
		}
	}
	return strings.TrimSpace(os.Getenv("PB_ADMIN_KEY"))
}

// GetMeshAdminKeySet reports whether an admin key is available, so the panel
// can disable the action buttons with a reason instead of surfacing a 403.
func (a *App) GetMeshAdminKeySet() bool { return a.meshAdminKey() != "" }

// SetMeshAdminKey stores the operator key.
func (a *App) SetMeshAdminKey(key string) error {
	if a.db == nil {
		return fmt.Errorf("no local database")
	}
	return a.db.SetSetting(settingMeshAdminKey, strings.TrimSpace(key))
}

func (a *App) meshActionClient() *http.Client {
	return &http.Client{Timeout: 30 * time.Second}
}

// RetryMeshPeer pulls one peer immediately instead of waiting for the next
// sync round — the "did my fix work?" button.
func (a *App) RetryMeshPeer(id string) (MeshActionResult, error) {
	return postMeshAdmin(a.meshURL(), a.meshAdminKey(), meshRetryPath(id), a.meshActionClient())
}

// ResetMeshPeerCursor forces a full op-log re-read for one peer. Costs
// bandwidth, cannot lose data; the fix for a wedged or stale cursor.
func (a *App) ResetMeshPeerCursor(id string) (MeshActionResult, error) {
	return postMeshAdmin(a.meshURL(), a.meshAdminKey(), meshResetCursorPath(id), a.meshActionClient())
}

// ClearMeshErrors empties the daemon's retained error ring.
func (a *App) ClearMeshErrors() (MeshActionResult, error) {
	return postMeshAdmin(a.meshURL(), a.meshAdminKey(), meshClearErrorsPath, a.meshActionClient())
}

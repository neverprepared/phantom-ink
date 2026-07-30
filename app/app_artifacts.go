package main

import (
	"fmt"
	"mime"
	"os"
	"path"
	"path/filepath"
	"strings"

	"github.com/wailsapp/wails/v2/pkg/runtime"

	"phantom-ink/brainbox"
)

// Wails-bound surface for the MinIO artifact store.
//
// All methods delegate to brainbox.Client and pass any errors back
// to the frontend untouched. The Files panel renders the 503
// "integration disabled" state distinctly from 200 + ok=false
// "MinIO is unreachable", so we don't swallow either signal here.

// GetArtifactsHealth — used on Files panel mount + periodic refresh.
func (a *App) GetArtifactsHealth() (brainbox.ArtifactsHealth, error) {
	return a.client.GetArtifactsHealth()
}

// ListArtifactsBuckets returns the live bucket catalog scoped to the
// given profile ("" = all buckets).
func (a *App) ListArtifactsBuckets(profile string) ([]brainbox.ArtifactBucket, error) {
	return a.client.ListArtifactsBuckets(profile)
}

// ListArtifactsFolder lists one prefix level — folders + files.
func (a *App) ListArtifactsFolder(bucketKey, prefix string) (brainbox.ArtifactListing, error) {
	return a.client.ListArtifactsFolder(bucketKey, prefix)
}

// HeadArtifactObject fetches metadata for the detail pane.
func (a *App) HeadArtifactObject(bucketKey, key string) (brainbox.ArtifactObjectHead, error) {
	return a.client.HeadArtifactObject(bucketKey, key)
}

// DeleteArtifactObject removes an object.
func (a *App) DeleteArtifactObject(bucketKey, key string) error {
	return a.client.DeleteArtifactObject(bucketKey, key)
}

// resolveMinioAddress returns the MinIO base URL this app instance should
// fetch presigned URLs from, per the app's MinIO integration config:
// remote toggle on → remote_url, off → local_url. Empty = defer to the
// daemon's CL_MINIO__PUBLIC_ENDPOINT. SigV4 signs the Host header, so
// this address must be handed to the daemon at SIGNING time — it cannot
// be swapped into the URL afterwards.
func (a *App) resolveMinioAddress() string {
	if a.db == nil {
		return ""
	}
	row, ok := a.db.GetIntegration("minio")
	if !ok || !row.Enabled {
		return ""
	}
	if row.Remote && row.RemoteURL != "" {
		return row.RemoteURL
	}
	if !row.Remote && row.LocalURL != "" {
		return row.LocalURL
	}
	return ""
}

// GetMinioBrowserAddress exposes the resolved address for the Files
// panel's endpoint display ("" = daemon default).
func (a *App) GetMinioBrowserAddress() string {
	return a.resolveMinioAddress()
}

// MinioIntegrationEnabled reports whether the app's MinIO integration
// row is toggled on. Gates the Files menu item: the operator's toggle
// wins regardless of daemon-side health — off means hidden.
func (a *App) MinioIntegrationEnabled() bool {
	if a.db == nil {
		return false
	}
	row, ok := a.db.GetIntegration("minio")
	return ok && row.Enabled
}

// PresignArtifactURL mints a presigned GET (operator opens) or PUT
// (Phase 4 worker writes) URL, signed against this app's resolved
// MinIO address so the URL is fetchable from where the app runs.
func (a *App) PresignArtifactURL(bucketKey, key, op string, ttlSeconds int) (brainbox.ArtifactPresignedURL, error) {
	return a.client.PresignArtifactURL(bucketKey, key, op, ttlSeconds, a.resolveMinioAddress())
}

// UploadArtifactFile opens a native file picker and uploads the chosen
// file into destPrefix. Returns the new object key, or "" if the user
// cancelled. The daemon refuses (403) uploads into an immutable vault.
func (a *App) UploadArtifactFile(bucketKey, destPrefix string) (string, error) {
	picked, err := runtime.OpenFileDialog(a.ctx, runtime.OpenDialogOptions{
		Title: "Upload to " + bucketKey,
	})
	if err != nil {
		return "", err
	}
	if picked == "" {
		return "", nil // cancelled
	}
	data, err := os.ReadFile(picked)
	if err != nil {
		return "", err
	}
	name := filepath.Base(picked)
	key := path.Join(destPrefix, name)
	ct := mime.TypeByExtension(filepath.Ext(name))
	if err := a.client.PutArtifactObject(bucketKey, key, data, ct); err != nil {
		return "", err
	}
	return key, nil
}

// CreateArtifactFolder creates an empty folder placeholder at
// prefix/name/. The daemon refuses (403) folders inside an immutable vault.
func (a *App) CreateArtifactFolder(bucketKey, prefix, name string) error {
	name = strings.Trim(strings.TrimSpace(name), "/")
	if name == "" || strings.Contains(name, "/") {
		return fmt.Errorf("folder name is required and must contain no slashes")
	}
	key := path.Join(prefix, name) + "/"
	return a.client.PutArtifactObject(bucketKey, key, nil, "")
}

// RenameArtifactObject renames a key within its current folder via a
// server-side copy+delete. newName is a bare name (no slashes) — renames
// stay in place. The daemon refuses (403) renames of vault keys.
func (a *App) RenameArtifactObject(bucketKey, srcKey, newName string) error {
	newName = strings.TrimSpace(newName)
	if newName == "" || strings.Contains(newName, "/") {
		return fmt.Errorf("new name is required and must contain no slashes")
	}
	dst := path.Join(path.Dir(srcKey), newName)
	return a.client.CopyArtifactObject(bucketKey, srcKey, dst, true)
}

package main

import "phantom-ink/brainbox"

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

// ListArtifactsBuckets returns the two-bucket catalog (vault, artifacts).
func (a *App) ListArtifactsBuckets() ([]brainbox.ArtifactBucket, error) {
	return a.client.ListArtifactsBuckets()
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

// PresignArtifactURL mints a presigned GET (operator opens) or PUT
// (Phase 4 worker writes) URL.
func (a *App) PresignArtifactURL(bucketKey, key, op string, ttlSeconds int) (brainbox.ArtifactPresignedURL, error) {
	return a.client.PresignArtifactURL(bucketKey, key, op, ttlSeconds)
}

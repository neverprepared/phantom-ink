package main

// Wails-bound surface for per-profile credential bundles: source selection
// (catalog toggles + custom sources), capture/upload ("Sync now" and the
// image-rebuild hook), and bundle metadata. The daemon side is
// brainbox/src/brainbox/gateway_bundle.py.

import (
	"encoding/json"
	"fmt"

	"phantom-ink/brainbox"
	"phantom-ink/profileimage"
)

// BundleSourceView is one row in the Credential bundle editor.
type BundleSourceView struct {
	Name     string `json:"name"`
	Label    string `json:"label"`
	Kind     string `json:"kind"`     // catalog | custom
	Audience string `json:"audience"` // gateway | session | both
	Enabled  bool   `json:"enabled"`
	Detected bool   `json:"detected"`   // any of the source's files exist on this machine
	Definition string `json:"definition"` // custom sources: raw JSON def
}

// ListBundleSources merges the built-in catalog with the profile's stored
// toggle state and custom source definitions.
func (a *App) ListBundleSources(profileName string) ([]BundleSourceView, error) {
	prof, err := a.findProfile(profileName)
	if err != nil {
		return nil, err
	}
	stored := map[string]BundleSourceRow{}
	if a.db != nil {
		for _, r := range a.db.GetBundleSources(profileName) {
			stored[r.Name] = r
		}
	}

	var out []BundleSourceView
	for _, src := range profileimage.Catalog() {
		row := stored[src.Name]
		out = append(out, BundleSourceView{
			Name:     src.Name,
			Label:    src.Label,
			Kind:     "catalog",
			Audience: src.Audience,
			Enabled:  row.Enabled,
			Detected: sourceDetected(src, prof.WorkspaceHome),
		})
	}
	for _, r := range stored {
		if r.Kind != "custom" {
			continue
		}
		var def profileimage.CustomSourceDef
		_ = json.Unmarshal([]byte(r.Definition), &def)
		view := BundleSourceView{
			Name: r.Name, Label: r.Name, Kind: "custom",
			Audience: def.Audience, Enabled: r.Enabled, Definition: r.Definition,
		}
		if view.Audience == "" {
			view.Audience = profileimage.AudienceSession
		}
		if src, err := profileimage.CustomSource(r.Name, def); err == nil {
			view.Detected = sourceDetected(src, prof.WorkspaceHome)
		}
		out = append(out, view)
	}
	return out, nil
}

// SetBundleSourceEnabled flips one catalog source's toggle for a profile.
func (a *App) SetBundleSourceEnabled(profileName, source string, enabled bool) error {
	if a.db == nil {
		return fmt.Errorf("db unavailable")
	}
	if _, ok := profileimage.CatalogSource(source); !ok {
		// Custom rows keep their definition — flip enabled in place.
		for _, r := range a.db.GetBundleSources(profileName) {
			if r.Name == source && r.Kind == "custom" {
				r.Enabled = enabled
				return a.db.UpsertBundleSource(r)
			}
		}
		return fmt.Errorf("unknown bundle source %q", source)
	}
	return a.db.UpsertBundleSource(BundleSourceRow{
		Profile: profileName, Name: source, Kind: "catalog", Enabled: enabled,
	})
}

// SaveCustomBundleSource creates or updates a custom source definition.
// definitionJSON is {globs: [...], audience: "...", env_map: {...}}.
func (a *App) SaveCustomBundleSource(profileName, name, definitionJSON string) error {
	if a.db == nil {
		return fmt.Errorf("db unavailable")
	}
	var def profileimage.CustomSourceDef
	if err := json.Unmarshal([]byte(definitionJSON), &def); err != nil {
		return fmt.Errorf("invalid definition: %w", err)
	}
	if len(def.Globs) == 0 {
		return fmt.Errorf("definition needs at least one glob")
	}
	if _, err := profileimage.CustomSource(name, def); err != nil {
		return err
	}
	if _, ok := profileimage.CatalogSource(name); ok {
		return fmt.Errorf("%q is a built-in source", name)
	}
	return a.db.UpsertBundleSource(BundleSourceRow{
		Profile: profileName, Name: name, Kind: "custom",
		Enabled: true, Definition: definitionJSON,
	})
}

// DeleteBundleSource removes a source row (custom, or a catalog toggle).
func (a *App) DeleteBundleSource(profileName, name string) error {
	if a.db == nil {
		return fmt.Errorf("db unavailable")
	}
	return a.db.DeleteBundleSource(profileName, name)
}

// GetProfileBundleMeta returns the stored bundle's metadata, or nil.
func (a *App) GetProfileBundleMeta(profileName string) (*brainbox.BundleMeta, error) {
	return a.client.GetProfileBundleMeta(profileName)
}

// DeleteProfileBundle removes the profile's stored bundle from the daemon.
func (a *App) DeleteProfileBundle(profileName string) error {
	return a.client.DeleteProfileBundle(profileName)
}

// SyncProfileBundleNow captures the profile's enabled sources and uploads
// the bundle — the standalone action (no image rebuild required).
func (a *App) SyncProfileBundleNow(profileName string) (brainbox.BundlePutResult, error) {
	prof, err := a.findProfile(profileName)
	if err != nil {
		return brainbox.BundlePutResult{}, err
	}
	return a.syncProfileBundle(profileName, prof.WorkspaceHome, nil)
}

// syncProfileBundle is the shared capture+upload path used by both the
// Sync-now binding and the image-rebuild hook. Returns an error the caller
// decides how to treat (rebuild: warn-and-continue; Sync now: surface).
func (a *App) syncProfileBundle(profileName, workspaceHome string, progress func(string)) (brainbox.BundlePutResult, error) {
	report := func(msg string) {
		if progress != nil {
			progress(msg)
		}
	}
	if a.db == nil {
		return brainbox.BundlePutResult{}, fmt.Errorf("db unavailable")
	}
	var sources []profileimage.ResolvedSource
	customEnv := map[string]map[string]string{}
	for _, r := range a.db.GetBundleSources(profileName) {
		if !r.Enabled {
			continue
		}
		if r.Kind == "custom" {
			var def profileimage.CustomSourceDef
			if err := json.Unmarshal([]byte(r.Definition), &def); err != nil {
				report(fmt.Sprintf("bundle: skipping custom source %s (bad definition)", r.Name))
				continue
			}
			src, err := profileimage.CustomSource(r.Name, def)
			if err != nil {
				report(fmt.Sprintf("bundle: skipping custom source %s: %v", r.Name, err))
				continue
			}
			sources = append(sources, profileimage.ResolvedSource{Source: src, Kind: "custom"})
			if len(def.EnvMap) > 0 {
				customEnv[r.Name] = def.EnvMap
			}
			continue
		}
		if src, ok := profileimage.CatalogSource(r.Name); ok {
			sources = append(sources, profileimage.ResolvedSource{Source: src, Kind: "catalog"})
		}
	}
	if len(sources) == 0 {
		return brainbox.BundlePutResult{}, fmt.Errorf("no bundle sources enabled for %s", profileName)
	}

	res, err := profileimage.CollectBundle(profileName, workspaceHome, sources, customEnv, appVersion())
	if err != nil {
		return brainbox.BundlePutResult{}, fmt.Errorf("collect bundle: %w", err)
	}
	for _, w := range res.Warnings {
		report("bundle: " + w)
	}
	if len(res.Manifest.Entries) == 0 {
		return brainbox.BundlePutResult{}, fmt.Errorf("no credential files found for the enabled sources")
	}
	out, err := a.client.PutProfileBundle(profileName, res.TarGz, appVersion())
	if err != nil {
		return brainbox.BundlePutResult{}, fmt.Errorf("upload bundle: %w", err)
	}
	return out, nil
}

// sourceDetected reports whether any of a source's files exist right now
// on this machine (drives the "detected" dot in the editor).
func sourceDetected(src profileimage.BundleSource, workspaceHome string) bool {
	res, err := profileimage.CollectBundle(
		"detect", workspaceHome,
		[]profileimage.ResolvedSource{{Source: src, Kind: "catalog"}},
		nil, "",
	)
	return err == nil && len(res.Manifest.Entries) > 0
}

// appVersion returns the app's version string for bundle provenance.
func appVersion() string {
	return "phantom-ink-app"
}

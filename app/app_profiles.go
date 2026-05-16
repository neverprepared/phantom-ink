package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/wailsapp/wails/v2/pkg/runtime"
)

// ---------------------------------------------------------------------------
// Profile helpers
// ---------------------------------------------------------------------------

// findProfile scans and returns a single profile by name.
func (a *App) findProfile(name string) (*Profile, error) {
	a.mu.RLock()
	root := a.config.WorkspacesRoot
	a.mu.RUnlock()
	profiles, err := scanProfiles(root)
	if err != nil {
		return nil, err
	}
	for i := range profiles {
		if profiles[i].Name == name {
			return &profiles[i], nil
		}
	}
	return nil, fmt.Errorf("profile %q not found", name)
}

// activeProfileName returns the currently-selected profile name without
// touching disk. Empty when no profile is set.
func (a *App) activeProfileName() string {
	a.mu.RLock()
	defer a.mu.RUnlock()
	if a.config == nil {
		return ""
	}
	return a.config.ActiveProfile
}

// BrowseProfileFiles opens a multi-select file picker rooted at the named
// profile's workspace_home. Returns paths relative to that workspace_home so
// chains stay profile-portable. Selections outside the profile root are
// rejected with an error.
//
// Empty profileName uses the currently-active profile.
func (a *App) BrowseProfileFiles(profileName string) ([]string, error) {
	if strings.TrimSpace(profileName) == "" {
		profileName = a.activeProfileName()
	}
	if profileName == "" {
		return nil, fmt.Errorf("no profile selected")
	}
	prof, err := a.findProfile(profileName)
	if err != nil {
		return nil, err
	}
	home, err := filepath.Abs(prof.WorkspaceHome)
	if err != nil {
		return nil, fmt.Errorf("resolve profile home: %w", err)
	}
	if a.ctx == nil {
		return nil, fmt.Errorf("no UI context — picker unavailable")
	}
	picked, err := runtime.OpenMultipleFilesDialog(a.ctx, runtime.OpenDialogOptions{
		Title:            fmt.Sprintf("Select files in %s", profileName),
		DefaultDirectory: home,
	})
	if err != nil {
		return nil, err
	}
	if len(picked) == 0 {
		return []string{}, nil // user cancelled
	}
	rels := make([]string, 0, len(picked))
	for _, abs := range picked {
		rel, err := filepath.Rel(home, abs)
		if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
			return nil, fmt.Errorf("file %q is outside profile %q workspace_home", abs, profileName)
		}
		rels = append(rels, rel)
	}
	return rels, nil
}

// resolveCwd produces the absolute working directory for a chain step given
// the owning profile and a user-supplied cwd. Rules:
//
//   - profile is required for chain execution (foundational)
//   - empty cwd  → profile.WorkspaceHome
//   - relative   → filepath.Join(WorkspaceHome, cwd), must stay inside
//   - absolute   → must have WorkspaceHome as prefix
//
// Anything that would escape the profile root is a hard error — this is
// how "isolated to the active profile" is enforced at execution time.
func (a *App) resolveCwd(profileName, rawCwd string) (string, error) {
	if profileName == "" {
		return "", fmt.Errorf("no profile in context — chain steps must run under a profile")
	}
	prof, err := a.findProfile(profileName)
	if err != nil {
		return "", err
	}
	home, err := filepath.Abs(prof.WorkspaceHome)
	if err != nil {
		return "", fmt.Errorf("resolve profile home: %w", err)
	}
	clean := strings.TrimSpace(rawCwd)
	if clean == "" {
		return home, nil
	}
	var candidate string
	if filepath.IsAbs(clean) {
		candidate = filepath.Clean(clean)
	} else {
		candidate = filepath.Clean(filepath.Join(home, clean))
	}
	// pathInside reports whether candidate is at or under home (no traversal).
	rel, err := filepath.Rel(home, candidate)
	if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return "", fmt.Errorf("cwd %q escapes profile %q workspace_home", rawCwd, profileName)
	}
	return candidate, nil
}

// ---------------------------------------------------------------------------
// Profiles
// ---------------------------------------------------------------------------

// ScanProfiles scans the configured workspaces root for shell-profiler profiles.
func (a *App) ScanProfiles() ([]Profile, error) {
	a.mu.RLock()
	root := a.config.WorkspacesRoot
	a.mu.RUnlock()
	return scanProfiles(root)
}

// GetActiveProfile returns the currently selected profile (empty if none set).
func (a *App) GetActiveProfile() Profile {
	a.mu.RLock()
	active := a.config.ActiveProfile
	a.mu.RUnlock()
	if active == "" {
		return Profile{}
	}
	p, err := a.findProfile(active)
	if err != nil {
		return Profile{}
	}
	return *p
}

// SetActiveProfile saves the active profile selection to config.
func (a *App) SetActiveProfile(name string) error {
	a.mu.Lock()
	a.config.ActiveProfile = name
	a.mu.Unlock()
	if a.db != nil {
		return a.db.SetSetting(settingActiveProfile, name)
	}
	return nil
}

// CreateProfile creates a new workspace profile directory under the workspaces root.
func (a *App) CreateProfile(name string) (Profile, error) {
	a.mu.RLock()
	root := a.config.WorkspacesRoot
	a.mu.RUnlock()
	p, err := createProfile(root, name)
	if err != nil {
		return Profile{}, err
	}
	a.mu.Lock()
	a.config.ActiveProfile = p.Name
	a.mu.Unlock()
	if a.db != nil {
		if err := a.db.SetSetting(settingActiveProfile, p.Name); err != nil {
			fmt.Fprintf(os.Stderr, "warning: failed to save setting %q: %v\n", settingActiveProfile, err)
		}
	}
	return p, nil
}

// DeleteProfile deletes a profile, optionally backing it up first.
func (a *App) DeleteProfile(name string, backup bool) error {
	a.mu.RLock()
	root := a.config.WorkspacesRoot
	a.mu.RUnlock()
	if err := deleteProfile(root, name, backup); err != nil {
		return err
	}
	a.mu.Lock()
	clearActive := a.config.ActiveProfile == name
	if clearActive {
		a.config.ActiveProfile = ""
	}
	a.mu.Unlock()
	if clearActive && a.db != nil {
		if err := a.db.SetSetting(settingActiveProfile, ""); err != nil {
			fmt.Fprintf(os.Stderr, "warning: failed to save setting %q: %v\n", settingActiveProfile, err)
		}
	}
	return nil
}

// RestoreProfile restores a profile from backup.
func (a *App) RestoreProfile(name string) error {
	a.mu.RLock()
	root := a.config.WorkspacesRoot
	a.mu.RUnlock()
	return restoreProfile(root, name)
}

// PurgeBackup permanently deletes a profile backup.
func (a *App) PurgeBackup(name string) error {
	a.mu.RLock()
	root := a.config.WorkspacesRoot
	a.mu.RUnlock()
	return purgeBackup(root, name)
}

// ListBackups returns names of profiles that have backups.
func (a *App) ListBackups() ([]string, error) {
	a.mu.RLock()
	root := a.config.WorkspacesRoot
	a.mu.RUnlock()
	return listBackups(root)
}

// ---------------------------------------------------------------------------
// Profile colors
// ---------------------------------------------------------------------------

// GetProfileColors returns all profile color overrides as a map of name → palette index.
func (a *App) GetProfileColors() (map[string]string, error) {
	if a.db == nil {
		return map[string]string{}, nil
	}
	return a.db.GetSettingsWithPrefix(settingProfileColorPrefix)
}

// SetProfileColor saves (or clears) a color override for a profile.
func (a *App) SetProfileColor(name, colorIndex string) error {
	if a.db == nil {
		return nil
	}
	return a.db.SetSetting(settingProfileColorPrefix+name, colorIndex)
}

// ---------------------------------------------------------------------------
// Secrets
// ---------------------------------------------------------------------------

// SecretKeyStatus describes a secret key's presence in a profile.
type SecretKeyStatus struct {
	Key      string `json:"key"`
	HasValue bool   `json:"has_value"`
	Source   string `json:"source"` // "1password", "plaintext", or "missing"
}

// wellKnownSecrets are the secret keys integrations typically need.
var wellKnownSecrets = []string{
	"LANGFUSE_API_PUBLIC_KEY",
	"LANGFUSE_API_SECRET_KEY",
	"QDRANT_API_KEY",
	"OPENAI_API_KEY",
}

// GetProfileSecrets returns the status of well-known secret keys for a profile.
func (a *App) GetProfileSecrets(profileName string) ([]SecretKeyStatus, error) {
	profile, err := a.findProfile(profileName)
	if err != nil {
		return nil, err
	}

	secrets := make(map[string]bool)
	secretsPath := filepath.Join(profile.WorkspaceHome, ".env.secrets")
	if data, err := os.ReadFile(secretsPath); err == nil {
		for _, line := range strings.Split(string(data), "\n") {
			line = strings.TrimSpace(line)
			if line == "" || strings.HasPrefix(line, "#") {
				continue
			}
			key, _, _ := strings.Cut(line, "=")
			key = strings.TrimSpace(key)
			if strings.HasPrefix(key, "export ") {
				key = strings.TrimSpace(key[7:])
			}
			if key != "" {
				secrets[key] = true
			}
		}
	}

	var result []SecretKeyStatus
	for _, key := range wellKnownSecrets {
		status := SecretKeyStatus{Key: key, Source: "missing"}
		if secrets[key] {
			status.HasValue = true
			status.Source = profile.SecretsMode
		}
		result = append(result, status)
	}
	return result, nil
}

// ExportSecretsTemplate generates a .env.secrets template for importing into 1Password.
func (a *App) ExportSecretsTemplate(profileName string) (string, error) {
	var lines []string
	lines = append(lines, fmt.Sprintf("# Secrets for profile: %s", profileName))
	lines = append(lines, "# Import this into a 1Password Environment")
	lines = append(lines, "")
	for _, key := range wellKnownSecrets {
		lines = append(lines, fmt.Sprintf("%s=", key))
	}
	return strings.Join(lines, "\n"), nil
}

// ---------------------------------------------------------------------------
// Filesystem helpers
// ---------------------------------------------------------------------------

// ListProfileDirs returns the top-level directories under a profile's workspace home.
func (a *App) ListProfileDirs(profileName string) ([]string, error) {
	profile, err := a.findProfile(profileName)
	if err != nil {
		return nil, err
	}
	entries, err := os.ReadDir(profile.WorkspaceHome)
	if err != nil {
		return nil, fmt.Errorf("read workspace home: %w", err)
	}
	var dirs []string
	for _, e := range entries {
		if e.IsDir() && e.Name()[0] != '.' {
			dirs = append(dirs, e.Name())
		}
	}
	return dirs, nil
}

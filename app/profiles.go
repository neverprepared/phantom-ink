package main

import (
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"syscall"

	_ "modernc.org/sqlite"
)

// Profile represents a workspace profile discovered on disk.
type Profile struct {
	Name          string `json:"name"`
	Path          string `json:"path"`
	WorkspaceHome string `json:"workspace_home"`
	HasSecrets    bool   `json:"has_secrets"`
	SecretsMode   string `json:"secrets_mode"`   // "1password", "plaintext", or "none"
	SecretsPath   string `json:"secrets_path"`
	HasBackup     bool   `json:"has_backup"`
}

// defaultWorkspacesRoot returns the profiles root from the phantom-ink database,
// falling back to ~/workspaces/profiles.
func defaultWorkspacesRoot() string {
	home := os.Getenv("HOME")

	db, err := sql.Open("sqlite", dbPath+"?mode=ro")
	if err == nil {
		defer db.Close()
		var val string
		if err := db.QueryRow("SELECT value FROM settings WHERE key = 'workspaces_root'").Scan(&val); err == nil && val != "" {
			if strings.HasPrefix(val, "~/") {
				val = filepath.Join(home, val[2:])
			}
			return val
		}
	}

	return filepath.Join(home, "workspaces", "profiles")
}

// scanProfiles scans root for directories containing an .envrc (shell-profiler
// convention) and returns a Profile for each one found.
func scanProfiles(root string) ([]Profile, error) {
	root = filepath.Clean(root)
	entries, err := os.ReadDir(root)
	if err != nil {
		return nil, fmt.Errorf("scan profiles: %w", err)
	}

	var profiles []Profile
	for _, e := range entries {
		if !e.IsDir() || strings.HasPrefix(e.Name(), ".") {
			continue
		}
		dirPath := filepath.Join(root, e.Name())
		if _, err := os.Stat(filepath.Join(dirPath, ".envrc")); err != nil {
			continue // not a shell-profiler workspace
		}
		secretsPath := filepath.Join(dirPath, ".env.secrets")
		mode, hasSecrets := detectSecretsMode(secretsPath)
		_, backupErr := os.Stat(filepath.Join(root, ".backups", e.Name()))
		profiles = append(profiles, Profile{
			Name:          e.Name(),
			Path:          dirPath,
			WorkspaceHome: dirPath,
			HasSecrets:    hasSecrets,
			SecretsMode:   mode,
			SecretsPath:   secretsPath,
			HasBackup:     backupErr == nil,
		})
	}
	return profiles, nil
}

// detectSecretsMode checks .env.secrets and returns ("1password"|"plaintext"|"none", hasContent).
// A FIFO (named pipe) indicates 1Password is mounting the file.
func detectSecretsMode(path string) (string, bool) {
	info, err := os.Lstat(path) // Lstat to detect FIFO without reading through it
	if err != nil {
		return "none", false
	}
	// Check if it's a FIFO (named pipe) — 1Password mounts these
	if info.Mode()&os.ModeNamedPipe != 0 {
		return "1password", true
	}
	// Check if it's a symlink to a FIFO (some 1Password versions)
	if info.Mode()&os.ModeSymlink != 0 {
		target, err := os.Stat(path) // follows symlink
		if err == nil && target.Mode()&os.ModeNamedPipe != 0 {
			return "1password", true
		}
	}
	// Regular file — check if it's a socket (another 1Password mechanism)
	if info.Mode()&os.ModeSocket != 0 {
		return "1password", true
	}
	// Check for 1Password's special file type via syscall
	if stat, ok := info.Sys().(*syscall.Stat_t); ok {
		// FIFO has S_IFIFO bit
		if stat.Mode&syscall.S_IFIFO != 0 {
			return "1password", true
		}
	}
	// Regular file — check if it has non-comment content
	data, err := os.ReadFile(path)
	if err != nil {
		return "none", false
	}
	for _, line := range strings.Split(string(data), "\n") {
		line = strings.TrimSpace(line)
		if line != "" && !strings.HasPrefix(line, "#") {
			return "plaintext", true
		}
	}
	return "none", false
}

// createProfile creates a new shell-profiler workspace directory under root with
// a minimal .envrc and empty .env so direnv can activate it immediately.
func createProfile(root, name string) (Profile, error) {
	name = strings.ToLower(strings.ReplaceAll(strings.TrimSpace(name), " ", "-"))
	if name == "" {
		return Profile{}, fmt.Errorf("profile name cannot be empty")
	}

	dirPath := filepath.Join(root, name)
	if err := os.MkdirAll(dirPath, 0755); err != nil {
		return Profile{}, fmt.Errorf("create profile dir: %w", err)
	}

	envrc := fmt.Sprintf(`export WORKSPACE_PROFILE=%s
export WORKSPACE_HOME=$PWD
dotenv_if_exists .env
`, name)
	if err := os.WriteFile(filepath.Join(dirPath, ".envrc"), []byte(envrc), 0644); err != nil {
		return Profile{}, fmt.Errorf("write .envrc: %w", err)
	}

	// Empty .env so dotenv_if_exists has something to source.
	if err := os.WriteFile(filepath.Join(dirPath, ".env"), []byte(""), 0600); err != nil {
		return Profile{}, fmt.Errorf("write .env: %w", err)
	}

	secretsPath := filepath.Join(dirPath, ".env.secrets")
	return Profile{
		Name:          name,
		Path:          dirPath,
		WorkspaceHome: dirPath,
		HasSecrets:    false,
		SecretsMode:   "none",
		SecretsPath:   secretsPath,
	}, nil
}

// backupDir returns the path to the backups directory under the workspaces root.
func backupDir(root string) string {
	return filepath.Join(root, ".backups")
}

// deleteProfile moves a profile to .backups/{name} (with backup) or removes it
// entirely (without backup). Returns an error if the profile doesn't exist.
func deleteProfile(root, name string, backup bool) error {
	profilePath := filepath.Join(root, name)
	if _, err := os.Stat(profilePath); err != nil {
		return fmt.Errorf("profile %q not found", name)
	}

	if backup {
		bDir := backupDir(root)
		dest := filepath.Join(bDir, name)
		// Remove any existing backup for this name first.
		_ = os.RemoveAll(dest)
		if err := os.MkdirAll(bDir, 0755); err != nil {
			return fmt.Errorf("create backup dir: %w", err)
		}
		if err := os.Rename(profilePath, dest); err != nil {
			return fmt.Errorf("backup profile: %w", err)
		}
		return nil
	}

	return os.RemoveAll(profilePath)
}

// restoreProfile moves a profile from .backups/{name} back to {root}/{name}.
func restoreProfile(root, name string) error {
	src := filepath.Join(backupDir(root), name)
	if _, err := os.Stat(src); err != nil {
		return fmt.Errorf("backup for %q not found", name)
	}
	dest := filepath.Join(root, name)
	if _, err := os.Stat(dest); err == nil {
		return fmt.Errorf("profile %q already exists — delete it first", name)
	}
	return os.Rename(src, dest)
}

// purgeBackup permanently deletes a profile backup from .backups/{name}.
func purgeBackup(root, name string) error {
	path := filepath.Join(backupDir(root), name)
	if _, err := os.Stat(path); err != nil {
		return fmt.Errorf("backup for %q not found", name)
	}
	return os.RemoveAll(path)
}

// listBackups returns names of profiles that have backups.
func listBackups(root string) ([]string, error) {
	bDir := backupDir(root)
	entries, err := os.ReadDir(bDir)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}
	var names []string
	for _, e := range entries {
		if e.IsDir() && !strings.HasPrefix(e.Name(), ".") {
			names = append(names, e.Name())
		}
	}
	return names, nil
}

package main

import (
	"fmt"
	"os"
	"strings"
)

// Setting key constants — single source of truth for database key names.
const (
	settingBaseURL            = "base_url"
	settingAPIKey             = "api_key"
	settingActiveProfile      = "active_profile"
	settingWorkspacesRoot     = "workspaces_root"
	settingTheme              = "theme"
	settingProfileColorPrefix = "profile_color:"
	settingRegistryURL        = "registry_url"
	settingRegistryUsername   = "registry_username"
	settingRegistryPassword   = "registry_password"
	settingOTLPHost           = "otlp_host"
	settingLocalRunnerEnabled = "local_runner_enabled"
	settingLocalRunnerName    = "local_runner_name"
	settingLocalRunnerWorkDir = "local_runner_work_dir"
	settingLocalRunnerMachineID = "local_runner_machine_id"
)

// Config is the in-memory representation of app settings.
// All persistence is handled by the SQLite database.
type Config struct {
	BaseURL        string `json:"base_url"`
	APIKey         string `json:"api_key"`
	ActiveProfile  string `json:"active_profile"`
	WorkspacesRoot string `json:"workspaces_root"`
	Theme          string `json:"theme"`
}

// loadConfigFromDB populates a Config from the database with sensible defaults.
func loadConfigFromDB(db *DB) *Config {
	cfg := &Config{
		BaseURL:        "http://127.0.0.1:9999",
		Theme:          "dark",
		WorkspacesRoot: defaultWorkspacesRoot(),
	}
	if db == nil {
		return cfg
	}
	if v := db.GetSetting(settingBaseURL, ""); v != "" {
		cfg.BaseURL = strings.TrimSpace(v)
	}
	if v := db.GetSetting(settingAPIKey, ""); v != "" {
		// Defensive trim: an earlier version stored values verbatim, so
		// pasted-with-trailing-newline keys made it into SQLite and silently
		// broke X-API-Key auth. Stripping here covers existing bad rows.
		cfg.APIKey = strings.TrimSpace(v)
	}
	if v := db.GetSetting(settingActiveProfile, ""); v != "" {
		cfg.ActiveProfile = v
	}
	if v := db.GetSetting(settingWorkspacesRoot, ""); v != "" {
		cfg.WorkspacesRoot = v
	}
	if v := db.GetSetting(settingTheme, ""); v != "" {
		cfg.Theme = v
	}
	// Fall back to file-based API key if not in DB
	if cfg.APIKey == "" {
		if key := readDeveloperAPIKey(); key != "" {
			cfg.APIKey = key
			// Persist to DB so we don't need the file next time
			if err := db.SetSetting(settingAPIKey, key); err != nil {
				fmt.Fprintf(os.Stderr, "warning: failed to persist api_key to database: %v\n", err)
			}
		}
	}
	return cfg
}

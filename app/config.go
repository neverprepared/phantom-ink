package main

// Config is the in-memory representation of app settings.
// All persistence is handled by the SQLite database.
type Config struct {
	BaseURL        string
	APIKey         string
	ActiveProfile  string
	WorkspacesRoot string
	Theme          string
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
	if v := db.GetSetting("base_url", ""); v != "" {
		cfg.BaseURL = v
	}
	if v := db.GetSetting("api_key", ""); v != "" {
		cfg.APIKey = v
	}
	if v := db.GetSetting("active_profile", ""); v != "" {
		cfg.ActiveProfile = v
	}
	if v := db.GetSetting("workspaces_root", ""); v != "" {
		cfg.WorkspacesRoot = v
	}
	if v := db.GetSetting("theme", ""); v != "" {
		cfg.Theme = v
	}
	// Fall back to file-based API key if not in DB
	if cfg.APIKey == "" {
		if key := readDeveloperAPIKey(); key != "" {
			cfg.APIKey = key
			// Persist to DB so we don't need the file next time
			_ = db.SetSetting("api_key", key)
		}
	}
	return cfg
}

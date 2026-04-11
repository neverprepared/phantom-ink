package config

import (
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	_ "modernc.org/sqlite"
)

// Config holds the profile manager configuration
type Config struct {
	ProfilesDir string `json:"profiles_dir"`
}

// dbPath returns the path to the phantom-ink SQLite database.
func dbPath() string {
	home, _ := os.UserHomeDir()
	return filepath.Join(home, ".config", "phantom-ink", "phantom-ink.db")
}

// LoadConfig loads configuration from the phantom-ink SQLite database,
// falling back to default if the DB doesn't exist or has no value.
func LoadConfig() (*Config, error) {
	if cfg, err := loadFromDB(); err == nil && cfg.ProfilesDir != "" {
		return cfg, nil
	}
	return GetDefaultConfig()
}

// loadFromDB reads workspaces_root from the phantom-ink database.
func loadFromDB() (*Config, error) {
	path := dbPath()
	if _, err := os.Stat(path); err != nil {
		return nil, err
	}

	db, err := sql.Open("sqlite", path+"?mode=ro")
	if err != nil {
		return nil, err
	}
	defer db.Close()

	var value string
	err = db.QueryRow("SELECT value FROM settings WHERE key = 'workspaces_root'").Scan(&value)
	if err != nil {
		return nil, err
	}

	return &Config{ProfilesDir: expandPath(value)}, nil
}

// SaveConfig saves the configuration to the phantom-ink database.
func SaveConfig(config *Config) error {
	path := dbPath()
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return err
	}

	db, err := sql.Open("sqlite", path)
	if err != nil {
		return err
	}
	defer db.Close()

	_, err = db.Exec(`
		CREATE TABLE IF NOT EXISTS settings (
			key   TEXT PRIMARY KEY,
			value TEXT NOT NULL DEFAULT ''
		)`)
	if err != nil {
		return err
	}

	_, err = db.Exec(
		"INSERT INTO settings (key, value) VALUES ('workspaces_root', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
		config.ProfilesDir)
	return err
}

// GetConfigPath returns the path to the legacy config file (for reference only).
func GetConfigPath() (string, error) {
	homeDir, err := os.UserHomeDir()
	if err != nil {
		return "", fmt.Errorf("failed to get home directory: %w", err)
	}
	return filepath.Join(homeDir, ".profile-manager"), nil
}

// GetDefaultConfig returns the default configuration
func GetDefaultConfig() (*Config, error) {
	homeDir, err := os.UserHomeDir()
	if err != nil {
		return nil, fmt.Errorf("failed to get home directory: %w", err)
	}

	return &Config{
		ProfilesDir: filepath.Join(homeDir, "workspaces", "profiles"),
	}, nil
}

// expandPath expands ~ and environment variables in a path
func expandPath(path string) string {
	if strings.HasPrefix(path, "~") {
		homeDir, err := os.UserHomeDir()
		if err == nil {
			path = filepath.Join(homeDir, path[1:])
		}
	}

	path = os.ExpandEnv(path)

	return filepath.Clean(path)
}

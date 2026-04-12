package config

import (
	"os"
	"path/filepath"
	"testing"
)

func TestExpandPath_Tilde(t *testing.T) {
	home, err := os.UserHomeDir()
	if err != nil {
		t.Fatalf("failed to get home dir: %v", err)
	}

	got := expandPath("~/foo")
	want := filepath.Join(home, "foo")
	if got != want {
		t.Errorf("expandPath(~/foo) = %q, want %q", got, want)
	}
}

func TestExpandPath_TildeOnly(t *testing.T) {
	home, err := os.UserHomeDir()
	if err != nil {
		t.Fatalf("failed to get home dir: %v", err)
	}

	got := expandPath("~")
	if got != home {
		t.Errorf("expandPath(~) = %q, want %q", got, home)
	}
}

func TestExpandPath_EnvVar(t *testing.T) {
	tmpDir := t.TempDir()
	t.Setenv("TEST_EXPAND_DIR", tmpDir)

	got := expandPath("$TEST_EXPAND_DIR/foo")
	want := filepath.Join(tmpDir, "foo")
	if got != want {
		t.Errorf("expandPath($TEST_EXPAND_DIR/foo) = %q, want %q", got, want)
	}
}

func TestExpandPath_AbsoluteUnchanged(t *testing.T) {
	got := expandPath("/usr/local")
	if got != "/usr/local" {
		t.Errorf("expandPath(/usr/local) = %q, want /usr/local", got)
	}
}

func TestExpandPath_CleanPath(t *testing.T) {
	got := expandPath("./foo/../bar")
	if got != "bar" {
		t.Errorf("expandPath(./foo/../bar) = %q, want bar", got)
	}
}

func TestGetDefaultConfig(t *testing.T) {
	tmpDir := t.TempDir()
	t.Setenv("HOME", tmpDir)

	cfg, err := GetDefaultConfig()
	if err != nil {
		t.Fatalf("GetDefaultConfig() error: %v", err)
	}

	want := filepath.Join(tmpDir, "workspaces", "profiles")
	if cfg.ProfilesDir != want {
		t.Errorf("ProfilesDir = %q, want %q", cfg.ProfilesDir, want)
	}
}

func TestGetConfigPath(t *testing.T) {
	tmpDir := t.TempDir()
	t.Setenv("HOME", tmpDir)

	got, err := GetConfigPath()
	if err != nil {
		t.Fatalf("GetConfigPath() error: %v", err)
	}

	want := filepath.Join(tmpDir, ".profile-manager")
	if got != want {
		t.Errorf("GetConfigPath() = %q, want %q", got, want)
	}
}

func TestLoadConfig_MissingFile(t *testing.T) {
	tmpDir := t.TempDir()
	t.Setenv("HOME", tmpDir)

	cfg, err := LoadConfig()
	if err != nil {
		t.Fatalf("LoadConfig() error: %v", err)
	}

	want := filepath.Join(tmpDir, "workspaces", "profiles")
	if cfg.ProfilesDir != want {
		t.Errorf("ProfilesDir = %q, want %q (expected default)", cfg.ProfilesDir, want)
	}
}

func TestLoadConfig_ValidKeyValue(t *testing.T) {
	tmpDir := t.TempDir()
	t.Setenv("HOME", tmpDir)

	// Save via SQLite and load back (round-trip test)
	if err := SaveConfig(&Config{ProfilesDir: "/custom/profiles"}); err != nil {
		t.Fatalf("SaveConfig() error: %v", err)
	}

	cfg, err := LoadConfig()
	if err != nil {
		t.Fatalf("LoadConfig() error: %v", err)
	}

	if cfg.ProfilesDir != "/custom/profiles" {
		t.Errorf("ProfilesDir = %q, want /custom/profiles", cfg.ProfilesDir)
	}
}

func TestLoadConfig_RoundTrip(t *testing.T) {
	tmpDir := t.TempDir()
	t.Setenv("HOME", tmpDir)

	// Save a value and verify it round-trips through LoadConfig
	if err := SaveConfig(&Config{ProfilesDir: "/my/profiles"}); err != nil {
		t.Fatalf("SaveConfig() error: %v", err)
	}

	cfg, err := LoadConfig()
	if err != nil {
		t.Fatalf("LoadConfig() error: %v", err)
	}

	if cfg.ProfilesDir != "/my/profiles" {
		t.Errorf("ProfilesDir = %q, want /my/profiles", cfg.ProfilesDir)
	}
}

func TestLoadConfig_TildeExpansion(t *testing.T) {
	tmpDir := t.TempDir()
	t.Setenv("HOME", tmpDir)

	// Save a tilde-prefixed path and verify it is expanded on load
	if err := SaveConfig(&Config{ProfilesDir: "~/my-profiles"}); err != nil {
		t.Fatalf("SaveConfig() error: %v", err)
	}

	cfg, err := LoadConfig()
	if err != nil {
		t.Fatalf("LoadConfig() error: %v", err)
	}

	want := filepath.Join(tmpDir, "my-profiles")
	if cfg.ProfilesDir != want {
		t.Errorf("ProfilesDir = %q, want %q", cfg.ProfilesDir, want)
	}
}

func TestLoadConfig_EmptyDB_ReturnsDefault(t *testing.T) {
	tmpDir := t.TempDir()
	t.Setenv("HOME", tmpDir)

	// With no DB present, LoadConfig should return the default profiles dir
	cfg, err := LoadConfig()
	if err != nil {
		t.Fatalf("LoadConfig() error: %v", err)
	}

	want := filepath.Join(tmpDir, "workspaces", "profiles")
	if cfg.ProfilesDir != want {
		t.Errorf("ProfilesDir = %q, want %q (expected default)", cfg.ProfilesDir, want)
	}
}

func TestLoadConfig_Overwrite(t *testing.T) {
	tmpDir := t.TempDir()
	t.Setenv("HOME", tmpDir)

	// Save once then overwrite; the last value should win
	if err := SaveConfig(&Config{ProfilesDir: "/first/path"}); err != nil {
		t.Fatalf("SaveConfig() first write error: %v", err)
	}
	if err := SaveConfig(&Config{ProfilesDir: "/good/path"}); err != nil {
		t.Fatalf("SaveConfig() second write error: %v", err)
	}

	cfg, err := LoadConfig()
	if err != nil {
		t.Fatalf("LoadConfig() error: %v", err)
	}

	if cfg.ProfilesDir != "/good/path" {
		t.Errorf("ProfilesDir = %q, want /good/path", cfg.ProfilesDir)
	}
}

func TestSaveConfig_WritesCorrectValue(t *testing.T) {
	tmpDir := t.TempDir()
	t.Setenv("HOME", tmpDir)

	if err := SaveConfig(&Config{ProfilesDir: "/custom/profiles"}); err != nil {
		t.Fatalf("SaveConfig() error: %v", err)
	}

	cfg, err := LoadConfig()
	if err != nil {
		t.Fatalf("LoadConfig() error: %v", err)
	}

	if cfg.ProfilesDir != "/custom/profiles" {
		t.Errorf("ProfilesDir = %q, want /custom/profiles", cfg.ProfilesDir)
	}
}

func TestSaveConfig_CreatesDBDirectory(t *testing.T) {
	tmpDir := t.TempDir()
	t.Setenv("HOME", tmpDir)

	if err := SaveConfig(&Config{ProfilesDir: "/opt/profiles"}); err != nil {
		t.Fatalf("SaveConfig() error: %v", err)
	}

	dbFile := filepath.Join(tmpDir, ".config", "phantom-ink", "phantom-ink.db")
	if _, err := os.Stat(dbFile); err != nil {
		t.Errorf("expected DB file at %s, got: %v", dbFile, err)
	}
}

func TestSaveConfig_AbsolutePathRoundTrip(t *testing.T) {
	tmpDir := t.TempDir()
	t.Setenv("HOME", tmpDir)

	if err := SaveConfig(&Config{ProfilesDir: "/opt/profiles"}); err != nil {
		t.Fatalf("SaveConfig() error: %v", err)
	}

	cfg, err := LoadConfig()
	if err != nil {
		t.Fatalf("LoadConfig() error: %v", err)
	}

	if cfg.ProfilesDir != "/opt/profiles" {
		t.Errorf("ProfilesDir = %q, want /opt/profiles", cfg.ProfilesDir)
	}
}

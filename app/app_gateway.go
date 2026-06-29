package main

import (
	"os"
	"strings"

	"github.com/wailsapp/wails/v2/pkg/runtime"

	"phantom-ink/brainbox"
)

// Wails-bound surface for the MCP gateway operator routes (ADR-002 phase 3):
// per-profile encrypted env editing + Tier-0 token minting. All delegate to
// brainbox.Client. Errors pass back to the frontend untouched except the
// "no env stored yet" 404, which we normalize to an empty map so the editor
// opens cleanly for a profile that has never had secrets.

// GatewayInfo reports which profiles have stored env + whether the gateway is
// unlocked (operator key configured). The Profiles panel uses `unlocked` to
// decide whether to show the editor or a "set CL_GATEWAY__SECRET_KEY" hint.
func (a *App) GatewayInfo() (brainbox.GatewayProfilesInfo, error) {
	return a.client.ListGatewayProfiles()
}

// GetGatewayEnv returns the decrypted env map for a profile. A profile with
// no stored env yet (404) returns an empty map, not an error.
func (a *App) GetGatewayEnv(profile string) (map[string]string, error) {
	env, err := a.client.GetGatewayProfileEnv(profile)
	if err != nil {
		if strings.Contains(err.Error(), "HTTP 404") {
			return map[string]string{}, nil
		}
		return nil, err
	}
	return env, nil
}

// SetGatewayEnv replaces a profile's stored env (full overwrite).
func (a *App) SetGatewayEnv(profile string, env map[string]string) error {
	if env == nil {
		env = map[string]string{}
	}
	return a.client.SetGatewayProfileEnv(profile, env)
}

// DeleteGatewayEnv removes a profile's stored env file entirely.
func (a *App) DeleteGatewayEnv(profile string) error {
	return a.client.DeleteGatewayProfileEnv(profile)
}

// MintGatewayToken mints a Tier-0 token for a profile. `scope` is a list of
// allowed `<server>__<tool>` patterns (empty = all tools). Returned once.
func (a *App) MintGatewayToken(profile string, scope []string, ttlSeconds int) (brainbox.GatewayToken, error) {
	if ttlSeconds <= 0 {
		ttlSeconds = 3600
	}
	return a.client.MintGatewayToken(profile, scope, ttlSeconds)
}

// ImportEnvFile opens a native file picker for a .env file and returns its raw
// contents. The frontend parses + merges it into the editor rows (parsing
// lives in JS so file-import and paste-import share one parser). An empty
// string with nil error means the operator cancelled the dialog.
func (a *App) ImportEnvFile() (string, error) {
	path, err := runtime.OpenFileDialog(a.ctx, runtime.OpenDialogOptions{
		Title: "Import .env file",
		Filters: []runtime.FileFilter{
			{DisplayName: "Env files (*.env, .env*)", Pattern: "*.env;.env*"},
			{DisplayName: "All files (*.*)", Pattern: "*.*"},
		},
	})
	if err != nil || path == "" {
		return "", err
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	return string(data), nil
}

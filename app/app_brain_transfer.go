package main

import (
	"fmt"
	"os"
	"os/exec"
	"strings"

	"github.com/wailsapp/wails/v2/pkg/runtime"
)

// Vault transfer — export/import a brain vault to/from a portable folder via the
// local pbrainctl binary (the tested round-trip CLI). The app resolves the
// vault's bearer token + a host-reachable brain API and shells out, mirroring
// how BackupDatabase shells out to pg_dump. Import is an idempotent, SHA-deduped
// union (safe to merge), so — unlike the destructive pg restore — it is not
// gated behind a scary confirm.

func pbrainctlPath() string {
	if p, err := exec.LookPath("pbrainctl"); err == nil {
		return p
	}
	for _, c := range []string{"/opt/homebrew/bin/pbrainctl", "/usr/local/bin/pbrainctl"} {
		if _, err := os.Stat(c); err == nil {
			return c
		}
	}
	return "pbrainctl" // last resort; will error clearly if missing
}

// brainVaultCreds resolves the host-reachable brain API + the vault's bearer
// token for (profile, vault). SessionURL is the daemon's in-container endpoint
// (host.docker.internal:9998); a host-run pbrainctl needs localhost.
func (a *App) brainVaultCreds(profile, vault string) (api, token string, err error) {
	res, err := a.client.GetBrainProfileTokens(profile)
	if err != nil {
		return "", "", err
	}
	api = strings.Replace(res.SessionURL, "host.docker.internal", "localhost", 1)
	if api == "" {
		api = "http://localhost:9998"
	}
	for _, t := range res.Tokens {
		if t.Vault == vault {
			token = t.Token
			break
		}
	}
	if token == "" {
		return "", "", fmt.Errorf("no token for vault %q", vault)
	}
	return api, token, nil
}

func (a *App) runPbrainctl(profile, vault, sub, dir string) (string, error) {
	api, token, err := a.brainVaultCreds(profile, vault)
	if err != nil {
		return "", err
	}
	cmd := exec.Command(pbrainctlPath(), "client", sub, "--api", api, "--token", token, dir)
	var out strings.Builder
	cmd.Stdout = &out
	cmd.Stderr = &out
	if err := cmd.Run(); err != nil {
		msg := strings.TrimSpace(out.String())
		if msg == "" {
			msg = err.Error()
		}
		return "", fmt.Errorf("%s", msg)
	}
	return strings.TrimSpace(out.String()), nil
}

// ExportBrainVault exports (profile, vault) to a user-chosen folder via
// `pbrainctl client export`. Returns the tool's summary line.
func (a *App) ExportBrainVault(profile, vault string) (string, error) {
	dir, err := runtime.OpenDirectoryDialog(a.ctx, runtime.OpenDialogOptions{
		Title: fmt.Sprintf("Export %s / %s — choose a destination folder", profile, vault),
	})
	if err != nil || dir == "" {
		return "", err
	}
	return a.runPbrainctl(profile, vault, "export", dir)
}

// ImportBrainVault imports a vault folder into (profile, vault) via
// `pbrainctl client import` — an idempotent, SHA-deduped union.
func (a *App) ImportBrainVault(profile, vault string) (string, error) {
	dir, err := runtime.OpenDirectoryDialog(a.ctx, runtime.OpenDialogOptions{
		Title: fmt.Sprintf("Import into %s / %s — choose a vault folder", profile, vault),
	})
	if err != nil || dir == "" {
		return "", err
	}
	return a.runPbrainctl(profile, vault, "import", dir)
}

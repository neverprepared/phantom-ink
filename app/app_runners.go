package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"net"

	"phantom-ink/brainbox"
)

// LocalRunnerStatus is returned by GetLocalRunnerStatus.
type LocalRunnerStatus struct {
	Enabled bool   `json:"enabled"`
	Running bool   `json:"running"`
	Name    string `json:"name"`
}

// GetLocalRunnerStatus returns the current local runner configuration and state.
func (a *App) GetLocalRunnerStatus() LocalRunnerStatus {
	if a.db == nil {
		return LocalRunnerStatus{}
	}
	return LocalRunnerStatus{
		Enabled: a.db.GetSetting(settingLocalRunnerEnabled, "") == "true",
		Running: a.localRunner != nil,
		Name:    a.db.GetSetting(settingLocalRunnerName, "local-mac"),
	}
}

// EnableLocalRunner saves the local runner config and starts the goroutine.
// workDir is no longer stored here — it is specified per-session at create time
// via workspace_home in the session payload.
func (a *App) EnableLocalRunner(name string) error {
	if name == "" {
		name = "local-mac"
	}

	// Generate a stable machine ID if we don't have one.
	machineID := ""
	if a.db != nil {
		machineID = a.db.GetSetting(settingLocalRunnerMachineID, "")
		if machineID == "" {
			b := make([]byte, 8)
			if _, err := rand.Read(b); err == nil {
				machineID = hex.EncodeToString(b)
			}
		}
	}

	// Stop existing runner if running.
	a.stopLocalRunner()

	if a.db != nil {
		_ = a.db.SetSetting(settingLocalRunnerEnabled, "true")
		_ = a.db.SetSetting(settingLocalRunnerName, name)
		if machineID != "" {
			_ = a.db.SetSetting(settingLocalRunnerMachineID, machineID)
		}
	}

	runnerCtx, cancel := context.WithCancel(a.ctx)
	a.localRunnerStop = cancel
	a.localRunner = newLocalRunner(a.client, name, machineID)
	a.localRunner.Start(runnerCtx)
	return nil
}

// DisableLocalRunner stops and deregisters the local runner.
func (a *App) DisableLocalRunner() error {
	if a.db != nil {
		_ = a.db.SetSetting(settingLocalRunnerEnabled, "false")
	}
	a.stopLocalRunner()
	// Best-effort deregister — ignore errors (runner may already be gone from API).
	if a.db != nil {
		name := a.db.GetSetting(settingLocalRunnerName, "local-mac")
		_ = a.client.DeleteRunner(name)
	}
	return nil
}

func (a *App) stopLocalRunner() {
	if a.localRunnerStop != nil {
		a.localRunnerStop()
		a.localRunnerStop = nil
	}
	if a.localRunner != nil {
		a.localRunner.Wait()
		a.localRunner = nil
	}
}

// ListRunners returns all registered runners on the active API.
func (a *App) ListRunners() ([]brainbox.Runner, error) {
	return a.client.ListRunners()
}

// DeleteRunner deregisters a runner by name.
func (a *App) DeleteRunner(name string) error {
	return a.client.DeleteRunner(name)
}

// StartRunnerPairing issues a one-time pairing token for a new runner.
// networkAPIURL is the URL embedded in the token — the address the remote
// runner uses to reach this API. Leave empty to use the client's own baseURL
// (fine for same-host runners). The Wails frontend shows the token to the user.
func (a *App) StartRunnerPairing(runnerNameSuggestion string, ttlSeconds int, networkAPIURL string) (brainbox.PairingTicket, error) {
	return a.client.StartRunnerPairing(runnerNameSuggestion, ttlSeconds, networkAPIURL)
}

// GetLANIP returns the first non-loopback IPv4 address on this machine —
// the address a remote runner should use to reach the local API.
// Returns empty string if none can be found.
func (a *App) GetLANIP() string {
	ifaces, err := net.Interfaces()
	if err != nil {
		return ""
	}
	// Prefer the primary interface names common on macOS/Linux
	preferred := []string{"en0", "eth0", "en1"}
	byName := map[string]string{}
	var fallback string
	for _, iface := range ifaces {
		if iface.Flags&net.FlagUp == 0 || iface.Flags&net.FlagLoopback != 0 {
			continue
		}
		addrs, err := iface.Addrs()
		if err != nil {
			continue
		}
		for _, addr := range addrs {
			var ip net.IP
			switch v := addr.(type) {
			case *net.IPNet:
				ip = v.IP
			case *net.IPAddr:
				ip = v.IP
			}
			if ip == nil || ip.IsLoopback() || ip.To4() == nil {
				continue
			}
			byName[iface.Name] = ip.String()
			if fallback == "" {
				fallback = ip.String()
			}
		}
	}
	for _, name := range preferred {
		if ip, ok := byName[name]; ok {
			return ip
		}
	}
	return fallback
}

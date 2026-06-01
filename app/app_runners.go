package main

import (
	"net"

	"phantom-ink/brainbox"
)

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

// GetAuthorityStatus returns the credential-authority health snapshot used by
// the status-bar dot and the Credentials modal. Returns the zero value with
// an error if the API is unreachable so the frontend can render "unknown".
func (a *App) GetAuthorityStatus() (brainbox.AuthorityStatus, error) {
	return a.client.GetAuthorityStatus()
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

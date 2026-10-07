package main

import (
	"fmt"
	"os"
)

// ---------------------------------------------------------------------------
// Services / Integrations
// ---------------------------------------------------------------------------

// getIntegrationConfig reads integration config from the database.
func (a *App) getIntegrationConfig(name string) ServiceConfig {
	if a.db != nil {
		if row, ok := a.db.GetIntegration(name); ok {
			return ServiceConfig{
				Enabled:   row.Enabled,
				Remote:    row.Remote,
				LocalURL:  row.LocalURL,
				RemoteURL: row.RemoteURL,
			}
		}
	}
	return ServiceConfig{}
}

// ListServices returns all known infrastructure services with their status.
func (a *App) ListServices() []ServiceStatus {
	var result []ServiceStatus
	for _, def := range knownServices {
		if def.Platform {
			continue // managed under the Platform Services card, not Integrations
		}
		cfg := a.getIntegrationConfig(def.Name)
		result = append(result, ServiceStatus{
			ServiceDef: def,
			Enabled:    cfg.Enabled,
			Remote:     cfg.Remote,
			LocalURL:   cfg.LocalURL,
			RemoteURL:  cfg.RemoteURL,
			URL:        cfg.ActiveURL(def.DefaultURL),
			Running:    isServiceRunning(def, cfg),
		})
	}
	return result
}

// StartService starts a docker compose service by name.
func (a *App) StartService(name string) error {
	for _, def := range knownServices {
		if def.Name == name {
			if def.Native {
				return fmt.Errorf("%s is a native service — start it outside phantom-ink", def.Label)
			}
			cfg := a.getIntegrationConfig(name)
			if cfg.Remote {
				return fmt.Errorf("%s is configured as remote — cannot start locally", def.Label)
			}
			extra, err := a.serviceComposeEnv(name)
			if err != nil {
				return err
			}
			if err := composeUp(name, extra...); err != nil {
				return err
			}
			if a.db != nil {
				if err := a.db.UpsertIntegration(IntegrationRow{
					Name: name, Enabled: true, Remote: cfg.Remote,
					LocalURL: cfg.LocalURL, RemoteURL: cfg.RemoteURL,
				}); err != nil {
					fmt.Fprintf(os.Stderr, "warning: failed to update integration %q: %v\n", name, err)
				}
				// Record run intent so ReconcileIntegrations restarts this
				// stack at launch if its container is torn down meanwhile.
				if err := a.db.SetDesiredRunning(name, true); err != nil {
					fmt.Fprintf(os.Stderr, "warning: failed to set desired_running for %q: %v\n", name, err)
				}
			}
			return nil
		}
	}
	return fmt.Errorf("unknown service: %s", name)
}

// StopService stops a docker compose service by name.
func (a *App) StopService(name string) error {
	for _, def := range knownServices {
		if def.Name == name {
			if def.Native {
				return fmt.Errorf("%s is a native service — stop it outside phantom-ink", def.Label)
			}
			cfg := a.getIntegrationConfig(name)
			if cfg.Remote {
				return fmt.Errorf("%s is configured as remote — cannot stop locally", def.Label)
			}
			extra, _ := a.serviceComposeEnv(name) // best-effort: down doesn't build
			if err := composeDown(name, extra...); err != nil {
				return err
			}
			// Clear run intent before returning so a deliberate stop is not
			// undone by ReconcileIntegrations on the next launch.
			if a.db != nil {
				if err := a.db.SetDesiredRunning(name, false); err != nil {
					fmt.Fprintf(os.Stderr, "warning: failed to clear desired_running for %q: %v\n", name, err)
				}
			}
			return nil
		}
	}
	return fmt.Errorf("unknown service: %s", name)
}

// SetServiceConfig updates a service's config in the database.
func (a *App) SetServiceConfig(name string, enabled bool, localURL string, remoteURL string, remote bool) error {
	if a.db != nil {
		return a.db.UpsertIntegration(IntegrationRow{
			Name:      name,
			Enabled:   enabled,
			Remote:    remote,
			LocalURL:  localURL,
			RemoteURL: remoteURL,
		})
	}
	return errNoDB
}

// ---------------------------------------------------------------------------
// Enabled toggle
// ---------------------------------------------------------------------------

// toggleAction is what flipping a service's Enabled switch should do beyond
// persisting the flag.
type toggleAction int

const (
	toggleConfigOnly toggleAction = iota // persist only — nothing to compose
	toggleStart
	toggleStop
)

func (t toggleAction) String() string {
	switch t {
	case toggleStart:
		return "start"
	case toggleStop:
		return "stop"
	default:
		return "config-only"
	}
}

// serviceToggleAction decides what the Enabled toggle does for a service. Only
// local docker integrations are started/stopped: native services are managed
// outside docker, remote ones live on another host, and platform services belong
// to the Platform Services card.
func serviceToggleAction(def ServiceDef, cfg ServiceConfig, enabled bool) toggleAction {
	if def.Native || def.Platform || cfg.Remote {
		return toggleConfigOnly
	}
	if enabled {
		return toggleStart
	}
	return toggleStop
}

// SetServiceEnabled persists a service's Enabled flag and, for local docker
// integrations, brings the container up or down to match — the toggle is a power
// switch, not just a label. The flag is persisted first so the card reflects the
// user's intent even when the container fails to start; the start/stop error is
// returned so the UI can surface it.
func (a *App) SetServiceEnabled(name string, enabled bool) error {
	for _, def := range knownServices {
		if def.Name != name {
			continue
		}
		cfg := a.getIntegrationConfig(name)
		if a.db != nil {
			if err := a.db.UpsertIntegration(IntegrationRow{
				Name: name, Enabled: enabled, Remote: cfg.Remote,
				LocalURL: cfg.LocalURL, RemoteURL: cfg.RemoteURL,
			}); err != nil {
				return fmt.Errorf("persist %s: %w", def.Label, err)
			}
		}
		switch serviceToggleAction(def, cfg, enabled) {
		case toggleStart:
			return a.StartService(name)
		case toggleStop:
			return a.StopService(name)
		}
		return nil
	}
	return fmt.Errorf("unknown service: %s", name)
}

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
			if err := composeUp(name); err != nil {
				return err
			}
			if a.db != nil {
				if err := a.db.UpsertIntegration(IntegrationRow{
					Name: name, Enabled: true, Remote: cfg.Remote,
					LocalURL: cfg.LocalURL, RemoteURL: cfg.RemoteURL,
				}); err != nil {
					fmt.Fprintf(os.Stderr, "warning: failed to save integration state for %s: %v\n", name, err)
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
			return composeDown(name)
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
	return fmt.Errorf("database not available")
}

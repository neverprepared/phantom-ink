package main

import (
	"fmt"
	"path/filepath"

	"phantom-ink/brainbox"
)

// Integrations (ADR-003): place operator-managed compose stacks (kroki, …) on a
// chosen fleet node, and wire the returned endpoint into the active profile's
// host MCP consumers. The router is the control plane; the app is the UI + the
// writer of host-side consumer config (which the router, in a container, can't
// reach). Gateway-side consumers are the router's job (target != "host").

// IntegrationPlacementOutcome is the placement result plus what the app wired.
type IntegrationPlacementOutcome struct {
	brainbox.IntegrationPlacementResult
	Wired   []string `json:"wired"`    // config files updated with the endpoint
	WireErr string   `json:"wire_err"` // non-fatal wiring error, if any
}

// ListIntegrations returns the catalog + placements + eligible nodes.
func (a *App) ListIntegrations() (brainbox.IntegrationsList, error) {
	return a.client.ListIntegrations()
}

// PlaceIntegration places/removes an integration on a node. On placement it
// best-effort wires the endpoint into the active profile's host consumers — a
// wiring failure is reported (WireErr) but does not fail the placement itself.
func (a *App) PlaceIntegration(name, node, desired string) (IntegrationPlacementOutcome, error) {
	res, err := a.client.SetIntegrationPlacement(name, node, desired)
	out := IntegrationPlacementOutcome{IntegrationPlacementResult: res}
	if err != nil {
		return out, err
	}
	if desired == "on" && res.Endpoint != "" {
		wired, werr := a.wireIntegrationConsumers(name, res.Endpoint)
		out.Wired = wired
		if werr != nil {
			out.WireErr = werr.Error()
		}
	}
	return out, nil
}

// wireIntegrationConsumers writes the endpoint into the active profile's host
// consumer config for every host-targeted consumer of the integration. Only
// updates servers that already exist in a config (never creates them).
func (a *App) wireIntegrationConsumers(name, endpoint string) ([]string, error) {
	list, err := a.client.ListIntegrations()
	if err != nil {
		return nil, err
	}
	var integ *brainbox.Integration
	for i := range list.Integrations {
		if list.Integrations[i].Name == name {
			integ = &list.Integrations[i]
			break
		}
	}
	if integ == nil {
		return nil, fmt.Errorf("integration %q not in catalog", name)
	}

	profile := a.config.ActiveProfile
	home := a.profileWorkspaceHome(profile)
	if home == "" {
		return nil, fmt.Errorf("no workspace_home for active profile %q", profile)
	}
	claudePath := filepath.Join(home, ".claude", ".claude.json")
	opencodePath := filepath.Join(home, ".config", "opencode", "opencode.json")

	var wired []string
	for _, con := range integ.Consumers {
		if con.Target != "host" {
			continue // gateway consumers are wired by the router
		}
		if ok, e := injectMCPServerEnv(claudePath, "mcpServers", "env", con.Server, con.Env, endpoint); e != nil {
			return wired, e
		} else if ok {
			wired = append(wired, claudePath)
		}
		if ok, e := injectMCPServerEnv(opencodePath, "mcp", "environment", con.Server, con.Env, endpoint); e != nil {
			return wired, e
		} else if ok {
			wired = append(wired, opencodePath)
		}
	}
	return wired, nil
}

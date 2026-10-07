package main

// Integrations are docker compose stacks whose containers can disappear behind
// the app's back: `docker compose down` (StopService, and the stop half of the
// Memory Graph panel's profile rebind) removes the container and its network
// outright, and `restart: unless-stopped` cannot resurrect a container that no
// longer exists. Nothing else ever called StartService, so a stack that went
// away stayed away — silently, forever.
//
// Reconcile closes that gap by converging on desired_running at launch: run
// intent recorded by StartService/StopService, deliberately NOT `enabled` (which
// the Integrations toggle sets as config intent without starting anything).

// runningFunc reports whether a service is currently up. Injected so the plan is
// testable without docker.
type runningFunc func(ServiceDef, ServiceConfig) bool

// reconcilePlan returns the names of services that should be started to converge
// on desired state. A service qualifies when it is a local docker integration
// the user has started and that is not currently running.
func reconcilePlan(defs []ServiceDef, rows map[string]IntegrationRow, running runningFunc) []string {
	var plan []string
	for _, def := range defs {
		if def.Native || def.Platform {
			continue // not ours to compose up
		}
		row, ok := rows[def.Name]
		if !ok || !row.DesiredRunning || row.Remote {
			continue
		}
		cfg := ServiceConfig{
			Enabled:   row.Enabled,
			Remote:    row.Remote,
			LocalURL:  row.LocalURL,
			RemoteURL: row.RemoteURL,
		}
		if running(def, cfg) {
			continue
		}
		plan = append(plan, def.Name)
	}
	return plan
}

// ReconcileIntegrations starts every local integration the user had running that
// is currently down. It is best-effort: a service whose start fails is logged and
// skipped so one broken stack cannot hold up the rest, or app startup. Returns
// the names actually started.
func (a *App) ReconcileIntegrations() []string {
	if a.db == nil {
		return nil
	}
	all, err := a.db.AllIntegrations()
	if err != nil {
		logErr("reconcile: read integrations: %v", err)
		return nil
	}
	rows := make(map[string]IntegrationRow, len(all))
	for _, r := range all {
		rows[r.Name] = r
	}

	var started []string
	for _, name := range reconcilePlan(knownServices, rows, isServiceRunning) {
		extra, err := a.serviceComposeEnv(name)
		if err != nil {
			logErr("reconcile %q: resolve env: %v", name, err)
			continue
		}
		if err := composeUp(name, extra...); err != nil {
			logErr("reconcile %q: %v", name, err)
			continue
		}
		started = append(started, name)
	}
	return started
}

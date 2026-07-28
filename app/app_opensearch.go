package main

import (
	"context"
	"fmt"
	"phantom-ink/opensearch"
	"strings"
)

// opensearchAPIURL resolves the OpenSearch HTTP API URL from the integrations
// table. The user-facing URL in ServicesPanel points at Dashboards (5601);
// the API lives on 9200, so we swap when the user kept the default port.
func (a *App) opensearchAPIURL() (string, error) {
	var def *ServiceDef
	for i := range knownServices {
		if knownServices[i].Name == "opensearch" {
			def = &knownServices[i]
			break
		}
	}
	if def == nil {
		return "", fmt.Errorf("opensearch service not registered")
	}
	// OpenSearch is a first-class platform service now, so default to its
	// endpoint (the DefaultURL) even when the hidden integration toggle was never
	// enabled. An explicit integration config still overrides.
	cfg := a.getIntegrationConfig("opensearch")
	url := def.DefaultURL
	if cfg.Enabled {
		url = cfg.ActiveURL(def.DefaultURL)
	}
	if i := strings.LastIndex(url, ":5601"); i >= 0 {
		url = url[:i] + ":9200" + url[i+len(":5601"):]
	}
	return url, nil
}

// TailLogs returns the most-recent log entries (newest first), optionally
// filtered by workspace.  Used by the Stream panel's Live tab.
func (a *App) TailLogs(workspace string, limit int) ([]opensearch.LogEntry, error) {
	url, err := a.opensearchAPIURL()
	if err != nil {
		return nil, err
	}
	return opensearch.NewClient(url).TailLogs(context.Background(), workspace, limit)
}

// GetObservabilityOverview returns the four metric cards rendered in the
// Observability panel.  Pass workspace="" to view all data unfiltered, or a
// workspace name to filter by resource.attributes.workspace — matching the
// value users set in OTEL_RESOURCE_ATTRIBUTES=workspace=<name>.
func (a *App) GetObservabilityOverview(workspace string) (*opensearch.Overview, error) {
	url, err := a.opensearchAPIURL()
	if err != nil {
		return nil, err
	}
	return opensearch.NewClient(url).GetOverview(context.Background(), workspace)
}

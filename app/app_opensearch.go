package main

import (
	"context"
	"fmt"
	"net"
	"net/url"
	"phantom-ink/opensearch"
	"strings"
)

// platformSubdomainURL derives a sibling platform service's URL from the
// configured router base URL, so services follow the platform wherever it runs
// instead of assuming co-location. Two shapes:
//   - Co-located (base host is loopback, a bare IP, or a dotless hostname): the
//     platform publishes the service on that same box → http://<host>:<localPort>.
//   - Remote (base host is a real domain): the platform fronts each service on
//     its own Traefik subdomain, TLS-terminated on 443 → https://<sub>.<baseDomain>,
//     where <baseDomain> is the router host minus its leading label
//     (api.example.com → opensearch.example.com). No hardcoded domain.
func platformSubdomainURL(baseURL, sub string, localPort int) string {
	u, err := url.Parse(baseURL)
	if err != nil || u.Hostname() == "" {
		return fmt.Sprintf("http://127.0.0.1:%d", localPort)
	}
	host := u.Hostname()
	if host == "localhost" || net.ParseIP(host) != nil || !strings.Contains(host, ".") {
		return fmt.Sprintf("http://%s:%d", host, localPort)
	}
	base := host
	if labels := strings.Split(host, "."); len(labels) >= 3 {
		base = strings.Join(labels[1:], ".") // drop the router's leading label (e.g. "api")
	}
	return "https://" + sub + "." + base
}

// opensearchAPIURL resolves the OpenSearch HTTP API URL. An explicit integration
// config wins; otherwise we derive the endpoint from the configured platform
// host (base_url) so a remote platform works without a manual toggle — the panel
// broke blank because the old default hardcoded localhost while OpenSearch runs
// on the remote platform node. The user-facing URL in ServicesPanel points at
// Dashboards (5601); the API lives on 9200, so we swap when the URL kept :5601.
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
	cfg := a.getIntegrationConfig("opensearch")
	var target string
	if cfg.Enabled {
		target = cfg.ActiveURL(def.DefaultURL)
	} else {
		target = platformSubdomainURL(a.config.BaseURL, "opensearch", 9200)
	}
	if i := strings.LastIndex(target, ":5601"); i >= 0 {
		target = target[:i] + ":9200" + target[i+len(":5601"):]
	}
	return target, nil
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

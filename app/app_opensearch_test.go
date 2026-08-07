package main

import "testing"

func TestPlatformSubdomainURL(t *testing.T) {
	cases := []struct {
		name    string
		baseURL string
		sub     string
		port    int
		want    string
	}{
		// Remote platform: Traefik subdomain-per-service, TLS on 443.
		{"remote api subdomain", "https://api.neverprepared.com", "opensearch", 9200, "https://opensearch.neverprepared.com"},
		{"remote with path", "https://api.neverprepared.com/", "opensearch", 9200, "https://opensearch.neverprepared.com"},
		{"remote apex domain", "https://neverprepared.com", "opensearch", 9200, "https://opensearch.neverprepared.com"},
		{"remote deep subdomain drops leading label", "https://phantom-api.example.com", "logs", 5601, "https://logs.example.com"},
		// Co-located: same box, service port, plain HTTP.
		{"loopback ip", "http://127.0.0.1:9910", "opensearch", 9200, "http://127.0.0.1:9200"},
		{"localhost", "http://localhost:9999", "opensearch", 9200, "http://localhost:9200"},
		{"lan ip", "http://100.89.35.33:9910", "opensearch", 9200, "http://100.89.35.33:9200"},
		{"dotless hostname", "http://mybox:9910", "opensearch", 9200, "http://mybox:9200"},
		// Degenerate input falls back to loopback.
		{"empty base", "", "opensearch", 9200, "http://127.0.0.1:9200"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := platformSubdomainURL(tc.baseURL, tc.sub, tc.port); got != tc.want {
				t.Errorf("platformSubdomainURL(%q, %q, %d) = %q, want %q", tc.baseURL, tc.sub, tc.port, got, tc.want)
			}
		})
	}
}

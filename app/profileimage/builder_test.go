package profileimage

import (
	"encoding/json"
	"testing"
)

func mcpServersFrom(t *testing.T, raw []byte) map[string]interface{} {
	t.Helper()
	var doc map[string]interface{}
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatalf("output is not valid JSON: %v", err)
	}
	servers, _ := doc["mcpServers"].(map[string]interface{})
	if servers == nil {
		t.Fatal("output has no mcpServers")
	}
	return servers
}

func assertGatewayEntry(t *testing.T, servers map[string]interface{}) {
	t.Helper()
	gw, ok := servers["phantom-gateway"].(map[string]interface{})
	if !ok {
		t.Fatal("phantom-gateway entry missing")
	}
	if gw["type"] != "http" {
		t.Errorf("type = %v, want http", gw["type"])
	}
	if gw["url"] != "${PHANTOM_GATEWAY_URL}" {
		t.Errorf("url = %v, want ${PHANTOM_GATEWAY_URL}", gw["url"])
	}
	headers, _ := gw["headers"].(map[string]interface{})
	if headers["Authorization"] != "Bearer ${PHANTOM_GATEWAY_TOKEN}" {
		t.Errorf("Authorization = %v, want Bearer ${PHANTOM_GATEWAY_TOKEN}", headers["Authorization"])
	}
}

// assertOnlyGateway is the core contract: the baked image carries the gateway
// entry and nothing else.
func assertOnlyGateway(t *testing.T, servers map[string]interface{}) {
	t.Helper()
	assertGatewayEntry(t, servers)
	if len(servers) != 1 {
		names := make([]string, 0, len(servers))
		for n := range servers {
			names = append(names, n)
		}
		t.Errorf("expected only phantom-gateway, got %v", names)
	}
}

func TestTranslateClaudeJSONBakesGatewayIntoEmptyConfig(t *testing.T) {
	out := translateClaudeJSON([]byte("{}"), "/Users/tester/ws")
	assertOnlyGateway(t, mcpServersFrom(t, out))
}

func TestTranslateClaudeJSONDropsProfileServers(t *testing.T) {
	// The host's direct MCP servers must NOT be baked into the image — the
	// gateway proxies them daemon-side with per-profile creds.
	in := []byte(`{"mcpServers": {
		"slack": {"command": "npx", "args": ["-y", "slack-mcp"]},
		"phantom-brain": {"command": "pbrainctl", "args": ["client", "mcp"]}
	}}`)
	assertOnlyGateway(t, mcpServersFrom(t, translateClaudeJSON(in, "/Users/tester/ws")))
}

func TestTranslateClaudeJSONGatewayReplacesStaleUserEntry(t *testing.T) {
	// A stale user-defined phantom-gateway (hand-added with a hardcoded token)
	// must be replaced by the env-reference form.
	in := []byte(`{"mcpServers": {"phantom-gateway": {"type": "http", "url": "http://old:9999", "headers": {"Authorization": "Bearer hardcoded"}}}}`)
	assertOnlyGateway(t, mcpServersFrom(t, translateClaudeJSON(in, "/Users/tester/ws")))
}

func TestTranslateClaudeJSONPreservesOtherKeys(t *testing.T) {
	// Non-mcpServers state (projects, onboarding flags) is retained, with Mac
	// paths translated.
	in := []byte(`{"hasCompletedOnboarding": true, "projects": {"/Users/tester/ws/repo": {"x": 1}}}`)
	out := translateClaudeJSON(in, "/Users/tester/ws")
	var doc map[string]interface{}
	if err := json.Unmarshal(out, &doc); err != nil {
		t.Fatalf("invalid JSON: %v", err)
	}
	if doc["hasCompletedOnboarding"] != true {
		t.Error("hasCompletedOnboarding not preserved")
	}
	projects, _ := doc["projects"].(map[string]interface{})
	if _, ok := projects["/home/developer/repo"]; !ok {
		t.Errorf("Mac project path not translated: %v", projects)
	}
	assertOnlyGateway(t, mcpServersFrom(t, out))
}

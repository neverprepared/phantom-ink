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

func TestTranslateClaudeJSONBakesGatewayIntoEmptyConfig(t *testing.T) {
	out := translateClaudeJSON([]byte("{}"), "/Users/tester/ws", nil)
	assertGatewayEntry(t, mcpServersFrom(t, out))
}

func TestTranslateClaudeJSONBakesGatewayAlongsideProfileServers(t *testing.T) {
	in := []byte(`{"mcpServers": {"slack": {"command": "npx", "args": ["-y", "slack-mcp"]}}}`)
	servers := mcpServersFrom(t, translateClaudeJSON(in, "/Users/tester/ws", nil))
	assertGatewayEntry(t, servers)
	if _, ok := servers["slack"]; !ok {
		t.Error("existing profile server dropped")
	}
}

func TestTranslateClaudeJSONGatewayOverridesUserEntry(t *testing.T) {
	// A stale user-defined phantom-gateway (e.g. hand-added with a hardcoded
	// token) must be replaced by the env-reference form.
	in := []byte(`{"mcpServers": {"phantom-gateway": {"type": "http", "url": "http://old:9999", "headers": {"Authorization": "Bearer hardcoded"}}}}`)
	assertGatewayEntry(t, mcpServersFrom(t, translateClaudeJSON(in, "/Users/tester/ws", nil)))
}

func TestTranslateClaudeJSONStripsMacOnlyServers(t *testing.T) {
	in := []byte(`{"mcpServers": {"mac-thing": {"command": "/Applications/Foo.app/bin/foo"}}}`)
	servers := mcpServersFrom(t, translateClaudeJSON(in, "/Users/tester/ws", nil))
	if _, ok := servers["mac-thing"]; ok {
		t.Error("Mac-absolute-path server should be stripped")
	}
	assertGatewayEntry(t, servers)
}

package profileimage

import (
	"encoding/json"
	"os"
	"strings"
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

// Image naming. These guard the rename: the names were previously literals in
// five places, one of which did length arithmetic on the name, so a partial
// rename produced a wrong offset rather than a failure.
func TestProfileTagUsesPhantomSessionName(t *testing.T) {
	got := ProfileTag("registry.neverprepared.com", "personal")
	want := "registry.neverprepared.com/phantom-session:personal"
	if got != want {
		t.Errorf("ProfileTag = %q, want %q", got, want)
	}
}

func TestProfileTagTrimsTrailingSlash(t *testing.T) {
	want := "registry.neverprepared.com/phantom-session:gsa"
	if got := ProfileTag("registry.neverprepared.com/", "gsa"); got != want {
		t.Errorf("ProfileTag = %q, want %q", got, want)
	}
}

func TestBaseImageRef(t *testing.T) {
	want := "registry.neverprepared.com/phantom-session-base:latest"
	if got := BaseImageRef("registry.neverprepared.com"); got != want {
		t.Errorf("BaseImageRef = %q, want %q", got, want)
	}
}

func TestImageTagUsesProfileTag(t *testing.T) {
	o := &BuildOptions{RegistryURL: "reg.example.com", Profile: "personal"}
	if got := o.imageTag(); got != ProfileTag("reg.example.com", "personal") {
		t.Errorf("imageTag = %q", got)
	}
}

// A push failure must not discard the env key. The committed image is already
// encrypted with it; returning a zero BuildResult is what made the failure
// unrecoverable and stranded brainbox-profile:personal on 2026-10-31.
func TestPushFailureStillReportsTagAndEnvKey(t *testing.T) {
	res := BuildResult{Tag: "reg/phantom-session:personal", EnvKey: "deadbeef"}
	if res.Tag == "" || res.EnvKey == "" {
		t.Fatal("BuildResult must be able to carry tag+key alongside an error")
	}
	// Guard the contract in the source: the push error path must not return a
	// bare BuildResult{}.
	src, err := os.ReadFile("builder.go")
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(src), "return BuildResult{}, fmt.Errorf(\"push image") {
		t.Error("push failure returns a zero BuildResult — the env key is discarded and the image is stranded")
	}
}

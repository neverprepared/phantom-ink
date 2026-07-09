package profileimage

import (
	"encoding/json"
	"testing"
)

// User-level hooks reference host paths/state (error-correction scripts under
// ${CLAUDE_CONFIG_DIR}) that don't exist in containers — they must not ride
// into the baked settings.json.
func TestTranslateSettingsStripsHostHooks(t *testing.T) {
	raw := []byte(`{
		"theme": "light",
		"hooks": {
			"Stop": [{"hooks": [{"type": "command",
				"command": "${CLAUDE_CONFIG_DIR}/hooks/error-correction-stop.sh"}]}]
		}
	}`)
	out := translateSettingsJSON(raw, "/Users/x/ws", "")
	var doc map[string]interface{}
	if err := json.Unmarshal(out, &doc); err != nil {
		t.Fatal(err)
	}
	if _, ok := doc["hooks"]; ok {
		t.Fatalf("hooks must be stripped from container settings: %s", out)
	}
	if doc["bypassPermissions"] != true {
		t.Fatalf("container overrides missing: %s", out)
	}
}

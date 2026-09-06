package profileimage

import (
	"encoding/json"
	"strings"
	"testing"
)

// Container-safe hooks — those whose command resolves via
// ${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/… — point at ~/.claude/hooks/ in the
// container, where packHookScripts bakes the scripts. They MUST survive so the
// security-critical secret-guard.py (PreToolUse Bash) fires. Hooks that hardcode
// a host absolute path or embed the workspace home can't run in the container and
// MUST be dropped, with empty groups/events pruned.
func TestTranslateSettingsFiltersHooks(t *testing.T) {
	raw := []byte(`{
		"theme": "light",
		"hooks": {
			"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command",
				"command": "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/secret-guard.py"}]}],
			"Stop": [{"hooks": [{"type": "command",
				"command": "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/error-correction-stop.sh"}]}],
			"SessionStart": [{"hooks": [{"type": "command",
				"command": "python3 /Users/x/ws/code/phantom-router/scripts/sync_skills.py || true"}]}]
		}
	}`)
	out := translateSettingsJSON(raw, "/Users/x/ws", "")
	var doc map[string]interface{}
	if err := json.Unmarshal(out, &doc); err != nil {
		t.Fatal(err)
	}

	hooks, ok := doc["hooks"].(map[string]interface{})
	if !ok {
		t.Fatalf("container-safe hooks must survive: %s", out)
	}
	// secret-guard PreToolUse survives.
	if _, ok := hooks["PreToolUse"]; !ok {
		t.Fatalf("PreToolUse secret-guard hook must be kept: %s", out)
	}
	if !strings.Contains(string(out), "secret-guard.py") {
		t.Fatalf("secret-guard.py hook missing from output: %s", out)
	}
	// error-correction Stop survives.
	if _, ok := hooks["Stop"]; !ok {
		t.Fatalf("Stop error-correction hook must be kept: %s", out)
	}
	// SessionStart with a host absolute path is dropped entirely.
	if _, ok := hooks["SessionStart"]; ok {
		t.Fatalf("host-path SessionStart hook must be dropped: %s", out)
	}
	if strings.Contains(string(out), "sync_skills.py") {
		t.Fatalf("host-path hook leaked into container settings: %s", out)
	}

	if doc["bypassPermissions"] != true {
		t.Fatalf("container overrides missing: %s", out)
	}
}

// When every hook is host-only, the entire hooks block is removed (no empty
// map left behind that would render as dead noise).
func TestTranslateSettingsDropsAllHostOnlyHooks(t *testing.T) {
	raw := []byte(`{
		"hooks": {
			"SessionStart": [{"hooks": [{"type": "command",
				"command": "bash /Users/x/ws/code/phantom-ink/statusline.sh"}]}]
		}
	}`)
	out := translateSettingsJSON(raw, "/Users/x/ws", "")
	var doc map[string]interface{}
	if err := json.Unmarshal(out, &doc); err != nil {
		t.Fatal(err)
	}
	if _, ok := doc["hooks"]; ok {
		t.Fatalf("all-host-only hooks block must be removed: %s", out)
	}
}

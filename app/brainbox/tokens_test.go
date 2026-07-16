package brainbox

import (
	"encoding/json"
	"testing"
)

func TestMintProfileToken(t *testing.T) {
	client, cap := stubServer(t, 200, `{
		"token_id": "pt_abc", "token": "raw-secret-value",
		"workspace_profile": "personal", "capabilities": ["agent_events:write"],
		"label": "ci runner"}`)

	tok, err := client.MintProfileToken("personal", []string{"agent_events:write"}, "ci runner")
	if err != nil {
		t.Fatal(err)
	}
	if cap.method != "POST" || cap.path != "/api/tokens" {
		t.Fatalf("unexpected request: %s %s", cap.method, cap.path)
	}
	if cap.apiKey != "test-key" {
		t.Fatalf("missing api key header")
	}
	// The mint body must carry the profile, capabilities, and label verbatim.
	var body map[string]any
	if err := json.Unmarshal(cap.body, &body); err != nil {
		t.Fatalf("body not json: %s", cap.body)
	}
	if body["workspace_profile"] != "personal" || body["label"] != "ci runner" {
		t.Fatalf("bad mint body: %s", cap.body)
	}
	caps, _ := body["capabilities"].([]any)
	if len(caps) != 1 || caps[0] != "agent_events:write" {
		t.Fatalf("capabilities not sent: %s", cap.body)
	}
	if tok.TokenID != "pt_abc" || tok.Token != "raw-secret-value" ||
		tok.WorkspaceProfile != "personal" || len(tok.Capabilities) != 1 {
		t.Fatalf("bad decode: %+v", tok)
	}
}

func TestMintProfileTokenNilCapabilities(t *testing.T) {
	// A nil capability slice must serialize as [] (never null), so the server's
	// list validation sees an empty list rather than a missing field.
	client, cap := stubServer(t, 200, `{"token_id": "pt_x", "token": "r",
		"workspace_profile": "personal", "capabilities": [], "label": ""}`)
	if _, err := client.MintProfileToken("personal", nil, ""); err != nil {
		t.Fatal(err)
	}
	var body map[string]any
	if err := json.Unmarshal(cap.body, &body); err != nil {
		t.Fatalf("body not json: %s", cap.body)
	}
	caps, ok := body["capabilities"].([]any)
	if !ok || caps == nil || len(caps) != 0 {
		t.Fatalf("nil capabilities must marshal as []: %s", cap.body)
	}
}

func TestListProfileTokens(t *testing.T) {
	client, cap := stubServer(t, 200, `{"tokens": [
		{"token_id": "pt_1", "workspace_profile": "personal",
		 "capabilities": ["agent_events:write"], "scope": [], "label": "a",
		 "issued": 111, "revoked": false, "revoked_at": 0, "last_used": 222},
		{"token_id": "pt_2", "workspace_profile": "work",
		 "capabilities": [], "scope": [], "label": "",
		 "issued": 100, "revoked": true, "revoked_at": 150, "last_used": 0}]}`)

	rows, err := client.ListProfileTokens()
	if err != nil {
		t.Fatal(err)
	}
	if cap.method != "GET" || cap.path != "/api/tokens" {
		t.Fatalf("unexpected request: %s %s", cap.method, cap.path)
	}
	if len(rows) != 2 {
		t.Fatalf("expected 2 rows, got %d", len(rows))
	}
	if rows[0].TokenID != "pt_1" || rows[0].Issued != 111 || rows[0].LastUsed != 222 || rows[0].Revoked {
		t.Fatalf("bad first row: %+v", rows[0])
	}
	if !rows[1].Revoked || rows[1].RevokedAt != 150 || rows[1].LastUsed != 0 {
		t.Fatalf("bad second row: %+v", rows[1])
	}
}

func TestRevokeProfileToken(t *testing.T) {
	client, cap := stubServer(t, 200, `{"token_id": "pt 1/weird", "revoked": true}`)
	// A token id with reserved characters must be path-escaped so it can't break
	// out of the /api/tokens/{id} route.
	if err := client.RevokeProfileToken("pt 1/weird"); err != nil {
		t.Fatal(err)
	}
	if cap.method != "DELETE" || cap.path != "/api/tokens/pt%201%2Fweird" {
		t.Fatalf("unexpected request: %s %s", cap.method, cap.path)
	}
}

func TestProfileTokenCatalogHelpers(t *testing.T) {
	client, cap := stubServer(t, 200, `{"capabilities": ["agent_events:write", "tasks:write"]}`)
	caps, err := client.ProfileTokenCapabilities()
	if err != nil {
		t.Fatal(err)
	}
	if cap.path != "/api/tokens/capabilities" || len(caps) != 2 || caps[1] != "tasks:write" {
		t.Fatalf("bad capabilities: %s %+v", cap.path, caps)
	}

	client, cap = stubServer(t, 200, `{"profiles": ["personal", "work"]}`)
	profiles, err := client.ProfileTokenProfiles()
	if err != nil {
		t.Fatal(err)
	}
	if cap.path != "/api/tokens/profiles" || len(profiles) != 2 || profiles[0] != "personal" {
		t.Fatalf("bad profiles: %s %+v", cap.path, profiles)
	}
}

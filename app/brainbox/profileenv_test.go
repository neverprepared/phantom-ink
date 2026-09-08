package brainbox

import (
	"encoding/json"
	"testing"
)

func TestPutProfileEnv(t *testing.T) {
	client, cap := stubServer(t, 200, `{"profile":"personal","saved":true,"count":2}`)

	res, err := client.PutProfileEnv("personal", map[string]string{
		"FOO":          "bar",
		"GITHUB_TOKEN": "x",
	})
	if err != nil {
		t.Fatal(err)
	}
	if cap.method != "PUT" || cap.path != "/api/gateway/profiles/personal/env" {
		t.Fatalf("request = %s %s, want PUT /api/gateway/profiles/personal/env", cap.method, cap.path)
	}
	if cap.apiKey != "test-key" {
		t.Fatalf("apiKey = %q, want test-key (hub auth)", cap.apiKey)
	}

	// Body must be {"env": {KEY: VALUE}} — the shape gateway_put_profile_env expects.
	var body struct {
		Env map[string]string `json:"env"`
	}
	if err := json.Unmarshal(cap.body, &body); err != nil {
		t.Fatalf("body not {\"env\":...}: %v (%s)", err, cap.body)
	}
	if body.Env["FOO"] != "bar" || body.Env["GITHUB_TOKEN"] != "x" || len(body.Env) != 2 {
		t.Fatalf("env body = %v", body.Env)
	}
	if !res.Saved || res.Count != 2 || res.Profile != "personal" {
		t.Fatalf("result = %+v", res)
	}
}

func TestPutProfileEnv_ServerError(t *testing.T) {
	client, _ := stubServer(t, 503, `{"detail":"credentials broker unavailable"}`)
	_, err := client.PutProfileEnv("personal", map[string]string{"A": "1"})
	if err == nil {
		t.Fatal("expected error on 503, got nil")
	}
}

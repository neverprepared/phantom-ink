package main

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"phantom-ink/brainbox"
)

// SaveRule must route create (empty ID) to POST /api/rules and update
// (ID set) to PUT /api/rules/{id}.
func TestSaveRuleRouting(t *testing.T) {
	var method, path string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		method, path = r.Method, r.URL.Path
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"id": "r1", "name": "n", "pattern": {}, "actions": []}`))
	}))
	defer srv.Close()

	app := &App{client: brainbox.NewClient(srv.URL, "")}

	if _, err := app.SaveRule(brainbox.Rule{Name: "n"}); err != nil {
		t.Fatal(err)
	}
	if method != "POST" || path != "/api/rules" {
		t.Fatalf("create routed to %s %s", method, path)
	}

	if _, err := app.SaveRule(brainbox.Rule{ID: "r1", Name: "n"}); err != nil {
		t.Fatal(err)
	}
	if method != "PUT" || path != "/api/rules/r1" {
		t.Fatalf("update routed to %s %s", method, path)
	}
}

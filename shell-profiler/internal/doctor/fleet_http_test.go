package doctor

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// recordedRequest is one call the fake orchestration API saw.
type recordedRequest struct {
	method string
	path   string
	apiKey string
	body   map[string]any
}

// fakeAPI stands in for the orchestration API. It pins the contract this
// client rides on — the endpoints, the auth header, and the payload shapes
// verified against brainbox/src/brainbox/api.py.
func fakeAPI(t *testing.T, seen *[]recordedRequest) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		rec := recordedRequest{method: r.Method, path: r.URL.Path, apiKey: r.Header.Get(apiKeyHeader)}
		if data, _ := io.ReadAll(r.Body); len(data) > 0 {
			_ = json.Unmarshal(data, &rec.body)
		}
		*seen = append(*seen, rec)

		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.URL.Path == "/api/runners":
			_, _ = w.Write([]byte(`[{"name":"m3-64","host":"10.0.0.1","capabilities":{"docker":true},"in_flight":2,"max_concurrent":15}]`))
		case strings.HasSuffix(r.URL.Path, "/exec"):
			_, _ = w.Write([]byte(`{"success":true,"exit_code":0,"output":"SPFLEET:HTTP:200"}`))
		default:
			_, _ = w.Write([]byte(`{"success":true}`))
		}
	}))
}

func TestHTTPFleetClient_EndpointContract(t *testing.T) {
	var seen []recordedRequest
	srv := fakeAPI(t, &seen)
	defer srv.Close()

	c := NewHTTPFleetClient(srv.URL+"/", "test-key")

	runners, err := c.ListRunners()
	if err != nil {
		t.Fatalf("ListRunners() error: %v", err)
	}
	if len(runners) != 1 || runners[0].Name != "m3-64" || !runners[0].CanHostSession() {
		t.Fatalf("runner decode lost fields: %+v", runners)
	}
	if !runners[0].IsRemote() || !runners[0].HasCapacity() {
		t.Errorf("runner should be remote with capacity: %+v", runners[0])
	}

	if err := c.CreateSession(FleetSessionSpec{
		Name: "doctor-credcheck-abc", Runner: "m3-64", Profile: "demo", Role: "developer",
	}); err != nil {
		t.Fatalf("CreateSession() error: %v", err)
	}

	res, err := c.Exec("doctor-credcheck-abc", githubTokenProbe("https://api.github.com"))
	if err != nil {
		t.Fatalf("Exec() error: %v", err)
	}
	if !res.Success || res.Output != "SPFLEET:HTTP:200" {
		t.Errorf("exec decode lost fields: %+v", res)
	}

	if err := c.DeleteSession("doctor-credcheck-abc"); err != nil {
		t.Fatalf("DeleteSession() error: %v", err)
	}

	want := []struct{ method, path string }{
		{http.MethodGet, "/api/runners"},
		{http.MethodPost, "/api/create"},
		{http.MethodPost, "/api/sessions/doctor-credcheck-abc/exec"},
		{http.MethodPost, "/api/delete"},
	}
	if len(seen) != len(want) {
		t.Fatalf("saw %d requests, want %d: %+v", len(seen), len(want), seen)
	}
	for i, w := range want {
		if seen[i].method != w.method || seen[i].path != w.path {
			t.Errorf("request %d = %s %s, want %s %s", i, seen[i].method, seen[i].path, w.method, w.path)
		}
		// Every call must authenticate; the trailing slash on the base URL
		// must not produce a doubled one.
		if seen[i].apiKey != "test-key" {
			t.Errorf("request %d did not carry the %s header", i, apiKeyHeader)
		}
	}

	// workspace_profile is what makes the broker deliver THAT profile's
	// credentials into the container — the whole check hinges on it.
	create := seen[1].body
	if create["workspace_profile"] != "demo" {
		t.Errorf("create body = %+v, want workspace_profile=demo", create)
	}
	if create["runner"] != "m3-64" {
		t.Errorf("create body = %+v, want runner=m3-64", create)
	}

	// The exec payload is {"command": ...}, and the token is not in it.
	cmd, _ := seen[2].body["command"].(string)
	if !strings.Contains(cmd, "GITHUB_TOKEN") || !strings.Contains(cmd, "-H @-") {
		t.Errorf("exec command lost the probe shape: %q", cmd)
	}
}

// A server error must surface as an error, not a silent pass.
func TestHTTPFleetClient_ServerError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
		_, _ = w.Write([]byte(`{"detail":"Runner exec failed"}`))
	}))
	defer srv.Close()

	c := NewHTTPFleetClient(srv.URL, "k")
	if _, err := c.Exec("s", "true"); err == nil {
		t.Error("a 500 must surface as an error")
	}
	if _, err := c.ListRunners(); err == nil {
		t.Error("a 500 must surface as an error")
	}
}

// A create that reports failure in its body must not read as success.
func TestHTTPFleetClient_CreateReportsBodyFailure(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"success":false,"error":"runner saturated"}`))
	}))
	defer srv.Close()

	err := NewHTTPFleetClient(srv.URL, "k").CreateSession(FleetSessionSpec{Name: "s"})
	if err == nil || !strings.Contains(err.Error(), "saturated") {
		t.Errorf("want the body's error surfaced, got %v", err)
	}
}

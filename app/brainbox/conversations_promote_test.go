package brainbox

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
)

// Promote-a-message client (PR3). The downstream (brainbox) is a httptest
// server, so what these assert is the exact request the desktop app makes:
// path, profile scoping, and body — the three things a wrong promote would get
// wrong silently.

func TestPromoteConversationMessageToMemory(t *testing.T) {
	var gotPath, gotProfile, gotMethod string
	var gotBody PromoteMessageRequest
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotMethod = r.Method
		gotPath = r.URL.EscapedPath()
		gotProfile = r.URL.Query().Get("profile")
		_ = json.NewDecoder(r.Body).Decode(&gotBody)
		_ = json.NewEncoder(w).Encode(PromoteMessageResult{
			OK: true, Target: "memory", SHA: "abc123", Detail: "written",
		})
	}))
	defer srv.Close()

	res, err := NewClient(srv.URL, "").PromoteConversationMessage(
		"01CONV", "01MSG", "personal",
		PromoteMessageRequest{Target: "memory", Note: "keep this", Tags: []string{"decision"}},
	)
	if err != nil {
		t.Fatalf("PromoteConversationMessage: %v", err)
	}

	if gotMethod != http.MethodPost {
		t.Errorf("method = %s, want POST", gotMethod)
	}
	if gotPath != "/api/conversations/01CONV/messages/01MSG/promote" {
		t.Errorf("path = %q", gotPath)
	}
	if gotProfile != "personal" {
		t.Errorf("profile = %q, want personal", gotProfile)
	}
	if gotBody.Target != "memory" || gotBody.Note != "keep this" {
		t.Errorf("body = %+v", gotBody)
	}
	if len(gotBody.Tags) != 1 || gotBody.Tags[0] != "decision" {
		t.Errorf("tags = %v", gotBody.Tags)
	}
	if !res.OK || res.SHA != "abc123" || res.Target != "memory" {
		t.Errorf("result = %+v", res)
	}
}

func TestPromoteConversationMessageToTaskCarriesAgentAndRepo(t *testing.T) {
	var gotBody PromoteMessageRequest
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewDecoder(r.Body).Decode(&gotBody)
		_ = json.NewEncoder(w).Encode(PromoteMessageResult{
			OK: true, Target: "task", TaskID: "task-9",
		})
	}))
	defer srv.Close()

	res, err := NewClient(srv.URL, "").PromoteConversationMessage(
		"01CONV", "01MSG", "work",
		PromoteMessageRequest{Target: "task", AgentName: "worker", RepoURL: "https://example.test/r.git"},
	)
	if err != nil {
		t.Fatalf("PromoteConversationMessage: %v", err)
	}
	if gotBody.AgentName != "worker" || gotBody.RepoURL != "https://example.test/r.git" {
		t.Errorf("body = %+v", gotBody)
	}
	if res.TaskID != "task-9" {
		t.Errorf("task_id = %q", res.TaskID)
	}
}

func TestPromoteConversationMessageOmitsEmptyTaskFields(t *testing.T) {
	// A "todo" promote must not send agent_name/repo_url at all — omitempty is
	// what keeps the vault targets from carrying meaningless task fields.
	var raw map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewDecoder(r.Body).Decode(&raw)
		_ = json.NewEncoder(w).Encode(PromoteMessageResult{OK: true, Target: "todo", SHA: "s"})
	}))
	defer srv.Close()

	if _, err := NewClient(srv.URL, "").PromoteConversationMessage(
		"01CONV", "01MSG", "personal", PromoteMessageRequest{Target: "todo"},
	); err != nil {
		t.Fatalf("PromoteConversationMessage: %v", err)
	}
	for _, k := range []string{"agent_name", "repo_url", "title", "note", "tags"} {
		if _, present := raw[k]; present {
			t.Errorf("body carries empty %q; want it omitted", k)
		}
	}
}

func TestPromoteConversationMessageEscapesIDs(t *testing.T) {
	var gotPath string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.EscapedPath()
		_ = json.NewEncoder(w).Encode(PromoteMessageResult{OK: true})
	}))
	defer srv.Close()

	if _, err := NewClient(srv.URL, "").PromoteConversationMessage(
		"a/b", "c/d", "personal", PromoteMessageRequest{Target: "memory"},
	); err != nil {
		t.Fatalf("PromoteConversationMessage: %v", err)
	}
	if gotPath != "/api/conversations/a%2Fb/messages/c%2Fd/promote" {
		t.Errorf("path = %q, want both ids percent-escaped", gotPath)
	}
}

func TestPromoteConversationMessageSurfacesServerError(t *testing.T) {
	// brainbox answers 400 for an unconfigured vault. The operator must see
	// that, not a silent no-op.
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusBadRequest)
		_, _ = w.Write([]byte(`{"detail":"profile 'personal' has no CL_TODO_API_TOKEN configured"}`))
	}))
	defer srv.Close()

	res, err := NewClient(srv.URL, "").PromoteConversationMessage(
		"01CONV", "01MSG", "personal", PromoteMessageRequest{Target: "todo"},
	)
	if err == nil {
		t.Fatalf("expected an error, got result %+v", res)
	}
	if !strings.Contains(err.Error(), "CL_TODO_API_TOKEN") {
		t.Errorf("error = %v, want it to carry the server's detail", err)
	}
}

func TestConversationPathAlwaysCarriesProfile(t *testing.T) {
	// Guards the invariant the whole file relies on: no conversation call can
	// forget the profile.
	got := conversationPath("/01CONV/messages/01MSG/promote", "work", nil)
	u, err := url.Parse(got)
	if err != nil {
		t.Fatalf("parse %q: %v", got, err)
	}
	if u.Query().Get("profile") != "work" {
		t.Errorf("profile missing from %q", got)
	}
}

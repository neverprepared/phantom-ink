package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"

	"phantom-ink/brainbox"
	"phantom-ink/provider"
	"phantom-ink/provider/github"
)

// The ticket list must not wait on the provider fan-out just to decorate rows
// with the pull requests that mention them. These tests pin the two halves:
// the list paints without refs and says so, and a second call attaches them.

// jiraRefsApp wires an App whose Jira is enabled, whose gateway env points at
// a fake Jira, and whose GitHub fan-out is a local server we can count and
// stall. Returns the app and the fan-out counter.
func jiraRefsApp(t *testing.T, prTitle string) (*App, *atomic.Int64) {
	t.Helper()

	jiraSrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{
			"issues": []map[string]any{
				{"key": "ABC-1", "fields": map[string]any{"summary": "first"}},
				{"key": "ABC-2", "fields": map[string]any{"summary": "second"}},
			},
		})
	}))
	t.Cleanup(jiraSrv.Close)

	var repoCalls atomic.Int64
	ghSrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/user/repos":
			repoCalls.Add(1)
			_, _ = w.Write([]byte(`[{"full_name":"org/repo","clone_url":"https://github.com/org/repo.git"}]`))
		case r.URL.Path == "/search/issues":
			_, _ = w.Write([]byte(`{"items":[{"number":7,"title":"` + prTitle + `","html_url":"u7","state":"open","repository_url":"https://api.github.com/repos/org/repo"}]}`))
		default:
			_, _ = w.Write([]byte(`[]`))
		}
	}))
	t.Cleanup(ghSrv.Close)

	envSrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{"profile": "work", "env": map[string]string{
			"JIRA_URL":       jiraSrv.URL,
			"JIRA_USERNAME":  "me@work.com",
			"JIRA_API_TOKEN": "tok",
			"GITHUB_TOKEN":   "gh",
		}})
	}))
	t.Cleanup(envSrv.Close)

	orig := newGitHubClient
	newGitHubClient = func(token string) provider.Client { return github.NewWithBase(ghSrv.URL, token, nil) }
	t.Cleanup(func() { newGitHubClient = orig })

	a := newJiraTestApp(t)
	a.ctx = context.Background()
	a.client = brainbox.NewClient(envSrv.URL, "")
	if err := a.SetJiraEnabled(true); err != nil {
		t.Fatalf("enable jira: %v", err)
	}
	if err := a.SetJiraEnabledForProfile("work", true); err != nil {
		t.Fatalf("opt in: %v", err)
	}

	codeOverviewMemo.invalidate("")
	jiraTicketsMemo.invalidate("")
	t.Cleanup(func() {
		codeOverviewMemo.invalidate("")
		jiraTicketsMemo.invalidate("")
	})
	return a, &repoCalls
}

func TestJiraTicketsPaintsWithoutWaitingForTheFanOut(t *testing.T) {
	a, repoCalls := jiraRefsApp(t, "ABC-1: add the webhook")

	res, err := a.JiraTickets("work", false)
	if err != nil {
		t.Fatalf("JiraTickets: %v", err)
	}

	if len(res.Tickets) != 2 {
		t.Fatalf("tickets = %d, want 2", len(res.Tickets))
	}
	if !res.RefsPending {
		t.Fatal("RefsPending = false; the frontend has no signal to fetch refs")
	}
	if n := repoCalls.Load(); n != 0 {
		t.Fatalf("fan-outs = %d, want 0: the list must not block on the providers", n)
	}
	for _, tk := range res.Tickets {
		if len(tk.Refs) != 0 {
			t.Fatalf("%s already has refs; nothing has fanned out yet", tk.Key)
		}
	}
}

func TestJiraTicketRefsAttachesTheCodeAndClearsPending(t *testing.T) {
	a, _ := jiraRefsApp(t, "ABC-1: add the webhook")

	if _, err := a.JiraTickets("work", false); err != nil {
		t.Fatalf("tickets: %v", err)
	}

	refs, err := a.JiraTicketRefs("work")
	if err != nil {
		t.Fatalf("JiraTicketRefs: %v", err)
	}

	got := refs.Refs["ABC-1"]
	if len(got) != 1 {
		t.Fatalf("ABC-1 refs = %d, want 1 (derived from the PR title)", len(got))
	}
	if got[0].Number != 7 || got[0].Repo != "org/repo" {
		t.Fatalf("ref = %+v", got[0])
	}
	if len(refs.Refs["ABC-2"]) != 0 {
		t.Fatal("ABC-2 has no PR mentioning it and must get no refs")
	}
}

// Folding the refs back into the memo is what stops a tab switch inside the
// TTL from repainting rows that have already been decorated once.
func TestJiraTicketRefsFoldsBackIntoTheMemo(t *testing.T) {
	a, _ := jiraRefsApp(t, "ABC-1: add the webhook")

	if _, err := a.JiraTickets("work", false); err != nil {
		t.Fatalf("tickets: %v", err)
	}
	if _, err := a.JiraTicketRefs("work"); err != nil {
		t.Fatalf("refs: %v", err)
	}

	again, err := a.JiraTickets("work", false)
	if err != nil {
		t.Fatalf("second read: %v", err)
	}
	if again.RefsPending {
		t.Fatal("RefsPending still set after the refs landed")
	}
	byKey := map[string]JiraTicket{}
	for _, tk := range again.Tickets {
		byKey[tk.Key] = tk
	}
	if len(byKey["ABC-1"].Refs) != 1 {
		t.Fatal("the memoised tickets did not keep the refs")
	}
}

// A warm overview means the refs are free, so the first read should carry them
// rather than making the frontend ask twice for no reason.
func TestJiraTicketsUsesAWarmOverviewImmediately(t *testing.T) {
	a, _ := jiraRefsApp(t, "ABC-1: add the webhook")

	// Whatever warmed the code panel first (its own effect, or a previous
	// visit) leaves the overview in the memo.
	if _, err := a.CodeOverview("work", false); err != nil {
		t.Fatalf("warm overview: %v", err)
	}

	res, err := a.JiraTickets("work", false)
	if err != nil {
		t.Fatalf("tickets: %v", err)
	}
	if res.RefsPending {
		t.Fatal("RefsPending set even though the overview was already cached")
	}
	byKey := map[string]JiraTicket{}
	for _, tk := range res.Tickets {
		byKey[tk.Key] = tk
	}
	if len(byKey["ABC-1"].Refs) != 1 {
		t.Fatal("a warm overview must decorate the first read")
	}
}

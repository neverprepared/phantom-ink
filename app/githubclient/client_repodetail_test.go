package githubclient

import (
	"context"
	"encoding/base64"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestListBranches(t *testing.T) {
	c, last := stub(t, map[string]string{
		"/repos/acme/phantom-ink/branches": `[
			{"name":"main","commit":{"sha":"aaa111"}},
			{"name":"feat/code-detail","commit":{"sha":"bbb222"}}
		]`,
	})
	bs, err := c.ListBranches(context.Background(), "tok", "acme", "phantom-ink")
	if err != nil {
		t.Fatalf("ListBranches: %v", err)
	}
	if len(bs) != 2 {
		t.Fatalf("want 2 branches, got %d", len(bs))
	}
	if bs[0].Name != "main" || bs[0].SHA != "aaa111" {
		t.Errorf("branch 0 wrong: %+v", bs[0])
	}
	if bs[1].Name != "feat/code-detail" || bs[1].SHA != "bbb222" {
		t.Errorf("branch 1 wrong: %+v", bs[1])
	}
	if got := last.URL.Query().Get("per_page"); got != "50" {
		t.Errorf("per_page = %q", got)
	}
	if got := last.Header.Get("Authorization"); got != "Bearer tok" {
		t.Errorf("auth header = %q", got)
	}
}

const commitsBody = `[{
	"sha":"deadbeef",
	"html_url":"https://github.com/acme/phantom-ink/commit/deadbeef",
	"commit":{
		"message":"feat(code): detail view\n\nLonger body that the UI hides.",
		"author":{"name":"Ada Lovelace","date":"2026-09-12T10:00:00Z"}
	},
	"author":{"login":"ada"}
},{
	"sha":"cafe",
	"html_url":"https://github.com/acme/phantom-ink/commit/cafe",
	"commit":{
		"message":"chore: bump deps",
		"author":{"name":"Unlinked Author","date":"2026-09-11T10:00:00Z"}
	},
	"author":null
}]`

func TestListRecentCommits(t *testing.T) {
	c, last := stub(t, map[string]string{"/repos/acme/phantom-ink/commits": commitsBody})
	cs, err := c.ListRecentCommits(context.Background(), "tok", "acme", "phantom-ink", 5)
	if err != nil {
		t.Fatalf("ListRecentCommits: %v", err)
	}
	if len(cs) != 2 {
		t.Fatalf("want 2 commits, got %d", len(cs))
	}
	// The full message survives; only the panel trims to the first line.
	if cs[0].Message != "feat(code): detail view\n\nLonger body that the UI hides." {
		t.Errorf("message must be kept whole, got %q", cs[0].Message)
	}
	if cs[0].Author != "ada" {
		t.Errorf("author must prefer the github login, got %q", cs[0].Author)
	}
	if cs[0].Date != "2026-09-12T10:00:00Z" || cs[0].SHA != "deadbeef" {
		t.Errorf("fields wrong: %+v", cs[0])
	}
	if cs[0].HTMLURL != "https://github.com/acme/phantom-ink/commit/deadbeef" {
		t.Errorf("html_url = %q", cs[0].HTMLURL)
	}
	// A commit whose email isn't linked to an account has a null top-level
	// author — the git trailer name is all there is.
	if cs[1].Author != "Unlinked Author" {
		t.Errorf("null author must fall back to the git name, got %q", cs[1].Author)
	}
	if got := last.URL.Query().Get("per_page"); got != "5" {
		t.Errorf("per_page = %q", got)
	}
}

func TestListRecentCommits_LimitBounds(t *testing.T) {
	c, last := stub(t, map[string]string{"/repos/a/b/commits": `[]`})
	if _, err := c.ListRecentCommits(context.Background(), "tok", "a", "b", 0); err != nil {
		t.Fatalf("limit 0: %v", err)
	}
	if got := last.URL.Query().Get("per_page"); got != "30" {
		t.Errorf("non-positive limit must default to 30, got %q", got)
	}
	if _, err := c.ListRecentCommits(context.Background(), "tok", "a", "b", 5000); err != nil {
		t.Fatalf("huge limit: %v", err)
	}
	if got := last.URL.Query().Get("per_page"); got != "100" {
		t.Errorf("limit must be capped at GitHub's 100, got %q", got)
	}
}

func TestGetReadme(t *testing.T) {
	md := "# phantom-ink\n\nA desk app.\n"
	// GitHub wraps the base64 payload at 60 columns; the newlines are not
	// valid base64 and must be stripped before decoding.
	enc := base64.StdEncoding.EncodeToString([]byte(md))
	wrapped := ""
	for i := 0; i < len(enc); i += 10 {
		end := i + 10
		if end > len(enc) {
			end = len(enc)
		}
		// `\n` escaped for the JSON literal below — it is a newline once decoded.
		wrapped += enc[i:end] + `\n`
	}
	c, last := stub(t, map[string]string{
		"/repos/acme/phantom-ink/readme": `{"encoding":"base64","content":"` + wrapped + `",
			"html_url":"https://github.com/acme/phantom-ink/blob/main/README.md"}`,
	})
	got, htmlURL, err := c.GetReadme(context.Background(), "tok", "acme", "phantom-ink")
	if err != nil {
		t.Fatalf("GetReadme: %v", err)
	}
	if got != md {
		t.Errorf("markdown = %q, want %q", got, md)
	}
	if htmlURL != "https://github.com/acme/phantom-ink/blob/main/README.md" {
		t.Errorf("html url = %q", htmlURL)
	}
	if last.URL.Path != "/repos/acme/phantom-ink/readme" {
		t.Errorf("path = %q", last.URL.Path)
	}
}

func TestGetReadme_MissingIsNotAnError(t *testing.T) {
	// stub 404s any path it has no body for — exactly what GitHub returns for
	// a repository with no README, which is a normal state.
	c, _ := stub(t, map[string]string{})
	md, htmlURL, err := c.GetReadme(context.Background(), "tok", "acme", "bare")
	if err != nil {
		t.Fatalf("a repo with no README must not be an error, got %v", err)
	}
	if md != "" || htmlURL != "" {
		t.Errorf("want empty readme, got %q / %q", md, htmlURL)
	}
}

func TestGetReadme_UnsupportedEncodingIsAnError(t *testing.T) {
	c, _ := stub(t, map[string]string{
		"/repos/a/b/readme": `{"encoding":"none","content":"","html_url":"x"}`,
	})
	if _, _, err := c.GetReadme(context.Background(), "tok", "a", "b"); err == nil {
		t.Fatal("a non-base64 encoding must be reported, not decoded blindly")
	}
}

func TestGetReadme_UndecodableContentIsAnError(t *testing.T) {
	c, _ := stub(t, map[string]string{
		"/repos/a/b/readme": `{"encoding":"base64","content":"!!not base64!!","html_url":"x"}`,
	})
	if _, _, err := c.GetReadme(context.Background(), "tok", "a", "b"); err == nil {
		t.Fatal("undecodable content must be an error")
	}
}

func TestSearchPRsByRepo(t *testing.T) {
	c, last := stub(t, map[string]string{"/search/issues": searchPRBody})
	prs, err := c.SearchPRsByRepo(context.Background(), "tok", "acme", "phantom-ink")
	if err != nil {
		t.Fatalf("SearchPRsByRepo: %v", err)
	}
	if len(prs) != 1 || !prs[0].IsPullRequest {
		t.Fatalf("want 1 pr, got %+v", prs)
	}
	if prs[0].RepoFullName != "acme/phantom-ink" || prs[0].Number != 41 {
		t.Errorf("fields wrong: %+v", prs[0])
	}
	// Repo-scoped rows have no "why you're seeing this" reason to badge.
	if prs[0].Reason != "" {
		t.Errorf("repo-scoped rows carry no reason, got %q", prs[0].Reason)
	}
	if q := last.URL.Query().Get("q"); q != "is:open is:pr repo:acme/phantom-ink" {
		t.Errorf("q = %q", q)
	}
}

func TestSearchIssuesByRepo(t *testing.T) {
	c, last := stub(t, map[string]string{"/search/issues": `{"items":[{
		"number":9,"title":"Panel blanks on 401","state":"open",
		"html_url":"https://github.com/acme/phantom-ink/issues/9",
		"updated_at":"2026-09-12T08:00:00Z",
		"repository_url":"https://api.github.com/repos/acme/phantom-ink",
		"user":{"login":"neo"}
	}]}`})
	issues, err := c.SearchIssuesByRepo(context.Background(), "tok", "acme", "phantom-ink")
	if err != nil {
		t.Fatalf("SearchIssuesByRepo: %v", err)
	}
	if len(issues) != 1 || issues[0].IsPullRequest {
		t.Fatalf("want 1 non-pr issue, got %+v", issues)
	}
	// is:issue is what keeps PRs (which GitHub also counts as issues) out.
	if q := last.URL.Query().Get("q"); q != "is:open is:issue repo:acme/phantom-ink" {
		t.Errorf("q = %q", q)
	}
}

// Every repo-detail read must surface a rejected credential as such, so one
// 401 flips the panel to its reconnect banner instead of five mystery errors.
func TestRepoDetailReads_UnauthorizedIsTyped(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusUnauthorized)
		_, _ = w.Write([]byte(`{"message":"Bad credentials"}`))
	}))
	t.Cleanup(srv.Close)
	c := NewWithBase(srv.URL, srv.Client())
	ctx := context.Background()

	calls := map[string]func() error{
		"ListBranches":       func() error { _, err := c.ListBranches(ctx, "bad", "a", "b"); return err },
		"ListRecentCommits":  func() error { _, err := c.ListRecentCommits(ctx, "bad", "a", "b", 5); return err },
		"GetReadme":          func() error { _, _, err := c.GetReadme(ctx, "bad", "a", "b"); return err },
		"SearchPRsByRepo":    func() error { _, err := c.SearchPRsByRepo(ctx, "bad", "a", "b"); return err },
		"SearchIssuesByRepo": func() error { _, err := c.SearchIssuesByRepo(ctx, "bad", "a", "b"); return err },
	}
	for name, call := range calls {
		err := call()
		if err == nil {
			t.Errorf("%s: 401 must be an error", name)
			continue
		}
		if !IsUnauthorized(err) {
			t.Errorf("%s: 401 must be detectable as unauthorized, got %v", name, err)
		}
	}
}

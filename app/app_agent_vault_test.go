package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"strconv"
	"strings"
	"testing"
)

// chainStub serves /api/brain/history/{sha} for a fixed chain and
// /api/brain/fetch/{sha} for each member, so vaultHistory's enrichment has
// something to read.
func chainStub(t *testing.T, chain []string, bodies map[string]string, dates map[string]string) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer tok" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		switch {
		case strings.HasPrefix(r.URL.Path, "/api/brain/history/"):
			entries := make([]map[string]any, 0, len(chain))
			for i, s := range chain {
				entries = append(entries, map[string]any{
					"sha": s, "title": "docker-patterns", "kind": "skill",
					"superseded": i < len(chain)-1, "is_head": i == len(chain)-1,
				})
			}
			_ = json.NewEncoder(w).Encode(map[string]any{
				"chain": entries, "head_sha": chain[len(chain)-1],
			})
		case strings.HasPrefix(r.URL.Path, "/api/brain/fetch/"):
			sha := strings.TrimPrefix(r.URL.Path, "/api/brain/fetch/")
			body, ok := bodies[sha]
			if !ok {
				w.WriteHeader(http.StatusNotFound)
				return
			}
			_ = json.NewEncoder(w).Encode(map[string]any{
				"sha": sha, "title": "docker-patterns", "kind": "skill",
				"body": body, "updated_at": dates[sha],
			})
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
}

func TestVaultHistoryNewestFirstWithStableVersionNumbers(t *testing.T) {
	chain := []string{"aaa", "bbb", "ccc"}
	bodies := map[string]string{"aaa": "v1 body", "bbb": "v2 body", "ccc": "v3 body"}
	dates := map[string]string{
		"aaa": "2026-09-01T00:00:00Z",
		"bbb": "2026-10-01T00:00:00Z",
		"ccc": "2026-10-19T00:00:00Z",
	}
	srv := chainStub(t, chain, bodies, dates)
	defer srv.Close()

	got, err := vaultHistory(srv.URL, "skills", "tok", "bbb")
	if err != nil {
		t.Fatalf("vaultHistory: %v", err)
	}
	if len(got) != 3 {
		t.Fatalf("want 3 versions, got %d", len(got))
	}
	// Newest first, so the panel can default to index 0 = head.
	if !got[0].IsHead || got[0].SHA != "ccc" {
		t.Errorf("want head ccc first, got %+v", got[0])
	}
	// Version numbers count from the OLDEST, independent of display order.
	if got[0].Version != 3 || got[1].Version != 2 || got[2].Version != 1 {
		t.Errorf("want versions 3,2,1 newest-first, got %d,%d,%d",
			got[0].Version, got[1].Version, got[2].Version)
	}
	if !got[2].Superseded || got[0].Superseded {
		t.Errorf("superseded flags wrong: oldest=%v head=%v", got[2].Superseded, got[0].Superseded)
	}
	// Timestamps come from the enrichment fetch, not the history response.
	if got[0].UpdatedAt.IsZero() || got[2].UpdatedAt.IsZero() {
		t.Errorf("want enriched timestamps, got head=%v oldest=%v", got[0].UpdatedAt, got[2].UpdatedAt)
	}
	if !got[2].UpdatedAt.Before(got[0].UpdatedAt) {
		t.Errorf("oldest version should predate the head")
	}
}

// A chain member whose record was forgotten leaves the link but no row. The
// entry must survive with a zero timestamp rather than failing the whole call.
func TestVaultHistoryToleratesMissingRecord(t *testing.T) {
	chain := []string{"aaa", "bbb"}
	bodies := map[string]string{"bbb": "head body"} // "aaa" forgotten
	srv := chainStub(t, chain, bodies, map[string]string{"bbb": "2026-10-19T00:00:00Z"})
	defer srv.Close()

	got, err := vaultHistory(srv.URL, "skills", "tok", "bbb")
	if err != nil {
		t.Fatalf("vaultHistory: %v", err)
	}
	if len(got) != 2 {
		t.Fatalf("want 2 versions, got %d", len(got))
	}
	if !got[1].UpdatedAt.IsZero() {
		t.Errorf("forgotten version should have a zero timestamp, got %v", got[1].UpdatedAt)
	}
}

func TestVaultHistorySingleVersion(t *testing.T) {
	srv := chainStub(t, []string{"aaa"}, map[string]string{"aaa": "only"},
		map[string]string{"aaa": "2026-10-19T00:00:00Z"})
	defer srv.Close()

	got, err := vaultHistory(srv.URL, "skills", "tok", "aaa")
	if err != nil {
		t.Fatalf("vaultHistory: %v", err)
	}
	if len(got) != 1 || !got[0].IsHead || got[0].Version != 1 {
		t.Fatalf("want a single head v1, got %+v", got)
	}
}

// Superseded bodies are the whole point: the head-only list cannot serve them.
func TestFetchVaultRecordReadsSupersededBody(t *testing.T) {
	srv := chainStub(t, []string{"aaa", "bbb"},
		map[string]string{"aaa": "v1 body", "bbb": "v2 body"},
		map[string]string{"aaa": "2026-09-01T00:00:00Z", "bbb": "2026-10-19T00:00:00Z"})
	defer srv.Close()

	rec, err := fetchVaultRecord(srv.URL, "skills", "tok", "aaa")
	if err != nil {
		t.Fatalf("fetchVaultRecord: %v", err)
	}
	if rec.Body != "v1 body" {
		t.Errorf("want the superseded body, got %q", rec.Body)
	}
}

// The browser switches on "token rejected" to re-prompt for a token, so a 401
// must keep that wording.
func TestVaultGetJSONClassifiesErrors(t *testing.T) {
	srv := chainStub(t, []string{"aaa"}, map[string]string{"aaa": "x"}, map[string]string{})
	defer srv.Close()

	if _, err := vaultHistory(srv.URL, "skills", "wrong-token", "aaa"); err == nil ||
		!strings.Contains(err.Error(), "token rejected") {
		t.Errorf("want a token-rejected error, got %v", err)
	}
	if _, err := fetchVaultRecord(srv.URL, "skills", "tok", "missing"); err == nil ||
		!strings.Contains(err.Error(), "not found") {
		t.Errorf("want a not-found error, got %v", err)
	}
	if _, err := vaultHistory(srv.URL, "skills", "tok", "  "); err == nil {
		t.Errorf("want an error for an empty sha")
	}
}

// A profile-to-profile copy or move must carry force. The destination can hold
// a TOMBSTONE for the same sha — identity is content-addressed, so a forgotten
// record keeps its row and its sha — and phantom-brain refuses an unforced
// write onto one with a 409. Without force, moving a record back to a profile
// it was previously moved out of fails and the panel has no way to recover.
func TestLearnRecordSendsForce(t *testing.T) {
	var gotForce any
	var sawForceKey bool
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var payload map[string]any
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			t.Errorf("decode learn payload: %v", err)
		}
		gotForce, sawForceKey = payload["force"]
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	if err := learnRecord(srv.URL, "tok", "aaa", "t", "b", nil, true); err != nil {
		t.Fatalf("learnRecord: %v", err)
	}
	if !sawForceKey {
		t.Fatal("learn payload carried no force key; a write onto a tombstone will 409")
	}
	if gotForce != true {
		t.Errorf("want force=true, got %#v", gotForce)
	}
}

// force is opt-in: an unforced call must not send the key at all, so a plain
// write keeps the daemon's refuse-on-tombstone behavior.
func TestLearnRecordOmitsForceWhenNotRequested(t *testing.T) {
	var sawForceKey bool
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var payload map[string]any
		_ = json.NewDecoder(r.Body).Decode(&payload)
		_, sawForceKey = payload["force"]
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	if err := learnRecord(srv.URL, "tok", "aaa", "t", "b", nil, false); err != nil {
		t.Fatalf("learnRecord: %v", err)
	}
	if sawForceKey {
		t.Error("unforced learn must omit force entirely")
	}
}

// A 409 that survives force must name the cause, not surface a bare status.
func TestLearnRecordExplains409(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusConflict)
	}))
	defer srv.Close()

	err := learnRecord(srv.URL, "tok", "aaa", "t", "b", nil, true)
	if err == nil {
		t.Fatal("want an error on 409")
	}
	if !strings.Contains(err.Error(), "forgotten record") {
		t.Errorf("409 error should explain the tombstone, got: %v", err)
	}
}

// The tests above prove learnRecord honors force; this one proves copyOrMove
// ASKS for it. That is the call site the bug lives at, and it needs a mesh
// integration plus a token database to exercise directly, so assert it against
// the source. A future edit that drops the flag fails here instead of silently
// reintroducing the 409 on move-back.
func TestCopyOrMoveRequestsForce(t *testing.T) {
	src, err := os.ReadFile("app_agent_vault.go")
	if err != nil {
		t.Fatalf("read source: %v", err)
	}
	const want = "a.learnWithToken(destTok, rec.SHA, rec.Title, rec.Body, rec.Tags, true)"
	if !strings.Contains(string(src), want) {
		t.Errorf("copyOrMove must pass force=true to learnWithToken.\n"+
			"Looked for: %s\n"+
			"Without it, moving a record back into a profile that still holds its "+
			"tombstone fails with HTTP 409 and the panel offers no way out.", want)
	}
}

// pagedRecordsStub serves /api/brain/records in pages of `pageSize` out of
// `total` records, with the daemon's cursor convention: next_after_id is the id
// to resume from, and 0 on the last page.
func pagedRecordsStub(t *testing.T, total, pageSize int, wantKind string) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer tok" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		if r.URL.Path != "/api/brain/records" {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		q := r.URL.Query()
		if q.Get("head") != "true" {
			t.Errorf("count must list heads only, got head=%q", q.Get("head"))
		}
		if wantKind != "" && q.Get("kind") != wantKind {
			t.Errorf("want kind=%q, got %q", wantKind, q.Get("kind"))
		}
		after, _ := strconv.Atoi(q.Get("after_id"))
		n := total - after
		if n > pageSize {
			n = pageSize
		}
		if n < 0 {
			n = 0
		}
		recs := make([]map[string]any, 0, n)
		for i := 0; i < n; i++ {
			recs = append(recs, map[string]any{"sha": fmt.Sprintf("s%d", after+i)})
		}
		next := 0
		if after+n < total {
			next = after + n
		}
		_ = json.NewEncoder(w).Encode(map[string]any{
			"records": recs, "next_after_id": next,
		})
	}))
}

// The badge must survive a vault outgrowing one page: the daemon clamps limit
// to 500, so a single request would silently cap the count.
func TestCountVaultHeadRecordsPagesPastTheServerCap(t *testing.T) {
	srv := pagedRecordsStub(t, 1203, 500, "skill")
	defer srv.Close()

	got, truncated, err := countVaultHeadRecords(srv.URL, "skills", "tok")
	if err != nil {
		t.Fatalf("countVaultHeadRecords: %v", err)
	}
	if got != 1203 {
		t.Errorf("want 1203 records, got %d", got)
	}
	if truncated {
		t.Errorf("a count that ran the cursor to the end is not truncated")
	}
}

func TestCountVaultHeadRecordsSinglePage(t *testing.T) {
	srv := pagedRecordsStub(t, 87, 500, "skill")
	defer srv.Close()

	got, truncated, err := countVaultHeadRecords(srv.URL, "skills", "tok")
	if err != nil {
		t.Fatalf("countVaultHeadRecords: %v", err)
	}
	if got != 87 || truncated {
		t.Errorf("want 87 not truncated, got %d truncated=%v", got, truncated)
	}
}

// A daemon that keeps handing back the same cursor must not spin forever.
func TestCountVaultHeadRecordsStopsOnNonAdvancingCursor(t *testing.T) {
	hits := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits++
		_ = json.NewEncoder(w).Encode(map[string]any{
			"records":       []map[string]any{{"sha": "a"}, {"sha": "b"}},
			"next_after_id": 0, // never advances past the first page's start
		})
	}))
	defer srv.Close()

	got, truncated, err := countVaultHeadRecords(srv.URL, "skills", "tok")
	if err != nil {
		t.Fatalf("countVaultHeadRecords: %v", err)
	}
	if hits != 1 {
		t.Errorf("want one request against a non-advancing cursor, got %d", hits)
	}
	if got != 2 || truncated {
		t.Errorf("want 2 not truncated, got %d truncated=%v", got, truncated)
	}
}

// Truncated is the honest answer when the page cap is reached: Count is a floor
// and the panel renders it "N+", never as a total.
func TestCountVaultHeadRecordsReportsTruncationAtThePageCap(t *testing.T) {
	srv := pagedRecordsStub(t, 500*countVaultHeadPages+10, 500, "skill")
	defer srv.Close()

	got, truncated, err := countVaultHeadRecords(srv.URL, "skills", "tok")
	if err != nil {
		t.Fatalf("countVaultHeadRecords: %v", err)
	}
	if !truncated {
		t.Errorf("want truncated at the page cap")
	}
	if got != 500*countVaultHeadPages {
		t.Errorf("want the floor %d, got %d", 500*countVaultHeadPages, got)
	}
}

// A rejected token must error, not count zero: a badge reading 0 would say the
// vault is empty when we simply could not read it.
func TestCountVaultHeadRecordsErrorsOn401(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnauthorized)
	}))
	defer srv.Close()

	if _, _, err := countVaultHeadRecords(srv.URL, "skills", "bad"); err == nil {
		t.Fatalf("want an error for a rejected token, got none")
	}
}

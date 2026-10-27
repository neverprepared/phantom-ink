package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
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

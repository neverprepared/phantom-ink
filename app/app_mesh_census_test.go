package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

// buildMeshCensus is the whole point of the feature: deciding whether the nodes
// AGREE. Every interesting case is about what counts as disagreement, so it is
// a pure function and these are plain unit tests.
func TestBuildMeshCensus(t *testing.T) {
	cnt := func(profile, vault string, live, tomb int) MeshVaultCount {
		return MeshVaultCount{Profile: profile, Vault: vault, Live: live, Tombstoned: tomb}
	}

	t.Run("nodes that agree are not flagged", func(t *testing.T) {
		got := buildMeshCensus([]nodeReport{
			{NodeID: "ncm5", Reachable: true, Counts: []MeshVaultCount{cnt("personal", "memory", 4601, 47)}},
			{NodeID: "m3", Reachable: true, Counts: []MeshVaultCount{cnt("personal", "memory", 4601, 47)}},
		})
		if len(got.Rows) != 1 {
			t.Fatalf("want 1 row, got %d", len(got.Rows))
		}
		if got.Rows[0].Diverged {
			t.Errorf("identical counts must not be flagged as diverged: %+v", got.Rows[0])
		}
		if got.Rows[0].Live != 4601 {
			t.Errorf("row Live should be the local node's count, got %d", got.Rows[0].Live)
		}
	})

	t.Run("a differing live count IS divergence", func(t *testing.T) {
		got := buildMeshCensus([]nodeReport{
			{NodeID: "ncm5", Reachable: true, Counts: []MeshVaultCount{cnt("personal", "memory", 4601, 47)}},
			{NodeID: "storage", Reachable: true, Counts: []MeshVaultCount{cnt("personal", "memory", 4580, 47)}},
		})
		if !got.Rows[0].Diverged {
			t.Errorf("4601 vs 4580 must be flagged as diverged: %+v", got.Rows[0])
		}
	})

	// The distinction that makes the panel trustworthy. An unreachable node
	// tells us NOTHING, so it can neither be rendered as 0 nor counted as
	// disagreement -- both would invent a data-loss incident out of a network
	// blip.
	t.Run("an unreachable node is unknown, not zero and not divergence", func(t *testing.T) {
		got := buildMeshCensus([]nodeReport{
			{NodeID: "ncm5", Reachable: true, Counts: []MeshVaultCount{cnt("personal", "memory", 4601, 47)}},
			{NodeID: "m3", Reachable: false, Note: "unreachable"},
		})
		if got.Rows[0].Diverged {
			t.Errorf("an unreachable peer must not read as divergence: %+v", got.Rows[0])
		}
		var m3 *MeshCountCell
		for i := range got.Rows[0].Cells {
			if got.Rows[0].Cells[i].NodeID == "m3" {
				m3 = &got.Rows[0].Cells[i]
			}
		}
		if m3 == nil {
			t.Fatal("the unreachable node must still get a cell, so the column exists")
		}
		if m3.Known {
			t.Error("an unreachable node's cell must be Known=false so the UI renders a dash, never 0")
		}
		if len(got.Unreachable) != 1 || got.Unreachable[0] != "m3" {
			t.Errorf("want Unreachable=[m3], got %v", got.Unreachable)
		}
	})

	// Distinct from unreachable: the node ANSWERED and did not list the vault,
	// so it really holds none. That is a genuine disagreement.
	t.Run("a reachable node omitting a vault counts as zero, which is divergence", func(t *testing.T) {
		got := buildMeshCensus([]nodeReport{
			{NodeID: "ncm5", Reachable: true, Counts: []MeshVaultCount{cnt("personal", "todo", 168, 264)}},
			{NodeID: "m3", Reachable: true, Counts: []MeshVaultCount{}},
		})
		if !got.Rows[0].Diverged {
			t.Errorf("a reachable node holding none of a vault the local node has is divergence: %+v", got.Rows[0])
		}
		for _, c := range got.Rows[0].Cells {
			if c.NodeID == "m3" && !c.Known {
				t.Error("a node that answered has a KNOWN zero, not an unknown")
			}
		}
	})

	t.Run("tombstone divergence is flagged even when live agrees", func(t *testing.T) {
		got := buildMeshCensus([]nodeReport{
			{NodeID: "ncm5", Reachable: true, Counts: []MeshVaultCount{cnt("personal", "memory", 4601, 47)}},
			{NodeID: "storage", Reachable: true, Counts: []MeshVaultCount{cnt("personal", "memory", 4601, 18)}},
		})
		if !got.Rows[0].Diverged {
			t.Errorf("equal live but 47 vs 18 tombstones is still a mesh disagreement: %+v", got.Rows[0])
		}
	})

	t.Run("local node leads the column order and rows sort by profile then vault", func(t *testing.T) {
		got := buildMeshCensus([]nodeReport{
			{NodeID: "ncm5", Reachable: true, Counts: []MeshVaultCount{
				cnt("personal", "todo", 1, 0), cnt("gsa", "memory", 2, 0), cnt("personal", "agents", 3, 0),
			}},
			{NodeID: "m3", Reachable: true},
		})
		if got.LocalNodeID != "ncm5" || len(got.Nodes) == 0 || got.Nodes[0] != "ncm5" {
			t.Errorf("local node must lead: LocalNodeID=%q Nodes=%v", got.LocalNodeID, got.Nodes)
		}
		var order []string
		for _, r := range got.Rows {
			order = append(order, r.Profile+"/"+r.Vault)
		}
		want := []string{"gsa/memory", "personal/agents", "personal/todo"}
		for i := range want {
			if i >= len(order) || order[i] != want[i] {
				t.Fatalf("row order: got %v, want %v", order, want)
			}
		}
	})

	t.Run("no nodes yields an empty census rather than panicking", func(t *testing.T) {
		got := buildMeshCensus(nil)
		if len(got.Rows) != 0 || len(got.Nodes) != 0 {
			t.Errorf("want empty census, got %+v", got)
		}
	})
}

// fetchNodeCensus must survive the realistic failure shapes, since the panel
// calls it against every peer and one bad node cannot break the view.
func TestFetchNodeCensus(t *testing.T) {
	t.Run("reads node_id and counts from a healthy node", func(t *testing.T) {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.URL.Path != "/admin/mesh/status" {
				t.Errorf("unexpected path %q", r.URL.Path)
			}
			_ = json.NewEncoder(w).Encode(map[string]any{
				"node_id": "storage",
				"counts": []map[string]any{
					{"profile": "personal", "vault": "memory", "live": 4601, "tombstoned": 47},
				},
			})
		}))
		defer srv.Close()

		got := fetchNodeCensus(srv.URL, srv.Client())
		if !got.Reachable {
			t.Fatalf("want reachable, got note=%q", got.Note)
		}
		if got.NodeID != "storage" || len(got.Counts) != 1 || got.Counts[0].Live != 4601 {
			t.Errorf("decoded wrong: %+v", got)
		}
	})

	t.Run("an unreachable node reports not-reachable with a note, not an error", func(t *testing.T) {
		got := fetchNodeCensus("http://127.0.0.1:1", &http.Client{})
		if got.Reachable {
			t.Error("a dead address must not report reachable")
		}
		if got.Note == "" {
			t.Error("an unreachable node needs a note the panel can show")
		}
	})

	t.Run("an older daemon with no counts field is reachable with zero counts", func(t *testing.T) {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			_ = json.NewEncoder(w).Encode(map[string]any{"node_id": "old-node"})
		}))
		defer srv.Close()

		got := fetchNodeCensus(srv.URL, srv.Client())
		if !got.Reachable {
			t.Error("a daemon predating the counts field is still reachable")
		}
		if got.NodeID != "old-node" || len(got.Counts) != 0 {
			t.Errorf("want old-node with no counts, got %+v", got)
		}
	})

	t.Run("a non-2xx response is not reachable", func(t *testing.T) {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(http.StatusInternalServerError)
		}))
		defer srv.Close()

		got := fetchNodeCensus(srv.URL, srv.Client())
		if got.Reachable {
			t.Error("HTTP 500 must not read as reachable")
		}
	})
}

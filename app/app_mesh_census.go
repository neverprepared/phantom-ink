package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"sort"
	"sync"
	"time"
)

// MeshVaultCount mirrors nodesync.VaultCount in phantom-brain — one
// (profile, vault)'s record totals in ONE node's own store.
type MeshVaultCount struct {
	Profile    string `json:"profile"`
	Vault      string `json:"vault"`
	Live       int    `json:"live"`
	Tombstoned int    `json:"tombstoned"`
}

// MeshCountCell is one node's answer for one (profile, vault).
//
// Known is the field that makes this trustworthy. A node we could not reach
// tells us NOTHING, and rendering that as 0 would be indistinguishable from
// "this node lost every record" — so the panel shows a dash instead, and
// divergence is computed only across nodes that actually answered.
type MeshCountCell struct {
	NodeID     string `json:"node_id"`
	Known      bool   `json:"known"`
	Live       int    `json:"live"`
	Tombstoned int    `json:"tombstoned"`
}

// MeshCountRow is one (profile, vault) across every node.
//
// Live is the LOCAL node's count — the authoritative "how much do I have".
// There is deliberately no mesh-wide sum: the mesh keeps a full replica per
// node, so adding the cells would multiply the same records by the node count.
type MeshCountRow struct {
	Profile  string          `json:"profile"`
	Vault    string          `json:"vault"`
	Live     int             `json:"live"`
	Diverged bool            `json:"diverged"`
	Cells    []MeshCountCell `json:"cells"`
}

// MeshCensus is the whole comparison, ready to render.
type MeshCensus struct {
	LocalNodeID string         `json:"local_node_id"`
	Nodes       []string       `json:"nodes"`
	Rows        []MeshCountRow `json:"rows"`
	Unreachable []string       `json:"unreachable,omitempty"`
}

// nodeReport is one node's raw reply, before comparison.
type nodeReport struct {
	NodeID    string
	BaseURL   string
	Reachable bool
	Note      string
	Counts    []MeshVaultCount
}

// censusStatus is the subset of /admin/mesh/status the census needs. Decoding
// into its own narrow type rather than MeshStatus keeps the fan-out independent
// of fields it does not use.
type censusStatus struct {
	NodeID string           `json:"node_id"`
	Counts []MeshVaultCount `json:"counts"`
}

// fetchNodeCensus reads one node's census. Free function over its dependencies
// so it is testable without an App, matching postMeshAdmin.
//
// NEVER returns an error: the panel fans out across every node and one bad node
// must degrade to a dash in its column, not break the view. Every failure shape
// — dead address, non-2xx, unparseable body — lands as Reachable=false plus a
// note the panel can show.
func fetchNodeCensus(baseURL string, client *http.Client) nodeReport {
	rep := nodeReport{BaseURL: baseURL}

	resp, err := client.Get(baseURL + "/admin/mesh/status")
	if err != nil {
		rep.Note = "unreachable"
		return rep
	}
	defer resp.Body.Close()

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		rep.Note = fmt.Sprintf("HTTP %d", resp.StatusCode)
		return rep
	}

	var st censusStatus
	if err := json.NewDecoder(resp.Body).Decode(&st); err != nil {
		rep.Note = "unreadable status"
		return rep
	}

	// A daemon predating the counts field is reachable with an empty census,
	// not broken — the field is omitempty on the wire.
	rep.Reachable = true
	rep.NodeID = st.NodeID
	rep.Counts = st.Counts
	return rep
}

// buildMeshCensus pivots per-node replies into one row per (profile, vault),
// and decides which rows disagree.
//
// The first report is treated as local: its count is the row's headline figure
// and its id leads the column order.
//
// DIVERGENCE is computed only over nodes that ANSWERED, and covers tombstoned
// as well as live. Equal live counts with unequal tombstone counts still means
// the nodes hold different things — one has forgotten records the other has
// not — which is exactly the condition that is invisible in a live-only view.
func buildMeshCensus(reports []nodeReport) MeshCensus {
	out := MeshCensus{Nodes: []string{}, Rows: []MeshCountRow{}}
	if len(reports) == 0 {
		return out
	}
	out.LocalNodeID = reports[0].NodeID

	for _, r := range reports {
		out.Nodes = append(out.Nodes, r.NodeID)
		if !r.Reachable {
			out.Unreachable = append(out.Unreachable, r.NodeID)
		}
	}

	// Index every (profile, vault) each answering node reported, so a vault
	// present on one node and absent on another still gets a row.
	type pk struct{ profile, vault string }
	byNode := make(map[string]map[pk]MeshVaultCount, len(reports))
	keys := map[pk]bool{}
	for _, r := range reports {
		m := map[pk]MeshVaultCount{}
		for _, c := range r.Counts {
			k := pk{c.Profile, c.Vault}
			m[k] = c
			keys[k] = true
		}
		byNode[r.NodeID] = m
	}

	ordered := make([]pk, 0, len(keys))
	for k := range keys {
		ordered = append(ordered, k)
	}
	sort.Slice(ordered, func(i, j int) bool {
		if ordered[i].profile != ordered[j].profile {
			return ordered[i].profile < ordered[j].profile
		}
		return ordered[i].vault < ordered[j].vault
	})

	for _, k := range ordered {
		row := MeshCountRow{Profile: k.profile, Vault: k.vault}
		var first *MeshCountCell

		for _, r := range reports {
			cell := MeshCountCell{NodeID: r.NodeID}
			if r.Reachable {
				// Answered. A vault it did not list is a KNOWN zero — it
				// genuinely holds none — which is a real disagreement, unlike
				// an unreachable node's silence.
				c := byNode[r.NodeID][k]
				cell.Known = true
				cell.Live = c.Live
				cell.Tombstoned = c.Tombstoned
			}
			row.Cells = append(row.Cells, cell)

			if cell.Known {
				if first == nil {
					c := cell
					first = &c
				} else if cell.Live != first.Live || cell.Tombstoned != first.Tombstoned {
					row.Diverged = true
				}
			}
			if r.NodeID == out.LocalNodeID && cell.Known {
				row.Live = cell.Live
			}
		}
		out.Rows = append(out.Rows, row)
	}
	return out
}

// GetMeshCensus answers "what do I hold, and do my nodes agree?".
//
// Each node reports only its OWN store, so the comparison requires asking every
// node directly — the local daemon cannot answer for its peers. The peer list
// from the local status already carries each machine's base_url, and
// /admin/mesh/status is unauthenticated, so no vault tokens are involved.
//
// Peers are deduplicated by base_url first: the daemon reports one peer per
// (machine, profile, vault) triple, so a 3-profile × 4-vault mesh against 2
// machines is 24 rows pointing at 2 daemons. Fanning out per row would be 24
// requests for 2 answers.
func (a *App) GetMeshCensus() (MeshCensus, error) {
	local, err := a.GetMeshStatus()
	if err != nil {
		return MeshCensus{}, err
	}

	seen := map[string]bool{}
	var peerURLs []string
	for _, p := range local.Peers {
		if p.BaseURL == "" || seen[p.BaseURL] {
			continue
		}
		seen[p.BaseURL] = true
		peerURLs = append(peerURLs, p.BaseURL)
	}
	sort.Strings(peerURLs)

	client := &http.Client{Timeout: 8 * time.Second}

	reports := make([]nodeReport, len(peerURLs)+1)
	reports[0] = nodeReport{
		NodeID:    local.NodeID,
		BaseURL:   a.meshURL(),
		Reachable: true,
		Counts:    local.Counts,
	}

	// Concurrent: a single unreachable peer would otherwise add its full
	// timeout to the panel's load, serially.
	var wg sync.WaitGroup
	for i, u := range peerURLs {
		wg.Add(1)
		go func(slot int, url string) {
			defer wg.Done()
			r := fetchNodeCensus(url, client)
			if r.NodeID == "" {
				// Unreachable nodes cannot name themselves; the url keeps the
				// column identifiable instead of showing a blank header.
				r.NodeID = url
			}
			reports[slot] = r
		}(i+1, u)
	}
	wg.Wait()

	return buildMeshCensus(reports), nil
}

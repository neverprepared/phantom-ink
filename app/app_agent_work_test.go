package main

import (
	"testing"

	"phantom-ink/opensearch"
)

func TestMergeLivenessStampsMatchingSessions(t *testing.T) {
	hb := int64(264000)
	rows := []AgentWorkRow{
		{SessionID: "live", LastSeen: "2026-10-17T12:00:00Z"},
		{SessionID: "langfuse-only", LastSeen: "2026-10-17T11:00:00Z"},
	}
	live := []opensearch.AgentSession{
		{
			SessionID: "live", Liveness: opensearch.LivenessActive,
			LastActivityMS: 40000, LastHeartbeatMS: &hb,
			// Fresher than the Langfuse row: OpenSearch ingests in ~33s while
			// the Stop hook lags a whole turn.
			LastSeen: "2026-10-17T12:05:00Z",
		},
		{SessionID: "opensearch-only", Liveness: opensearch.LivenessIdle},
	}

	mergeLiveness(rows, live)

	if !rows[0].LivenessAvailable {
		t.Error("matching row did not get the overlay")
	}
	if rows[0].Liveness != opensearch.LivenessActive {
		t.Errorf("Liveness = %q, want active", rows[0].Liveness)
	}
	if rows[0].LastActivityMS != 40000 {
		t.Errorf("LastActivityMS = %d, want 40000", rows[0].LastActivityMS)
	}
	if rows[0].LastHeartbeatMS == nil || *rows[0].LastHeartbeatMS != 264000 {
		t.Error("heartbeat age not carried through")
	}
	// The fresher timestamp must win, or the panel reports a working session as
	// last active minutes ago.
	if rows[0].LastSeen != "2026-10-17T12:05:00Z" {
		t.Errorf("LastSeen = %q, want OpenSearch's fresher value", rows[0].LastSeen)
	}

	// A session Langfuse knows and OpenSearch does not must show NO dot rather
	// than defaulting to dormant, which would assert something unmeasured.
	if rows[1].LivenessAvailable {
		t.Error("unmatched row claims liveness is available")
	}
	if rows[1].Liveness != "" {
		t.Errorf("unmatched row Liveness = %q, want empty", rows[1].Liveness)
	}
	if rows[1].LastSeen != "2026-10-17T11:00:00Z" {
		t.Error("unmatched row's LastSeen was altered")
	}
}

// An OpenSearch session with no Langfuse counterpart must not appear: the list
// is Langfuse's, and inventing a row with no observations would show a session
// the drill-down cannot open.
func TestMergeLivenessNeverAddsRows(t *testing.T) {
	rows := []AgentWorkRow{{SessionID: "a"}}
	mergeLiveness(rows, []opensearch.AgentSession{
		{SessionID: "b"}, {SessionID: "c"},
	})
	if len(rows) != 1 || rows[0].SessionID != "a" {
		t.Errorf("rows = %+v, want the single Langfuse row untouched", rows)
	}
}

func TestMergeLivenessKeepsLangfuseTimestampWhenFresher(t *testing.T) {
	rows := []AgentWorkRow{{SessionID: "s", LastSeen: "2026-10-17T12:10:00Z"}}
	mergeLiveness(rows, []opensearch.AgentSession{
		{SessionID: "s", Liveness: opensearch.LivenessActive, LastSeen: "2026-10-17T12:00:00Z"},
	})
	if rows[0].LastSeen != "2026-10-17T12:10:00Z" {
		t.Errorf("LastSeen = %q; the newer of the two must win", rows[0].LastSeen)
	}
}

func TestMergeLivenessHandlesEmptyInputs(t *testing.T) {
	mergeLiveness(nil, nil)
	mergeLiveness([]AgentWorkRow{}, []opensearch.AgentSession{{SessionID: "x"}})
	rows := []AgentWorkRow{{SessionID: "x"}}
	mergeLiveness(rows, nil)
	if rows[0].LivenessAvailable {
		t.Error("no OpenSearch data must mean no overlay")
	}
}

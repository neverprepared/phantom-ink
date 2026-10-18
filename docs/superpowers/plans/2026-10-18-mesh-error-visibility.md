# Mesh Error Visibility and Local Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make mesh sync errors legible in the Brain Mesh panel — what broke, whether it is still broken — and give the operator three local-only buttons to act on them.

**Architecture:** The daemon keeps an in-memory ring of structured sync errors beside the existing counter and exposes it on the already-unauthenticated `/admin/mesh/status`; three remediation actions mount on the existing `PB_ADMIN_KEY`-gated admin group. The desktop app renders the ring grouped by (peer × failure class) with a per-class diagnosis, turns the red counter into an honest recency signal, and POSTs the actions with an `X-API-Key` header.

**Tech Stack:** Go 1.25 (both repos; `chi` router, `pgx/v5`), Svelte 5 runes + TypeScript, Wails 2 bindings.

**Spec:** `docs/superpowers/specs/2026-10-18-mesh-error-visibility-design.md` (in the phantom-ink repo)

## Global Constraints

- **Two repos.** Tasks 1–6 are in `/Users/cdowning/workspaces/profiles/personal/code/phantom-brain`. Tasks 7–10 are in `/Users/cdowning/workspaces/profiles/personal/code/phantom-ink`. Each task names its repo. Never commit across both in one commit.
- **Branches.** phantom-brain: `feat/mesh-error-log`. phantom-ink: `feat/mesh-error-remediation` (already exists, already holds the spec). Never commit to `main`.
- **phantom-brain builds with `make`, never bare `go build`** — it needs CGO and the `sqlite_fts5` tag. Tests run as `go test ./internal/... ` which is fine, but any build step uses `make`.
- **`/admin/mesh/status` stays unauthenticated and must never carry a secret.** No field added to `Report`, `PeerStatus`, or `SyncError` may contain a peer token.
- **No write endpoint is added under `/admin/mesh/`.** All three actions mount under `/api/brain/admin/` behind `d.adminAuthMiddleware()`.
- **Error ring capacity: 200 entries.** Recency window for the "is it live?" signal: **15 minutes**. Per-peer error window: **24 hours**.
- **Nil-safety is load-bearing.** `PullWorker.Errors` may be nil (existing construction sites and the CLI path). `Daemon.pullWorker` is nil in the test rig and whenever sync is disabled. Every new code path must tolerate both.
- **Class vocabulary is fixed** — exactly these strings, no others: `dns`, `timeout`, `refused`, `auth`, `proto`, `fenced`, `blob`, `pool`, `other`.
- **Phase vocabulary is fixed** — exactly these strings: `pool`, `pull`, `links`, `tombstone`, `blob`, `gc`.
- Go tests run with `-race`. Frontend checks run `npm run check` in `app/frontend`.

---

### Task 1: Error classifier

**Repo:** phantom-brain

**Files:**
- Create: `internal/nodesync/errorlog.go`
- Test: `internal/nodesync/errorlog_test.go`

**Interfaces:**
- Consumes: `ErrProtocolMismatch`, `ErrPeerFenced` (existing sentinels in `internal/nodesync/protocol.go` and `fence.go`).
- Produces: `func ClassifyError(phase string, err error) string` — returns one of the nine fixed class strings.

- [ ] **Step 1: Write the failing test**

Create `internal/nodesync/errorlog_test.go`:

```go
package nodesync

import (
	"errors"
	"fmt"
	"testing"
)

func TestClassifyError(t *testing.T) {
	cases := []struct {
		name  string
		phase string
		err   error
		want  string
	}{
		{"dns server misbehaving", "pull",
			errors.New(`Get "http://node16:9998/x": dial tcp: lookup node16 on 127.0.0.11:53: server misbehaving`), "dns"},
		{"dns no such host", "pull",
			errors.New(`dial tcp: lookup node64: no such host`), "dns"},
		{"timeout", "pull",
			errors.New("context deadline exceeded (Client.Timeout exceeded while awaiting headers)"), "timeout"},
		{"refused", "pull", errors.New("dial tcp 10.0.0.1:9998: connect: connection refused"), "refused"},
		{"auth 401", "pull", errors.New("peer export: status 401"), "auth"},
		{"auth 403", "links", errors.New("peer export: status 403"), "auth"},
		{"proto sentinel", "pull", fmt.Errorf("pull: %w", ErrProtocolMismatch), "proto"},
		{"fenced sentinel", "pull", fmt.Errorf("pull: %w", ErrPeerFenced), "fenced"},
		{"blob phase", "blob", errors.New("put object: boom"), "blob"},
		{"gc phase", "gc", errors.New("list objects: boom"), "blob"},
		{"pool phase", "pool", errors.New("no binding for profile"), "pool"},
		{"unmatched", "pull", errors.New("something nobody predicted"), "other"},
		{"nil error", "pull", nil, "other"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := ClassifyError(tc.phase, tc.err); got != tc.want {
				t.Fatalf("ClassifyError(%q, %v) = %q, want %q", tc.phase, tc.err, got, tc.want)
			}
		})
	}
}

// A transport error that is BOTH a sentinel and has DNS-ish text must classify
// by the sentinel — sentinels are the precise signal, text is the fallback.
func TestClassifyError_SentinelBeatsText(t *testing.T) {
	err := fmt.Errorf("lookup node16: no such host: %w", ErrPeerFenced)
	if got := ClassifyError("pull", err); got != "fenced" {
		t.Fatalf("sentinel must win over text, got %q", got)
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/cdowning/workspaces/profiles/personal/code/phantom-brain && go test ./internal/nodesync/ -run TestClassifyError -v`
Expected: FAIL — `undefined: ClassifyError`

- [ ] **Step 3: Write minimal implementation**

Create `internal/nodesync/errorlog.go`:

```go
package nodesync

import (
	"errors"
	"strings"
)

// ClassifyError maps a sync failure to a stable, operator-facing class. The
// class — not the raw message — is what the desktop panel groups by and what
// decides which remediation button it offers, so the vocabulary is closed:
// dns, timeout, refused, auth, proto, fenced, blob, pool, other.
//
// Sentinels are checked before message text: a wrapped ErrPeerFenced is fenced
// even if the chain also mentions a DNS lookup. Phase-derived classes (blob,
// gc, pool) come next, because "the blob store failed" is more useful to an
// operator than re-deriving the transport cause. Text matching is the fallback.
func ClassifyError(phase string, err error) string {
	if err == nil {
		return "other"
	}
	switch {
	case errors.Is(err, ErrProtocolMismatch):
		return "proto"
	case errors.Is(err, ErrPeerFenced):
		return "fenced"
	}
	switch phase {
	case "blob", "gc":
		return "blob"
	case "pool":
		return "pool"
	}
	msg := err.Error()
	switch {
	case strings.Contains(msg, "no such host"),
		strings.Contains(msg, "server misbehaving"),
		strings.Contains(msg, "lookup "):
		return "dns"
	case strings.Contains(msg, "context deadline exceeded"),
		strings.Contains(msg, "Client.Timeout"),
		strings.Contains(msg, "i/o timeout"):
		return "timeout"
	case strings.Contains(msg, "connection refused"):
		return "refused"
	case strings.Contains(msg, "status 401"), strings.Contains(msg, "status 403"):
		return "auth"
	}
	return "other"
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/cdowning/workspaces/profiles/personal/code/phantom-brain && go test ./internal/nodesync/ -run TestClassifyError -race -v`
Expected: PASS, all subtests

- [ ] **Step 5: Commit**

```bash
cd /Users/cdowning/workspaces/profiles/personal/code/phantom-brain
git checkout -b feat/mesh-error-log
git add internal/nodesync/errorlog.go internal/nodesync/errorlog_test.go
git commit -m "feat(nodesync): classify sync failures into a closed vocabulary

The panel groups errors and picks remediations by class, so the class
must be stable across message-text churn. Sentinels beat text."
```

---

### Task 2: The error ring

**Repo:** phantom-brain

**Files:**
- Modify: `internal/nodesync/errorlog.go`
- Test: `internal/nodesync/errorlog_test.go`

**Interfaces:**
- Consumes: `ClassifyError` from Task 1.
- Produces:
  - `type SyncError struct { At time.Time; Peer, Profile, Phase, Class, Err string }` with JSON tags `at`, `peer`, `profile`, `phase`, `class`, `err`.
  - `func NewErrorLog(capacity int) *ErrorLog`
  - `func (l *ErrorLog) Record(e SyncError)` — nil-receiver safe
  - `func (l *ErrorLog) Snapshot() []SyncError` — newest first
  - `func (l *ErrorLog) Since(now time.Time, d time.Duration) int`
  - `func (l *ErrorLog) Clear() int` — returns how many were dropped

- [ ] **Step 1: Write the failing test**

Append to `internal/nodesync/errorlog_test.go`:

```go
func mkErr(peer string, at time.Time) SyncError {
	return SyncError{At: at, Peer: peer, Profile: "p", Phase: "pull", Class: "dns", Err: "boom"}
}

func TestErrorLog_SnapshotNewestFirst(t *testing.T) {
	base := time.Date(2026, 10, 18, 12, 0, 0, 0, time.UTC)
	l := NewErrorLog(10)
	l.Record(mkErr("a", base))
	l.Record(mkErr("b", base.Add(time.Minute)))
	got := l.Snapshot()
	if len(got) != 2 {
		t.Fatalf("want 2 entries, got %d", len(got))
	}
	if got[0].Peer != "b" || got[1].Peer != "a" {
		t.Fatalf("want newest first [b a], got [%s %s]", got[0].Peer, got[1].Peer)
	}
}

func TestErrorLog_EvictsPastCapacity(t *testing.T) {
	base := time.Date(2026, 10, 18, 12, 0, 0, 0, time.UTC)
	l := NewErrorLog(3)
	for i := 0; i < 5; i++ {
		l.Record(mkErr(fmt.Sprintf("p%d", i), base.Add(time.Duration(i)*time.Minute)))
	}
	got := l.Snapshot()
	if len(got) != 3 {
		t.Fatalf("want capacity 3, got %d", len(got))
	}
	if got[0].Peer != "p4" || got[2].Peer != "p2" {
		t.Fatalf("want newest three [p4 p3 p2], got [%s %s %s]", got[0].Peer, got[1].Peer, got[2].Peer)
	}
}

func TestErrorLog_Since(t *testing.T) {
	now := time.Date(2026, 10, 18, 12, 0, 0, 0, time.UTC)
	l := NewErrorLog(10)
	l.Record(mkErr("old", now.Add(-2*time.Hour)))
	l.Record(mkErr("recent", now.Add(-5*time.Minute)))
	if got := l.Since(now, 15*time.Minute); got != 1 {
		t.Fatalf("Since(15m) = %d, want 1", got)
	}
	if got := l.Since(now, 24*time.Hour); got != 2 {
		t.Fatalf("Since(24h) = %d, want 2", got)
	}
}

func TestErrorLog_Clear(t *testing.T) {
	l := NewErrorLog(10)
	l.Record(mkErr("a", time.Now()))
	l.Record(mkErr("b", time.Now()))
	if n := l.Clear(); n != 2 {
		t.Fatalf("Clear() = %d, want 2", n)
	}
	if got := l.Snapshot(); len(got) != 0 {
		t.Fatalf("want empty after Clear, got %d", len(got))
	}
}

// A nil *ErrorLog is the normal state for the CLI path and for existing
// PullWorker construction sites. Every method must be a safe no-op.
func TestErrorLog_NilSafe(t *testing.T) {
	var l *ErrorLog
	l.Record(mkErr("a", time.Now()))
	if got := l.Snapshot(); got != nil {
		t.Fatalf("nil log Snapshot = %v, want nil", got)
	}
	if got := l.Since(time.Now(), time.Hour); got != 0 {
		t.Fatalf("nil log Since = %d, want 0", got)
	}
	if got := l.Clear(); got != 0 {
		t.Fatalf("nil log Clear = %d, want 0", got)
	}
}

func TestErrorLog_ConcurrentRecord(t *testing.T) {
	l := NewErrorLog(200)
	var wg sync.WaitGroup
	for i := 0; i < 50; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			l.Record(mkErr(fmt.Sprintf("p%d", i), time.Now()))
		}(i)
	}
	wg.Wait()
	if got := len(l.Snapshot()); got != 50 {
		t.Fatalf("want 50 recorded, got %d", got)
	}
}
```

Add `"sync"` and `"time"` to that file's imports.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/cdowning/workspaces/profiles/personal/code/phantom-brain && go test ./internal/nodesync/ -run TestErrorLog -v`
Expected: FAIL — `undefined: NewErrorLog`, `undefined: SyncError`

- [ ] **Step 3: Write minimal implementation**

Append to `internal/nodesync/errorlog.go` (and add `"sync"`, `"time"` to its imports):

```go
// SyncError is one recorded sync failure. It is served on the UNAUTHENTICATED
// /admin/mesh/status, so it must never carry a secret: Err holds daemon-built
// error text, and peer tokens travel in the Authorization header rather than
// the URL, so transport errors cannot leak one. Keep it that way.
type SyncError struct {
	At      time.Time `json:"at"`
	Peer    string    `json:"peer"`
	Profile string    `json:"profile"`
	Phase   string    `json:"phase"` // pool|pull|links|tombstone|blob|gc
	Class   string    `json:"class"` // see ClassifyError
	Err     string    `json:"err"`
}

// ErrorLog is a bounded, concurrency-safe ring of recent sync failures. It
// exists because pb_sync_errors_total is a bare counter: it can say 996 but
// never which peer, which phase, or whether any of it is still happening.
//
// Deliberately in-memory only. The counter it accompanies already resets on
// daemon restart, and a ring that outlived the counter would make "is this
// live?" — the question the panel most needs to answer — harder, not easier.
type ErrorLog struct {
	mu      sync.Mutex
	entries []SyncError // oldest first
	cap     int
}

func NewErrorLog(capacity int) *ErrorLog {
	if capacity <= 0 {
		capacity = 200
	}
	return &ErrorLog{cap: capacity, entries: make([]SyncError, 0, capacity)}
}

// Record appends an entry, evicting the oldest past capacity. A nil receiver
// is a no-op: the CLI path and pre-existing PullWorker construction sites
// legitimately have no log.
func (l *ErrorLog) Record(e SyncError) {
	if l == nil {
		return
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	if len(l.entries) >= l.cap {
		l.entries = l.entries[len(l.entries)-l.cap+1:]
	}
	l.entries = append(l.entries, e)
}

// Snapshot returns a copy of the ring, newest first.
func (l *ErrorLog) Snapshot() []SyncError {
	if l == nil {
		return nil
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	out := make([]SyncError, len(l.entries))
	for i, e := range l.entries {
		out[len(l.entries)-1-i] = e
	}
	return out
}

// Since counts entries recorded within d of now.
func (l *ErrorLog) Since(now time.Time, d time.Duration) int {
	if l == nil {
		return 0
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	cutoff := now.Add(-d)
	n := 0
	for _, e := range l.entries {
		if e.At.After(cutoff) {
			n++
		}
	}
	return n
}

// Clear empties the ring and reports how many entries were dropped.
func (l *ErrorLog) Clear() int {
	if l == nil {
		return 0
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	n := len(l.entries)
	l.entries = l.entries[:0]
	return n
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/cdowning/workspaces/profiles/personal/code/phantom-brain && go test ./internal/nodesync/ -run 'TestErrorLog|TestClassifyError' -race -v`
Expected: PASS, all subtests

- [ ] **Step 5: Commit**

```bash
cd /Users/cdowning/workspaces/profiles/personal/code/phantom-brain
git add internal/nodesync/errorlog.go internal/nodesync/errorlog_test.go
git commit -m "feat(nodesync): bounded ring of recent sync failures

A bare counter can say 996 but not which peer, which phase, or whether
any of it is still happening. In-memory by design — the counter it
accompanies already resets on restart."
```

---

### Task 3: Wire the ring into PullWorker

**Repo:** phantom-brain

**Files:**
- Modify: `internal/nodesync/worker.go` (add `Errors` field; add `recordErr`; pair all 7 `mErrors.Add(1)` sites)
- Test: `internal/nodesync/errorlog_test.go`

**Interfaces:**
- Consumes: `ErrorLog`, `SyncError`, `ClassifyError` from Tasks 1–2; `Peer` from `pull.go`.
- Produces: `PullWorker.Errors *ErrorLog` field; `func (w *PullWorker) recordErr(peer Peer, phase string, err error)`.

The 7 existing `w.mErrors.Add(1)` sites in `worker.go` and their phases:

| Approx. line | Context | Phase |
|---|---|---|
| 87 | `PoolFor` resolve failure | `pool` |
| 103 | default branch of `PullFromPeerWithTombstones` error | `pull` |
| 121 | `OnTombstone` projection delete failure | `tombstone` |
| 142 | default branch of `PullLinksFromPeer` error | `links` |
| 164 | `BlobStoreFor` resolve failure | `blob` |
| 169 | `ReconcileBlobs` failure | `blob` |
| 178 | `GCOrphanBlobs` failure | `gc` |

Line numbers drift as you edit — find each by its surrounding `w.log().Warn(...)` call, which is unique per site.

- [ ] **Step 1: Write the failing test**

Append to `internal/nodesync/errorlog_test.go`:

```go
func TestPullWorker_recordErr(t *testing.T) {
	log := NewErrorLog(10)
	w := &PullWorker{Errors: log}
	peer := Peer{ID: "m3-personal-memory", BaseURL: "http://node64:9998", Token: "secret-token", Profile: "personal"}

	w.recordErr(peer, "pull", errors.New(`lookup node64: no such host`))

	got := log.Snapshot()
	if len(got) != 1 {
		t.Fatalf("want 1 entry, got %d", len(got))
	}
	e := got[0]
	if e.Peer != "m3-personal-memory" || e.Profile != "personal" {
		t.Errorf("peer/profile = %q/%q", e.Peer, e.Profile)
	}
	if e.Phase != "pull" || e.Class != "dns" {
		t.Errorf("phase/class = %q/%q, want pull/dns", e.Phase, e.Class)
	}
	if e.At.IsZero() {
		t.Error("At must be stamped")
	}
	if strings.Contains(e.Err, "secret-token") {
		t.Fatalf("recorded error leaked the peer token: %q", e.Err)
	}
}

// The worker must keep working when no log is attached — every existing
// construction site leaves Errors nil.
func TestPullWorker_recordErr_NilLog(t *testing.T) {
	w := &PullWorker{}
	w.recordErr(Peer{ID: "x", Profile: "p"}, "pull", errors.New("boom"))
}
```

Add `"strings"` to that file's imports.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/cdowning/workspaces/profiles/personal/code/phantom-brain && go test ./internal/nodesync/ -run TestPullWorker_recordErr -v`
Expected: FAIL — `w.recordErr undefined`

- [ ] **Step 3: Write minimal implementation**

In `internal/nodesync/worker.go`, add the field to the `PullWorker` struct, immediately after `Log *slog.Logger`:

```go
	// Errors retains structured detail for the failures mErrors only counts.
	// Optional: nil disables retention (the counter still increments).
	Errors *ErrorLog
```

Then add this method next to `func (w *PullWorker) log()`:

```go
// recordErr retains one failure alongside the mErrors counter bump. Every
// mErrors.Add(1) site pairs with a call here so the panel can say which peer
// and which phase, not just how many.
func (w *PullWorker) recordErr(peer Peer, phase string, err error) {
	if err == nil {
		return
	}
	w.Errors.Record(SyncError{
		At:      time.Now(),
		Peer:    peer.ID,
		Profile: peer.Profile,
		Phase:   phase,
		Class:   ClassifyError(phase, err),
		Err:     err.Error(),
	})
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/cdowning/workspaces/profiles/personal/code/phantom-brain && go test ./internal/nodesync/ -run TestPullWorker_recordErr -race -v`
Expected: PASS

- [ ] **Step 5: Pair every counter bump with a record**

In `internal/nodesync/worker.go`, add a `w.recordErr(...)` line immediately after each of the 7 `w.mErrors.Add(1)` calls, using the phase from the table above. Two of the sites do not have a `peer` in scope in the same shape — handle them as follows:

The `BlobStoreFor` resolve site logs `"blob: resolve store"` and has `peer` in scope (it is inside the `for _, peer := range w.Peers` loop) — use `w.recordErr(peer, "blob", err)`.

The `GCOrphanBlobs` site is profile-scoped but still inside the peer loop — use `w.recordErr(peer, "gc", err)`.

The tombstone site iterates `ts` within the peer loop; the peer id is what the operator needs, so use `w.recordErr(peer, "tombstone", err)`.

Example, for the `PullFromPeerWithTombstones` default branch:

```go
			default:
				w.mErrors.Add(1)
				w.recordErr(peer, "pull", err)
				w.log().Warn("pull: from peer", "peer", peer.ID, "profile", peer.Profile, "err", err)
			}
```

- [ ] **Step 6: Verify every bump is paired**

Run:
```bash
cd /Users/cdowning/workspaces/profiles/personal/code/phantom-brain
grep -c "mErrors.Add(1)" internal/nodesync/worker.go
grep -c "w.recordErr(" internal/nodesync/worker.go
```
Expected: both print `7`.

- [ ] **Step 7: Run the full nodesync suite**

Run: `cd /Users/cdowning/workspaces/profiles/personal/code/phantom-brain && go test ./internal/nodesync/ -race`
Expected: PASS. Postgres-backed tests (`TestPullWorkerTick` and friends) may skip if no test DSN is configured — a skip is acceptable, a failure is not.

- [ ] **Step 8: Commit**

```bash
cd /Users/cdowning/workspaces/profiles/personal/code/phantom-brain
git add internal/nodesync/worker.go internal/nodesync/errorlog_test.go
git commit -m "feat(nodesync): retain detail at every error-counter bump

All seven mErrors.Add sites now also record peer, phase and class, so
the detail stops being log-only."
```

---

### Task 4: Per-peer error annotation

**Repo:** phantom-brain

**Files:**
- Modify: `internal/nodesync/meshstatus.go`
- Test: `internal/nodesync/meshstatus_test.go`

**Interfaces:**
- Consumes: `SyncError`, `ErrorLog` from Task 2; existing `PeerStatus`, `Report`, `BuildDeps`, `BuildReport`.
- Produces:
  - `PeerStatus.LastError *SyncError` (JSON `last_error,omitempty`), `PeerStatus.Errors24h int` (JSON `errors_24h`)
  - `Report.RecentErrors []SyncError` (JSON `recent_errors,omitempty`), `Report.Errors15m int` (JSON `errors_15m`)
  - `BuildDeps.ErrorLog *ErrorLog`, `BuildDeps.Now time.Time` (zero ⇒ `time.Now()`)
  - `func annotatePeerErrors(peers []PeerStatus, snapshot []SyncError, now time.Time)` — mutates in place

- [ ] **Step 1: Write the failing test**

Append to `internal/nodesync/meshstatus_test.go`:

```go
func TestAnnotatePeerErrors(t *testing.T) {
	now := time.Date(2026, 10, 18, 12, 0, 0, 0, time.UTC)
	peers := []PeerStatus{{ID: "a", Profile: "p"}, {ID: "b", Profile: "p"}}
	// Snapshot order is newest-first, as ErrorLog.Snapshot returns it.
	snap := []SyncError{
		{At: now.Add(-1 * time.Minute), Peer: "a", Class: "dns", Err: "newest a"},
		{At: now.Add(-2 * time.Hour), Peer: "a", Class: "timeout", Err: "older a"},
		{At: now.Add(-48 * time.Hour), Peer: "a", Class: "dns", Err: "ancient a"},
	}

	annotatePeerErrors(peers, snap, now)

	if peers[0].LastError == nil || peers[0].LastError.Err != "newest a" {
		t.Fatalf("peer a last_error = %+v, want 'newest a'", peers[0].LastError)
	}
	if peers[0].Errors24h != 2 {
		t.Errorf("peer a errors_24h = %d, want 2 (the 48h-old one is outside the window)", peers[0].Errors24h)
	}
	if peers[1].LastError != nil || peers[1].Errors24h != 0 {
		t.Errorf("peer b should be untouched, got %+v / %d", peers[1].LastError, peers[1].Errors24h)
	}
}

func TestBuildReport_CarriesErrors(t *testing.T) {
	now := time.Date(2026, 10, 18, 12, 0, 0, 0, time.UTC)
	log := NewErrorLog(10)
	log.Record(SyncError{At: now.Add(-1 * time.Minute), Peer: "dead", Profile: "p", Phase: "pull", Class: "dns", Err: "lookup failed"})
	log.Record(SyncError{At: now.Add(-3 * time.Hour), Peer: "dead", Profile: "p", Phase: "pull", Class: "dns", Err: "older"})

	rep := BuildReport(context.Background(), BuildDeps{
		NodeID:      "n1",
		SyncEnabled: true,
		Peers:       []Peer{{ID: "dead", BaseURL: "http://127.0.0.1:1", Profile: "p"}},
		Client:      DefaultClient(),
		ProbeLimit:  10,
		PoolFor:     func(string) *pgxpool.Pool { return nil },
		Metrics:     map[string]string{},
		ErrorLog:    log,
		Now:         now,
	})

	if len(rep.RecentErrors) != 2 {
		t.Fatalf("want 2 recent_errors, got %d", len(rep.RecentErrors))
	}
	if rep.RecentErrors[0].Err != "older" && rep.RecentErrors[0].Err != "lookup failed" {
		t.Fatalf("unexpected first entry %+v", rep.RecentErrors[0])
	}
	if rep.Errors15m != 1 {
		t.Errorf("errors_15m = %d, want 1", rep.Errors15m)
	}
	if len(rep.Peers) != 1 || rep.Peers[0].Errors24h != 2 {
		t.Errorf("peer errors_24h = %+v, want 2", rep.Peers)
	}
}

// A nil ErrorLog (the CLI path) must leave the new fields empty, not panic.
func TestBuildReport_NilErrorLog(t *testing.T) {
	rep := BuildReport(context.Background(), BuildDeps{
		NodeID:  "n1",
		Peers:   nil,
		Client:  DefaultClient(),
		PoolFor: func(string) *pgxpool.Pool { return nil },
		Metrics: map[string]string{},
	})
	if rep.RecentErrors != nil || rep.Errors15m != 0 {
		t.Fatalf("nil log must yield empty error fields, got %+v / %d", rep.RecentErrors, rep.Errors15m)
	}
}
```

Ensure `"time"` and `"context"` are imported in that file.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/cdowning/workspaces/profiles/personal/code/phantom-brain && go test ./internal/nodesync/ -run 'TestAnnotatePeerErrors|TestBuildReport_CarriesErrors|TestBuildReport_NilErrorLog' -v`
Expected: FAIL — `undefined: annotatePeerErrors`, `unknown field ErrorLog in struct literal`

- [ ] **Step 3: Write minimal implementation**

In `internal/nodesync/meshstatus.go`, add to `PeerStatus`:

```go
	// LastError is the most recent recorded failure for this peer, and
	// Errors24h how many it has had in the last day. Both empty when no
	// ErrorLog is injected (the CLI path).
	LastError *SyncError `json:"last_error,omitempty"`
	Errors24h int        `json:"errors_24h"`
```

Add to `Report`:

```go
	RecentErrors []SyncError `json:"recent_errors,omitempty"`
	// Errors15m is the recency signal the bare counter cannot give: a
	// cumulative 996 with Errors15m==0 is a healed outage, not an incident.
	Errors15m int `json:"errors_15m"`
```

Add to `BuildDeps`:

```go
	// ErrorLog is the pull worker's retained failure ring. MAY be nil (the CLI
	// opens no worker) → the error fields are omitted from the report.
	ErrorLog *ErrorLog
	// Now overrides the clock for the error windows. Zero ⇒ time.Now().
	Now time.Time
```

Add the helper at the bottom of the file:

```go
// annotatePeerErrors stamps each peer with its most recent failure and its
// 24-hour failure count. snapshot must be newest-first (as ErrorLog.Snapshot
// returns), so the first match per peer is the latest.
func annotatePeerErrors(peers []PeerStatus, snapshot []SyncError, now time.Time) {
	if len(peers) == 0 || len(snapshot) == 0 {
		return
	}
	cutoff := now.Add(-24 * time.Hour)
	for i := range peers {
		for _, e := range snapshot {
			if e.Peer != peers[i].ID {
				continue
			}
			if peers[i].LastError == nil {
				cp := e
				peers[i].LastError = &cp
			}
			if e.At.After(cutoff) {
				peers[i].Errors24h++
			}
		}
	}
}
```

Finally, in `BuildReport`, just before `return rep`:

```go
	now := deps.Now
	if now.IsZero() {
		now = time.Now()
	}
	if deps.ErrorLog != nil {
		rep.RecentErrors = deps.ErrorLog.Snapshot()
		rep.Errors15m = deps.ErrorLog.Since(now, 15*time.Minute)
		annotatePeerErrors(rep.Peers, rep.RecentErrors, now)
	}

	return rep
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/cdowning/workspaces/profiles/personal/code/phantom-brain && go test ./internal/nodesync/ -race`
Expected: PASS (existing `TestBuildReport_*` tests included — they pass no `ErrorLog` and must be unaffected)

- [ ] **Step 5: Commit**

```bash
cd /Users/cdowning/workspaces/profiles/personal/code/phantom-brain
git add internal/nodesync/meshstatus.go internal/nodesync/meshstatus_test.go
git commit -m "feat(nodesync): carry recent errors and a recency count in the report

errors_15m is the field that distinguishes a live incident from a
cumulative counter still showing a healed one."
```

---

### Task 5: Extract TickPeer

**Repo:** phantom-brain

**Files:**
- Modify: `internal/nodesync/worker.go`
- Test: existing `internal/nodesync/worker_test.go` is the regression guard

**Interfaces:**
- Produces: `type PeerResult struct { RowsMerged, LinksMerged, BlobsFetched int }` and `func (w *PullWorker) TickPeer(ctx context.Context, peer Peer, gcProfile bool) PeerResult`.
- Consumed by: Task 6's retry handler.

This is a pure refactor: the body of the `for _, peer := range w.Peers` loop in `Tick` moves into `TickPeer`, and `Tick` becomes a loop that calls it. Behaviour must not change — that is what the existing tests prove. The retry action and the scheduled round must share one code path so they cannot drift.

- [ ] **Step 1: Record the pre-refactor baseline**

Run: `cd /Users/cdowning/workspaces/profiles/personal/code/phantom-brain && go test ./internal/nodesync/ -race 2>&1 | tail -5`
Expected: note the exact PASS/SKIP output. The post-refactor run must match it.

- [ ] **Step 2: Extract the method**

In `internal/nodesync/worker.go`, add above `Tick`:

```go
// PeerResult is what one peer's pull round applied.
type PeerResult struct {
	RowsMerged   int `json:"rows_merged"`
	LinksMerged  int `json:"links_merged"`
	BlobsFetched int `json:"blobs_fetched"`
}

// TickPeer runs one pull round for a single peer: records, links, then blob
// reconciliation (and orphan GC when gcProfile is set — GC is profile-scoped,
// so Tick only sets it for the first peer of each distinct profile).
//
// Both the scheduled round and the operator's retry action call this, so the
// two can never drift apart. Failures are recorded and skipped, never returned:
// one bad peer must not stall the others.
func (w *PullWorker) TickPeer(ctx context.Context, peer Peer, gcProfile bool) PeerResult {
	var res PeerResult

	pool, err := w.PoolFor(peer.Profile)
	if err != nil {
		w.mErrors.Add(1)
		w.recordErr(peer, "pool", err)
		w.log().Warn("pull: resolve pool", "peer", peer.ID, "profile", peer.Profile, "err", err)
		return res
	}
	n, tombstoned, err := PullFromPeerWithTombstones(ctx, pool, peer, w.Client, w.limit())
	if err != nil {
		switch {
		case errors.Is(err, ErrPeerFenced):
			// Fenced peer (restored, reconciling) deliberately withholds its
			// op-log — benign, silent skip.
		case errors.Is(err, ErrProtocolMismatch):
			w.log().Warn("pull: sync-protocol mismatch — skipping peer until both nodes match",
				"peer", peer.ID, "profile", peer.Profile, "err", err)
		default:
			w.mErrors.Add(1)
			w.recordErr(peer, "pull", err)
			w.log().Warn("pull: from peer", "peer", peer.ID, "profile", peer.Profile, "err", err)
		}
		return res
	}
	if n > 0 {
		w.mRows.Add(int64(n))
		w.log().Info("pull: merged", "peer", peer.ID, "profile", peer.Profile, "rows", n)
	}
	res.RowsMerged += n

	if w.OnTombstone != nil {
		for _, ts := range tombstoned {
			if err := w.OnTombstone(ctx, ts.Profile, ts.Vault, ts.SHA); err != nil {
				w.mErrors.Add(1)
				w.recordErr(peer, "tombstone", err)
				w.log().Warn("pull: project-delete tombstone",
					"peer", peer.ID, "profile", ts.Profile, "vault", ts.Vault, "sha", ts.SHA, "err", err)
			}
		}
	}

	linkFetch := HTTPFetchLinks(w.Client, peer.BaseURL, peer.Token)
	if ln, err := PullLinksFromPeer(ctx, pool, peer, linkFetch, w.limit()); err != nil {
		switch {
		case errors.Is(err, ErrLinksNotEnabled), errors.Is(err, ErrPeerFenced):
			// Older node without the links endpoint, or a fenced peer — benign.
		case errors.Is(err, ErrProtocolMismatch):
			w.log().Warn("pull: links sync-protocol mismatch — skipping peer until both nodes match",
				"peer", peer.ID, "profile", peer.Profile, "err", err)
		default:
			w.mErrors.Add(1)
			w.recordErr(peer, "links", err)
			w.log().Warn("pull: links from peer", "peer", peer.ID, "profile", peer.Profile, "err", err)
		}
	} else if ln > 0 {
		w.mRows.Add(int64(ln))
		w.log().Info("pull: merged links", "peer", peer.ID, "profile", peer.Profile, "links", ln)
		res.LinksMerged += ln
	}

	if w.BlobStoreFor != nil {
		store, err := w.BlobStoreFor(peer.Profile)
		switch {
		case errors.Is(err, ErrNoBlobStore):
			// No blob backend on this node — not an error.
			return res
		case err != nil:
			w.mErrors.Add(1)
			w.recordErr(peer, "blob", err)
			w.log().Warn("blob: resolve store", "profile", peer.Profile, "err", err)
			return res
		}
		if fetched, err := ReconcileBlobs(ctx, pool, store, w.Client, peer); err != nil {
			w.mErrors.Add(1)
			w.recordErr(peer, "blob", err)
			w.log().Warn("blob: reconcile", "peer", peer.ID, "profile", peer.Profile, "err", err)
		} else if fetched > 0 {
			w.mBlobs.Add(int64(fetched))
			res.BlobsFetched += fetched
			w.log().Info("blob: fetched", "peer", peer.ID, "profile", peer.Profile, "blobs", fetched)
		}
		if gcProfile {
			if gcd, err := GCOrphanBlobs(ctx, pool, store); err != nil {
				w.mErrors.Add(1)
				w.recordErr(peer, "gc", err)
				w.log().Warn("blob: gc", "profile", peer.Profile, "err", err)
			} else if gcd > 0 {
				w.mGC.Add(int64(gcd))
				w.log().Info("blob: gc'd orphans", "profile", peer.Profile, "blobs", gcd)
			}
		}
	}
	return res
}
```

Note the one deliberate behaviour difference: the original `continue`d past blob
handling on `ErrNoBlobStore`, which also skipped that round's orphan GC for the
profile while still marking it GC'd. `TickPeer` returns at the same point, and
`Tick` has already claimed the profile, so the behaviour matches. If the existing
tests disagree, trust the tests.

Replace the `Tick` loop body with:

```go
	for _, peer := range w.Peers {
		gcProfile := !gcdProfiles[peer.Profile]
		if gcProfile {
			gcdProfiles[peer.Profile] = true
		}
		r := w.TickPeer(ctx, peer, gcProfile)
		applied += r.RowsMerged + r.LinksMerged
	}
```

Note the GC bookkeeping moves *out* of `TickPeer` and into `Tick`, so a retry of a single peer can still run GC for that profile without consulting the round's map.

- [ ] **Step 3: Run the regression suite**

Run: `cd /Users/cdowning/workspaces/profiles/personal/code/phantom-brain && go test ./internal/nodesync/ -race 2>&1 | tail -5`
Expected: identical PASS/SKIP output to Step 1. Any new failure means the extraction changed behaviour — fix the extraction, do not change the test.

- [ ] **Step 4: Commit**

```bash
cd /Users/cdowning/workspaces/profiles/personal/code/phantom-brain
git add internal/nodesync/worker.go
git commit -m "refactor(nodesync): extract TickPeer from the Tick loop

The operator's retry action needs to pull one peer off-tick. Sharing
one code path with the scheduled round keeps them from drifting."
```

---

### Task 6: Daemon endpoints

**Repo:** phantom-brain

**Files:**
- Modify: `internal/server/handlers_mesh.go` (inject the log into `BuildDeps`; add three handlers)
- Modify: `internal/server/server.go` (three routes in the admin group; construct the worker's `ErrorLog`)
- Test: `internal/server/handlers_mesh_admin_test.go` (create)

**Interfaces:**
- Consumes: `nodesync.NewErrorLog`, `PullWorker.Errors`, `PullWorker.TickPeer`, `BuildDeps.ErrorLog` from Tasks 2–5.
- Produces: three routes —
  - `POST /api/brain/admin/mesh/peers/{id}/retry` → `{"rows_merged":N,"links_merged":N,"blobs_fetched":N}`
  - `POST /api/brain/admin/mesh/peers/{id}/reset-cursor` → `{"reset":true,"peer":"<id>"}`
  - `POST /api/brain/admin/mesh/errors/clear` → `{"cleared":N}`

- [ ] **Step 1: Write the failing test**

Create `internal/server/handlers_mesh_admin_test.go`:

```go
package server

import (
	"encoding/json"
	"net/http"
	"testing"
)

// The three mesh actions mount on the SAME admin door as /admin/profiles, so
// they inherit its fail-closed posture: no PB_ADMIN_KEY means no surface.
func TestMeshAdmin_DisabledWithoutKey(t *testing.T) {
	t.Setenv("PB_ADMIN_KEY", "")
	rig := newRouterRig(t)
	for _, path := range []string{
		"/api/brain/admin/mesh/peers/anything/retry",
		"/api/brain/admin/mesh/peers/anything/reset-cursor",
		"/api/brain/admin/mesh/errors/clear",
	} {
		resp := adminReq(t, "POST", rig.server.URL+path, "anything")
		resp.Body.Close()
		if resp.StatusCode != http.StatusForbidden {
			t.Errorf("%s: want 403 with unset key, got %d", path, resp.StatusCode)
		}
	}
}

func TestMeshAdmin_WrongKeyForbidden(t *testing.T) {
	t.Setenv("PB_ADMIN_KEY", "op-secret")
	rig := newRouterRig(t)
	resp := adminReq(t, "POST", rig.server.URL+"/api/brain/admin/mesh/errors/clear", "wrong")
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusForbidden {
		t.Fatalf("want 403 for wrong key, got %d", resp.StatusCode)
	}
}

// The rig has no pull worker (sync disabled). Every action must say so with a
// 503 rather than panicking on a nil worker.
func TestMeshAdmin_NoWorker503(t *testing.T) {
	t.Setenv("PB_ADMIN_KEY", "op-secret")
	rig := newRouterRig(t)
	for _, path := range []string{
		"/api/brain/admin/mesh/peers/anything/retry",
		"/api/brain/admin/mesh/peers/anything/reset-cursor",
		"/api/brain/admin/mesh/errors/clear",
	} {
		resp := adminReq(t, "POST", rig.server.URL+path, "op-secret")
		resp.Body.Close()
		if resp.StatusCode != http.StatusServiceUnavailable {
			t.Errorf("%s: want 503 with no pull worker, got %d", path, resp.StatusCode)
		}
	}
}

// With a worker attached, clear empties the ring and reports the count.
func TestMeshAdmin_ClearErrors(t *testing.T) {
	t.Setenv("PB_ADMIN_KEY", "op-secret")
	rig := newRouterRig(t)
	log := nodesync.NewErrorLog(10)
	log.Record(nodesync.SyncError{Peer: "a", Class: "dns", Err: "boom"})
	log.Record(nodesync.SyncError{Peer: "b", Class: "dns", Err: "boom"})
	rig.d.pullWorker = &nodesync.PullWorker{Errors: log}

	resp := adminReq(t, "POST", rig.server.URL+"/api/brain/admin/mesh/errors/clear", "op-secret")
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("want 200, got %d", resp.StatusCode)
	}
	var out struct {
		Cleared int `json:"cleared"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if out.Cleared != 2 {
		t.Fatalf("cleared = %d, want 2", out.Cleared)
	}
	if n := len(log.Snapshot()); n != 0 {
		t.Fatalf("ring not emptied: %d left", n)
	}
}

// An unknown peer id is a 404, not a silent no-op — the panel's peer list may
// be stale and the operator needs to know.
func TestMeshAdmin_RetryUnknownPeer404(t *testing.T) {
	t.Setenv("PB_ADMIN_KEY", "op-secret")
	t.Setenv("CL_SYNC__PEERS", "")
	rig := newRouterRig(t)
	rig.d.pullWorker = &nodesync.PullWorker{Errors: nodesync.NewErrorLog(10)}

	resp := adminReq(t, "POST", rig.server.URL+"/api/brain/admin/mesh/peers/nosuch/retry", "op-secret")
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusNotFound {
		t.Fatalf("want 404 for unknown peer, got %d", resp.StatusCode)
	}
}

// /admin/mesh/status must stay reachable with no credentials at all.
func TestMeshStatus_StaysUnauthenticated(t *testing.T) {
	t.Setenv("PB_ADMIN_KEY", "op-secret")
	rig := newRouterRig(t)
	resp, err := http.Get(rig.server.URL + "/admin/mesh/status")
	if err != nil {
		t.Fatalf("GET: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("want 200 unauthenticated, got %d", resp.StatusCode)
	}
}
```

Add the import `"github.com/neverprepared/phantom-brain/internal/nodesync"`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/cdowning/workspaces/profiles/personal/code/phantom-brain && go test ./internal/server/ -run TestMeshAdmin -v`
Expected: FAIL — routes return 404/405 rather than 403/503

- [ ] **Step 3: Add the handlers**

Append to `internal/server/handlers_mesh.go`:

```go
// --- mesh remediation actions ----------------------------------------------
//
// These MUTATE, so unlike handleMeshStatus they mount behind
// adminAuthMiddleware (PB_ADMIN_KEY) in the /api/brain/admin group — the same
// fail-closed door /admin/profiles uses. No write path is added to the
// unauthenticated /admin/mesh/ tree.

// meshPeerByID finds a configured peer by id. The peer list is env-derived, so
// a panel working from a stale snapshot can name one that no longer exists.
func meshPeerByID(id string) (nodesync.Peer, bool) {
	for _, p := range nodesync.ParsePeers(os.Getenv("CL_SYNC__PEERS")) {
		if p.ID == id {
			return p, true
		}
	}
	return nodesync.Peer{}, false
}

// writeMeshJSON writes a 200 JSON body.
func writeMeshJSON(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(v)
}

// handleMeshRetryPeer pulls ONE peer immediately, off-tick. This is the
// operator's "did my fix work?" button: it returns a fresh verdict in one
// request instead of making them wait out a sync interval.
func (d *Daemon) handleMeshRetryPeer(w http.ResponseWriter, r *http.Request) {
	if d.pullWorker == nil {
		WriteErrorEnvelope(w, http.StatusServiceUnavailable, ErrCodeUnavailable,
			"sync is disabled on this node (no pull worker)", nil)
		return
	}
	id := chi.URLParam(r, "id")
	peer, ok := meshPeerByID(id)
	if !ok {
		WriteErrorEnvelope(w, http.StatusNotFound, ErrCodeNotFound,
			"no configured peer with id "+id, nil)
		return
	}
	res := d.pullWorker.TickPeer(r.Context(), peer, true)
	writeMeshJSON(w, res)
}

// handleMeshResetCursor clears this node's pull cursor for a peer so the next
// round re-reads its op-log from the beginning. Destructive only in the cheap
// sense: it costs a full re-read, and cannot lose data.
func (d *Daemon) handleMeshResetCursor(w http.ResponseWriter, r *http.Request) {
	if d.pullWorker == nil {
		WriteErrorEnvelope(w, http.StatusServiceUnavailable, ErrCodeUnavailable,
			"sync is disabled on this node (no pull worker)", nil)
		return
	}
	id := chi.URLParam(r, "id")
	peer, ok := meshPeerByID(id)
	if !ok {
		WriteErrorEnvelope(w, http.StatusNotFound, ErrCodeNotFound,
			"no configured peer with id "+id, nil)
		return
	}
	pool, err := d.poolForProfile(peer.Profile)
	if err != nil || pool == nil {
		WriteErrorEnvelope(w, http.StatusServiceUnavailable, ErrCodeUnavailable,
			"no database for profile "+peer.Profile, nil)
		return
	}
	if _, err := pool.Exec(r.Context(),
		`DELETE FROM sync_cursors WHERE peer_id=$1`, peer.ID); err != nil {
		WriteErrorEnvelope(w, http.StatusInternalServerError, ErrCodeInternal,
			"reset cursor: "+err.Error(), nil)
		return
	}
	writeMeshJSON(w, map[string]any{"reset": true, "peer": peer.ID})
}

// handleMeshClearErrors zeroes the retained ring. The counter is cumulative and
// never decays, so an operator who has fixed the cause needs a way to say so.
func (d *Daemon) handleMeshClearErrors(w http.ResponseWriter, r *http.Request) {
	if d.pullWorker == nil {
		WriteErrorEnvelope(w, http.StatusServiceUnavailable, ErrCodeUnavailable,
			"sync is disabled on this node (no pull worker)", nil)
		return
	}
	n := d.pullWorker.Errors.Clear()
	writeMeshJSON(w, map[string]any{"cleared": n})
}
```

Add `"github.com/go-chi/chi/v5"` to the imports. If `ErrCodeUnavailable`, `ErrCodeNotFound`, or `ErrCodeInternal` do not exist, grep `internal/server/` for the existing `ErrCode*` constants and use the nearest equivalents — do not invent new ones.

Also pass the log into the read path. In `handleMeshStatus`'s `BuildDeps` literal, add:

```go
		ErrorLog:    d.meshErrorLog(),
```

and add this helper beside the handlers:

```go
// meshErrorLog returns the pull worker's error ring, or nil when sync is off.
func (d *Daemon) meshErrorLog() *nodesync.ErrorLog {
	if d.pullWorker == nil {
		return nil
	}
	return d.pullWorker.Errors
}
```

- [ ] **Step 4: Mount the routes**

In `internal/server/server.go`, inside the existing admin group (next to `r.Get("/admin/profiles/{profile}/{vault}", ...)`), add:

```go
			// Mesh remediation. Mutating, hence inside the PB_ADMIN_KEY door —
			// unlike the read-only, unauthenticated /admin/mesh/status.
			r.Post("/admin/mesh/peers/{id}/retry", d.handleMeshRetryPeer)
			r.Post("/admin/mesh/peers/{id}/reset-cursor", d.handleMeshResetCursor)
			r.Post("/admin/mesh/errors/clear", d.handleMeshClearErrors)
```

- [ ] **Step 5: Give the real worker a ring**

Find where `server.go` constructs the `nodesync.PullWorker` (grep for `&nodesync.PullWorker{`) and add to the literal:

```go
		Errors: nodesync.NewErrorLog(200),
```

- [ ] **Step 6: Run the tests**

Run: `cd /Users/cdowning/workspaces/profiles/personal/code/phantom-brain && go test ./internal/server/ -run 'TestMeshAdmin|TestMeshStatus' -race -v`
Expected: PASS

- [ ] **Step 7: Run the full suite and build**

Run:
```bash
cd /Users/cdowning/workspaces/profiles/personal/code/phantom-brain
go test ./internal/... -race 2>&1 | tail -20
make
```
Expected: tests PASS (pre-existing skips are fine), `make` succeeds.

- [ ] **Step 8: Commit**

```bash
cd /Users/cdowning/workspaces/profiles/personal/code/phantom-brain
git add internal/server/handlers_mesh.go internal/server/server.go internal/server/handlers_mesh_admin_test.go
git commit -m "feat(server): expose mesh errors and three remediation actions

Status gains the retained ring (still unauthenticated — no secrets in
it). Retry, reset-cursor and clear-errors mount behind the existing
PB_ADMIN_KEY door and fail closed without it."
```

---

### Task 7: App-side admin key and HTTP helper

**Repo:** phantom-ink

**Files:**
- Modify: `app/config.go` (one setting constant)
- Modify: `app/app_mesh.go`
- Test: `app/app_mesh_test.go` (create)

**Interfaces:**
- Produces:
  - `settingMeshAdminKey = "mesh_admin_key"` in `app/config.go`
  - `type MeshActionResult struct { RowsMerged, LinksMerged, BlobsFetched, Cleared int; Reset bool }`
  - `func postMeshAdmin(base, key, path string, hc *http.Client) (MeshActionResult, error)`

`postMeshAdmin` is a free function taking its dependencies, following the `checkGitHubToken` pattern in `app/app_github_token_test.go` — that is what makes it testable without an `App`.

- [ ] **Step 1: Write the failing test**

Create `app/app_mesh_test.go`:

```go
package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func meshAdminStub(t *testing.T, status int, body string) (string, *http.Client, *string, *string) {
	t.Helper()
	var gotKey, gotPath string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotKey = r.Header.Get("X-API-Key")
		gotPath = r.URL.Path
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		_, _ = w.Write([]byte(body))
	}))
	t.Cleanup(srv.Close)
	return srv.URL, srv.Client(), &gotKey, &gotPath
}

func TestPostMeshAdmin_SendsKeyAndDecodes(t *testing.T) {
	base, hc, key, path := meshAdminStub(t, http.StatusOK, `{"rows_merged":3,"links_merged":1}`)
	got, err := postMeshAdmin(base, "op-secret", "/api/brain/admin/mesh/peers/p1/retry", hc)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if *key != "op-secret" {
		t.Errorf("X-API-Key = %q, want op-secret", *key)
	}
	if *path != "/api/brain/admin/mesh/peers/p1/retry" {
		t.Errorf("path = %q", *path)
	}
	if got.RowsMerged != 3 || got.LinksMerged != 1 {
		t.Errorf("result = %+v, want rows 3 links 1", got)
	}
}

// A missing key must fail BEFORE the request — a silent 403 from the daemon is
// a worse diagnosis than "you never set a key".
func TestPostMeshAdmin_NoKeyShortCircuits(t *testing.T) {
	base, hc, _, path := meshAdminStub(t, http.StatusOK, `{}`)
	_, err := postMeshAdmin(base, "", "/api/brain/admin/mesh/errors/clear", hc)
	if err == nil {
		t.Fatal("want an error with no admin key")
	}
	if !strings.Contains(err.Error(), "admin key") {
		t.Errorf("error should name the missing key, got %q", err)
	}
	if *path != "" {
		t.Errorf("no request should have been sent, but got path %q", *path)
	}
}

func TestPostMeshAdmin_ForbiddenIsTyped(t *testing.T) {
	base, hc, _, _ := meshAdminStub(t, http.StatusForbidden, `{"error":"nope"}`)
	_, err := postMeshAdmin(base, "wrong", "/api/brain/admin/mesh/errors/clear", hc)
	if err == nil {
		t.Fatal("want an error on 403")
	}
	if !strings.Contains(err.Error(), "rejected") {
		t.Errorf("403 should read as a rejected key, got %q", err)
	}
}

func TestPostMeshAdmin_NotFoundIsTyped(t *testing.T) {
	base, hc, _, _ := meshAdminStub(t, http.StatusNotFound, `{"error":"nope"}`)
	_, err := postMeshAdmin(base, "k", "/api/brain/admin/mesh/peers/gone/retry", hc)
	if err == nil {
		t.Fatal("want an error on 404")
	}
	if !strings.Contains(err.Error(), "unknown peer") {
		t.Errorf("404 should read as an unknown peer, got %q", err)
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/cdowning/workspaces/profiles/personal/code/phantom-ink/app && go test ./ -run TestPostMeshAdmin -v`
Expected: FAIL — `undefined: postMeshAdmin`

- [ ] **Step 3: Write minimal implementation**

In `app/config.go`, add to the setting constants block:

```go
	// settingMeshAdminKey is the phantom-brain operator key (PB_ADMIN_KEY) used
	// for the mesh remediation actions. Distinct from the per-vault bearer
	// tokens: those authenticate a vault, this authorises node-wide admin ops.
	settingMeshAdminKey = "mesh_admin_key"
```

In `app/app_mesh.go`, add:

```go
// MeshActionResult is the union of the three remediation responses — each
// endpoint fills the fields it owns and leaves the rest zero.
type MeshActionResult struct {
	RowsMerged   int  `json:"rows_merged"`
	LinksMerged  int  `json:"links_merged"`
	BlobsFetched int  `json:"blobs_fetched"`
	Cleared      int  `json:"cleared"`
	Reset        bool `json:"reset"`
}

// postMeshAdmin POSTs one mesh remediation action. Free function over its
// dependencies so it is testable without an App.
//
// A missing key short-circuits before the request: the daemon's 403 for an
// unset PB_ADMIN_KEY and for a wrong key are indistinguishable, and "you never
// set a key" is the more useful of the two diagnoses.
func postMeshAdmin(base, key, path string, hc *http.Client) (MeshActionResult, error) {
	var out MeshActionResult
	if strings.TrimSpace(key) == "" {
		return out, fmt.Errorf("no mesh admin key configured — set one in Settings")
	}
	req, err := http.NewRequest(http.MethodPost, strings.TrimRight(base, "/")+path, nil)
	if err != nil {
		return out, fmt.Errorf("build mesh action request: %w", err)
	}
	req.Header.Set("X-API-Key", key)
	resp, err := hc.Do(req)
	if err != nil {
		return out, fmt.Errorf("mesh daemon unreachable at %s: %w", base, err)
	}
	defer resp.Body.Close()

	switch {
	case resp.StatusCode == http.StatusForbidden:
		return out, fmt.Errorf("mesh admin key rejected by the daemon")
	case resp.StatusCode == http.StatusNotFound:
		return out, fmt.Errorf("unknown peer — the peer list is stale, refresh and retry")
	case resp.StatusCode == http.StatusServiceUnavailable:
		return out, fmt.Errorf("sync is disabled on this node")
	case resp.StatusCode < 200 || resp.StatusCode >= 300:
		return out, fmt.Errorf("mesh action failed: HTTP %d", resp.StatusCode)
	}
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return out, fmt.Errorf("decode mesh action result: %w", err)
	}
	return out, nil
}
```

Add `"strings"` to the file's imports.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/cdowning/workspaces/profiles/personal/code/phantom-ink/app && go test ./ -run TestPostMeshAdmin -race -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/cdowning/workspaces/profiles/personal/code/phantom-ink
git add app/config.go app/app_mesh.go app/app_mesh_test.go
git commit -m "feat(mesh): typed POST helper for the daemon's admin actions

A missing key fails before the request — the daemon cannot distinguish
'no key configured' from 'wrong key', and the operator needs to."
```

---

### Task 8: Wails-bound mesh actions

**Repo:** phantom-ink

**Files:**
- Modify: `app/app_mesh.go`
- Test: `app/app_mesh_test.go`

**Interfaces:**
- Consumes: `postMeshAdmin`, `MeshActionResult`, `settingMeshAdminKey` from Task 7; existing `a.meshURL()`.
- Produces, all on `*App` (so Wails binds them):
  - `func (a *App) GetMeshAdminKeySet() bool`
  - `func (a *App) SetMeshAdminKey(key string) error`
  - `func (a *App) RetryMeshPeer(id string) (MeshActionResult, error)`
  - `func (a *App) ResetMeshPeerCursor(id string) (MeshActionResult, error)`
  - `func (a *App) ClearMeshErrors() (MeshActionResult, error)`

- [ ] **Step 1: Write the failing test**

Append to `app/app_mesh_test.go`:

```go
// The three actions must target the exact daemon paths the server mounts. A
// typo here is a 404 at runtime with no compiler help, so pin them.
func TestMeshActionPaths(t *testing.T) {
	cases := []struct {
		name string
		call func(base, key string, hc *http.Client) error
		want string
	}{
		{"retry", func(b, k string, hc *http.Client) error {
			_, err := postMeshAdmin(b, k, meshRetryPath("p1"), hc)
			return err
		}, "/api/brain/admin/mesh/peers/p1/retry"},
		{"reset", func(b, k string, hc *http.Client) error {
			_, err := postMeshAdmin(b, k, meshResetCursorPath("p1"), hc)
			return err
		}, "/api/brain/admin/mesh/peers/p1/reset-cursor"},
		{"clear", func(b, k string, hc *http.Client) error {
			_, err := postMeshAdmin(b, k, meshClearErrorsPath, hc)
			return err
		}, "/api/brain/admin/mesh/errors/clear"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			base, hc, _, path := meshAdminStub(t, http.StatusOK, `{}`)
			if err := tc.call(base, "k", hc); err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if *path != tc.want {
				t.Fatalf("path = %q, want %q", *path, tc.want)
			}
		})
	}
}

// A peer id with a slash or space must not forge a path.
func TestMeshRetryPath_EscapesID(t *testing.T) {
	got := meshRetryPath("a/../b")
	if strings.Contains(got, "..") {
		t.Fatalf("path traversal not escaped: %q", got)
	}
}

// An OLDER daemon sends no recent_errors / errors_15m / errors_24h. The panel
// must keep working against it, so decoding must leave the new fields zero
// rather than failing. This is the degradation contract in test form.
func TestMeshStatus_DecodesOldDaemonPayload(t *testing.T) {
	const old = `{"node_id":"ncm5","sync_enabled":true,
	  "peers":[{"id":"p1","base_url":"http://x:9998","profile":"personal",
	            "live":true,"db_lag":"0","links_lag":"0","cursor_age":"10h"}],
	  "metrics":{"pb_sync_errors_total":"996"}}`
	var got MeshStatus
	if err := json.Unmarshal([]byte(old), &got); err != nil {
		t.Fatalf("old payload must still decode: %v", err)
	}
	if got.Errors15m != 0 || got.RecentErrors != nil {
		t.Errorf("new fields should be zero, got %d / %v", got.Errors15m, got.RecentErrors)
	}
	if len(got.Peers) != 1 || got.Peers[0].LastError != nil || got.Peers[0].Errors24h != 0 {
		t.Errorf("peer new fields should be zero, got %+v", got.Peers)
	}
	if got.Metrics.ErrorsTotal != "996" {
		t.Errorf("existing fields must still decode, got %q", got.Metrics.ErrorsTotal)
	}
}
```

Add `"encoding/json"` to the test file's imports.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/cdowning/workspaces/profiles/personal/code/phantom-ink/app && go test ./ -run 'TestMeshActionPaths|TestMeshRetryPath' -v`
Expected: FAIL — `undefined: meshRetryPath`

- [ ] **Step 3: Write minimal implementation**

Append to `app/app_mesh.go`:

```go
const meshClearErrorsPath = "/api/brain/admin/mesh/errors/clear"

// Peer ids come from the daemon but round-trip through the frontend, so escape
// them rather than trusting the shape.
func meshRetryPath(id string) string {
	return "/api/brain/admin/mesh/peers/" + url.PathEscape(id) + "/retry"
}

func meshResetCursorPath(id string) string {
	return "/api/brain/admin/mesh/peers/" + url.PathEscape(id) + "/reset-cursor"
}

// meshAdminKey reads the stored operator key, falling back to an ambient
// PB_ADMIN_KEY so a developer running the daemon locally needs no setup.
func (a *App) meshAdminKey() string {
	if a.db != nil {
		if k := strings.TrimSpace(a.db.GetSetting(settingMeshAdminKey, "")); k != "" {
			return k
		}
	}
	return strings.TrimSpace(os.Getenv("PB_ADMIN_KEY"))
}

// GetMeshAdminKeySet reports whether an admin key is available, so the panel
// can disable the action buttons with a reason instead of surfacing a 403.
func (a *App) GetMeshAdminKeySet() bool { return a.meshAdminKey() != "" }

// SetMeshAdminKey stores the operator key.
func (a *App) SetMeshAdminKey(key string) error {
	if a.db == nil {
		return fmt.Errorf("no local database")
	}
	return a.db.SetSetting(settingMeshAdminKey, strings.TrimSpace(key))
}

func (a *App) meshActionClient() *http.Client {
	return &http.Client{Timeout: 30 * time.Second}
}

// RetryMeshPeer pulls one peer immediately instead of waiting for the next
// sync round — the "did my fix work?" button.
func (a *App) RetryMeshPeer(id string) (MeshActionResult, error) {
	return postMeshAdmin(a.meshURL(), a.meshAdminKey(), meshRetryPath(id), a.meshActionClient())
}

// ResetMeshPeerCursor forces a full op-log re-read for one peer. Costs
// bandwidth, cannot lose data; the fix for a wedged or stale cursor.
func (a *App) ResetMeshPeerCursor(id string) (MeshActionResult, error) {
	return postMeshAdmin(a.meshURL(), a.meshAdminKey(), meshResetCursorPath(id), a.meshActionClient())
}

// ClearMeshErrors empties the daemon's retained error ring.
func (a *App) ClearMeshErrors() (MeshActionResult, error) {
	return postMeshAdmin(a.meshURL(), a.meshAdminKey(), meshClearErrorsPath, a.meshActionClient())
}
```

Add `"net/url"`, `"os"`, and `"time"` to the imports if not present.

Then extend the `MeshStatus` and `MeshPeer` structs in the same file to carry the new daemon fields:

```go
// SyncError mirrors nodesync.SyncError — one retained sync failure.
type SyncError struct {
	At      string `json:"at"`
	Peer    string `json:"peer"`
	Profile string `json:"profile"`
	Phase   string `json:"phase"`
	Class   string `json:"class"`
	Err     string `json:"err"`
}
```

Add to `MeshPeer`:

```go
	LastError *SyncError `json:"last_error,omitempty"`
	Errors24h int        `json:"errors_24h"`
```

Add to `MeshStatus`:

```go
	RecentErrors []SyncError `json:"recent_errors,omitempty"`
	Errors15m    int         `json:"errors_15m"`
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/cdowning/workspaces/profiles/personal/code/phantom-ink/app && go test ./ -run 'TestMesh|TestPostMeshAdmin' -race -v`
Expected: PASS, including `TestMeshStatus_DecodesOldDaemonPayload`

- [ ] **Step 5: Regenerate the Wails bindings**

Run: `cd /Users/cdowning/workspaces/profiles/personal/code/phantom-ink && just app-build 2>&1 | tail -5`
Expected: build succeeds and `app/frontend/wailsjs/go/main/App.d.ts` now declares `RetryMeshPeer`, `ResetMeshPeerCursor`, `ClearMeshErrors`, `GetMeshAdminKeySet`, `SetMeshAdminKey`.

Verify: `grep -c "RetryMeshPeer\|ClearMeshErrors" app/frontend/wailsjs/go/main/App.d.ts` → at least `2`.

- [ ] **Step 6: Commit**

```bash
cd /Users/cdowning/workspaces/profiles/personal/code/phantom-ink
git add app/app_mesh.go app/app_mesh_test.go app/frontend/wailsjs
git commit -m "feat(mesh): bind the three remediation actions to the frontend"
```

---

### Task 9: Honest error counter

**Repo:** phantom-ink

**Files:**
- Modify: `app/frontend/src/lib/panels/MeshPanel.svelte`

**Interfaces:**
- Consumes: `MeshStatus.errors_15m`, `MeshStatus.recent_errors`, `MeshPeer.errors_24h`, `MeshPeer.last_error` from Task 8.
- Produces: `errorsAreLive` and `lastErrorAge` deriveds, consumed by Task 10.

This task is what actually answers the original complaint: a red 996 for a two-day-old healed outage.

- [ ] **Step 1: Extend the TypeScript interfaces**

In the `<script>` block, add above `interface MeshPeer`:

```ts
  // One retained sync failure from the daemon's ring. Optional throughout:
  // an older daemon sends none, and the panel must still work against it.
  interface SyncError {
    at: string;
    peer: string;
    profile: string;
    phase: string;
    class: string;
    err: string;
  }
```

Add to `interface MeshPeer`:

```ts
    last_error?: SyncError;
    errors_24h?: number;
```

Add to `interface MeshStatus`:

```ts
    recent_errors?: SyncError[];
    errors_15m?: number;
```

- [ ] **Step 2: Add the recency deriveds**

Below the existing `const peers = $derived(...)` line:

```ts
  // The counter is cumulative since daemon start and never decays, so a healed
  // outage stays red forever. errors_15m is the live signal; the counter alone
  // is history.
  const recentErrors = $derived(status?.recent_errors ?? []);
  const errorsAreLive = $derived((status?.errors_15m ?? 0) > 0);
  const lastErrorAge = $derived.by(() => {
    if (recentErrors.length === 0) return '';
    const ms = Date.parse(recentErrors[0].at);
    return Number.isFinite(ms) ? timeAgo(ms) : '';
  });
```

- [ ] **Step 3: Make the counter reflect it**

Replace the errors counter block:

```svelte
        <div class="counter">
          <span class="c-val err">{metric('pb_sync_errors_total')}</span>
          <span class="c-label">errors</span>
        </div>
```

with:

```svelte
        <div class="counter">
          <span class="c-val" class:err={errorsAreLive} class:stale={!errorsAreLive && metric('pb_sync_errors_total') !== '0'}
            >{metric('pb_sync_errors_total')}</span>
          <span class="c-label">errors</span>
          {#if !errorsAreLive && lastErrorAge}
            <span class="c-note">none in {lastErrorAge}</span>
          {/if}
        </div>
```

- [ ] **Step 4: Add the styles**

Next to the existing `.c-val.err` rule:

```css
  .c-val.stale { color: var(--color-warning); }
  .c-note { font-size: 0.62rem; color: var(--color-text-tertiary); }
```

If `--color-warning` is not defined in the app's theme, grep `app/frontend/src` for the token used for amber/warning states and use that instead — do not hardcode a hex value.

- [ ] **Step 5: Verify**

Run: `cd /Users/cdowning/workspaces/profiles/personal/code/phantom-ink/app/frontend && npm run check`
Expected: no new errors.

- [ ] **Step 6: Commit**

```bash
cd /Users/cdowning/workspaces/profiles/personal/code/phantom-ink
git add app/frontend/src/lib/panels/MeshPanel.svelte
git commit -m "fix(mesh): stop painting a healed outage red

pb_sync_errors_total is cumulative and never decays. Red now means
errors in the last 15 minutes; a stale count reads amber with its age."
```

---

### Task 10: Errors card with remediation buttons

**Repo:** phantom-ink

**Files:**
- Modify: `app/frontend/src/lib/panels/MeshPanel.svelte`

**Interfaces:**
- Consumes: `recentErrors`, `errorsAreLive` from Task 9; `RetryMeshPeer`, `ResetMeshPeerCursor`, `ClearMeshErrors`, `GetMeshAdminKeySet` from Task 8.

- [ ] **Step 1: Add the diagnosis table and grouping**

In the `<script>` block, below the deriveds from Task 9:

```ts
  // The class is the daemon's closed vocabulary; the diagnosis is what the
  // operator actually needs to read. Keep these in step with ClassifyError.
  const DIAGNOSIS: Record<string, string> = {
    dns: "The daemon container's resolver cannot reach this peer's hostname. Usually Docker DNS churn, not a peer outage.",
    timeout: 'The peer accepted the connection but did not answer in time.',
    refused: 'Nothing is listening at the peer URL.',
    auth: "This node's peer token is missing, wrong, or revoked.",
    proto: 'Breaking wire-version gap — finish the staggered upgrade on both nodes.',
    fenced: 'The peer is reconciling and withholding its op-log. Benign.',
    blob: 'Record metadata merged; attachment reconciliation failed.',
    pool: 'This node could not open its local database for the profile.',
    other: 'Unclassified — the message below is the whole diagnosis.',
  };

  interface ErrorGroup {
    key: string;
    peer: string;
    cls: string;
    count: number;
    latest: SyncError;
  }

  // Group by (peer × class): one row per distinct failure mode per peer, which
  // is the unit an operator acts on. recentErrors is newest-first, so the first
  // entry per key is the latest.
  const errorGroups = $derived.by<ErrorGroup[]>(() => {
    const byKey = new Map<string, ErrorGroup>();
    for (const e of recentErrors) {
      const key = `${e.peer}::${e.class}`;
      const existing = byKey.get(key);
      if (existing) existing.count++;
      else byKey.set(key, { key, peer: e.peer, cls: e.class, count: 1, latest: e });
    }
    return [...byKey.values()].sort((a, b) => b.count - a.count);
  });

  let errorsOpen = $state(false);
  let errorPeerFilter = $state<string | null>(null);
  let adminKeySet = $state(false);
  let actionBusy = $state<string | null>(null);
  let actionNote = $state<string | null>(null);

  const shownGroups = $derived(
    errorPeerFilter ? errorGroups.filter((g) => g.peer === errorPeerFilter) : errorGroups,
  );

  function errAge(e: SyncError): string {
    const ms = Date.parse(e.at);
    return Number.isFinite(ms) ? timeAgo(ms) : '';
  }

  async function runAction(label: string, fn: () => Promise<any>, note: (r: any) => string) {
    actionBusy = label;
    actionNote = null;
    try {
      actionNote = note(await fn());
    } catch (e: any) {
      actionNote = e?.message ?? String(e);
    } finally {
      actionBusy = null;
      await refresh();
    }
  }

  async function retryPeer(id: string) {
    const a = await getApi();
    if (!a) return;
    await runAction(`retry:${id}`, () => (a as any).RetryMeshPeer(id),
      (r) => `${id}: merged ${r.rows_merged ?? 0} rows, ${r.links_merged ?? 0} links`);
  }

  async function resetCursor(id: string) {
    if (!window.confirm(`Reset the sync cursor for ${id}?\n\nThe next pull re-reads its whole op-log. Costs bandwidth; cannot lose data.`)) return;
    const a = await getApi();
    if (!a) return;
    await runAction(`reset:${id}`, () => (a as any).ResetMeshPeerCursor(id), () => `${id}: cursor reset`);
  }

  async function clearErrors() {
    const a = await getApi();
    if (!a) return;
    await runAction('clear', () => (a as any).ClearMeshErrors(), (r) => `cleared ${r.cleared ?? 0} recorded errors`);
  }
```

In `refresh()`, after the `vaultAuth` block, add:

```ts
    try {
      adminKeySet = Boolean(await (a as any).GetMeshAdminKeySet());
    } catch {
      adminKeySet = false;
    }
```

- [ ] **Step 2: Make the counter a disclosure button**

Replace the errors `<div class="counter">` from Task 9 with a `<button>` of the same class plus `counter-btn`:

```svelte
        <button
          class="counter counter-btn"
          onclick={() => (errorsOpen = !errorsOpen)}
          disabled={recentErrors.length === 0}
          title={recentErrors.length ? 'show recorded errors' : 'this daemon records no error detail'}
        >
          <span class="c-val" class:err={errorsAreLive} class:stale={!errorsAreLive && metric('pb_sync_errors_total') !== '0'}
            >{metric('pb_sync_errors_total')}</span>
          <span class="c-label">errors{#if recentErrors.length} {errorsOpen ? '▾' : '▸'}{/if}</span>
          {#if !errorsAreLive && lastErrorAge}
            <span class="c-note">none in {lastErrorAge}</span>
          {/if}
        </button>
```

- [ ] **Step 3: Add the errors card**

Immediately after the closing `</section>` of the counters card:

```svelte
    {#if errorsOpen && errorGroups.length}
      <section class="card errors-card">
        <h2>
          Errors
          <span class="peer-count">{errorGroups.length} grouped by peer and cause</span>
          {#if errorPeerFilter}
            <button class="pill active" onclick={() => (errorPeerFilter = null)}>{errorPeerFilter} ✕</button>
          {/if}
          <button class="btn sm" onclick={() => void clearErrors()} disabled={!adminKeySet || actionBusy !== null}>
            clear errors
          </button>
        </h2>
        {#if !adminKeySet}
          <p class="no-match">No mesh admin key configured — actions are disabled. Set one in Settings.</p>
        {/if}
        {#if actionNote}<p class="action-note">{actionNote}</p>{/if}
        <div class="err-list">
          {#each shownGroups as g (g.key)}
            <div class="err-row">
              <div class="err-head">
                <span class="err-peer">{g.peer}</span>
                <span class="err-class {g.cls}">{g.cls}</span>
                <span class="err-count">×{g.count}</span>
                <span class="err-age">{errAge(g.latest)}</span>
              </div>
              <div class="err-diag">{DIAGNOSIS[g.cls] ?? DIAGNOSIS.other}</div>
              <div class="err-msg">{g.latest.err}</div>
              <div class="err-actions">
                <button class="btn sm" onclick={() => void retryPeer(g.peer)}
                  disabled={!adminKeySet || actionBusy !== null}>
                  {actionBusy === `retry:${g.peer}` ? 'retrying…' : 'retry peer'}
                </button>
                {#if g.latest.phase === 'pull' || g.latest.phase === 'links'}
                  <button class="btn sm danger" onclick={() => void resetCursor(g.peer)}
                    disabled={!adminKeySet || actionBusy !== null}>
                    {actionBusy === `reset:${g.peer}` ? 'resetting…' : 'reset cursor'}
                  </button>
                {/if}
              </div>
            </div>
          {/each}
        </div>
      </section>
    {/if}
```

- [ ] **Step 4: Add the peer-row warning glyph**

In the peer table's first `<td>`, after the `peer-url` div:

```svelte
                    {#if p.errors_24h}
                      <button class="peer-warn" title={`${p.errors_24h} errors in 24h — show them`}
                        onclick={() => { errorPeerFilter = p.id; errorsOpen = true; }}>⚠ {p.errors_24h}</button>
                    {/if}
```

- [ ] **Step 5: Add the styles**

In the `<style>` block:

```css
  .counter-btn { background: none; border: none; padding: 0; text-align: left; cursor: pointer; font: inherit; }
  .counter-btn:disabled { cursor: default; }

  .errors-card h2 { display: flex; align-items: center; gap: var(--spacing-sm); flex-wrap: wrap; }
  .err-list { display: flex; flex-direction: column; gap: var(--spacing-sm); }
  .err-row { border: 1px solid var(--color-border); border-radius: 6px; padding: var(--spacing-sm); }
  .err-head { display: flex; align-items: baseline; gap: var(--spacing-sm); flex-wrap: wrap; }
  .err-peer { font-family: var(--font-mono); font-weight: 600; }
  .err-class { font-size: 0.66rem; text-transform: uppercase; letter-spacing: 0.04em;
    padding: 1px 6px; border-radius: 999px; border: 1px solid var(--color-border); }
  .err-count, .err-age { font-size: 0.72rem; color: var(--color-text-muted); }
  .err-diag { margin-top: 4px; font-size: 0.82rem; }
  .err-msg { margin-top: 4px; font-family: var(--font-mono); font-size: 0.7rem;
    color: var(--color-text-tertiary); overflow-wrap: anywhere; }
  .err-actions { margin-top: 6px; display: flex; gap: 6px; }
  .action-note { font-size: 0.78rem; color: var(--color-text-secondary); margin: 0 0 var(--spacing-sm); }
  .peer-warn { margin-top: 2px; font-size: 0.66rem; background: none; cursor: pointer;
    border: 1px solid var(--color-border); border-radius: 999px; padding: 0 6px; color: var(--color-warning); }
```

Reuse the panel's existing `.btn` class; if no `.sm` or `.danger` modifier exists, grep the file for how other compact/destructive buttons are styled and follow that rather than inventing tokens.

- [ ] **Step 6: Verify**

Run:
```bash
cd /Users/cdowning/workspaces/profiles/personal/code/phantom-ink/app/frontend && npm run check
cd /Users/cdowning/workspaces/profiles/personal/code/phantom-ink/app && go test ./... -race 2>&1 | tail -10
```
Expected: no new svelte-check errors; Go tests PASS.

- [ ] **Step 7: Verify against the real daemon**

The daemon on this machine is the container `phantom_daemon` and has real recorded history. After rebuilding and restarting it with the Task 1–6 changes:

```bash
curl -s http://127.0.0.1:9998/admin/mesh/status | head -40
```
Expected: the payload now carries `errors_15m` and (once errors accrue) `recent_errors`.

Then run the app (`just app-dev`), open Brain Mesh, and confirm: the counter is amber rather than red when `errors_15m` is 0, clicking it opens the grouped card, and the buttons are disabled with the "no admin key" note until `PB_ADMIN_KEY` is set in the daemon's environment and the matching key is saved in the app.

- [ ] **Step 8: Commit**

```bash
cd /Users/cdowning/workspaces/profiles/personal/code/phantom-ink
git add app/frontend/src/lib/panels/MeshPanel.svelte
git commit -m "feat(mesh): grouped error detail with per-class diagnosis and actions

Errors group by peer and cause, each carrying what the class means and
the buttons that class warrants. Actions disable with a reason when no
admin key is set rather than surfacing a bare 403."
```

---

## Verification

Before opening either PR:

```bash
# phantom-brain
cd /Users/cdowning/workspaces/profiles/personal/code/phantom-brain
go vet ./internal/...
go test ./internal/... -race
make

# phantom-ink
cd /Users/cdowning/workspaces/profiles/personal/code/phantom-ink/app
go vet ./...
go test ./... -race
cd frontend && npm run check
```

Then confirm by hand, against the running daemon, the one property no unit test covers: that `/admin/mesh/status` still answers with no credentials and that nothing in its `recent_errors` resembles a token.

```bash
curl -s http://127.0.0.1:9998/admin/mesh/status | grep -ciE '[0-9a-f]{32,}' || echo "no long hex strings — clean"
```

## Delivery

Two PRs. phantom-brain (`feat/mesh-error-log`) merges first; phantom-ink (`feat/mesh-error-remediation`) second. The ink side treats every new field as optional, so the two can deploy in either order without breaking the panel.

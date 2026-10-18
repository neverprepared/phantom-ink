# Mesh error visibility and local remediation

**Date:** 2026-10-18
**Repos:** `phantom-brain` (daemon), `phantom-ink` (desktop app)
**Status:** approved design, not yet implemented

## Problem

The Brain Mesh panel shows a single red number — `996 ERRORS` — and nothing else.
It is unactionable in three distinct ways.

**It does not say what broke.** `pb_sync_errors_total` is one counter incremented at seven
distinct call sites in `PullWorker.Tick`. The detail (which peer, which
phase, what the error said) goes to the daemon's `slog` and nowhere else. To learn
anything an operator must shell onto the host and `docker logs phantom_daemon`.

**It does not say whether it is still broken.** The counter is cumulative since
daemon start and never decays. On ncm5 today it reads 996 while the mesh is
healthy: every peer `live: true`, every lag `0`, last pull error 2026-10-17 02:06.
The panel renders a two-day-old, already-healed outage as a live red alarm. A
number that is red when nothing is wrong trains the operator to ignore it.

**It offers no way to act.** Even with the diagnosis in hand, every fix is a
terminal round-trip to the host.

### What the errors on ncm5 actually are

Measured from the running daemon's log (4920 pull errors over the container's
lifetime; the in-process counter reads lower because it resets on restart):

| Cause | Count |
|---|---|
| `server misbehaving` (Docker embedded DNS at 127.0.0.11) | 2101 |
| `no such host` (same resolver, different phase) | 1650 |
| `context deadline exceeded` | 778 |
| `connection refused` | 26 |
| `EOF` | 1 |

Roughly 3750 of 4920 are one root cause: the daemon container's embedded DNS
intermittently fails to resolve `node16.mesh.neverprepared.com` and
`node64.mesh.neverprepared.com`. That is an infrastructure bug in the daemon's
compose networking, **out of scope here** and tracked separately — this work makes
it legible and retryable, it does not fix it.

### A second signal, surfaced but not addressed

ncm5 reports `pb_sync_rows_merged_total = 0` across 8959 rounds while
`storage-gsa-memory`'s cursor age is **47d** and its `db_lag` is **0**. Zero lag
against a 47-day-stale cursor is suspicious — plausibly a peer that answers
`/health` but serves an empty or wrong op-log. This spec does not diagnose it, but
the `reset-cursor` action below is the probe that would settle it.

## Non-goals

- Fixing the container DNS bug.
- Restarting the daemon container or any host process from the panel.
- Acting on remote peers, or pulling runner/fleet credentials into the mesh panel.
- Persisting error history across daemon restarts.

The last one is a deliberate choice, not an omission. The counter already resets on
restart; a ring that outlived it would make "is this live?" harder to answer, which
is the question the panel most needs to answer.

## Architecture

Three units, each independently testable, in dependency order.

```
phantom-brain
  nodesync.ErrorLog          in-memory ring; Record() / Snapshot()
  nodesync.ClassifyError     error -> stable class string
  PullWorker.Errors          wired at all 7 mErrors.Add(1) sites
        |
        v
  GET /admin/mesh/status     + recent_errors, errors_15m, per-peer last_error
       (unauthenticated — still carries no secrets)
  POST /api/brain/admin/mesh/...   three actions, PB_ADMIN_KEY-gated
        |
        v
phantom-ink
  app_mesh.go                RetryMeshPeer / ResetMeshPeerCursor / ClearMeshErrors
  MeshPanel.svelte           errors card, per-class diagnosis, action buttons
```

## Unit 1 — `nodesync.ErrorLog`

A fixed-size ring recording what the counter currently throws away.

```go
type SyncError struct {
    At      time.Time `json:"at"`
    Peer    string    `json:"peer"`
    Profile string    `json:"profile"`
    Phase   string    `json:"phase"`  // pool|pull|links|tombstone|blob|gc
    Class   string    `json:"class"`  // see ClassifyError
    Err     string    `json:"err"`    // verbatim error text
}

type ErrorLog struct { /* mutex + []SyncError, cap 200 */ }
func (l *ErrorLog) Record(e SyncError)
func (l *ErrorLog) Snapshot() []SyncError   // newest first
func (l *ErrorLog) Since(d time.Duration) int
func (l *ErrorLog) Clear()
```

`Phase` is *where* in the round it failed and comes from the call site. `Class` is
*what kind* of failure and comes from `ClassifyError`:

| Class | Matches | Diagnosis shown to the operator |
|---|---|---|
| `dns` | `no such host`, `server misbehaving`, `lookup ` | The daemon container's resolver cannot reach the peer's hostname. Usually Docker DNS churn, not a peer outage. |
| `timeout` | `context deadline exceeded`, `Client.Timeout` | The peer accepted the connection but did not answer in time. |
| `refused` | `connection refused` | Nothing is listening at the peer's URL. |
| `auth` | HTTP 401/403 in the error text | This node's peer token is missing, wrong, or revoked. |
| `proto` | `errors.Is(err, ErrProtocolMismatch)` | Breaking wire-version gap; finish the staggered upgrade. |
| `fenced` | `errors.Is(err, ErrPeerFenced)` | Peer is reconciling and withholding its op-log. Benign. |
| `blob` | phase is `blob` or `gc` | Record metadata merged; attachment reconciliation failed. |
| `pool` | phase is `pool` | This node could not open the local per-profile database. |
| `other` | anything else | Unclassified — the verbatim message is the diagnosis. |

Class is the hinge of the whole design: it drives the grouping in the UI **and**
which remediation button is offered. Sentinel checks (`proto`, `fenced`) run before
substring checks. Note `proto` and `fenced` do not currently increment the counter —
they are deliberately silent skips — so they appear in the ring only if a future
call site records them; the classifier handles them so that stays a one-line change.

`PullWorker` gains `Errors *ErrorLog`. All seven existing `w.mErrors.Add(1)` sites are paired
with a `Record`. Nil-safe: a nil `Errors` makes `Record` a no-op, so existing
construction sites and tests compile unchanged.

## Unit 2 — daemon endpoints

### Read: extend the existing status payload

`nodesync.Report` gains:

```go
RecentErrors []SyncError `json:"recent_errors,omitempty"`
Errors15m    int         `json:"errors_15m"`
```

and `PeerStatus` gains `LastError *SyncError` and `Errors24h int`.

This stays on the **unauthenticated** `/admin/mesh/status`. The existing contract is
that the report carries no secrets; `SyncError` holds peer ids, profiles, and error
text, which is the same class of information `PeerStatus` already exposes. Error
text is daemon-generated and never includes a token — the peer token lives in the
`Authorization` header, not the URL, so the `Get "<url>"` text in a transport error
cannot leak it. **Implementation must verify this holds for every recorded site**
rather than assume it.

`BuildDeps` gains an `ErrorLog *ErrorLog` field. The CLI path (`pbrainctl server
mesh status`) has no in-process worker, so it passes nil and the fields are omitted —
the CLI keeps working, just without error detail.

### Write: three actions behind `PB_ADMIN_KEY`

Mounted inside the existing admin group in `buildRouter`, under
`d.adminAuthMiddleware()` — the same door `/admin/profiles` uses, which fails closed
when `PB_ADMIN_KEY` is unset and compares the `X-API-Key` header in constant time.
No write path is added to the unauthenticated `/admin/mesh/` tree.

| Endpoint | Does | Returns |
|---|---|---|
| `POST /api/brain/admin/mesh/peers/{id}/retry` | One immediate pull for that peer, off-tick | `{rows_merged, links_merged, error}` |
| `POST /api/brain/admin/mesh/peers/{id}/reset-cursor` | `DELETE FROM sync_cursors WHERE peer_id=$1` on that peer's profile pool | `{reset: true}` |
| `POST /api/brain/admin/mesh/errors/clear` | Zeroes `mErrors` and the ring | `{cleared: <n>}` |

`retry` is the verification button: it gives a fresh verdict in one request instead
of waiting out a tick. It reuses the single-peer body of `Tick` — extracted into
`PullWorker.TickPeer(ctx, peer) (PeerResult, error)` so `Tick` becomes a loop over
it and the action and the scheduled round cannot drift apart. An unknown peer id is
404. Concurrency with a running tick is acceptable: the pull path is already
idempotent (cursor-driven upsert) and the worker tolerates overlapping work.

`reset-cursor` is destructive in the cheap sense — it forces a full op-log re-read,
which costs bandwidth but cannot lose data. It is the fix for a wedged cursor and the
probe for the 47d case above.

## Unit 3 — the panel

**The counter becomes an honest signal.** Red only when `errors_15m > 0`. Otherwise
amber, with the age of the most recent error beneath it ("none in 2d"). That change
alone resolves the complaint that prompted this work.

**The counter becomes a button.** Clicking expands an Errors card below the counters:
rows grouped by (peer x class), each showing count, last-seen, the verbatim message,
and the plain-English diagnosis from the class table. Each row carries the buttons its
class warrants — `dns` / `timeout` / `refused` get **Retry peer**; a stale cursor or a
`pull` phase failure also gets **Reset cursor** behind a confirm. The card header
carries **Clear errors**.

Peer rows in the existing table gain a warning glyph when `errors_24h > 0`; clicking
it filters the error card to that peer.

**Go bridge.** `app_mesh.go` gains `RetryMeshPeer(id)`, `ResetMeshPeerCursor(id)`, and
`ClearMeshErrors()`, each POSTing with an `X-API-Key` header. `ServiceConfig` has no
credential field, so the key comes from a new `mesh_admin_key` setting in the app's
SQLite settings table, following the `settingAPIKey` pattern and seeded from an
ambient `PB_ADMIN_KEY` when one is present. With no key configured the buttons render
**disabled** with "set a mesh admin key in Settings" — never a silent 403.

**Degradation.** The new status fields are optional in the TypeScript interface. An
old daemon returns no `recent_errors`; the panel then renders the counter as it does
today and hides the errors card. The app must not require a daemon upgrade to keep
working.

## Error handling

- Daemon unreachable: unchanged — existing `loadError` path.
- Action returns 403: surface "admin key rejected by the daemon", not a raw status.
- Action returns 404 (unknown peer): the peer list is stale; refresh and say so.
- `retry` succeeding but still erroring: show the fresh error in place. A retry that
  fails the same way is a useful answer, not a failure of the button.

## Testing

**phantom-brain**
- Table test for `ClassifyError` over every row of the class table, including the two
  sentinel cases and an unmatched error falling to `other`.
- `ErrorLog`: eviction past cap, newest-first ordering, `Since` windowing, `Clear`.
- `TickPeer` extraction: existing `PullWorker` tests must pass unchanged (this is the
  regression guard that the refactor preserved behaviour), plus one test that a
  recorded error lands in the ring with the right phase and class.
- Handler tests mirroring `handlers_admin_test.go`: 403 on unset `PB_ADMIN_KEY`, 403
  on wrong key, 200 on correct key, 404 on unknown peer id, for all three actions.
- One test asserting `/admin/mesh/status` stays reachable without credentials and
  that no recorded error text contains a peer token.

**phantom-ink**
- `httptest`-backed tests for the three new Wails methods: header is sent, non-2xx
  becomes a typed error, missing key short-circuits before the request.
- Existing `GetMeshStatus` tests extended with a payload that omits the new fields,
  asserting the old-daemon path.
- `cd app && go test ./... -race`; `cd app/frontend && npm run check`.

## Delivery

Two PRs, in order. phantom-brain first — the panel cannot read fields that do not
exist. phantom-ink second, written so it degrades against a daemon without the
change, which means the two can be deployed independently and in either order.

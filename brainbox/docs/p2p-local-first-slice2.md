# Local-first / P2P store — Slice 2: merge engine + tombstones

Builds on Slice 1 (additive ULID / node_id / deleted_at columns). Slice 2 makes
the substrate *do* something: a deterministic, unit-tested MERGE engine plus the
first real tombstone conversion. Still no network — the transport that moves rows
between nodes is Slice 3 (below), deliberately deferred because it encodes
deployment decisions.

## What landed

### `node_sync.py` — the merge engine (transport-agnostic)

Two merge classes, matching the table taxonomy in
`docs/p2p-local-first-slice1.md`:

- **Op-log union** (`import_events` / `export_events`) for `agent_events`.
  `INSERT ... ON CONFLICT (event_ulid) DO NOTHING` — idempotent and
  order-independent. Key property: a merged row is assigned a fresh **local**
  `seq` on insert, so the existing seq-based consumers (rules engine, OpenSearch
  sink) pick up merged remote events **with no cursor cutover**. This is why the
  Slice-1 plan's "seq→ulid cursor cutover" turned out to be unnecessary for event
  flow — the local `seq` is simply "order this node learned the event", which is
  exactly what a per-node consumer wants. `event_ulid` is only the cross-node
  dedup identity, and `export_events(since_ulid)` uses it as a resumable cursor.

- **Owner-keyed LWW** (`merge_owner_row` / `merge_owner_rows`), tombstone-aware.
  Apply a remote row iff its `updated_at` is newer than the local row's. A
  tombstone needs no special case: it is just a row whose newest update carries
  `deleted_at`, so "newest wins" deletes locally when the tombstone is newest and
  a later re-creation revives it. Exact-tie → keep local (deterministic per node).

- **Pull orchestration** (`sync_pull_events`) — pure: takes a `fetch(since, limit)`
  callable (HTTP client, in-process peer, or test stub), merges the batch, returns
  `(num_new, new_cursor_ulid)`. All transport lives in `fetch`.

### Runner tombstones (first delete→tombstone conversion)

`delete_runner` now UPDATEs `deleted_at`/`updated_at` instead of `DELETE`;
`load_all_runners` filters `WHERE deleted_at IS NULL`; `upsert_runner` clears the
tombstone on re-registration (a returning runner revives, `updated_at` bump wins
LWW). This is the pattern every owner-keyed delete follows.

### Tests — `test_node_sync.py` (10) + `test_p2p_slice1.py` (9)

Two "nodes" simulated in one process (remote rows are dicts). Covers op-log
idempotency, ulid-less rows skipped, local-seq assignment, cursor export/pull,
owner LWW (newer wins / older skipped / tie keeps local), tombstone-wins, and the
runner delete→tombstone→revive round-trip.

## Deferred, on purpose

- **agent_state reprojection.** `import_events` unions the log only; folding
  merged remote events into the derived `agent_state` projection is a follow-up
  (agent_state is meant to be rebuilt from the log). Local agent_state reads
  reflect local-origin state until then.
- **Remaining delete→tombstone conversions.** `session_store.delete`, and the
  config/token tables — the token tables need *remove-wins* (revocation must
  dominate), which is a different merge rule; not in this slice.
- **Config + consensus tables.** The 5 shared-mutable config tables (single-owner
  or MV-register) and the 3 consensus/token tables are untouched — they were
  flagged hard/security-critical and get their own design.

## Slice 3a — server half of pull-sync (BUILT, flag-gated off)

Landed, inert until enabled:
- `SyncSettings` (`CL_SYNC__ENABLED` default **false**, `CL_SYNC__PEERS`,
  `CL_SYNC__BATCH_LIMIT`, `CL_SYNC__INTERVAL_SECS`).
- `GET /api/sync/events?since=<ulid>&limit=<n>` → `node_sync.export_events(...)`,
  returns `{events, count, cursor}`. **Returns 404 unless `CL_SYNC__ENABLED`** —
  the surface is invisible in a default deployment, so this changes nothing until
  turned on. Tested: 404-when-disabled, and cursor-correct export when enabled.

A peer node can now pull this node's event log by ULID cursor. What remains is
the *client* that does the pulling.

## Slice 3b — the pull client (NEEDS A DECISION, not built)

The merge engine and the export endpoint are done; what remains is the background
tick that fetches peers and applies `sync_pull_events`. Intentionally not
auto-built — it hard-codes deployment choices AND must navigate a known gotcha:

> **Transport gotcha:** the daemon's outbound `httpx` to LAN/tailnet destinations
> reproduces `OSError(65, 'No route to host')` on macOS/Python 3.14 (see
> CLAUDE.md "Known issues"). The pull client MUST use the curl-subprocess pattern
> (`ollama.py::acurl_request`), not httpx, exactly like the Ollama calls do.

Decisions required:

1. **Pull vs push.** Pull (each node periodically `fetch`es peers' `export_*`)
   composes cleanly with `sync_pull_events` and needs no inbound auth beyond the
   existing API token. Recommended default.
2. **Peer discovery.** Static peer list in `brainbox.env` (`CL_SYNC_PEERS=host:port,…`)
   vs. tailnet enumeration. Static is the safe first step.
3. **Inter-node auth.** Reuse the profile/service token, or a dedicated sync token.
4. **Cadence + scope.** How often to pull; which tables (start with `agent_events`
   only — highest value, lowest risk).

Proposed thin wiring when decided (all flag-gated `CL_SYNC_ENABLED`, default off):
- `GET /api/sync/events?since=<ulid>&limit=<n>` → `node_sync.export_events(...)`
- a background tick that, per configured peer, calls its `/api/sync/events` and
  feeds `sync_pull_events`, persisting the per-peer cursor (a row per peer in
  `event_rule_cursor`-style storage, keyed by `node_id`).

Nothing above changes existing behavior until `CL_SYNC_ENABLED=1`, so it is safe
to land incrementally once the transport shape is chosen.

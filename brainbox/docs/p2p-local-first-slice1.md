# Local-first / P2P store — Slice 1: additive identity + tombstone substrate

**Goal of the overall effort:** drop the single centralized Postgres. Each node
writes to its own local store and syncs as needed. See the table classification
that motivated this (op-log / owner-keyed / shared-mutable / needs-consensus).

**Scope of Slice 1 (this doc): additive-only.** Add the columns that later
slices need — a globally-unique row identity (`*_ulid`), an owning `node_id`, and
`deleted_at` tombstones — and **dual-write them on every insert/delete**. Change
**no read paths, no ordering, no delete semantics, no merge logic** yet. Result:
nothing breaks, the existing suite still passes, and the substrate is in place.

Deferred to later slices (explicitly NOT here):
- Cursor cutover `event_rule_cursor.last_seq` → `last_ulid` (behavior change).
- `DELETE` → tombstone `UPDATE` + `WHERE deleted_at IS NULL` read filters.
- Actual gossip/replication + per-class merge (op-log union, owner-map LWW-by-owner).
- Anything touching the 5 shared-mutable config tables or the 3 consensus/token tables.

Why additive is safe: every new column is nullable or defaulted; Postgres
`ADD COLUMN IF NOT EXISTS` is idempotent (store.py already uses this pattern for
`gateway_tokens.residency_ceiling`); the local autoincrement `seq`/`id` PKs stay
exactly as they are and remain the read path. The ULID is written *alongside*,
unused by reads until Slice 2.

New dependency: none. New module: `node_identity.py` (`ulid()`, `node_id()`).

---

## The 10 "free" tables and their Slice-1 changes

Legend — class drives what columns get added:
- **op-log** → needs a unique row identity that sorts by time (`*_ulid`) + `node_id`.
- **owner-keyed map** → needs `owner_node` (provenance/partition) + `deleted_at`.
- **derived** → no schema change; rebuilt from an op-log.

### 1. `agent_events` — op-log (the core event log)

```sql
ALTER TABLE agent_events ADD COLUMN IF NOT EXISTS event_ulid TEXT;
ALTER TABLE agent_events ADD COLUMN IF NOT EXISTS node_id    TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_events_ulid ON agent_events(event_ulid);
-- time-ordered scan index for the Slice-2 ULID cursor (unused by reads in Slice 1)
CREATE INDEX IF NOT EXISTS idx_agent_events_ulid_ord ON agent_events(event_ulid);
```

- Keep `seq BIGINT IDENTITY PRIMARY KEY` — it stays the LOCAL order + read cursor.
  It is a local materialization detail, never synced, so its single-writer nature
  is fine.
- Write path (insert): stamp `event_ulid = ulid(ts_ms)` (use the event's own `ts`
  so order is faithful) and `node_id = node_id()`.
- Backfill (one-time, optional, deterministic): derive a ULID from each existing
  row's `ts` so history sorts correctly.
  ```sql
  UPDATE agent_events SET node_id = :this_node WHERE node_id IS NULL;
  -- event_ulid backfill done in Python: ulid(ts) per row (needs randomness),
  -- ordered by (ts, seq) so ties are stable. Run once via a small script.
  ```

### 2. `session_history` — op-log

```sql
ALTER TABLE session_history ADD COLUMN IF NOT EXISTS row_ulid TEXT;
ALTER TABLE session_history ADD COLUMN IF NOT EXISTS node_id  TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS uq_session_history_ulid ON session_history(row_ulid);
```
Insert stamps `row_ulid = ulid(stopped_at)`, `node_id = node_id()`. Keep `id IDENTITY`.

### 3. `audit_log` — op-log

```sql
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS row_ulid TEXT;
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS node_id  TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS uq_audit_log_ulid ON audit_log(row_ulid);
```
Insert stamps `row_ulid = ulid(ts)`, `node_id = node_id()`. Keep `id IDENTITY`.

### 4. `loop_iteration_metric` — op-log, but already merge-keyed

Real identity is the existing `UNIQUE(loop_id, iteration)`, and `loop_id` is owned
by one executing node, so no ULID is needed — only provenance:
```sql
ALTER TABLE loop_iteration_metric ADD COLUMN IF NOT EXISTS node_id TEXT;
```
The vestigial `id IDENTITY` can be dropped in Slice 2; leave it for now.

### 5. `sessions` — owner-keyed map

```sql
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS owner_node TEXT;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS deleted_at BIGINT;  -- tombstone, unused in Slice 1
```
`upsert_session` stamps `owner_node = node_id()`. (Sessions already soft-stop via
`active=0`; `deleted_at` is reserved for the Slice-2 tombstone semantics. No read
change now.)

### 6. `runners` — owner-keyed map (each runner owns its own row)

```sql
ALTER TABLE runners ADD COLUMN IF NOT EXISTS owner_node TEXT;
ALTER TABLE runners ADD COLUMN IF NOT EXISTS deleted_at BIGINT;
```
`upsert_runner` stamps `owner_node = node_id()`. `delete_runner` stays a hard
`DELETE` in Slice 1; Slice 2 flips it to `UPDATE ... SET deleted_at = now` plus a
`WHERE deleted_at IS NULL` filter on `load_all_runners`.

### 7. `loop_instances` — owner-keyed map

```sql
ALTER TABLE loop_instances ADD COLUMN IF NOT EXISTS owner_node TEXT;
```
`upsert_loop_instance` stamps `owner_node = node_id()`. `id` is already TEXT (a
uuid4 today; new loops may switch to `ulid()` later for sortability — not required).

### 8. `session_store` — owner-keyed map

```sql
ALTER TABLE session_store ADD COLUMN IF NOT EXISTS owner_node TEXT;
ALTER TABLE session_store ADD COLUMN IF NOT EXISTS deleted_at BIGINT;
```
Upsert stamps `owner_node = node_id()`. (`content BYTEA` → candidate for MinIO
offload later; out of scope here.)

### 9. `event_rule_cursor` — per-node cursor

```sql
ALTER TABLE event_rule_cursor ADD COLUMN IF NOT EXISTS node_id TEXT;
```
Stamp `node_id = node_id()` on upsert. Slice 2 makes the PK `(node_id, name)` and
introduces `last_ulid` so each node tracks its own position in the merged log.

### 10. `agent_state` — DERIVED, no schema change

`agent_state` is the fold of `agent_events`. Do **not** give it its own identity
or sync it — in the P2P model each node rebuilds `agent_state` locally from the
merged event log. Documenting it here so it is explicitly excluded.

---

## store.py changes required (write paths only)

1. `from .node_identity import node_id, ulid` at module top.
2. Add the DDL above to `_SCHEMA` **and** as `ADD COLUMN IF NOT EXISTS` /
   `CREATE ... IF NOT EXISTS` statements in `init_db()` (the additive-migration
   block already there) so existing DBs get the columns.
3. Extend the write functions to populate the new columns:
   - In **store.py**: `upsert_session`, `upsert_runner`, `insert_session_history`,
     `insert_audit`, `upsert_loop_instance`, `insert_loop_iteration_metric`, and
     the `event_rule_cursor` upsert.
   - In **agent_store.py**: the `agent_events` `INSERT` inside `ingest()` (stamps
     `event_ulid = ulid(now)`, `node_id = node_id()`). `agent_store.py` already
     imports `_conn` from store.py and wraps state-upsert + event-append in one
     `db.transaction()`, so the ULID append stays atomic with the state upsert.
     The `agent_state` upsert there is left alone (derived; it already does a
     COALESCE partial-merge).
4. No read function changes. No delete function changes.

## Test impact

- Existing 1300 tests: expected to stay green (purely additive; no read/delete
  change). Run `just bb-test` against a throwaway `brainbox_test` Postgres.
- New tests to add: `ulid()` monotonic-sortability + length; `node_id()`
  stability across calls + env override; a store test asserting each of the 8
  write paths populates its new column.

## Open question for review

- ULID backfill of existing `agent_events`/`session_history`/`audit_log` rows:
  do it now (one-time script) so history sorts under the Slice-2 cursor, or leave
  old rows `NULL` and only stamp new rows? Recommendation: backfill `agent_events`
  (the cursor reads it), skip the other two (pure history).

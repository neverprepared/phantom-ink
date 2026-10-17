# Review — data model and emission points

Lens: what gets emitted, by whom, and whether it survives. I read the real emitters, not
just the brief. Three of the brief's load-bearing claims are wrong; one free win was missed.

---

## The headline correction

**`runner-work.parent_id` is not "one field server-side". It is a notarized macOS app
release plus a fleet-wide runner update.**

`runner-work` envelopes are built by the Swift runner, not the router:

- `phantom-runner/BrainboxRunner/Core/RunnerCore.swift:329` — `envelopeForWork()`
  constructs `id: "runner-work:\(name):\(work.id)"` with
  `metadata = {kind, work_id, runner}`. That is exactly the three fields the brief
  measured, which confirms this is the sole producer.

So the "add one field" change is: edit Swift → build → sign → notarize → release → every
runner picks it up. And the runner auto-updater checks the wrong repo and tag prefix, so
it never finds releases (your own open TODO). Until that is fixed, "add a field to the
runner" means hand-updating each machine.

**The cheap route the brief missed:** the *router* already holds everything needed at
dispatch time, synchronously. `FleetRegistry.enqueue()` returns the `work_id` to the
caller (`phantom-router/src/phantom_router/fleet_client.py:203-214`), and every call site
knows the runner, the session and the profile:

- `lifecycle.py:1154` — `session.create` (has `runner`, `_rwp` profile, session name)
- `lifecycle.py:2077` — `_dispatch_runner_op` (has `ctx.runner_name`, `session_name`)
- `integrations.py:284,330`, `platform.py:131,149,184`

The router can emit a one-shot envelope with the **same deterministic id**
(`runner-work:<runner>:<work_id>`) carrying only `parent_id` + `workspace`, and let the
upsert merge it with the runner's lifecycle rows. No Swift, no release, no fleet update.

Except it does not work today, because of finding 1.

---

## 1. P0 — The upsert is a full replace. It clobbers `parent_id` and `description`.

`phantom-events/src/phantom_events/store.py:243-260`:

```sql
ON CONFLICT (id) DO UPDATE SET
    status      = COALESCE(EXCLUDED.status, agent_state.status),
    workspace   = COALESCE(EXCLUDED.workspace, agent_state.workspace),
    parent_id   = EXCLUDED.parent_id,          -- clobbers
    description = EXCLUDED.description,        -- clobbers
    start_at    = EXCLUDED.start_at,           -- clobbers
    ...
```

Exactly two fields are protected. Everything else is last-writer-wins, and **a producer
that omits a field erases it.** Consequences, all of them live:

- The router pre-stamp above is wiped by the runner's very next envelope for that work
  item (running → done, `parent_id` nil). Silently.
- `session:{name}` is upserted on *every* lifecycle transition with `parent_id=None`
  (`agent_store.py:630`), so any materialised session parentage is erased on the next
  stop/delete.
- Any plan to put the activity line in `description` is erased by the next upsert from
  the primary producer.

**Change:** `parent_id = COALESCE(EXCLUDED.parent_id, agent_state.parent_id)`.
One line, one test. Cost: parentage becomes write-once/un-unsettable. That is the correct
semantic — parentage is structural, not state. Do **not** apply the same treatment to
`description`: it is display text a producer may legitimately want to clear.

This is the cheapest change in the review and it is a prerequisite for findings 2, 3 and
most of 6. Nothing multi-producer works until it lands.

---

## 2. P1 — `spawned_by` already exists and is thrown away. One line.

`phantom-router/src/phantom_router/agent_store.py:545`:

```python
parent_id=(f"hub-task:{task.job_id}" if task.job_id and task.job_id != task.id else None)
```

But `Task` (`models.py:272`) carries **both**:

- `job_id` — "Parent supervisor task ID (own id if this is the root)"
- `spawned_by` — "Task ID that directly spawned this task (None for roots)"

The envelope uses only `job_id`, which flattens every spawn chain to its job root. That
is why you measured exactly **one** `hub-task→hub-task` edge. Prefer `spawned_by`, fall
back to `job_id`:

```python
_p = task.spawned_by or (task.job_id if task.job_id != task.id else None)
parent_id=(f"hub-task:{_p}" if _p else None)
```

This is "an agent spawned another agent" — for fleet tasks, already recorded in the task
store, already profile-stamped, currently discarded at the envelope boundary. It is the
highest value-per-character change available and the brief does not mention it.

---

## 3. P1 — "runner-work's parent is its dispatching hub-task" is wrong for most rows.

The work kinds the runner actually handles (`RunnerCore.swift:376-406`):

`session.create`, `session.stop`, `session.delete`, `session.exec`, `session.query`,
`integration.up/down/status`, `platform.ps`, `platform.action`, `db.list`.

- `session.*` → the correct parent is **`session:<name>`**, not the hub-task. These are
  operations *on* a session, structurally identical to the `session-cmd` rows you already
  parent that way (`agent_store.py:656`).
- `platform.ps`, `db.list`, `integration.status` → **operator and UI polling.** No parent
  exists. These are not agent work and do not belong in an agent-work visualisation at
  all.

So the brief's "roots 403→71, depth-2 nodes 1→307" is inflated by stapling infrastructure
chatter underneath tasks. You would get a dramatically deeper tree that is less true than
the flat one. **Dead end: a uniform parent rule for `runner-work`.**

**Change:** at each enqueue site, stamp `parent_id = session:<name>` for the `session.*`
kinds, and add `tags: ["infra"]` (or `metadata.edge = "poll"`) for the rest so the view
excludes them by default. Same router-side emission as finding 2's cheap route; the
classification is a dict lookup on `kind`.

---

## 4. Deriving the edge from `metadata.session_name` — wrong, but not for the reason you think

The string join is not the real problem. The real problem is that
**`session:{name}` is identity-keyed by name** (`agent_store.py:630`). A session deleted
and recreated with the same name **upserts the same row**. So:

- In the audit view, two unrelated tasks months apart both correctly resolve to one
  session node. The tree is not wrong because of a bad join — it is wrong because the
  node is an alias for every session that ever had that name, with one overwritten status.
- Materialising the edge inherits the collision exactly. This is not fixed by moving the
  work server-side.

Your listed failure modes (renamed, missing, outside the window) are real but minor by
comparison; 37/38 resolving is a window artifact, and a 500-row window is not an audit.

**Verdict:** derive-now is acceptable in the **operate/live** view only — the window is
minutes, names are unique in practice, and a wrong edge costs a misleading click.
It is not acceptable in the **audit** view, which is precisely where name reuse bites.
Concretely:
- Label it as derived in the UI. Never persist it. Never let a derived edge feed profile
  inheritance (finding 5).
- Materialise properly for new rows: thread one optional `parent_task_id` through
  `_audit_log` (`phantom-router/src/phantom_router/api.py:129`, emission at `:179`) into
  `envelope_from_audit` (`agent_store.py:600`), which already accepts `workspace` the same
  way. ~6 lines. Requires finding 1 first or the stop/delete upsert erases it.
- The id-collision fix (include container id or creation ms in the session envelope id) is
  a breaking id change across rules, summaries and `session-cmd` parentage. Phase it
  separately, or accept and *document* the ambiguity in the detail panel — which your
  prototype's "what this row cannot tell you" line is the right place for.

---

## 5. Profile by inheritance — no. Stamp at emission.

Inheritance is the wrong recovery mechanism here because the claim is security-shaped:
"this work ran with this profile's credentials". Computing that from a client-side tree
built on a name join means a wrong parent silently mis-attributes every descendant, with
no record that the value was inferred. 35% → 94% coverage is not a win if the 59% is
guessed.

And the stamp is available. The router has the profile at every emission point:
`_rwp` in `lifecycle.py` before the session.create enqueue; `workspace=_profile_for(
session_name)` is *already* passed in `_audit_log` (`api.py:184`). `workspace` is one of
the two COALESCE-protected columns, so a stamp is both safe and sticky.

**Changes:**
1. Pass `workspace` on the router-side `runner-work` envelope (findings 2/3). That is the
   295 rows, attributed rather than inferred.
2. **Fix the leak first, it is a one-character class of bug:**
   `app/frontend/src/lib/stores/streamLive.svelte.ts:213`
   ```ts
   if (env.workspace && this.workspaceFilter && env.workspace !== this.workspaceFilter) return;
   ```
   A workspace-less envelope passes every filter, so those 295 rows appear under *every*
   profile simultaneously. Under the standing rule that cross-profile leakage is a bug,
   this is the bug. Drop the `env.workspace &&` guard and route unscoped envelopes to an
   explicit "unscoped" bucket the user opts into.
3. If you keep inheritance as a display aid, render it visually distinct (dashed, or a
   "via parent" chip) and **never let it widen what the profile filter admits** — inferred
   attribution may narrow a view, never expand it.

---

## 6. The activity line — backend, but not `description`, and phase-boundary only

Frontend SSE aggregation is a dead end. It gives an activity line only for the window you
happened to be watching: reopen the panel and it is blank, and it is worth exactly zero
for audit. The user asked for both operate *and* audit from one surface.

`description` is also the wrong home: it is `EXCLUDED`-clobbered (finding 1), and the
contract itself says "Display-only ... Not routed or filtered on"
(`app/internal/contract/envelope.gen.go:23-25`). Its 0% fill is not an oversight to
exploit; it is a field with no owner.

**Contract that does not become a firehose:** one mutable `activity` string on the task
row, updated at **phase boundaries only**, from a closed vocabulary the step executor
already knows:

`provisioning` · `cloning` · `planning` · `editing (n files)` · `running tests` ·
`opening PR` · `waiting on review` · `retrying (2/3)`

Rules: last-write-wins, no history, rate-limit 1 update / 10s per task, emitted by the
router's step/loop executor — **not** from inside the container (it has no reliable path
out and you would be trusting the agent's self-report). ~10 writes per task, not ~1000.

Explicitly do **not** stream tool calls to the bus. That is the firehose, it duplicates
the OTEL path you already have, and "what is it doing right now" is answered by a phase,
not by a syscall.

---

## 7. In-session subagents — emit from the harness, two events each

Who emits, in order of trustworthiness:

- **The harness (correct).** Hook-level `SubagentStart`/`SubagentStop` sees spawn and
  termination, including crashes, and cannot be lied to by the agent.
- **The agent (wrong).** Self-reported parentage is unverifiable and absent on crash —
  which is the case you most want recorded.
- **The container wrapper (wrong).** Cannot see inside the process.

Minimum honest record, per subagent, two events (start, end), upserted on one id:

```
id        = subagent:<session_name>:<agent_uuid>
parent_id = session:<name>   (or the parent subagent's id, for nesting)
kind      = "subagent"
title     = <agent type> + the 3-5 word description
status    = active → done | failed
start_at / end_at
workspace = <profile>        (stamped, not inherited)
metadata  = {agent_type, model, depth}
```

Omit: prompts, outputs, token counts, tool calls, intermediate progress. Volume: the 28
subagents in this session = 56 events. There is nothing to sample at that rate; sampling
would only make the tree lie.

The real cost is not volume — it is that the reflex-era hooks no longer load, so this is a
hook in whatever replaced them. Scope the work as "find the hook surface", not "write the
emitter". If no hook surface exists, this is the one item worth deferring.

---

## 8. Memory provenance — log writes, do not log reads

**Writes: yes, and it is cheap.** The brain client already runs inside a session with the
ids in its environment. Add `session_id` / `task_id` / `agent` to the record at `learn`
time, additively. Attribution goes from ~22% (the `task_summary` title convention) to
~100%, and it replaces a convention with a field — the convention is the kind of thing
that is silently wrong the first time someone retitles a note.

**Reads: no.** Three reasons, any one sufficient:
1. Volume — `recall` runs before every unit of work and returns K hits each. This is the
   firehose, far worse than the activity line.
2. Value — the records are the user's own notes. "I read my own note" is not an audit
   finding.
3. Privacy — a per-query log of what you asked your memory is the single most sensitive
   artifact the platform could hold, and it would sync node-to-node.

**If you want "data touched"**, record it at task granularity, not query granularity:
one `memory_touched: {read_count: n, wrote: [sha, ...]}` block on task completion.
`task_summary` already exists as the carrier and already promotes. One field, no new
store, no per-query trail.

---

## 9. Is the existing contract enough for a three-level tree?

For the *shape*: yes. `parent_id` + `start_at`/`end_at` + `status`, with both read APIs
filtering on `parent_id` (`app/brainbox/agent_events.go:67`, `:98`). Three real gaps, one
non-gap, one trap:

- **Gap: no edge kind.** `parent_id` currently conflates "this is subwork of that" with
  "this is a UI affordance on that". `action.*` (`app_envelope.go:150`), `session-cmd`,
  `session:*:summary` all use the same field as real subwork. That is why 75 of 96 depth-1
  nodes are an object and its own button, and why the recorded tree is deep and
  meaningless. Cheapest fix with no contract change: derive the edge kind from the child's
  `kind`/`tags` in the view and collapse affordance children into a badge on the parent.
  Proper fix: `metadata.edge ∈ {subwork, affordance, summary, poll}` stamped by the
  producer — additive, no codegen, no migration.
- **Gap: no closure semantics.** A parent reaching `done` says nothing about its children,
  and a parent's `end_at` can precede a child's. "What happened in a complete session
  tree" needs *tree-complete* = all descendants terminal, computed at read time. Do not
  add a rollup status column to the parent; it will be stale and it will lie.
- **Gap: no cancellation/orphan propagation.** The fleet already emits `work.orphaned`
  (`phantom-fleet/src/phantom_fleet/registry.py:229`) but it is forwarded as a *log
  message string* through `_forward_event` into an SSE broadcast
  (`phantom-router/src/phantom_router/api.py:1896-1910`) — it never becomes an envelope.
  So an abandoned subtree looks `active` forever. Converting that one emitter to an
  envelope status transition is small and it is the difference between the live view being
  trustworthy and not.
- **Non-gap: ordering.** `start_at` is 100% filled. Sibling order by `start_at` is correct
  and sufficient. **Do not add a sequence number.**
- **Trap: span semantics.** Do not put trace/span ids or OTEL parentage on this contract.
  You already export OTLP from sessions (`CL_OTEL`) into OpenSearch. Duplicating spans on
  the envelope bus gives you two half-truths that disagree. If you want spans, read them
  from OpenSearch and join on session, in the audit view only.

---

## Dead ends, named

1. Changing the Swift runner as the *first* move. (Do it eventually, if ever; the router
   can emit the same id today.)
2. `description` as the activity field.
3. Frontend SSE aggregation as the activity source.
4. A uniform parent rule for `runner-work`.
5. Logging memory reads.
6. Trace/span semantics on the envelope contract.
7. Persisting the client-derived `hub-task→session` edge.
8. Adding a sibling sequence number.

## Priority

| # | Change | File / service | Cost |
|---|---|---|---|
| P0 | `COALESCE` `parent_id` in the upsert | `phantom-events/src/phantom_events/store.py:252` | 1 line + test; unblocks P2 |
| P0 | Workspace-less envelopes must not pass the profile filter | `app/frontend/src/lib/stores/streamLive.svelte.ts:213` | ~5 lines; closes a leak |
| P1 | Use `spawned_by` for hub-task parentage | `phantom-router/.../agent_store.py:545` | 1 line; real spawn chains |
| P1 | `work.orphaned` → envelope status transition | `phantom-fleet/.../registry.py:229` + router `api.py:1896` | ~20 lines; live view stops lying |
| P2 | Router-side `runner-work` envelope: `parent_id` (session for `session.*`), `workspace`, `metadata.edge` | `phantom-router/.../fleet_client.py` + 6 enqueue sites | ~40 lines; no Swift |
| P2 | Materialise `session.parent_id = hub-task:<id>` | `api.py:129,179` → `agent_store.py:600` | ~6 lines; needs P0 |
| P2 | `metadata.edge` on `action.*` / `session-cmd` / summaries | `app/app_envelope.go:150`, `agent_store.py:647` | ~6 lines; de-noises the tree |
| P3 | `activity` field, phase-boundary writes | router step/loop executor + contract (additive) | new field + ~1 emit per phase |
| P3 | Subagent envelopes from the harness hook | hook surface (unknown — scope first) | 2 events/subagent; cost is finding the hook |
| P4 | `session_id`/`task_id` on brain records at `learn` | phantom-brain client | additive field; 22% → ~100% |
| Never | Memory read logging; span semantics | — | — |

## One sentence

Fix the upsert and use `spawned_by` — two lines across two repos — before building any of
the richer emission, because every multi-producer edge you add is being silently erased
today and one real parent-child edge is already recorded and discarded.

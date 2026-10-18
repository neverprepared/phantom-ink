# Agent-work visibility — task graph

Planner: `task-planner` (phantom-brain agents vault).

## End state, observably

1. **Operate** — the operator opens one surface, sees every running agent **for the active
   profile only**, each row saying what it is doing, and opens its terminal in one click.
2. **Audit** — the operator opens a finished session and sees its full tree (session →
   hub-task → runner-work) with per-node outcome and timings.

These are not equal. (1) is the stated primary ("up front quick and easy to navigate set of
running tasks"); (2) is explicitly framed as "as an audit". Sequence (1) first, entirely.

## Correction to the brief — the cost asymmetry is mispriced

The brief calls `runner-work.parent_id` "one field". It is not, at the emitter.
`runner-work` envelopes are constructed in **`phantom-runner/BrainboxRunner/Core/RunnerCore.swift:346`**
(`envelopeForWork`), from a `WorkItem` carrying only `{id, kind, payload}`
(`phantom-router/src/phantom_router/runners.py:46`). **The runner does not know which
hub-task dispatched it.** Setting the field there needs a router change *and* a
signed+notarized macOS runner release across every runner host — on a fleet whose
auto-updater is known broken (`project_runner_updatecheck_fix_todo`).

There is a cheaper equivalent: the **dispatcher** knows the parent
(`phantom-router/src/phantom_router/lifecycle.py:1156`), and
**`phantom-events/src/phantom_events/store.py:193 ingest()` is a single chokepoint** where
an incoming envelope can be enriched by joining `metadata.work_id`. So do it server-side
(T7 → T8): one deploy, zero runner releases, same measured gain. T9 keeps the emitter route
as a documented fallback, not the plan.

---

## Decision nodes (resolve before the tasks they block)

### D1 — What is the activity line's data source? → **backend field**
Frontend SSE aggregation only works while the app is open and that panel is subscribed; an
agent that started an hour ago shows nothing, which defeats the whole point of an "up front"
list. And it reconstructs, per client, a fact the dispatcher already holds.
**Recommendation:** emit a one-line `description` from the router on each hub-task status
transition. Do **not** add `current_activity` — `description` is already in the contract and
measured at **0% filled across all 500 rows**. Use the field that exists.
*Blocks:* T6. *Decider:* user.

### D2 — What does connect/focus mean for a container agent vs a host process? → **one `Open` action, backend-resolved**
Both halves already exist: container sessions have a ttyd URL rendered in
`SessionsPanel.svelte:674-698`; host processes have `FocusTerminalTab(tty)` at
`SessionsPanel.svelte:265`. No new capability is required — only a resolver that picks the
branch from the row's `backend` metadata (100% filled on hub-task).
**Recommendation:** single `Open` button; container → shared slide-out, host → AppleScript
tab focus; neither available → the button is absent, never a dead click.
*Blocks:* T4, T5. *Decider:* user.

### D3 — Does memory provenance belong in the graph at all? → **CUT**
See Cuts. *Decider:* user, but the recommendation is firm.

---

## Tasks

Bands: **S** = under a day, **M** = a few days, **L** = a week+ / multi-repo.
Tags: **[CHEAP-HIGH]** do it now · **[EXP-DEFER]** expensive, defer · **[CUT]**.

### Layer 1 — correctness and free structure

**T1 · Fix the cross-profile leak on the live SSE path** — `[CHEAP-HIGH]` · S
Deps: none. Role: frontend dev (`claude`).
`streamLive.svelte.ts:213` passes an envelope when `env.workspace` is empty, so the 295
workspace-less `runner-work` rows (59% of the window) appear under **every** profile at once.
`refreshLive` has the same hole via `workspace: this.workspaceFilter`.
*Verify:* unit test over the store — a `runner-work` envelope with `workspace: ''` must not
survive a `lakeview` filter; and must still be reachable via an explicit "unattributed" view
so it is not silently hidden. `npm run check` clean.
**This is a prerequisite, not an improvement.** Every task below builds on this path and
inherits the bug otherwise — and the user's standing rule makes it a bug, not a nit.

**T2 · Derive the `hub-task → session` edge client-side** — `[CHEAP-HIGH]` · S
Deps: T1. Role: frontend dev.
`metadata.session_name` is 100% filled on all 38 hub-task rows; 37 resolve in-window. No
backend change, no contract change.
*Verify:* fixture test over the measured 500-row window asserts roots 403→366, depth-2
nodes 1→20, parents-with-children 38→75. Unresolvable `session_name` renders as a
disconnected root, never as a fabricated parent.

**T3 · Decompose `StreamPanel.svelte` (1017 lines) — no behaviour change** — `[CHEAP-HIGH]` · M
Deps: T1. Role: refactorer (`claude`, Sonnet acceptable).
Extract the live/logs list rendering and the row/detail markup into components. Retires the
panel's own existing improvement note rather than competing with it.
*Verify:* `npm run check` clean; `cd app && go test ./... -race` unaffected; panel renders
identically before/after (screenshot diff or manual); StreamPanel under ~500 lines.

### Layer 2 — the operate surface

**T4 · Lift the ttyd slide-out into a shared `TerminalSlideOut` component** — `[CHEAP-HIGH]` · S
Deps: D2. Role: frontend dev.
Second consumer is T5. Retires the existing TODO in `SessionsPanel.svelte`.
*Verify:* SessionsPanel terminal still opens, closes, and pops out to browser; the
`{#key}`-remount behaviour at line 690 is preserved; svelte-check clean.

**T5 · Build the StreamPanel `active` tab (rename `live` → `logs`)** — `[CHEAP-HIGH]` · M
Deps: T2, T3, T4, D2. Role: frontend dev.
Rows from `hubTasks` filtered to running, profile-scoped. Per row: title, status, runner,
backend, attempts/max_attempts (all 100%-filled hub-task metadata), and the D2 `Open`
action. Retires the planned-but-unbuilt TODO whose data source was already identified.
*Verify:* with ≥1 running hub task, the tab lists it and `Open` attaches a terminal; with
**zero** running — the measured normal case (`/api/hub/tasks?status=running` → 0) — the tab
shows an explicit, informative empty state, not a blank pane.
**This is the shortest-path payoff. Stop and ship here before Layer 3.**

**T6 · Activity line** — M
Deps: D1, T5. Role: backend dev (router) + frontend dev.
Per D1: router writes a short `description` at each hub-task status transition; the active
tab renders it.
*Verify:* `description` fill-rate on **new** hub-task rows goes 0% → ~100% (query
`/api/agent_state`); the line is visible in the active tab; old rows degrade to a dash, not
to an empty row.

### Layer 3 — parenting the fleet work (59% of the data)

**T7 · Router records dispatch provenance** — M
Deps: none (parallel with Layer 1–2). Role: backend dev (phantom-router).
At the runner-dispatch call sites (`lifecycle.py:1156` and siblings) persist
`work_id → (dispatching hub-task id, workspace)`. Must survive a router restart, or the
mapping is lost exactly when it is most wanted.
*Verify:* dispatch a `session.create` on `workspace_profile=personal`; the mapping is
readable afterwards and still readable after a router restart.

**T8 · Enrich `runner-work` on ingest** — `[CHEAP-HIGH]` (given T7) · M
Deps: T7. Role: backend dev (phantom-events).
In `store.py:ingest()`, fill `parent_id` and `workspace` on a `runner-work` envelope by
joining `metadata.work_id` against T7. Child inherits parent's profile.
*Verify:* replay the measured window through ingest — roots 403→71, depth-2 nodes 1→307,
max fan-out 6→11, workspace fill 35%→94% (470/500). No envelope without a mapping gets a
guessed parent. **Single deploy, no runner release.**

**T9 · (Fallback only) payload-carried parent + runner release** — `[EXP-DEFER]` · L
Deps: T7. Role: Swift dev + release engineer.
Router stamps parent into `WorkItem.payload`; `RunnerCore.envelopeForWork` copies it. Needs
a signed+notarized release and, because the updater is broken, a manual install on every
runner host. **Only if T8 is rejected.** Do not sequence both.

**T10 · Backfill historical `runner-work` parents** — `[CUT]`
Deps: T8. The `work_id → parent` map was in-memory and is mostly gone for the existing 295
rows. Expect low recovery for a real migration cost. Let history stay flat; T8 fixes
everything from deploy forward.

### Layer 4 — audit

**T11 · Session-tree audit view** — M
Deps: T2, T8. Role: frontend dev.
Given a session, render session → hub-task → runner-work with per-node outcome, start/end,
duration, and the prototype's detail panel — including its best feature, the explicit line
naming what a row **cannot** tell you. Keep that line.
*Verify:* opening a finished session shows ≥1 hub-task and its runner-work children with
`end_at` and outcome populated.

**T12 · Promote list/tree/radial into the app over the real store** — M
Deps: T2, T11. Role: frontend dev.
Collapse the prototype's five synthetic scopes to **two real ones**: live and recorded. Keep
the radial as a toggle — "data in different forms" is a stated preference and the drag-rotate
is good — but **land the operator on the list**. A radial is a poor first read for "which of
my six jobs is stuck".
*Verify:* all three views render the same filtered set from the same store; switching views
preserves selection; no scope in the UI is backed by anything other than telemetry.

### Layer 5 — in-session subagents

**T13 · Decide and spec the emission contract for agent-spawns-agent** — `[EXP-DEFER]` · M
Deps: none; blocks T14. Role: architect (ADR).
Zero records exist today — the 28 subagents in this very session produced none. The emitter
is the harness **inside the container**, so this crosses the contract, the guest MCP helper,
and the gateway token path. Write it as an ADR, not a ticket.

**T14 · Implement it** — `[EXP-DEFER]` · L
Deps: T13, T8. **Do not sequence before T11.** This is the only item in the graph requiring
a new contract plus a guest-side change plus a token/gateway path. It is also the item most
likely to be redesigned once T11 shows what the tree actually needs.

---

## Critical path

`T1 → T3 → T5 → T6` (operate), honest duration ≈ T3 + T5 + T6.
`T7 → T8 → T11` runs **in parallel** from the start and is the audit path's whole length.

**Parallel sets:** {T1, T7} · {T2, T3, T4, T8} after their deps · {T6, T11} · T12 last.

## Shortest path that genuinely helps the operator

**T1 → T2 → T4 → T5.** Four tasks, three of them S, no backend change, no contract change,
no release. It delivers: a correctly profile-scoped list of what is running, with session
structure, and a one-click terminal. T6's activity line is the next increment; everything in
Layer 3+ is for audit, and audit is the second job.

## Cuts — do not sequence these

- **Memory provenance (D3).** Reads are not logged at all and reads are most of "touched";
  writes are attributable only by title convention (~22%); `/links`, `/audit`,
  `/record_links`, `/provenance` all 404. Delivering it means a phantom-brain write-path
  change *plus* a decision to log every recall, which has a real cost on the hot path. The
  honest version of this feature is "which long-term memory a task **wrote**", which is a
  different, much smaller feature. **Cut from this graph; revisit as its own ADR.**
- **T10 backfill.** The mapping is gone. Cost without payoff.
- **The prototype's "this session" scope.** Assembled from conversation history, not
  telemetry. Shipping it puts a view in the app that cannot ever refresh and that teaches the
  operator to trust a number the platform did not produce. Cut.
- **The prototype's invented middle layer.** A readability grouping presented in the same
  visual language as a real session boundary. Either it becomes a real derived edge (T2) or
  it goes. Cut as-is.
- **`current_activity` as a new field.** Use the 0%-filled `description` instead (D1).

## Risks and open questions needing a human decision

1. **D1 and D2 are genuinely open** and block T6 and T4/T5 respectively. Recommendations
   above; neither needs a meeting, both need a yes.
2. **Liveness is near-empty** (0 sessions, 0 running tasks, 2 active rows — both heartbeats).
   The operate surface will be an empty list most of the time. That is not a reason to skip
   it, but the empty state is a first-class design surface, not an afterthought — and it is
   also why T5's acceptance criterion names it explicitly.
3. **T7 persistence.** If the `work_id → parent` map stays in-memory, a router restart
   silently reverts T8 to today's behaviour. Flagged in T7's verification; do not let it slip.
4. **Sizing guess:** T3 (StreamPanel decomposition) is banded M from line count alone. If the
   panel's state is more tangled than its size suggests, it could reach L and it sits on the
   critical path. Consider doing T5 as a **sibling panel** and decomposing later if T3 opens
   badly — that trade is worth making to protect the shortest path.
5. **Per prior feedback**, validation runs on `workspace_profile=personal` only, and
   create→verify→cancel without full runs unless the user approves.

## Explicitly out of scope

Gateway/token changes; anything requiring a runner release (unless T9 is chosen); OpenSearch
or Langfuse-sourced views; the DashboardPanel widget layer; changes to the brain's
similar/supersedes graph or `MemoryGraphPanel`; multi-node loop bodies.

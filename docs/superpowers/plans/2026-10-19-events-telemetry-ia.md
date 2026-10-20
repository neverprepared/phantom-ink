# Events / Telemetry IA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split phantom-ink's six overlapping observability surfaces into two coherent planes — an **Events** panel over the Postgres agent bus and a **Telemetry** panel over OpenSearch — moving Rules into Events and Timeline into Automations.

**Architecture:** Frontend-only. Panel **ids are not changed** (`stream` stays `stream` under the label "Events"; `agent-work` stays `agent-work` under "Telemetry"), because `currentPanel` persists through `location.hash` and an id migration would break deep links. The one-shot `streamFocus` signal is replaced by two ordinary hub stores (`eventsState`, `telemetryState`) matching the established `settingsState` / `jobsState` / `workState` pattern, which makes cross-panel navigation testable instead of implicit.

**Tech Stack:** Svelte 5 (runes), TypeScript, Vitest (`environment: 'node'`, browser resolve conditions, `flushSync` from `svelte` for reactivity), `svelte-check`.

**Spec:** `docs/superpowers/specs/2026-10-19-events-telemetry-ia-design.md`

## Global Constraints

- **Panel ids are frozen.** `stream` and `agent-work` keep their ids. Never rename a `Panel.id` in `src/lib/panels.ts` in this plan.
- **Branch:** `feat/events-telemetry-ia` (already created; the spec is already committed on it).
- All paths below are relative to `app/frontend/` unless stated otherwise.
- Run tests with `npm test` and the type gate with `npm run check`, both from `app/frontend/`.
- `npm run check` (svelte-check) must report **0 errors** at the end of every task.
- Tab state is **in-memory only** — no tab value is persisted anywhere. Do not add persistence.
- The only persisted UI state touched by this plan is `debug_event_log` in `localStorage`, read by `debugState.showEventLog`. Leave its key name alone.
- Do not touch any Go file. There are no backend changes in this plan.

### Three spec corrections found during planning

The spec is authoritative on intent; these three details in it are factually wrong and this plan overrides them:

1. **The spec says `ruleSeed` "can be retired."** It cannot. `ruleSeed` carries a pattern from Events' inline Live tab into `ServerRulesTab`, which are still two separate components after the move. It stops being a *cross-panel* handoff and becomes an intra-panel one: `seed()` keeps working but targets the Events tab instead of the Automations tab.
2. **The spec lists "stored tab state" as a risk**, claiming `jobsState.tab` persists. It does not — `let _jobsTab = $state<JobsTab>('jobs')` is in-memory, reset on every app start. No stale-tab guard is needed for the removed `rules` tab.
3. **The spec does not mention that `currentPanel` persists via `location.hash`** (`panelFromHash()` at `src/lib/stores.svelte.ts:36`, `history.replaceState` at `:60`). This is the real persistence risk: a user whose last panel was `#event-log` must be redirected, which Task 5 handles.
4. **The spec names `ServerRulesTab.svelte` as the component to move.** The component `JobsHubPanel` actually renders for its `rules` tab is `AutomationsPanel.svelte`, a thin wrapper around `ServerRulesTab`. This plan moves `AutomationsPanel`, which is the same change one level up.
5. **An earlier draft of the spec called for decomposing `AgentWorkPanel` into `TracesTab` / `TrendsTab` / `BreakdownTab`.** That instruction has been removed from the spec and there is no task for it here. `trends` and `breakdown` are two branches of a single `{#if view !== 'sessions'}` block sharing one data source, one loading/error notice pair, and the `.charts` / `.chart-card` / `.pie-row` / `ul.legend` rules; splitting them into siblings would duplicate all of it — the same reason the spec leaves `live` + `attention` inline. Reducing that file is separate work, and wants a store-based decomposition rather than a component split.

---

### Task 1: Navigation store — Events and Telemetry hub state

Adds the two new hub stores **alongside** the existing `streamFocus`, so nothing breaks yet. `streamFocus` is deleted in Task 5 once its last consumer is gone.

**Files:**
- Modify: `src/lib/stores.svelte.ts` (tab unions and redirect maps near `:177`; `ruleSeed` at `:535`)
- Test: `src/lib/stores.nav.test.ts` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces, all exported from `src/lib/stores.svelte.ts`:
  - `type EventsTab = 'attention' | 'live' | 'rules' | 'raw'`
  - `eventsState: { get tab(): EventsTab; set tab(v: EventsTab); open(tab: EventsTab): void }`
  - `type TelemetryTab = 'logs' | 'traces' | 'trends' | 'breakdown'`
  - `type TelemetrySort = 'cost' | 'duration' | 'tokens'`
  - `telemetryState: { get tab(): TelemetryTab; get sortBy(): TelemetrySort | null; set sortBy(v: TelemetrySort | null); open(tab: TelemetryTab, sortBy?: TelemetrySort | null): void }`
  - `type JobsTab = 'jobs' | 'loops' | 'collectors' | 'timeline'` (changed: `rules` removed, `timeline` added)
  - `EVENTS_TAB_PANELS: Record<string, EventsTab>`
  - `JOBS_TAB_PANELS: Record<string, JobsTab>` (changed: gains `timeline`, loses `automations`)

- [ ] **Step 1: Write the failing test**

Create `src/lib/stores.nav.test.ts`:

```ts
/**
 * Navigation-store tests for the Events / Telemetry IA split.
 *
 * These exist because every regression in this change is SILENT: a
 * mis-pointed navigation intent renders a real panel on the wrong tab
 * rather than throwing, so only an assertion catches it.
 */

import { describe, expect, it, beforeEach, vi } from 'vitest';

// stores.svelte.ts imports these at :359-361; neither is exercised by the
// navigation surface, so they are stubbed rather than faked.
vi.mock('./utils/api', () => ({ getApi: async () => null }));
vi.mock('./utils/wailsTopic', () => ({ onWailsTopic: () => () => {} }));

const {
  currentPanel,
  eventsState,
  telemetryState,
  jobsState,
  ruleSeed,
  EVENTS_TAB_PANELS,
  JOBS_TAB_PANELS,
} = await import('./stores.svelte');

beforeEach(() => {
  currentPanel.value = 'dashboard';
  eventsState.tab = 'attention';
  telemetryState.open('traces', null);
  jobsState.tab = 'jobs';
  ruleSeed.consume();
});

describe('eventsState', () => {
  it('defaults to the attention tab, because attention is the only actionable surface', () => {
    expect(eventsState.tab).toBe('attention');
  });

  it('open() sets the tab and navigates to the retained stream panel id', () => {
    eventsState.open('rules');
    expect(eventsState.tab).toBe('rules');
    expect(currentPanel.value).toBe('stream');
  });
});

describe('telemetryState', () => {
  it('open() sets the tab, carries a sort key, and navigates to agent-work', () => {
    telemetryState.open('logs', 'cost');
    expect(telemetryState.tab).toBe('logs');
    expect(telemetryState.sortBy).toBe('cost');
    expect(currentPanel.value).toBe('agent-work');
  });

  it('open() without a sort key CLEARS a previous one', () => {
    telemetryState.open('logs', 'cost');
    telemetryState.open('logs');
    expect(telemetryState.sortBy).toBeNull();
  });
});

describe('legacy panel id redirects', () => {
  it('routes the retired event-log panel id to the Events raw tab', () => {
    expect(EVENTS_TAB_PANELS['event-log']).toBe('raw');
  });

  it('routes the legacy automations panel id to the Events rules tab', () => {
    expect(EVENTS_TAB_PANELS['automations']).toBe('rules');
  });

  it('routes the legacy timeline panel id to the Automations timeline tab', () => {
    expect(JOBS_TAB_PANELS['timeline']).toBe('timeline');
  });

  it('no longer routes any legacy id to an Automations rules tab', () => {
    expect(Object.values(JOBS_TAB_PANELS)).not.toContain('rules');
  });
});

describe('ruleSeed', () => {
  it('seeds a draft onto the Events rules tab, not Automations', () => {
    ruleSeed.seed({ pattern: { type: 'task.failed' } });
    expect(currentPanel.value).toBe('stream');
    expect(eventsState.tab).toBe('rules');
    expect(ruleSeed.consume()?.pattern).toEqual({ type: 'task.failed' });
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd app/frontend && npx vitest run src/lib/stores.nav.test.ts`
Expected: FAIL — the import destructure throws, because `eventsState`, `telemetryState` and `EVENTS_TAB_PANELS` are not exported yet.

- [ ] **Step 3: Change the Automations tab union and redirect map**

In `src/lib/stores.svelte.ts`, replace the `JobsTab` type at `:177` and the `JOBS_TAB_PANELS` map at `:182`:

```ts
export type JobsTab = 'jobs' | 'loops' | 'collectors' | 'timeline';

// Legacy panel ids that are now hub tabs → their tab. Used by AppShell to
// redirect stale navigation/#hash deep-links and by the command palette.
// 'rules' moved OUT to the Events panel (it matches on the agent bus, so it
// belongs beside the bus); 'timeline' moved IN (it is a schedule, so it
// belongs beside the things that run on a schedule).
export const JOBS_TAB_PANELS: Record<string, JobsTab> = {
  loops: 'loops',
  collectors: 'collectors',
  timeline: 'timeline',
};
```

- [ ] **Step 4: Add the Events hub store**

Insert into `src/lib/stores.svelte.ts` immediately after the `jobsState` block (which ends at `:213`):

```ts
// ---------------------------------------------------------------------------
// "Events" hub sub-tabs — the event plane. Everything here reads or acts on
// the Postgres agent bus (`agent_events`): the live tail, the attention queue
// of things that fired and need a human, the EventBridge-style rules that
// match on it, and the raw envelope view. The panel id is still 'stream'
// internally (label-only rename to "Events"), because currentPanel persists
// through location.hash and an id migration would break deep links.
// ---------------------------------------------------------------------------

export type EventsTab = 'attention' | 'live' | 'rules' | 'raw';

// Legacy panel ids that are now Events tabs → their tab.
// 'automations' was the rules panel's original id; 'event-log' was the
// debug-gated raw bus view that no longer has a sidebar entry.
export const EVENTS_TAB_PANELS: Record<string, EventsTab> = {
  automations: 'rules',
  'event-log': 'raw',
};

// Attention is the default because it is the only actionable surface in the
// panel, and it owns the sidebar badge.
let _eventsTab = $state<EventsTab>('attention');

export const eventsState = {
  get tab(): EventsTab { return _eventsTab; },
  set tab(v: EventsTab) { _eventsTab = v; },
  /** Navigate to the Events panel with a specific tab pre-selected. */
  open(tab: EventsTab): void {
    _eventsTab = tab;
    currentPanel.value = 'stream';
  },
};

// ---------------------------------------------------------------------------
// "Telemetry" hub sub-tabs — the telemetry plane. Everything here reads
// OpenSearch: service logs, agent traces (the turn/prompt drill-down that
// replaced Langfuse), and cost/token metrics. Panel id stays 'agent-work'.
//
// `sortBy` is a presentation intent a caller can hand in with the tab — the
// Dashboard's OpenSearch metric widgets drill into Logs pre-sorted by the
// metric that was clicked. open() always rewrites it, so a sort from one
// drill-down cannot leak into the next.
// ---------------------------------------------------------------------------

export type TelemetryTab = 'logs' | 'traces' | 'trends' | 'breakdown';
export type TelemetrySort = 'cost' | 'duration' | 'tokens';

let _telemetryTab = $state<TelemetryTab>('traces');
let _telemetrySort = $state<TelemetrySort | null>(null);

export const telemetryState = {
  get tab(): TelemetryTab { return _telemetryTab; },
  set tab(v: TelemetryTab) { _telemetryTab = v; },
  get sortBy(): TelemetrySort | null { return _telemetrySort; },
  set sortBy(v: TelemetrySort | null) { _telemetrySort = v; },
  /** Navigate to the Telemetry panel with a tab and optional sort intent. */
  open(tab: TelemetryTab, sortBy: TelemetrySort | null = null): void {
    _telemetryTab = tab;
    _telemetrySort = sortBy;
    currentPanel.value = 'agent-work';
  },
};
```

- [ ] **Step 5: Re-point `ruleSeed` at the Events panel**

In `src/lib/stores.svelte.ts`, replace the body of `ruleSeed.seed` (at `:536-541`). The old body set `_jobsTab = 'rules'` and navigated to `'jobs'`:

```ts
  seed(target: RuleSeed): void {
    _ruleSeed = target;
    // Rules now live as the "rules" tab of the Events panel, beside the bus
    // they match on. This used to be a cross-panel handoff to Automations.
    _eventsTab = 'rules';
    currentPanel.value = 'stream';
  },
```

Also update the comment block above `interface RuleSeed` at `:520-523`, replacing "to the Automations panel's Rules tab" with "to the Events panel's Rules tab".

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd app/frontend && npx vitest run src/lib/stores.nav.test.ts`
Expected: PASS, 9 tests.

- [ ] **Step 7: Confirm nothing else referenced the removed `rules` tab value**

Run: `cd app/frontend && grep -rn "'rules'" src/ | grep -v stores.nav.test`
Expected: hits in `src/lib/panels/JobsHubPanel.svelte` and `src/lib/components/CommandPalette.svelte` only. Both are fixed in Tasks 3 and 5. `npm run check` will flag them as type errors until then, which is expected and is why this task does not run it.

- [ ] **Step 8: Commit**

```bash
cd app/frontend && git add src/lib/stores.svelte.ts src/lib/stores.nav.test.ts
git commit -m "feat(app): add Events and Telemetry hub stores

Replaces the one-shot streamFocus signal with two ordinary hub stores
matching settingsState/jobsState/workState, so cross-panel navigation
intents are assertable. Rules moves out of the Automations tab union and
Timeline moves in."
```

---

### Task 2: Panel registry — labels, shortcut renumbering, drop the Event Log entry

**Files:**
- Modify: `src/lib/panels.ts`
- Test: `src/lib/panels.test.ts` (create)

**Interfaces:**
- Consumes: nothing. `src/lib/panels.ts` has no imports.
- Produces: the `panels` array with `stream` labelled `Events`, `agent-work` labelled `Telemetry` and carrying `shortcut: '⌘3'`, the `event-log` entry removed, and `sessions`/`jobs`/`conversations`/`code` renumbered to ⌘4–⌘7.

Shortcuts are derived from this registry in `src/App.svelte:15-19`, so renumbering is registry-only — no keyboard-handler change.

- [ ] **Step 1: Write the failing test**

Create `src/lib/panels.test.ts`:

```ts
/**
 * Panel-registry tests.
 *
 * The registry is the single source of truth for both the sidebar and the
 * ⌘-shortcut map (src/App.svelte:15-19), so a duplicate shortcut silently
 * shadows a panel rather than erroring. These assertions make the IA
 * contract mechanical.
 */

import { describe, expect, it } from 'vitest';
import { panels } from './panels';

describe('panel registry', () => {
  it('assigns each shortcut to exactly one panel', () => {
    const shortcuts = panels.filter(p => p.shortcut).map(p => p.shortcut);
    expect(new Set(shortcuts).size).toBe(shortcuts.length);
  });

  it('binds the renumbered workspace shortcuts to the expected panels', () => {
    const byShortcut = Object.fromEntries(
      panels.filter(p => p.shortcut).map(p => [p.shortcut, p.id]),
    );
    expect(byShortcut['⌘1']).toBe('dashboard');
    expect(byShortcut['⌘2']).toBe('stream');
    expect(byShortcut['⌘3']).toBe('agent-work');
    expect(byShortcut['⌘4']).toBe('sessions');
    expect(byShortcut['⌘5']).toBe('jobs');
    expect(byShortcut['⌘6']).toBe('conversations');
    expect(byShortcut['⌘7']).toBe('code');
  });

  it('keeps the stream and agent-work ids under their new labels', () => {
    const label = (id: string) => panels.find(p => p.id === id)?.label;
    expect(label('stream')).toBe('Events');
    expect(label('agent-work')).toBe('Telemetry');
  });

  it('no longer ships a panel whose label collides with Work', () => {
    expect(panels.map(p => p.label)).not.toContain('Agent Work');
    expect(panels.filter(p => p.label === 'Work')).toHaveLength(1);
  });

  it('drops the Event Log sidebar entry, which is now an Events tab', () => {
    expect(panels.find(p => p.id === 'event-log')).toBeUndefined();
  });

  it('keeps every panel in a known sidebar section', () => {
    for (const p of panels) {
      expect(['workspace', 'system']).toContain(p.section);
    }
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd app/frontend && npx vitest run src/lib/panels.test.ts`
Expected: FAIL on the label, shortcut-binding and `event-log` assertions.

- [ ] **Step 3: Edit the registry**

In `src/lib/panels.ts`, make exactly these changes. **Leave every `icon` string untouched.**

1. The `stream` entry: change `label: 'Streams'` to `label: 'Events'`.
2. The `agent-work` entry: change `label: 'Agent Work'` to `label: 'Telemetry'` and add `shortcut: '⌘3',`. Move the entry so it sits directly after `stream` in the array (array order drives sidebar display order).
3. `sessions`: `shortcut: '⌘3'` → `'⌘4'`.
4. `jobs`: `shortcut: '⌘4'` → `'⌘5'`.
5. `conversations`: `shortcut: '⌘5'` → `'⌘6'`.
6. `code`: `shortcut: '⌘6'` → `'⌘7'`.
7. `mesh`: remove its `shortcut: '⌘7'` line entirely — ⌘7 now belongs to `code`, and Brain Mesh is a `system` panel reachable from the sidebar and the command palette.
8. Delete the whole `event-log` entry (the last element, carrying `debug: true`).

Then update the comment above the `stream` entry's section to say:

```ts
  // ── Workspace ────────────────────────────────────────────────────────────
  // Order matters: the sidebar renders array order within each section.
  // Events (the agent bus) and Telemetry (OpenSearch) sit adjacent because
  // they are the two halves of one question; they are separate panels because
  // they have different transports and different durability guarantees.
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd app/frontend && npx vitest run src/lib/panels.test.ts`
Expected: PASS, 6 tests.

- [ ] **Step 5: Commit**

```bash
cd app/frontend && git add src/lib/panels.ts src/lib/panels.test.ts
git commit -m "feat(app): relabel Streams/Agent Work to Events/Telemetry

Resolves two name collisions: Agent Work vs Work, and Agent Work's
sessions view vs the Sessions panel. Event Log leaves the sidebar to
become an Events tab. Shortcuts renumber to 3-7; they derive from this
registry so no handler changes."
```

---

### Task 3: Events panel — Rules in, Timeline out

Moves `AutomationsPanel` (the rules UI) from the Automations hub into the Events panel, and `TimelinePanel` the other way. Both are already standalone components, so no logic is rewritten.

**Files:**
- Modify: `src/lib/panels/StreamPanel.svelte` (tab type `:79`, tab state `:83`, focus effects `:178-186`, `onMount` focus consume `:213-217`, tab strip `:396-435`, tab bodies `:437-650`)
- Modify: `src/lib/panels/JobsHubPanel.svelte` (imports `:3-6`, `TABS` `:17-22`, body `:40-48`)

**Interfaces:**
- Consumes: `eventsState`, `EventsTab`, `JobsTab` from Task 1.
- Produces: `StreamPanel` renders its tab from `eventsState.tab` rather than panel-local `$state`; `JobsHubPanel` renders a `timeline` tab.

- [ ] **Step 1: Point StreamPanel's tab at the store**

In `src/lib/panels/StreamPanel.svelte`:

Delete the local tab type and state (`:79` and `:83`):

```svelte
  type Tab = 'live' | 'attention' | 'logs' | 'timeline';
  ...
  let tab = $state<Tab>('attention');
```

Replace them with a derived read plus a setter helper:

```svelte
  // The tab lives in eventsState so other panels can land us on a specific
  // tab without a one-shot signal to consume.
  let tab = $derived(eventsState.tab);
  function setTab(t: EventsTab) { eventsState.tab = t; }
```

Update the store import at `:5`:

```svelte
  import { profileState, currentPanel, attentionStore, eventsState, type EventsTab, ruleSeed, outboxStore } from '../stores.svelte';
```

Then replace every `onclick={() => (tab = 'X')}` in the tab strip with `onclick={() => setTab('X')}`.

- [ ] **Step 2: Delete the focus-signal plumbing from StreamPanel**

Remove the `$effect` at `:177-186` that reads `streamFocus.value` and the three lines in `onMount` at `:213-217` that call `streamFocus.consume()`. Keep the rest of `onMount` exactly as it is. `streamFocus` itself is deleted in Task 5.

- [ ] **Step 3: Swap the rules and timeline tabs**

In the tab strip, replace the `timeline` tab button with a `rules` one. The existing `live` and `attention` buttons are unchanged; this is the fourth button (`:430-434`):

```svelte
    <button
      class="tab"
      class:active={tab === 'rules'}
      onclick={() => setTab('rules')}>
      rules
    </button>
```

In the tab bodies, replace the `{:else if tab === 'timeline'}` branch (`:644-650`) with:

```svelte
  {:else if tab === 'rules'}
    <!-- The rules engine matches on the agent bus (event_match.py is
         EventBridge-shaped), so it lives beside the bus rather than beside
         the jobs it triggers. AutomationsPanel brings its own header and
         scroll region, so it gets the same filling flex cell the timeline
         embed used to. -->
    <div class="tab-embed">
      <AutomationsPanel />
    </div>
```

Swap the import at `:14`, replacing `TimelinePanel` with `AutomationsPanel`:

```svelte
  import AutomationsPanel from './AutomationsPanel.svelte';
```

Rename the `.timeline-embed` CSS rule (`:672`) to `.tab-embed`, keeping its declarations and updating its comment to name the rules tab.

- [ ] **Step 4: Give the Automations hub the timeline tab**

In `src/lib/panels/JobsHubPanel.svelte`, replace the `AutomationsPanel` import with `TimelinePanel`:

```svelte
  import TimelinePanel from './TimelinePanel.svelte';
```

Replace the `TABS` array (`:17-22`):

```svelte
  const TABS: { id: JobsTab; label: string }[] = [
    { id: 'jobs', label: 'jobs' },
    { id: 'loops', label: 'loops' },
    { id: 'collectors', label: 'collectors' },
    { id: 'timeline', label: 'timeline' },
  ];
```

Replace the `rules` branch in the body (`:46-48`):

```svelte
    {:else if activeTab === 'timeline'}
      <TimelinePanel />
```

Update the hub's doc comment (`:8-16`), replacing the `rules` line with:

```svelte
  //   timeline — the planned-vs-executed schedule axis
  // Rules moved OUT to the Events panel: a rule matches on the agent bus, so
  // it belongs beside the bus, not beside the work it triggers.
```

- [ ] **Step 5: Verify the types and tab wiring**

Run: `cd app/frontend && npm run check`
Expected: 0 errors. If `svelte-check` reports an unused `TimelinePanel` import in `StreamPanel.svelte` or an unused `AutomationsPanel` in `JobsHubPanel.svelte`, the swap in Steps 3–4 was incomplete — fix before continuing.

Run: `cd app/frontend && npm test`
Expected: PASS, all suites.

- [ ] **Step 6: Commit**

```bash
cd app/frontend && git add src/lib/panels/StreamPanel.svelte src/lib/panels/JobsHubPanel.svelte
git commit -m "feat(app): move Rules into Events and Timeline into Automations

A rule matches on the agent bus, so it belongs beside the bus; a planned
axis is a schedule, so it belongs beside the scheduled runners. Also
drops StreamPanel's local tab state in favour of eventsState."
```

---

### Task 4: Telemetry panel — Logs in, sessions renamed to traces

`StreamLogsTab` moves wholesale. Its OpenSearch reachability probe moves with it, because `AgentWorkPanel` has no health probe today — it only surfaces failures after an API call rejects.

**Files:**
- Modify: `src/lib/panels/StreamPanel.svelte` (remove logs state `:88-101`, `:107-131`, the logs effects `:173-175`, `:258-270`, the filter effect's logs line `:283`, `openDashboardsDiscover` `:366-372`, the logs tab button and body)
- Modify: `src/lib/panels/AgentWorkPanel.svelte` (`VIEWS` `:151-155`, the view segment `:539-550`, tab bodies)
- Modify: `src/lib/components/StreamLogsTab.svelte` (comment only)

**Interfaces:**
- Consumes: `telemetryState`, `TelemetryTab`, `TelemetrySort` from Task 1.
- Produces: `AgentWorkPanel` renders four tabs — `logs`, `traces`, `trends`, `breakdown` — reading `telemetryState.tab`, with `telemetryState.sortBy` driving the logs sort.

Note the rename: `AgentWorkPanel`'s existing `sessions` view becomes `traces`. That is the collision-resolving rename from the spec. The panel's local `View` type is **deleted**, not edited — `TelemetryTab` from Task 1 replaces it, so there is only one definition of the tab set.

- [ ] **Step 1: Move the logs state machine into AgentWorkPanel**

Cut the following from `src/lib/panels/StreamPanel.svelte` and paste into `src/lib/panels/AgentWorkPanel.svelte`'s script block:

- `logs`, `logsLoading`, `logsError` state and the `displayLogs` `$derived.by` (`:88-101`)
- `logsPoll`, `LOGS_POLL_MS`, `LOGS_LIMIT` (`:108`, `:117`, `:119`)
- `opensearchActive` and `refreshOpenSearchHealth` (`:124-134`), plus `healthPoll` and `HEALTH_POLL_MS`
- `refreshLogs` (`:197-210`)
- `openDashboardsDiscover` (`:366-372`)

In `AgentWorkPanel.svelte`, `logsSortBy` becomes a read of the store rather than local state:

```ts
  // The sort key is a navigation intent handed in by the Dashboard's
  // OpenSearch metric widgets, so it lives in the hub store, not here.
  let logsSortBy = $derived(telemetryState.sortBy);
```

`AgentWorkPanel` already derives `activeProfile` at `:225`; `refreshLogs` and `openDashboardsDiscover` reference `workspaceFilter`, so add:

```ts
  const workspaceFilter = $derived(activeProfile);
```

`AgentWorkPanel` imports `getApi` already; add `openInBrowser` to that import for `openDashboardsDiscover`.

- [ ] **Step 2: Remove the logs machinery from StreamPanel**

Delete from `src/lib/panels/StreamPanel.svelte`:

- the `$effect` at `:173-175` that bounced off the logs tab when OpenSearch was down
- the logs polling `$effect` at `:258-270`
- the `if (tab === 'logs') void refreshLogs();` line inside the workspace-filter effect (`:283`)
- the `logsPoll` and `healthPoll` clears in `onDestroy` (`:264`, `:266`), and the `healthPoll` interval and `void refreshOpenSearchHealth()` in `onMount`
- the `logs` tab button and its `{:else if tab === 'logs'}` body with the `<StreamLogsTab …/>` call
- the `StreamLogsTab` import (`:12`)

- [ ] **Step 3: Add the logs tab to the Telemetry hub and rename sessions → traces**

In `src/lib/panels/AgentWorkPanel.svelte`, replace `VIEWS` (`:151-155`) and drive the tab from the store:

```ts
  // Tab order puts logs first: it is the broadest surface and the one the
  // Dashboard's metric widgets drill into.
  const VIEWS: { id: TelemetryTab; label: string }[] = [
    { id: 'logs', label: 'logs' },
    { id: 'traces', label: 'traces' },
    { id: 'trends', label: 'trends' },
    { id: 'breakdown', label: 'breakdown' },
  ];

  let view = $derived(telemetryState.tab);
```

Import the store and type:

```ts
  import { telemetryState, type TelemetryTab } from '../stores.svelte';
```

In the view segment (`:539-550`), change `onclick={() => (view = v.id)}` to `onclick={() => (telemetryState.tab = v.id)}`.

Rename the old `sessions` view: **delete** the local `View` type declaration (it is superseded by `TelemetryTab`), change `{#if view === 'sessions'}` (`:578`) to `{#if view === 'traces'}`, and the refresh button's `view === 'sessions' ? load() : loadCharts()` (`:534`) to `view === 'traces' ? load() : loadCharts()`.

Add the logs branch before the traces branch:

```svelte
  {#if view === 'logs'}
    <StreamLogsTab
      {opensearchActive}
      {logsError}
      {logs}
      {logsLoading}
      {logsSortBy}
      {displayLogs}
      onClearSort={() => (telemetryState.sortBy = null)}
      onViewMore={openDashboardsDiscover}
    />
  {:else if view === 'traces'}
```

and import it:

```svelte
  import StreamLogsTab from '../components/StreamLogsTab.svelte';
```

- [ ] **Step 4: Wire the logs poll and health probe, without bouncing tabs**

Add to `AgentWorkPanel.svelte`. Note the deliberate difference from StreamPanel's behaviour: StreamPanel bounced off the logs tab when OpenSearch was down because it had three working tabs to fall back to. Telemetry has none — all four tabs need OpenSearch — so it must stay put and let the existing error notice explain itself.

```ts
  // Telemetry does NOT bounce off a tab when OpenSearch is unreachable: all
  // four tabs read OpenSearch, so there is nowhere to bounce to. The panel
  // shows its error notice instead, and stays in the sidebar so the feature
  // is still discoverable before the integration is switched on.
  $effect(() => {
    if (view === 'logs' && opensearchActive) {
      void refreshLogs();
      if (logsPoll === undefined) {
        logsPoll = window.setInterval(refreshLogs, LOGS_POLL_MS);
      }
    } else if (logsPoll !== undefined) {
      window.clearInterval(logsPoll);
      logsPoll = undefined;
    }
  });
```

In `AgentWorkPanel`'s `onMount`, add `void refreshOpenSearchHealth();` and `healthPoll = window.setInterval(refreshOpenSearchHealth, HEALTH_POLL_MS);`. In its cleanup, clear both `logsPoll` and `healthPoll`.

- [ ] **Step 5: Fix the stale Langfuse copy**

`AgentWorkPanel.svelte:586` still tells the user "Agent Work reads Langfuse, whose API keys are per profile" — stale since the move to OpenSearch, and contradicted by the panel's own scope note at `:550`. Since the panel is being relabelled anyway, replace that string with:

```svelte
      message={activeProfile ? emptyMessage : 'Telemetry reads OpenSearch, scoped per profile by resource.attributes.workspace. Select a profile to load its sessions.'}
```

Also update the panel's header comment at `:2` from "Agent Work: the table of agent sessions" to "Telemetry: logs, traces and metrics, all read from OpenSearch."

- [ ] **Step 6: Update StreamLogsTab's own comment**

`src/lib/components/StreamLogsTab.svelte`'s header comment names Stream as its host. Replace that comment with:

```svelte
<!-- The service-log table for the Telemetry panel's `logs` tab. Reads
     OpenSearch via TailLogs. Named for the Stream panel it was extracted
     from; the file keeps that name so the move stays reviewable as a
     relocation rather than a rewrite. -->
```

No prop or logic change — the component's interface is unchanged, which is why this move is safe.

- [ ] **Step 7: Verify**

Run: `cd app/frontend && npm run check`
Expected: 0 errors. A `logs`-related "unused variable" error in `StreamPanel.svelte` means Step 2 missed a deletion.

Run: `cd app/frontend && npm test`
Expected: PASS, all suites.

Run: `cd app/frontend && grep -n "logs\|opensearch" src/lib/panels/StreamPanel.svelte`
Expected: no matches. StreamPanel must retain nothing OpenSearch-related.

- [ ] **Step 8: Commit**

```bash
cd app/frontend && git add src/lib/panels/StreamPanel.svelte src/lib/panels/AgentWorkPanel.svelte src/lib/components/StreamLogsTab.svelte
git commit -m "feat(app): move Logs into Telemetry and rename sessions to traces

Logs, traces and metrics all read OpenSearch, so they share a panel; the
reachability probe moves with them, since AgentWorkPanel had none. The
sessions view becomes traces, resolving the collision with the Sessions
panel. Also fixes stale Langfuse copy in the empty state."
```

---

### Task 5: Re-point every navigation consumer and delete `streamFocus`

This is the task where a mistake is invisible, because a wrong target renders a real panel on the wrong tab. Deleting `streamFocus` is what makes the compiler find the stragglers.

**Files:**
- Modify: `src/lib/widgets/OpenSearchMetricWidget.svelte:1-16`
- Modify: `src/lib/components/CommandPalette.svelte:1-62`
- Modify: `src/lib/layout/AppShell.svelte:5`, `:22`, `:64-81`, `:120-127`
- Modify: `src/lib/panels/StreamPanel.svelte` (add the raw tab)
- Modify: `src/lib/stores.svelte.ts` (delete `StreamFocus` / `streamFocus`, `:490-517`)

**Interfaces:**
- Consumes: `eventsState`, `telemetryState`, `EVENTS_TAB_PANELS`, `JOBS_TAB_PANELS` from Task 1.
- Produces: no exports. After this task `streamFocus` does not exist.

- [ ] **Step 1: Re-point the Dashboard metric widget at Telemetry**

In `src/lib/widgets/OpenSearchMetricWidget.svelte`, change the import at `:2` to pull `telemetryState` instead of `streamFocus`, and replace `drill()` (`:15-17`):

```ts
  function drill() {
    telemetryState.open('logs', SORT_BY[config.metric]);
  }
```

This is the single most regression-prone line in the change: it used to land on the Streams panel's logs tab, and must now land on Telemetry's.

- [ ] **Step 2: Re-point the command palette**

In `src/lib/components/CommandPalette.svelte`, change the import at `:2` to:

```ts
  import { commandPalette, currentPanel, integrationState, settingsState, jobsState, eventsState, type SettingsTab, type JobsTab, type EventsTab } from '../stores.svelte';
```

Replace `jobsTabCommands` (`:15-19`) — `rules` leaves, `timeline` arrives:

```ts
  // Automations-hub tabs that used to be top-level panels — same treatment.
  const jobsTabCommands: { tab: JobsTab; label: string }[] = [
    { tab: 'loops', label: 'Loops' },
    { tab: 'collectors', label: 'Collectors' },
    { tab: 'timeline', label: 'Timeline' },
  ];

  // Events-hub tabs. Rules and the raw bus view have no sidebar entry of
  // their own, so the palette is the only way to jump straight to them.
  const eventsTabCommands: { tab: EventsTab; label: string }[] = [
    { tab: 'attention', label: 'Attention' },
    { tab: 'live', label: 'Live' },
    { tab: 'rules', label: 'Rules' },
  ];
```

Delete the hardcoded `nav:stream:timeline` command (`:56-61`) — `Go to Timeline` now comes from `jobsTabCommands`. In its place add the Events block:

```ts
    ...eventsTabCommands.map(t => ({
      id: `nav:events:${t.tab}`,
      label: `Go to ${t.label}`,
      description: 'Events',
      action: () => { eventsState.open(t.tab); commandPalette.close(); },
    })),
```

The `Raw` tab is intentionally absent: it is debug-gated, and the palette has no access to `debugState` today. Adding a gated command is out of scope.

- [ ] **Step 3: Re-point AppShell's redirect effects**

In `src/lib/layout/AppShell.svelte`, change the store import at `:5` to drop `streamFocus` and add `eventsState` and `EVENTS_TAB_PANELS`.

Replace the timeline redirect effect (`:76-81`) with an Events redirect that mirrors the existing Settings and Automations patterns:

```svelte
  // Rules ('automations') and the raw bus view ('event-log') are now tabs of
  // the Events panel. Redirect stale navigation and legacy #hash deep-links
  // to the hub with the matching tab active. Converges immediately: once on
  // 'stream' the map misses.
  $effect(() => {
    const et = EVENTS_TAB_PANELS[currentPanel.value];
    if (et) {
      eventsState.tab = et;
      currentPanel.value = 'stream';
    }
  });
```

The `timeline` id needs no special case — Task 1 added `timeline: 'timeline'` to `JOBS_TAB_PANELS`, so the existing Automations effect at `:64-72` already handles it.

- [ ] **Step 4: Drop the Event Log branch and import**

In `src/lib/layout/AppShell.svelte`, delete the `{:else if currentPanel.value === 'event-log'}` branch and its `<EventLogPanel />` (`:120-121`), and the import at `:20`. `EventLogPanel` is now rendered by `StreamPanel`, not the shell.

- [ ] **Step 5: Render the raw tab inside Events**

In `src/lib/panels/StreamPanel.svelte`, import `EventLogPanel` and `debugState`, add the gated tab button after `rules`:

```svelte
    {#if debugState.showEventLog}
      <button
        class="tab"
        class:active={tab === 'raw'}
        onclick={() => setTab('raw')}>
        raw
      </button>
    {/if}
```

and the body branch:

```svelte
  {:else if tab === 'raw' && debugState.showEventLog}
    <div class="tab-embed">
      <EventLogPanel />
    </div>
```

Add a guard so turning the debug flag off while the raw tab is open does not render a blank panel — the same shape `WorkHubPanel` uses for its Jira tab:

```svelte
  $effect(() => {
    if (eventsState.tab === 'raw' && !debugState.showEventLog) {
      eventsState.tab = 'attention';
    }
  });
```

- [ ] **Step 6: Delete `streamFocus` and let the compiler find stragglers**

In `src/lib/stores.svelte.ts`, delete the whole `StreamFocus` / `streamFocus` block (`:490-517`), including its section comment.

Run: `cd app/frontend && npm run check`
Expected: 0 errors. **Any** error naming `streamFocus` is a consumer missed in Steps 1–3 — fix it rather than restoring the store.

- [ ] **Step 7: Verify no references survive**

Run: `cd app/frontend && grep -rn "streamFocus\|StreamFocus\|event-log" src/`
Expected: exactly one match — `'event-log': 'raw'` in the `EVENTS_TAB_PANELS` map in `src/lib/stores.svelte.ts`. Anything else is a straggler.

Run: `cd app/frontend && npm test`
Expected: PASS, all suites.

- [ ] **Step 8: Commit**

```bash
cd app/frontend && git add src/lib/widgets/OpenSearchMetricWidget.svelte src/lib/components/CommandPalette.svelte src/lib/layout/AppShell.svelte src/lib/panels/StreamPanel.svelte src/lib/stores.svelte.ts
git commit -m "feat(app): re-point navigation consumers and retire streamFocus

Metric-widget drill-downs now land on Telemetry's logs tab; Go to
Timeline lands on Automations; the retired event-log and automations
panel ids redirect into Events tabs. Deleting streamFocus is what makes
the type checker find any consumer left behind."
```

---

### Task 6: Full verification sweep

No code changes unless a check fails. This task exists because the plan's riskiest failures are silent at compile time.

**Files:** none modified unless a check fails.

**Interfaces:** none.

- [ ] **Step 1: Run the whole frontend gate**

```bash
cd app/frontend && npm run check && npm test
```
Expected: `svelte-check` 0 errors; all vitest suites pass.

- [ ] **Step 2: Run the Go gate to confirm nothing backend-side moved**

```bash
cd app && go test ./... -race
```
Expected: PASS. No Go file was touched, but `app_conformance_test.go` enforces event-bus conformance and this confirms the branch is clean end to end. If this fails, it failed before the branch — compare against `main`.

- [ ] **Step 3: Build and launch the app**

```bash
cd /Users/cdowning/workspaces/profiles/personal/code/phantom-ink && just app-dev
```

- [ ] **Step 4: Walk the manual checklist**

Each line is a claim the automated tests cannot make. Check every one; do not batch-assert.

- [ ] ⌘1–⌘7 each open the panel its sidebar label names, in sidebar order.
- [ ] The sidebar reads: Dashboard, Events, Telemetry, Sessions, Automations, Conversations, Work — then the system group.
- [ ] Events opens on **attention** and the attention badge renders on the Events row.
- [ ] Collapse the sidebar: the badge still renders as a dot on the Events icon.
- [ ] Events tab strip: live · attention · rules (· raw only with the debug flag on).
- [ ] Events → rules renders the rules UI that used to be Automations → rules.
- [ ] Right-click a Live event card → "create rule" opens the rule form **on Events → rules**, pre-filled, without leaving the panel.
- [ ] Automations tab strip: jobs · loops · collectors · timeline, and timeline renders the planned/executed axis.
- [ ] Telemetry tab strip: logs · traces · trends · breakdown.
- [ ] Telemetry → logs shows the log table that used to be Streams → logs, and live-refreshes.
- [ ] On the Dashboard, click an OpenSearch metric widget (cost today, tokens today, api requests, avg latency). Each lands on **Telemetry → logs**, sorted by that metric. **This is the regression to watch for** — landing on Events means Step 1 of Task 5 is wrong.
- [ ] Click a second metric widget with a different metric: the sort changes rather than keeping the first one.
- [ ] Clear the sort on Telemetry → logs, then navigate away and back: the sort stays cleared.
- [ ] Command palette (⌘K): "Go to Timeline" lands on Automations → timeline; "Go to Rules" lands on Events → rules; there is no "Go to Agent Work" and no "Go to Event Log".
- [ ] Visit `#event-log` directly in the hash: it redirects to Events, and to the raw tab when the debug flag is on.
- [ ] Visit `#automations` directly: it redirects to Events → rules.
- [ ] Visit `#timeline` directly: it redirects to Automations → timeline.
- [ ] Visit `#agent-work` directly: it opens Telemetry (the id is retained, so an old deep link still works).
- [ ] Toggle the Event Log debug flag off while on Events → raw: the tab disappears and the panel falls back to attention rather than going blank.
- [ ] With OpenSearch stopped (`just opensearch-stop`): Telemetry stays in the sidebar, shows its error notice, and does **not** bounce between tabs. Restart it with `just opensearch-start`.
- [ ] ⌘[ and ⌘] still cycle panel history across the renamed panels.

- [ ] **Step 5: Commit any fixes, then record the outcome**

If Step 4 found nothing, there is nothing to commit. If it found fixes, commit them with a message naming the specific checklist line that failed.

```bash
cd /Users/cdowning/workspaces/profiles/personal/code/phantom-ink
pbrainctl client task update 5df261c7025e7fb9 \
  --artifact "Events/Telemetry IA" --reference "branch feat/events-telemetry-ia"
```

- [ ] **Step 6: Open the PR**

```bash
cd /Users/cdowning/workspaces/profiles/personal/code/phantom-ink
git push -u origin feat/events-telemetry-ia
gh pr create \
  --title "feat(app): split observability into Events and Telemetry planes" \
  --body-file docs/superpowers/specs/2026-10-19-events-telemetry-ia-design.md
```

If `gh` fails with `403 Resource not accessible` or `401 Bad credentials`, an ambient `GITHUB_TOKEN` is shadowing the working keyring login. Run `gh auth status` to confirm, then re-run the command with the token variables unset for that invocation only (`env -u GITHUB_TOKEN -u GH_TOKEN gh ...`).

- [ ] **Step 7: Watch CI**

```bash
cd /Users/cdowning/workspaces/profiles/personal/code/phantom-ink
gh pr checks --watch
```
Expected: `test-app` runs (the change touches `app/`) and passes. CI is path-filtered, so a docs-only commit triggers nothing — the frontend commits are what make `test-app` fire. Reading check contexts is the call most likely to need the unset-token workaround from Step 6. Failures must be fixed before merge.

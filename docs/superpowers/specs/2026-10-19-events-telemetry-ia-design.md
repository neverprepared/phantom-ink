# Events plane / Telemetry plane: splitting phantom-ink's observability IA

**Date:** 2026-10-19
**Status:** approved design, pending implementation plan
**Scope:** `app/frontend` navigation and panel composition only. No Go backend
changes, no new endpoints, no schema changes.

## Problem

Six surfaces in the app all answer some version of "what happened?", and the
sidebar gives no hint which one to open:

| Surface | Sidebar | Contents |
|---|---|---|
| Dashboard ⌘1 | workspace | widgets, metric widgets, action-items fold-in |
| Streams ⌘2 | workspace | `live` · `attention` (default) · `logs` · `timeline` |
| Agent Work | workspace | `sessions` (+ turn drill-down) · `trends` · `breakdown` |
| Automations ⌘4 | workspace | `jobs` · `loops` · `collectors` · `rules` |
| Work ⌘6 | workspace | `code` · `jira` · `delivery` |
| Event Log | system (debug) | raw bus envelopes |

Two of those names actively mislead:

- **`Agent Work` vs `Work`.** `Work` is the things assigned to you (code, Jira,
  delivery). `Agent Work` is agent telemetry. The labels suggest they are
  siblings; they share nothing.
- **`Sessions` vs Agent Work's `sessions` tab.** The `Sessions` panel is live
  container lifecycle. Agent Work's `sessions` view is historical OpenSearch
  telemetry with a turn/prompt drill-down. Same word, unrelated objects.

Both are the same root cause: panels named after their data source rather than
the question they answer.

## The two planes

The grouping is not one "observability" bucket. There are two planes with
different transports, different latencies, and different affordances, and the
codebase already draws the line sharply:

**Event plane** — Postgres `agent_events`, push, EventBridge-shaped.
`brainbox/src/brainbox/event_match.py:1` opens with *"EventBridge-style pattern
matching over agent-bus event documents"*, and `event_rules.py` matches
**exclusively** over rows of `agent_events`. Members: `live` (tail),
`attention` (what fired and needs a human), `rules` (matchers), the raw event
log.

**Telemetry plane** — OpenSearch, pull, read-only, batched (the logs export
batches every 10s; `AgentWorkPanel` polls at 15s because faster cannot surface
fresher data). Members: `logs`, traces (Agent Work's session drill-down —
functionally X-Ray; it replaced Langfuse), metrics (`trends`, `breakdown`).

Nothing on the telemetry plane can emit onto the event plane. That gap is real
and is explicitly out of scope here (see *Deferred*).

## Target IA

| Panel | id | Shortcut | Tabs |
|---|---|---|---|
| Dashboard | `dashboard` | ⌘1 | *(unchanged)* |
| **Events** | `stream` | ⌘2 | **Attention** (default) · Live · Rules · Raw *(debug-gated)* |
| **Telemetry** | `agent-work` | ⌘3 | Logs · Traces · Metrics |
| Sessions | `sessions` | ⌘4 | *(unchanged)* |
| Automations | `jobs` | ⌘5 | Jobs · Loops · Collectors · **Timeline** |
| Conversations | `conversations` | ⌘6 | *(unchanged)* |
| Work | `code` | ⌘7 | *(unchanged)* |

Panel **ids are not changed** — `stream` and `agent-work` are retained under
new labels, the same label-only rename used for Jobs→Automations and
Code→Work. This keeps deep links, stored layout state, and `⌘[`/`⌘]` history
working with no id migration.

`Event Log` leaves the sidebar and becomes the debug-gated `Raw` tab of Events.
`Agent Work` leaves the sidebar entirely.

### Why this grouping

- **Events tells one story in one place:** something happened (Live) → it
  matched (Rules) → it needs me (Attention). Rules today sit in Automations,
  away from the bus they watch; AWS puts rules with the bus, not with the
  compute they invoke.
- **Both name collisions resolve without a rename.** `Agent Work` ceases to
  exist, so `Work` is unambiguous. Its `sessions` view becomes `Traces`, so
  `Sessions` is unambiguous.
- **`ruleSeed` collapses.** `stores.svelte.ts:520` documents `ruleSeed` as
  existing solely to hand a pre-filled rule draft from Stream's "create rule
  from this event" flow *across panels* to Automations' Rules tab. With Rules
  inside Events that handoff is local state, and the cross-panel store can be
  retired. The store is evidence the two surfaces were already coupled.
- **Timeline → Automations is principled, not cosmetic.** It is a planned vs.
  executed axis — EventBridge Scheduler — so it belongs with the targets it
  fires, not with a telemetry read surface.
- **Attention keeps its default-tab position and its badge.** It is the only
  actionable surface among these; promoting it to a separate sidebar row was
  considered and rejected, because under this framing it is exactly where it
  belongs — the terminal state of the event plane.

## Component moves

Both relocated tabs are already clean component boundaries, so the moves are
mechanical:

- `components/StreamLogsTab.svelte` — rendered by `StreamPanel` at :633 with
  seven props. Moves to the Telemetry panel unchanged. The `opensearchActive`
  gate, `refreshLogs`, `logsSortBy`, `displayLogs`, and `openDashboardsDiscover`
  move with it; `AgentWorkPanel` already owns an OpenSearch reachability and
  error-notice path those can share.
- `components/ServerRulesTab.svelte` — rendered by `JobsHubPanel`. Moves to
  Events unchanged, along with the `ruleSeed` consumption.
- `panels/TimelinePanel.svelte` — embedded by `StreamPanel` at :644 inside a
  `.timeline-embed` flex cell. The same wrapper moves to `JobsHubPanel`.
- `panels/EventLogPanel.svelte` — becomes the Events `Raw` tab, gated on
  `debugState.showEventLog` exactly as its sidebar entry is today.

### Composition: hub panels, with one deliberate exception

`JobsHubPanel` (Automations) and `WorkHubPanel` (Work) are thin hubs that
render a component per tab. Telemetry should follow that idiom:
`AgentWorkPanel` is 1155 lines and adding Logs inline would push it past 1400,
so its three existing views become components (`TracesTab`, `TrendsTab`,
`BreakdownTab`) alongside the relocated `StreamLogsTab`, leaving a thin hub.

Events does **not** get the same treatment for `live` and `attention`. Those
two share CSS and state inside `StreamPanel` (1036 lines), and a prior lesson
in this repo is explicit that siblings sharing CSS should have state lifted
into a rune store rather than be split into components — splitting them would
duplicate styles for no gain. So the Events panel keeps `live` + `attention`
inline and renders `ServerRulesTab` / `EventLogPanel` as components. Net
`StreamPanel` size falls, because it sheds the logs state machine and the
timeline embed.

## Navigation and store changes

- `panels.ts` — relabel `stream` → `Events`, `agent-work` → `Telemetry`; add
  `shortcut: '⌘3'` to `agent-work`; renumber `sessions`/`jobs`/`conversations`/
  `code` to ⌘4–⌘7; delete the `event-log` entry. Shortcuts are derived from
  this registry in `App.svelte:18`, so renumbering is registry-only.
- `stores.svelte.ts` —
  - `JobsTab` becomes `'jobs' | 'loops' | 'collectors' | 'timeline'`
    (loses `rules`, gains `timeline`).
  - A new `EventsTab = 'attention' | 'live' | 'rules' | 'raw'` and a
    `TelemetryTab = 'logs' | 'traces' | 'trends' | 'breakdown'` replace
    `StreamFocus`'s current tab union.
  - `streamFocus` keeps targeting `stream` but its tab union narrows to
    `EventsTab`; a parallel `telemetryFocus` carries the `sortBy` intent to
    `agent-work`.
  - `ruleSeed` is retired once Rules is local to Events.
  - `JOBS_TAB_PANELS` gains a `timeline` → `timeline` entry and loses nothing
    (the legacy `rules` panel id, if present, redirects to Events instead).
- **Three callers of `streamFocus` must be re-pointed** (this is the part most
  likely to break silently, since a wrong target renders a valid panel on the
  wrong tab rather than erroring):
  - `widgets/OpenSearchMetricWidget.svelte:15` — `focus({tab:'logs', sortBy})`
    → `telemetryFocus`. A metric widget whose drill-down lands on Events
    instead of Telemetry is the regression to watch for.
  - `layout/AppShell.svelte:80` — the `timeline` redirect effect →
    `jobsState.open('timeline')`.
  - `components/CommandPalette.svelte:60` — the `Go to Timeline` command →
    `jobsState.open('timeline')`. Its `jobsTabCommands` list drops `Rules`,
    and an Events tab command list is added.
- `AppShell.svelte` — drop the `event-log` branch; keep an `event-log` →
  Events/`raw` redirect effect so stale deep links and the debug toggle still
  land somewhere, following the existing `SETTINGS_TAB_PANELS` pattern.

## Deferred: the telemetry → bus bridge

Out of scope for this change, recorded here because this IA is what makes its
home obvious.

Today no OpenSearch condition can raise a bus event, so a log pattern or a cost
threshold cannot drive a rule. In AWS this is a CloudWatch metric filter or
alarm feeding EventBridge. For us it would be: evaluate a saved OpenSearch
query on a schedule and emit an `AgentEnvelope` when a condition holds — after
which the existing rules engine handles it with **zero changes**, because it is
just another row in `agent_events`.

The mechanism already exists: Collectors are scheduled data-ingestion jobs. A
log-watch is a collector whose output is a bus envelope rather than a stored
row. So this is a new collector target, not a new subsystem.

The affordance would be a "watch this query" control on Telemetry → Logs that
writes a collector. It needs its own design (condition language, dedup/flap
suppression, envelope shape, per-profile scoping) and must not be bundled into
a navigation change.

## Risks and edge cases

- **Wrong-tab regressions are silent.** Every re-pointed navigation intent
  renders a real panel on the wrong tab rather than throwing. Each of the three
  `streamFocus` callers needs an explicit check.
- **OpenSearch-disabled profiles.** `StreamPanel:174` bounces off `logs` to
  `attention` when OpenSearch is inactive. Telemetry has no equivalent
  fallback, because *all three* of its tabs need OpenSearch — so with it
  disabled the panel must show its existing error notice rather than bounce,
  and the sidebar entry stays visible (unlike `Files`, which is hidden behind
  `requires: 'minio'`). Hiding Telemetry would make the feature undiscoverable
  for anyone who has not yet enabled OpenSearch.
- **Attention badge.** `Sidebar.svelte:7` hardcodes `badgeFor(id) = id ===
  'stream' ? attentionCount : 0`. The id is unchanged, so the badge survives;
  verify it still renders under the new label.
- **Stored tab state.** `jobsState.tab` persists. A profile last left on the
  now-removed `rules` tab must not strand on an unknown tab — `WorkHubPanel`
  already has this exact guard pattern (`if (!on && workState.tab === 'jira')
  workState.tab = 'code'`) to copy.
- **Shortcut renumbering is user-visible muscle memory.** It is contained to
  `panels.ts`, but it changes four existing bindings; worth calling out in the
  PR rather than slipping it in.

## Verification

- `cd app/frontend && npm run check` — svelte-check must be clean.
- `cd app && go test ./... -race` — no Go changes expected, but
  `app_conformance_test.go` enforces event-bus conformance and the panel
  registry is referenced from Go-side deep-link handling.
- Manual, per claim:
  - Each of ⌘1–⌘7 opens the panel its sidebar label names.
  - A Dashboard OpenSearch metric widget drill-down lands on
    **Telemetry → Logs**, sorted by the widget's metric.
  - Command palette `Go to Timeline` lands on **Automations → Timeline**.
  - "Create rule from this event" on a Live event card opens the rule form
    **in Events**, pre-filled.
  - The attention badge still renders on the Events row, collapsed and
    expanded.
  - With OpenSearch disabled, Telemetry shows its error notice and stays in
    the sidebar; Events does not offer a Logs tab.
  - With `debugState.showEventLog` off, Events shows no `Raw` tab; with it on,
    `Raw` renders what `Event Log` rendered.

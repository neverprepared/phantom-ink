<script lang="ts">
  // Agent Work: the table of agent sessions, and the drill-down into one.
  //
  // Reads OpenSearch only. It read BOTH stores until 2026-10-19 -- Langfuse for
  // the sessions and their content, OpenSearch for the activity dot -- because
  // Langfuse was the only place the conversation existed. That stopped being
  // true when Claude Code's content-logging flags were switched on: logs-otel
  // now carries full prompt and assistant text (measured at 46 user prompts and
  // 201 assistant responses on one session), so the one thing Langfuse was
  // here for is available from the store that was already fresher and complete.
  //
  // The measurements that settled it, all on live data:
  //   freshness  OpenSearch ~33s behind, Langfuse 725s -- its capture is a Stop
  //              hook firing once per COMPLETED turn, so a session mid-turn was
  //              invisible and a short window looked like a broken panel.
  //   cost       Langfuse understated by roughly half (813 tool calls / $309.05
  //              against 1,786 / $416.26 for the same session) because that
  //              same hook misses delegated subagent work.
  //   attribution  query_source is on the OTEL events, so subagent work is
  //              distinguishable here. Langfuse could not do it at all.
  //
  // What was lost: the parent/child observation tree, and with it the tree and
  // radial shapes. OpenSearch gives a flat event stream keyed by turn_id, so
  // the drill-down is turn-grouped instead.
  //
  // Scope is the ACTIVE PROFILE, always. It is now enforced by
  // resource.attributes.workspace on the events themselves rather than by which
  // project the credentials pointed at -- native stamping, so no inheritance
  // across derived edges and no cross-profile leakage.

  import { getApi } from '../utils/api';
  import { profileState } from '../stores.svelte';
  import Badge from '../components/Badge.svelte';
  import EmptyState from '../components/EmptyState.svelte';
  import MetricsChart from '../components/MetricsChart.svelte';
  import PieChart from '../components/PieChart.svelte';
  import Spinner from '../components/Spinner.svelte';
  import { openInBrowser } from '../utils/api';

  // opensearch.AgentSession. Replaced a Langfuse-shaped row on 2026-10-19 when
  // Langfuse was deprecated: this is a strict superset of what that carried,
  // and its cost is the complete figure rather than the Stop hook's partial
  // view (813 tool calls / $309.05 there against 1,786 / $416.26 here for the
  // same session).
  interface AgentWorkRow {
    session_id: string;
    workspace: string;
    first_seen: string;
    last_seen: string;
    // Distinct prompt@id values — USER turns, not tool calls.
    turns: number;
    tool_calls: number;
    failures: number;
    last_tool?: string;
    top_tools?: string[] | null;
    // Typed as possibly absent on purpose: Go marshals a nil slice as `null`,
    // and reading .length on it threw during render and killed this component.
    models?: string[] | null;
    cost_usd: number;
    // tokens_in EXCLUDES cache reads and writes, which dominate by orders of
    // magnitude on a long session. Reporting it alone beside the cost reads as
    // a contradiction, so the cache counters are shown rather than folded in.
    tokens_in: number;
    tokens_out: number;
    tokens_cache_in: number;
    tokens_cache_new: number;
    liveness?: 'active' | 'idle' | 'dormant' | 'unknown';
    last_activity_ms?: number;
    last_heartbeat_ms?: number;
    last_beat?: string;
  }

  // opensearch.SessionEvent. A FLAT event stream grouped by turn_id, not a
  // parent/child tree: that is the one structural thing Langfuse had that this
  // does not. In exchange `agent` carries query_source, so subagent work is
  // attributable — which Langfuse could not do at all.
  interface SessionEvent {
    sequence: number;
    time: string;
    // user_prompt | assistant_response | tool_result | api_request
    kind: string;
    // prompt@id — the join key that groups events into one turn.
    turn_id: string;
    tool_name?: string;
    tool_use_id?: string;
    success?: boolean;
    duration_ms?: number;
    input_size?: number;
    result_size?: number;
    // query_source: repl_main_thread for the session's own thread,
    // agent:* for a subagent, away_summary/compact/etc for harness overhead
    // that still costs money. Absent on tool_result.
    agent?: string;
    model?: string;
    cost_usd?: number;
    input_tokens?: number;
    output_tokens?: number;
    prompt?: string;
    response?: string;
    // Length-only when content logging is off, so a redacted turn still
    // reports its size rather than looking empty.
    redacted?: boolean;
  }

  interface SessionTimeline {
    session_id: string;
    workspace: string;
    events: SessionEvent[];
    total_events: number;
    truncated: boolean;
    // Non-empty when the search only partially succeeded (shard failures).
    // Surfaced rather than swallowed: a drill-down that silently drops events
    // is worse than one that says it did.
    warning?: string;
  }

  interface Point {
    ts: number; // unix seconds — matches MetricsChart's Sample
    value: number;
  }

  interface Share {
    name: string;
    value: number;
  }

  interface Charts {
    granularity: string;
    cost_series?: Point[] | null;
    cost_by_model?: Share[] | null;
    // main / subagent / auxiliary. Replaced an "observations by type"
    // breakdown that came from Langfuse: the delegated work its Stop hook
    // misses is exactly the subagent bucket, so this split was the one thing
    // it could never show.
    cost_by_source?: Share[] | null;
  }

  type View = 'sessions' | 'trends' | 'breakdown';

  // Three shapes of one session, per the Agent Work Wheel probe: a list to act
  // on, a tree to audit, a wheel to see scale. Each answers a different
  // question, so all three are kept rather than one being chosen.

  // The tree and radial shapes are GONE with Langfuse, not merely hidden: they
  // rendered a parent/child observation tree, and OpenSearch gives a flat event
  // stream keyed by turn_id. Rebuilding them means deriving a turn -> events
  // hierarchy first, which is a design question rather than a port, so the
  // drill-down is a turn-grouped list for now. SessionTree.svelte is left in
  // the tree for whoever takes that on.

  const VIEWS: { id: View; label: string }[] = [
    { id: 'sessions', label: 'sessions' },
    { id: 'trends', label: 'trends' },
    { id: 'breakdown', label: 'breakdown' },
  ];

  // The logs export batches every 10s; polling faster cannot surface fresher
  // data.
  const POLL_MS = 15_000;

  // A client-side deadline on every call. The Go side bounds each of its own
  // stages, but this panel sat on a permanent spinner twice while that was
  // being established, and a spinner is the least diagnosable failure there
  // is: no error, no state, nothing to act on. With this, a stuck call becomes
  // a message that names the timeout.
  const CALL_TIMEOUT_MS = 20_000;

  function withTimeout<T>(p: Promise<T>, label: string): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      const t = setTimeout(
        () => reject(new Error(`${label} did not return within ${CALL_TIMEOUT_MS / 1000}s`)),
        CALL_TIMEOUT_MS,
      );
      p.then(
        (v) => {
          clearTimeout(t);
          resolve(v);
        },
        (e) => {
          clearTimeout(t);
          reject(e);
        },
      );
    });
  }

  const RANGES = [
    { label: '15m', value: 'now-15m' },
    { label: '30m', value: 'now-30m' },
    { label: '60m', value: 'now-60m' },
    { label: '24h', value: 'now-24h' },
    { label: '3d', value: 'now-3d' },
  ];

  // Short ranges used to be routinely empty because Langfuse only wrote at the
  // end of a turn. OpenSearch is event-driven and lags roughly a minute, so a
  // 15-minute window now reflects a 15-minute reality and these ranges mean
  // what they say.

  let view = $state<View>('sessions');
  // A pinned observation survives hover, so clicking one and then moving the
  // mouse does not lose it.
  let pinnedObsId = $state<string | null>(null);
  let charts = $state<Charts | null>(null);
  let chartsLoading = $state(false);
  let chartsError = $state('');
  let chartsEpoch = 0;

  let sessions = $state<AgentWorkRow[]>([]);
  let loading = $state(true);
  let error = $state('');
  let range = $state('now-15m');
  let now = $state(Date.now());

  // The detail pane tracks an ID, not a row object: a refresh replaces
  // `sessions` with fresh rows, and holding the object would freeze the pane's
  // counts at the moment it was opened. `selectedSnapshot` covers the case
  // where the row legitimately leaves the list.
  let selectedId = $state<string | null>(null);
  let selectedSnapshot = $state<AgentWorkRow | null>(null);
  let timeline = $state<SessionTimeline | null>(null);
  let timelineLoading = $state(false);
  let timelineError = $state('');

  const activeProfile = $derived(profileState.active?.name ?? '');
  const selected = $derived(
    sessions.find((s) => s.session_id === selectedId) ?? selectedSnapshot,
  );

  // Guards against an out-of-order response overwriting a newer one.
  let listEpoch = 0;
  let timelineEpoch = 0;


  async function load() {
    const mine = ++listEpoch;
    loading = true;
    error = '';
    try {
      const api = await getApi();
      if (!api) {
        error = 'Wails bindings unavailable.';
        return;
      }
      if (!activeProfile) {
        // Normal transient state while the profile store resolves, and a
        // normal steady state with no profile selected. Not an error: a red
        // notice here reads as a failure when nothing has failed.
        sessions = [];
        error = '';
        return;
      }
      const rows = await withTimeout(
        api.ListAgentSessions(activeProfile, range, 50),
        'ListAgentSessions',
      );
      if (mine !== listEpoch) return;
      // The server already returns most-recently-active first; lift the
      // working ones, since "what is running" is the question this answers.
      sessions = ((rows ?? []) as AgentWorkRow[])
        .slice()
        .sort((a, b) => Number(isActive(b)) - Number(isActive(a)));
    } catch (e) {
      if (mine !== listEpoch) return;
      error = String(e);
      sessions = [];
    } finally {
      if (mine === listEpoch) loading = false;
    }
  }

  // Charts are fetched only while a chart tab is open. GetCharts issues four
  // aggregations, and running them every 15s behind the Sessions tab would be
  // four wasted queries per poll.
  async function loadCharts() {
    const mine = ++chartsEpoch;
    chartsLoading = true;
    chartsError = '';
    try {
      const api = await getApi();
      if (!api || !activeProfile) return;
      const c = await withTimeout(
        api.GetAgentWorkCharts(activeProfile, range),
        'GetAgentWorkCharts',
      );
      if (mine !== chartsEpoch) return;
      charts = c as Charts;
    } catch (e) {
      if (mine !== chartsEpoch) return;
      chartsError = String(e);
      charts = null;
    } finally {
      if (mine === chartsEpoch) chartsLoading = false;
    }
  }

  $effect(() => {
    // Re-read when the profile or range changes.
    void activeProfile;
    void range;
    void load();
  });

  $effect(() => {
    // And re-read the charts when the tab, profile or range changes.
    const v = view;
    void activeProfile;
    void range;
    if (v !== 'sessions') void loadCharts();
  });

  $effect(() => {
    const tick = setInterval(() => {
      now = Date.now();
      if (document.hidden) return;
      if (!loading) void load();
      if (view !== 'sessions' && !chartsLoading) void loadCharts();
    }, POLL_MS);
    return () => clearInterval(tick);
  });

  function isActive(s: AgentWorkRow): boolean {
    return s.liveness === 'active';
  }

  const activeCount = $derived(sessions.filter(isActive).length);

  function livenessTitle(s: AgentWorkRow): string {
    const secs = (ms?: number) => `${Math.round((ms ?? 0) / 1000)}s`;
    switch (s.liveness) {
      case 'active':
        if (s.last_heartbeat_ms !== undefined && s.last_heartbeat_ms > 150_000) {
          return `Activity ${secs(s.last_activity_ms)} ago, but the main-thread heartbeat is ${secs(s.last_heartbeat_ms)} old — subagents are probably running.`;
        }
        return `Activity ${secs(s.last_activity_ms)} ago — working now.`;
      case 'idle':
        return `No activity for ${secs(s.last_activity_ms)}. Idle, or finished — this telemetry has no session-end event, so the two are indistinguishable.`;
      case 'dormant':
        return `Silent for ${secs(s.last_activity_ms)}. Finished, crashed or waiting on a human — not distinguishable from this data.`;
      default:
        return 'No activity timestamp for this session.';
    }
  }

  // A selected session must not survive a profile change, or the pane shows
  // work the new scope excludes.
  $effect(() => {
    const p = activeProfile;
    const snap = selectedSnapshot;
    if (snap && p && snap.workspace && snap.workspace !== p) close();
  });

  async function open(s: AgentWorkRow) {
    const mine = ++timelineEpoch;
    selectedId = s.session_id;
    selectedSnapshot = s;
    timeline = null;
    timelineError = '';
    timelineLoading = true;
    try {
      const api = await getApi();
      if (!api) {
        timelineError = 'Wails bindings unavailable.';
        return;
      }
      const tl = await withTimeout(
        api.GetAgentSessionTimeline(s.session_id, activeProfile, 500),
        'GetAgentWorkTimeline',
      );
      if (mine !== timelineEpoch) return;
      timeline = tl as SessionTimeline;
      // A pin from the previous session would point at an id this one has not
      // got, leaving an empty detail block.
      pinnedObsId = null;
    } catch (e) {
      if (mine !== timelineEpoch) return;
      timelineError = String(e);
    } finally {
      if (mine === timelineEpoch) timelineLoading = false;
    }
  }

  function close() {
    selectedId = null;
    selectedSnapshot = null;
    timeline = null;
    timelineError = '';
    pinnedObsId = null;
  }

  const pinnedObs = $derived(
    (timeline?.events ?? []).find((e) => String(e.sequence) === pinnedObsId) ?? null,
  );

  // --- formatting -----------------------------------------------------------

  function ago(iso: string, ref: number): string {
    if (!iso) return '—';
    const t = new Date(iso).getTime();
    if (Number.isNaN(t)) return '—';
    const s = Math.max(0, Math.round((ref - t) / 1000));
    if (s < 60) return `${s}s ago`;
    if (s < 3600) return `${Math.round(s / 60)}m ago`;
    if (s < 86400) return `${Math.round(s / 3600)}h ago`;
    return `${Math.round(s / 86400)}d ago`;
  }

  function duration(a: string, b: string): string {
    const s = new Date(a).getTime();
    const e = new Date(b).getTime();
    if (Number.isNaN(s) || Number.isNaN(e) || e < s) return '—';
    const sec = Math.round((e - s) / 1000);
    if (sec < 60) return `${sec}s`;
    if (sec < 3600) return `${Math.floor(sec / 60)}m ${sec % 60}s`;
    return `${Math.floor(sec / 3600)}h ${Math.floor((sec % 3600) / 60)}m`;
  }

  function usd(n: number): string {
    if (!n) return '—';
    return n < 0.01 ? '<$0.01' : `$${n.toFixed(2)}`;
  }

  function compact(n: number): string {
    if (!n) return '—';
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
    return String(n);
  }

  function ms(n?: number): string {
    if (!n) return '';
    return n < 1000 ? `${Math.round(n)}ms` : `${(n / 1000).toFixed(1)}s`;
  }

  function clock(iso: string): string {
    const t = new Date(iso);
    if (Number.isNaN(t.getTime())) return '';
    return t.toLocaleTimeString([], { hour12: false });
  }

  // Observation types, mapped onto the Badge VARIANTS this codebase already
  // defines (Badge takes text + variant props, not slot children).
  function typeVariant(t: string): string {
    if (t === 'GENERATION') return 'completed';
    if (t === 'TOOL') return 'worker';
    return 'default';
  }

  // success is TRI-STATE: absent on events that have no outcome (a user_prompt,
  // an api_request), so only an explicit false is an error. Absent must not
  // render as failed.
  function isError(e: SessionEvent): boolean {
    return e.success === false;
  }

  // PieChart wants {name, value, label}; the label is formatted here rather
  // than in Go, which keeps presentation out of the read layer.
  const modelSlices = $derived(
    (charts?.cost_by_model ?? []).map((s) => ({
      name: s.name,
      value: s.value,
      label: usd(s.value),
    })),
  );
  const sourceSlices = $derived(
    (charts?.cost_by_source ?? []).map((s) => ({
      name: s.name.toLowerCase(),
      value: s.value,
      label: usd(s.value),
    })),
  );

  // Says WHY the window is empty. The previous version explained Langfuse's
  // Stop-hook lag -- it wrote once per COMPLETED turn, so a short window was
  // routinely empty while work was happening, and the panel had to probe how
  // far behind the store was to say so. OpenSearch is event-driven and lags
  // roughly 33s, so an empty short window now means genuinely idle rather than
  // "wait for the turn to end", and no freshness probe is needed.
  // Group the flat event stream into turns. turn_id is prompt@id, so one turn
  // is one user prompt and everything it caused — the unit a person actually
  // reads. Events arrive oldest-first, and insertion order preserves that, so
  // the groups come out in the order the work happened.
  //
  // Events with no turn_id (session-level plumbing) fall into one trailing
  // group rather than one group each, which would otherwise shred the list.
  const turnGroups = $derived.by(() => {
    const byTurn = new Map<string, SessionEvent[]>();
    for (const e of timeline?.events ?? []) {
      const key = e.turn_id || '(session)';
      const bucket = byTurn.get(key);
      if (bucket) bucket.push(e);
      else byTurn.set(key, [e]);
    }
    return [...byTurn.entries()].map(([id, events]) => ({
      id,
      events,
      cost: events.reduce((a, e) => a + (e.cost_usd ?? 0), 0),
      // Which agents touched this turn. tool_result carries no query_source, so
      // this is drawn from the model calls — absence is not "main thread", it
      // is "not recorded on this event".
      agents: [...new Set(events.map((e) => e.agent).filter(Boolean))] as string[],
    }));
  });

  const emptyMessage = $derived.by(() => {
    const win = range.replace('now-', 'the last ');
    return `No agent activity in ${win} for "${activeProfile}". Events reach OpenSearch within about a minute of happening, so an empty short window means nothing ran — not that a turn is still in flight.`;
  });

  const costTotal = $derived(
    (charts?.cost_series ?? []).reduce((a, p) => a + p.value, 0),
  );


</script>

<div class="agentwork">
  <header class="panel-header">
    <h1 class="page-title">agent work</h1>
    <div class="head-actions">
      <div class="seg" role="group" aria-label="Time range">
        {#each RANGES as r (r.value)}
          <button
            class="seg-btn"
            class:active={range === r.value}
            disabled={loading}
            onclick={() => (range = r.value)}
          >{r.label}</button>
        {/each}
      </div>
      <button
        class="btn btn-sm"
        disabled={loading || chartsLoading}
        onclick={() => (view === 'sessions' ? load() : loadCharts())}
      >refresh</button>
    </div>
  </header>

  <div class="seg tabs" role="tablist" aria-label="View">
    {#each VIEWS as v (v.id)}
      <button
        class="seg-btn"
        class:active={view === v.id}
        role="tab"
        aria-selected={view === v.id}
        onclick={() => (view = v.id)}
      >{v.label}</button>
    {/each}
  </div>

  <p class="scope-note">
    Sessions, content and cost from <strong>OpenSearch</strong>. Scoped to
    <strong>{activeProfile || 'no profile'}</strong> by
    <code>resource.attributes.workspace</code>, stamped on the events
    themselves.
  </p>

  {#if activeCount}
    <p class="working">
      <span class="dot dot-active"></span>{activeCount} working right now.
    </p>
  {/if}

  {#if error}
    <div class="notice notice-bad">
      <strong>Could not load sessions.</strong>
      <p>{error}</p>
      <p class="notice-sub">
        This panel reads OpenSearch, so it needs the platform reachable and the
        OpenSearch integration enabled. Sessions are scoped by
        <code>resource.attributes.workspace</code>, which Claude Code stamps
        from <code>OTEL_RESOURCE_ATTRIBUTES</code> — so a profile with no
        sessions may simply not be stamping one.
      </p>
    </div>
  {/if}

  {#if view === 'sessions'}
  {#if loading && sessions.length === 0}
    <p class="muted"><Spinner /> Loading sessions…</p>
  {:else if !error && sessions.length === 0}
    <EmptyState
      title={activeProfile
        ? 'No agent sessions in this window'
        : 'No profile selected'}
      message={activeProfile ? emptyMessage : 'Agent Work reads Langfuse, whose API keys are per profile. Select a profile to load its sessions.'}
    />
  {:else if sessions.length > 0}
    <div class="table-wrap">
      <table class="list">
        <thead>
          <tr>
            <th>session</th>
            <th class="num">turns</th>
            <th class="num">tools</th>
            <th class="num">fails</th>
            <th class="num">tokens</th>
            <th class="num">cached</th>
            <th class="num">cost</th>
            <th class="num">elapsed</th>
            <th>last active</th>
          </tr>
        </thead>
        <tbody>
          {#each sessions as s (s.session_id)}
            <tr
              class="session-row"
              class:selected={s.session_id === selectedId}
              onclick={() => open(s)}
              tabindex="0"
              role="button"
              onkeydown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  open(s);
                }
              }}
            >
              <td class="sid" title={s.session_id}>
                {s.session_id.slice(0, 8)}
                {#if s.models?.length}
                  <span class="muted models">{s.models.join(', ')}</span>
                {/if}
              </td>
              <td class="num">{s.turns || '—'}</td>
              <td class="num" title={s.last_tool ? `last: ${s.last_tool}` : ''}
                >{s.tool_calls || '—'}</td>
              <!-- Only a non-zero failure count is worth colouring; zero is the
                   normal case and should not read as a warning. -->
              <td class="num" class:bad={s.failures > 0}>{s.failures || '—'}</td>
              <td class="num">{compact((s.tokens_in ?? 0) + (s.tokens_out ?? 0))}</td>
              <!-- Cache reads get their own column rather than being folded in:
                   they dominate by orders of magnitude, so one merged total
                   reads as a contradiction against the cost. -->
              <td class="num muted">{compact((s.tokens_cache_in ?? 0) + (s.tokens_cache_new ?? 0))}</td>
              <td class="num">{usd(s.cost_usd)}</td>
              <td class="num">{duration(s.first_seen, s.last_seen)}</td>
              <td class="muted">
                <span
                  class="dot dot-{s.liveness ?? 'none'}"
                  title={livenessTitle(s)}
                  aria-label={livenessTitle(s)}
                ></span>
                {ago(s.last_seen, now)}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}

  {#if selected}
    <section class="detail">
      <header class="detail-head">
        <h2>
          session {selected.session_id.slice(0, 8)}
          <span class="muted">{selected.workspace}</span>
        </h2>
        <button class="btn btn-sm" onclick={close}>close</button>
      </header>

      <p class="muted totals">
        {selected.turns} turns · {selected.tool_calls} tool calls ·
        {selected.failures} failed · {compact((selected.tokens_in ?? 0) + (selected.tokens_out ?? 0))} tokens
        ({compact((selected.tokens_cache_in ?? 0) + (selected.tokens_cache_new ?? 0))} cached) ·
        <span title="Langfuse's own pricing of the generations it captured. The
OpenSearch figure for the same session is higher, because the Stop hook misses
delegated work — treat this as Langfuse's view, not authoritative spend."
          >{usd(selected.cost_usd)} <span class="qual">(Langfuse view)</span></span>
      </p>

      {#if timelineLoading}
        <p class="muted"><Spinner /> Loading timeline…</p>
      {:else if timelineError}
        <p class="error">{timelineError}</p>
      {:else if timeline}
        {#if timeline.truncated}
          <div class="notice">
            <strong>This timeline is truncated.</strong>
            <p>
              Showing the most recent {timeline.events.length} of
              {timeline.total_events} events. The query takes the NEWEST events,
              so what is missing is the earliest part of the session — for a
              running session the recent end is the useful end.
            </p>
          </div>
        {/if}

        {#if timeline.events.length > 0 && timeline.events.every((e) => e.redacted || (!e.prompt && !e.response))}
          <div class="notice">
            <strong>No prompt or completion text in this session.</strong>
            <p>
              Its observations came from native OTLP spans, which carry metadata
              only — timings, models, tool names — and no message text. Content
              arrives via the transcript hook, which captures conversations but
              misses most delegated subagent work.
            </p>
          </div>
        {/if}

        <!-- Turn-grouped event list. OpenSearch gives a FLAT event stream
             keyed by turn_id (prompt@id), not the parent/child observation tree
             Langfuse produced, so SessionTree and the radial view have nothing
             to draw and their toggles are hidden above. What is gained: `agent`
             carries query_source, so a model call made by a subagent is
             distinguishable from the main thread — which Langfuse could not do
             at all. -->
        <ol class="turns">
          {#each turnGroups as turn (turn.id)}
            <li class="turn">
              <div class="turn-head">
                <span class="t">{clock(turn.events[0].time)}</span>
                <span class="muted">{turn.events.length} events</span>
                {#if turn.cost}<span class="muted">{usd(turn.cost)}</span>{/if}
                {#if turn.agents.length}
                  <span class="muted agents">{turn.agents.join(', ')}</span>
                {/if}
              </div>
              <ul class="turn-events">
                {#each turn.events as e (e.sequence)}
                  <li
                    class="ev"
                    class:ev-bad={isError(e)}
                    class:ev-pinned={String(e.sequence) === pinnedObsId}
                  >
                    <button
                      class="ev-btn"
                      onclick={() => (pinnedObsId = String(e.sequence) === pinnedObsId ? null : String(e.sequence))}
                    >
                      <Badge text={e.kind.replace(/_/g, ' ')} variant={typeVariant(e.kind)} />
                      {#if e.tool_name}<span class="name">{e.tool_name}</span>{/if}
                      {#if e.model}<span class="muted">{e.model}</span>{/if}
                      {#if e.duration_ms}<span class="muted">{ms(e.duration_ms)}</span>{/if}
                      {#if e.cost_usd}<span class="muted">{usd(e.cost_usd)}</span>{/if}
                      {#if isError(e)}<span class="bad">failed</span>{/if}
                    </button>
                  </li>
                {/each}
              </ul>
            </li>
          {/each}
        </ol>

        <!-- The pinned event's text. Lives here rather than inline per row:
             inside the list every row became a collapsible, which buried the
             thing being looked for. -->
        {#if pinnedObs}
          <div class="obsdetail">
            <div class="obs-head">
              <span class="t">{clock(pinnedObs.time)}</span>
              <Badge text={pinnedObs.kind.replace(/_/g, ' ')} variant={typeVariant(pinnedObs.kind)} />
              {#if pinnedObs.tool_name}<strong class="name">{pinnedObs.tool_name}</strong>{/if}
              {#if pinnedObs.agent}<span class="muted">{pinnedObs.agent}</span>{/if}
              {#if pinnedObs.model}<span class="muted">{pinnedObs.model}</span>{/if}
              {#if pinnedObs.duration_ms}<span class="muted">{ms(pinnedObs.duration_ms)}</span>{/if}
              {#if pinnedObs.cost_usd}<span class="muted">{usd(pinnedObs.cost_usd)}</span>{/if}
              <button class="linkish" onclick={() => (pinnedObsId = null)}>clear</button>
            </div>
            {#if pinnedObs.prompt}
              <div class="io">
                <span class="io-label">prompt</span>
                <pre>{pinnedObs.prompt}</pre>
              </div>
            {/if}
            {#if pinnedObs.response}
              <div class="io">
                <span class="io-label">response</span>
                <pre>{pinnedObs.response}</pre>
              </div>
            {/if}
            {#if !pinnedObs.prompt && !pinnedObs.response}
              <p class="muted">
                {pinnedObs.redacted
                  ? 'Text redacted — content logging was off when this ran.'
                  : 'This event carries no message text; tool calls and model requests record metadata only.'}
              </p>
            {/if}
          </div>
        {/if}
      {/if}
    </section>
  {/if}
  {/if}

  {#if view !== 'sessions'}
    {#if chartsError}
      <div class="notice notice-bad">
        <strong>Could not load charts.</strong>
        <p>{chartsError}</p>
      </div>
    {:else if chartsLoading && !charts}
      <p class="muted"><Spinner /> Loading charts…</p>
    {:else if charts}
      {#if view === 'trends'}
        <div class="charts">
          <div class="chart-card">
            <MetricsChart
              data={charts.cost_series ?? []}
              label={`cost per ${charts.granularity}`}
              current={usd(costTotal)}
              formatY={(v: number) => (v >= 1 ? `$${v.toFixed(0)}` : `$${v.toFixed(2)}`)}
              height={150}
            />
          </div>
        </div>
      {:else}
        <div class="charts">
          <div class="chart-card">
            <h3>cost by model</h3>
            {#if modelSlices.length}
              <div class="pie-row">
                <PieChart slices={modelSlices} size={150} />
                <ul class="legend">
                  {#each modelSlices as s (s.name)}
                    <li><span class="k">{s.name}</span><span class="v">{s.label}</span></li>
                  {/each}
                </ul>
              </div>
            {:else}
              <p class="muted">No priced model calls in this window.</p>
            {/if}
          </div>
          <div class="chart-card">
            <h3>cost by source</h3>
            {#if sourceSlices.length}
              <div class="pie-row">
                <PieChart slices={sourceSlices} size={150} />
                <ul class="legend">
                  {#each sourceSlices as s (s.name)}
                    <li><span class="k">{s.name}</span><span class="v">{s.label}</span></li>
                  {/each}
                </ul>
              </div>
              <p class="note">
                Main thread vs delegated subagents vs auxiliary overhead. This is
                the split the Langfuse-sourced charts could not show — its Stop
                hook never saw the delegated work.
              </p>
            {:else}
              <p class="muted">No priced calls in this window.</p>
            {/if}
          </div>
        </div>
      {/if}

    {/if}
  {/if}
</div>

<style>
  /* Follows the panel idiom used across this codebase: `.panel-header` + h1,
     `.btn` with space-separated modifiers, the --spacing-* scale and the
     --color-* tokens. No literal colours. */
  .agentwork {
    padding: var(--spacing-lg);
    display: flex;
    flex-direction: column;
    gap: var(--spacing-md);
  }
  .head-actions {
    display: flex;
    align-items: center;
    gap: var(--spacing-md);
  }
  .seg {
    display: inline-flex;
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm);
    overflow: hidden;
  }
  .seg-btn {
    background: transparent;
    border: 0;
    color: var(--color-text-secondary);
    font-size: 11px;
    padding: 3px 10px;
    cursor: pointer;
  }
  .seg-btn:not(:last-child) {
    border-right: 1px solid var(--color-border-primary);
  }
  .seg-btn.active {
    background: var(--color-muted-bg);
    color: var(--color-text-primary);
  }
  .seg-btn:disabled {
    cursor: default;
    opacity: 0.6;
  }
  .scope-note,
  .working,
  .totals {
    margin: 0;
    font-size: 11px;
    color: var(--color-text-secondary);
  }
  .qual {
    opacity: 0.75;
  }
  .table-wrap {
    overflow-x: auto;
  }
  table.list {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
  }
  table.list th {
    text-align: left;
    font-weight: 500;
    color: var(--color-text-tertiary);
    border-bottom: 1px solid var(--color-border-primary);
    padding: 4px 8px;
    white-space: nowrap;
  }
  table.list td {
    padding: 4px 8px;
    border-bottom: 1px solid var(--color-border-secondary);
    white-space: nowrap;
  }
  table.list th.num,
  table.list td.num {
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
  /* NOT class="row": the global .row in styles/components.css is a flex card
     utility (display:flex + background + gap + border). On a <tr> it takes the
     row out of the table layout entirely — the cells stop participating in the
     column algorithm, so the header spreads across the full width while the
     values bunch left, and the table overflows its wrapper. */
  tr.session-row {
    cursor: pointer;
  }
  tr.session-row:hover td {
    background: var(--color-muted-bg);
  }
  tr.session-row.selected td {
    background: var(--color-muted-bg);
  }
  .sid {
    font-family: var(--font-mono, monospace);
  }
  .models {
    margin-left: var(--spacing-xs);
    font-family: inherit;
  }
  .muted {
    color: var(--color-text-tertiary);
  }
  .error {
    color: var(--color-status-error-text);
    font-size: 12px;
  }
  /* Liveness. Never colour-alone: every dot carries a title and aria-label,
     and the relative age sits beside it in the same cell. The filled/hollow
     distinction also survives a monochrome or forced-colours rendering. */
  .dot {
    display: inline-block;
    width: 6px;
    height: 6px;
    border-radius: 50%;
    margin-right: 4px;
    vertical-align: middle;
    background: var(--color-status-success-text, var(--color-text-primary));
  }
  .dot-active {
    background: var(--color-status-success-text, var(--color-text-primary));
  }
  .dot-idle {
    background: var(--color-status-warning-text, var(--color-text-secondary));
  }
  /* Dormant and unknown are hollow: this telemetry has no session-end event,
     so a solid dot would overstate what the data supports. */
  .dot-dormant {
    background: transparent;
    box-shadow: inset 0 0 0 1px var(--color-text-tertiary, var(--color-border));
  }
  .dot-unknown,
  .dot-none {
    background: transparent;
    box-shadow: inset 0 0 0 1px var(--color-border-secondary, var(--color-border));
    opacity: 0.6;
  }
  .notice {
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm);
    padding: var(--spacing-sm) var(--spacing-md);
    font-size: 11px;
    color: var(--color-text-secondary);
  }
  .notice-bad {
    border-color: var(--color-status-error-text);
  }
  .notice p {
    margin: var(--spacing-xs) 0 0;
  }
  .notice-sub {
    opacity: 0.8;
  }
  .detail {
    border-top: 1px solid var(--color-border-primary);
    padding-top: var(--spacing-md);
    display: flex;
    flex-direction: column;
    gap: var(--spacing-sm);
  }
  .detail-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--spacing-md);
  }
  .detail-head h2 {
    margin: 0;
    font-size: 13px;
    font-weight: 600;
  }
  .obs-head {
    display: flex;
    align-items: center;
    gap: var(--spacing-xs);
    flex-wrap: wrap;
  }
  .obs-head .t {
    font-family: var(--font-mono, monospace);
    color: var(--color-text-tertiary);
    font-size: 11px;
  }
  .status {
    margin: 2px 0 0;
    font-size: 11px;
    color: var(--color-status-error-text);
  }
  .io {
    display: flex;
    gap: var(--spacing-xs);
    margin-top: 2px;
  }
  .io-label {
    flex: 0 0 24px;
    font-size: 10px;
    color: var(--color-text-tertiary);
    text-transform: uppercase;
  }
  /* Content is unbounded, so it scrolls in its own box rather than pushing the
     timeline off screen. */
  .tabs {
    align-self: flex-start;
  }
  .viewbar {
    display: flex;
    align-items: center;
    gap: var(--spacing-sm);
    flex-wrap: wrap;
  }
  .vlbl {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--color-text-tertiary);
  }
  .vhint {
    font-size: 11px;
  }
  .obsdetail {
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm);
    padding: var(--spacing-sm) var(--spacing-md);
    display: flex;
    flex-direction: column;
    gap: var(--spacing-xs);
  }
  .caveat {
    border-style: dashed;
  }
  .charts {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
    gap: var(--spacing-md);
  }
  .chart-card {
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm);
    padding: var(--spacing-sm) var(--spacing-md);
    min-width: 0;
  }
  .chart-card h3 {
    margin: 0 0 var(--spacing-xs);
    font-size: 11px;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--color-text-tertiary);
  }
  .pie-row {
    display: flex;
    align-items: center;
    gap: var(--spacing-md);
    flex-wrap: wrap;
  }
  ul.legend {
    list-style: none;
    margin: 0;
    padding: 0;
    font-size: 11px;
    min-width: 0;
  }
  ul.legend li {
    display: flex;
    justify-content: space-between;
    gap: var(--spacing-md);
    padding: 1px 0;
  }
  ul.legend .k {
    color: var(--color-text-secondary);
    overflow: hidden;
    text-overflow: ellipsis;
  }
  ul.legend .v {
    color: var(--color-text-primary);
    font-variant-numeric: tabular-nums;
  }
  .links {
    display: flex;
    gap: var(--spacing-md);
    flex-wrap: wrap;
    margin: 0;
  }
  .linkish {
    background: none;
    border: 0;
    padding: 0;
    cursor: pointer;
    font-size: 11px;
    color: var(--color-text-secondary);
    text-decoration: underline;
  }
  .linkish:hover {
    color: var(--color-text-primary);
  }
  .io pre {
    margin: 0;
    flex: 1 1 auto;
    max-height: 220px;
    overflow: auto;
    white-space: pre-wrap;
    word-break: break-word;
    font-family: var(--font-mono, monospace);
    font-size: 11px;
    background: var(--color-muted-bg);
    border-radius: var(--radius-sm);
    padding: var(--spacing-xs) var(--spacing-sm);
  }
</style>

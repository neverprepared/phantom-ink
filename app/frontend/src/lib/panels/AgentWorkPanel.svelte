<script lang="ts">
  // Agent Work: the table of agent sessions, and the drill-down into one.
  //
  // Reads from BOTH stores, because neither can do the whole job and the
  // measurements are the reason this is a split rather than a choice:
  //
  //  - LANGFUSE owns the work. Its observations carry the real prompt and
  //    completion text, so "click a session and read what it said" is only
  //    answerable there. OpenSearch has no content at all — it is stripped from
  //    spans by design, and the log attributes read "<REDACTED>".
  //  - OPENSEARCH owns liveness. Measured together on one live session,
  //    OpenSearch was 33s behind and Langfuse 725s: the Claude Code capture is
  //    a Stop hook, firing once per COMPLETED turn, so a session mid-turn is
  //    invisible in Langfuse until the turn ends. A dot built on Langfuse
  //    recency marks a working session dead.
  //
  // The Go side merges them (app_agent_work.go) and each half degrades on its
  // own: either store being unreachable costs its own columns, never the row.
  //
  // Scope is the ACTIVE PROFILE, always. There is no "all profiles" view and
  // cannot be: Langfuse API keys are project-scoped, so the credentials
  // themselves select the profile. That is also what stops this panel reading
  // another profile's traces.
  import { getApi } from '../utils/api';
  import { profileState } from '../stores.svelte';
  import Badge from '../components/Badge.svelte';
  import EmptyState from '../components/EmptyState.svelte';
  import MetricsChart from '../components/MetricsChart.svelte';
  import PieChart from '../components/PieChart.svelte';
  import SessionTree from '../components/SessionTree.svelte';
  import Spinner from '../components/Spinner.svelte';
  import { openInBrowser } from '../utils/api';

  interface AgentWorkRow {
    session_id: string;
    workspace: string;
    first_seen: string;
    last_seen: string;
    // From Langfuse. `observations` counts generations, tool calls and spans
    // together — it is NOT the OpenSearch tool-call figure.
    observations: number;
    generations: number;
    tool_calls: number;
    tokens: number;
    // Typed as possibly absent on purpose: Go marshals a nil slice as `null`,
    // and reading .length on it threw during render and killed this component.
    models?: string[] | null;
    cost_usd: number;
    // From OpenSearch. `liveness_available` distinguishes "no overlay" from
    // "the overlay says unknown"; without it a missing store would render as
    // a confident "dormant".
    liveness?: 'active' | 'idle' | 'dormant' | 'unknown';
    last_activity_ms?: number;
    last_heartbeat_ms?: number;
    liveness_available: boolean;
  }

  interface Observation {
    id: string;
    parent_id?: string;
    // Precomputed in Go from the parent links, so rendering never walks the
    // tree. A parent outside the fetched page counts as a root, which stops a
    // truncated fetch producing runaway indentation.
    depth: number;
    name: string;
    type: string;
    start_time: string;
    end_time?: string;
    latency_ms?: number;
    model?: string;
    cost_usd?: number;
    level?: string;
    status_message?: string;
    input?: string;
    output?: string;
  }

  interface SessionTimeline {
    session_id: string;
    workspace: string;
    observations: Observation[];
    total: number;
    truncated: boolean;
    // False when nothing carried input or output. Native OTLP spans carry
    // neither, so a span-only session has structure but no conversation — said
    // plainly rather than rendered as an empty transcript.
    content_available: boolean;
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
    obs_series?: Point[] | null;
    cost_by_model?: Share[] | null;
    obs_by_type?: Share[] | null;
    // Always true for this source. Langfuse's Claude Code capture is a Stop
    // hook that misses delegated subagent work, so its cost is lower than
    // OpenSearch's for the same sessions. Labelled rather than silently shown.
    cost_understated: boolean;
    sessions_url?: string;
    traces_url?: string;
    dashboard_url?: string;
  }

  type View = 'sessions' | 'trends' | 'breakdown';

  // Three shapes of one session, per the Agent Work Wheel probe: a list to act
  // on, a tree to audit, a wheel to see scale. Each answers a different
  // question, so all three are kept rather than one being chosen.
  type SessionView = 'list' | 'tree' | 'radial';

  const SESSION_VIEWS: { id: SessionView; label: string; hint: string }[] = [
    { id: 'list', label: 'list', hint: 'Chronological rows — scan and act' },
    { id: 'tree', label: 'tree', hint: 'Depth across, time down — the audit shape' },
    { id: 'radial', label: 'radial', hint: 'Time clockwise, depth outward — scale and fan-out' },
  ];

  const VIEWS: { id: View; label: string }[] = [
    { id: 'sessions', label: 'sessions' },
    { id: 'trends', label: 'trends' },
    { id: 'breakdown', label: 'breakdown' },
  ];

  // The logs export batches every 10s; polling faster cannot surface fresher
  // data. Langfuse is slower still (Stop hook), so this is generous for it.
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
    { label: '24h', value: 'now-24h' },
    { label: '7d', value: 'now-7d' },
  ];

  let view = $state<View>('sessions');
  let sessionView = $state<SessionView>('list');
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
  let range = $state('now-24h');
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
        api.ListAgentWork(activeProfile, range, 50),
        'ListAgentWork',
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
    if (!s.liveness_available) {
      return 'No liveness data — the OpenSearch overlay was unavailable. This is not a statement that the session is idle.';
    }
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
        api.GetAgentWorkTimeline(s.session_id, activeProfile, 500),
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
    (timeline?.observations ?? []).find((o) => o.id === pinnedObsId) ?? null,
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

  function isError(o: Observation): boolean {
    return (o.level ?? '').toUpperCase() === 'ERROR';
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
  const typeSlices = $derived(
    (charts?.obs_by_type ?? []).map((s) => ({
      name: s.name.toLowerCase(),
      value: s.value,
      label: String(s.value),
    })),
  );

  const costTotal = $derived(
    (charts?.cost_series ?? []).reduce((a, p) => a + p.value, 0),
  );
  const obsTotal = $derived(
    (charts?.obs_series ?? []).reduce((a, p) => a + p.value, 0),
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
    Sessions and content from <strong>Langfuse</strong>; the activity dot from
    OpenSearch, which ingests in seconds where the Langfuse capture waits for a
    turn to finish. Scoped to <strong>{activeProfile || 'no profile'}</strong> —
    Langfuse keys are per project, so there is no all-profiles view.
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
        This panel needs <code>LANGFUSE_BASE_URL</code>,
        <code>LANGFUSE_PUBLIC_KEY</code> and <code>LANGFUSE_SECRET_KEY</code> in
        the profile's env file. The keys are project-scoped, so each profile
        carries its own.
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
      message={activeProfile
        ? `Queried Langfuse for environment "${activeProfile}" over ${range.replace('now-', 'the last ')}. Nothing has been traced there in that range.`
        : 'Agent Work reads Langfuse, whose API keys are per profile. Select a profile to load its sessions.'}
    />
  {:else if sessions.length > 0}
    <div class="table-wrap">
      <table class="list">
        <thead>
          <tr>
            <th>session</th>
            <th class="num">obs</th>
            <th class="num">model calls</th>
            <th class="num">tools</th>
            <th class="num">tokens</th>
            <th class="num">cost</th>
            <th class="num">elapsed</th>
            <th>last active</th>
          </tr>
        </thead>
        <tbody>
          {#each sessions as s (s.session_id)}
            <tr
              class="row"
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
              <td class="num">{s.observations}</td>
              <td class="num">{s.generations || '—'}</td>
              <td class="num">{s.tool_calls || '—'}</td>
              <td class="num">{compact(s.tokens)}</td>
              <td class="num">{usd(s.cost_usd)}</td>
              <td class="num">{duration(s.first_seen, s.last_seen)}</td>
              <td class="muted">
                <span
                  class="dot dot-{s.liveness_available ? s.liveness : 'none'}"
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
        {selected.observations} observations · {selected.generations} model calls ·
        {selected.tool_calls} tool calls · {compact(selected.tokens)} tokens ·
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
              Showing the first {timeline.observations.length} observations of a
              longer session. Deeper history is in Langfuse itself.
            </p>
          </div>
        {/if}

        {#if !timeline.content_available}
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

        <div class="viewbar">
          <span class="vlbl">shape</span>
          <div class="seg" role="tablist" aria-label="Session shape">
            {#each SESSION_VIEWS as v (v.id)}
              <button
                class="seg-btn"
                class:active={sessionView === v.id}
                role="tab"
                aria-selected={sessionView === v.id}
                title={v.hint}
                onclick={() => (sessionView = v.id)}
              >{v.label}</button>
            {/each}
          </div>
          <span class="muted vhint">
            {SESSION_VIEWS.find((v) => v.id === sessionView)?.hint}
          </span>
        </div>

        <SessionTree
          observations={timeline.observations}
          view={sessionView}
          selectedId={pinnedObsId}
          onSelect={(id) => (pinnedObsId = id)}
        />

        <!-- The pinned observation's full detail, including the conversation.
             Lives here rather than inside the shapes: a tree node or a wheel dot
             cannot hold a transcript, and in the list it made every row a
             collapsible, which buried the thing being looked for. -->
        {#if pinnedObs}
          <div class="obsdetail">
            <div class="obs-head">
              <span class="t">{clock(pinnedObs.start_time)}</span>
              <Badge text={pinnedObs.type.toLowerCase()} variant={typeVariant(pinnedObs.type)} />
              <strong class="name">{pinnedObs.name || '(unnamed)'}</strong>
              {#if pinnedObs.model}<span class="muted">{pinnedObs.model}</span>{/if}
              {#if pinnedObs.latency_ms}<span class="muted">{ms(pinnedObs.latency_ms)}</span>{/if}
              {#if pinnedObs.cost_usd}<span class="muted">{usd(pinnedObs.cost_usd)}</span>{/if}
              <button class="linkish" onclick={() => (pinnedObsId = null)}>clear</button>
            </div>
            {#if isError(pinnedObs) && pinnedObs.status_message}
              <p class="status">{pinnedObs.status_message}</p>
            {/if}
            {#if pinnedObs.input}
              <div class="io">
                <span class="io-label">in</span>
                <pre>{pinnedObs.input}</pre>
              </div>
            {/if}
            {#if pinnedObs.output}
              <div class="io">
                <span class="io-label">out</span>
                <pre>{pinnedObs.output}</pre>
              </div>
            {/if}
            {#if !pinnedObs.input && !pinnedObs.output}
              <p class="muted">
                This observation carries no message text — native OTLP spans
                record metadata only.
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
      {#if charts.cost_understated}
        <p class="note caveat">
          Cost here is <strong>understated</strong>. Langfuse's Claude Code
          capture is a Stop hook that misses delegated subagent work — measured
          on one session, $318.82 here against $515.78 in OpenSearch. Use it for
          shape and trend, not for a spend figure.
        </p>
      {/if}

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
          <div class="chart-card">
            <MetricsChart
              data={charts.obs_series ?? []}
              label={`observations per ${charts.granularity}`}
              current={compact(obsTotal)}
              formatY={(v: number) => compact(v)}
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
            <h3>observations by type</h3>
            {#if typeSlices.length}
              <div class="pie-row">
                <PieChart slices={typeSlices} size={150} />
                <ul class="legend">
                  {#each typeSlices as s (s.name)}
                    <li><span class="k">{s.name}</span><span class="v">{s.label}</span></li>
                  {/each}
                </ul>
              </div>
            {:else}
              <p class="muted">No observations in this window.</p>
            {/if}
          </div>
        </div>
      {/if}

      {#if charts.dashboard_url || charts.sessions_url}
        <p class="links">
          {#if charts.dashboard_url}
            <button class="linkish" onclick={() => openInBrowser(charts!.dashboard_url!)}
              >open Langfuse dashboards ↗</button>
          {/if}
          {#if charts.sessions_url}
            <button class="linkish" onclick={() => openInBrowser(charts!.sessions_url!)}
              >open Langfuse sessions ↗</button>
          {/if}
        </p>
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
  tr.row {
    cursor: pointer;
  }
  tr.row:hover td {
    background: var(--color-muted-bg);
  }
  tr.row.selected td {
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

<script lang="ts">
  // Agent Work: the table/list of agent sessions, and the drill-down into one.
  //
  // Everything here reads `logs-otel-*` in OpenSearch, which Claude Code
  // already writes. No new capture pipeline stands behind this panel — the
  // session list, the tool timeline, per-turn cost and model are all present
  // today. Prompt and completion TEXT is the one thing that is not: the
  // exporter emits those attributes as the literal "<REDACTED>" unless the
  // content flags are set. We report that as a state rather than rendering the
  // sentinel as if it were a message.
  //
  // Scope follows the active profile, matching the rest of the app: with a
  // profile selected you see its sessions; "All profiles" is an explicit
  // choice, not the default. Profile comes from the event's own
  // resource.attributes.workspace — stamped at emission, never inherited.
  import { getApi } from '../utils/api';
  import { profileState } from '../stores.svelte';
  import Badge from '../components/Badge.svelte';
  import EmptyState from '../components/EmptyState.svelte';
  import Spinner from '../components/Spinner.svelte';
  import { SvelteSet } from 'svelte/reactivity';

  interface AgentSession {
    session_id: string;
    workspace: string;
    first_seen: string;
    last_seen: string;
    turns: number;
    tool_calls: number;
    failures: number;
    last_tool: string;
    top_tools: string[];
    models: string[];
    cost_usd: number;
    tokens_in: number;
    tokens_out: number;
    tokens_cache_in: number;
    tokens_cache_new: number;
  }

  interface SessionEvent {
    sequence: number;
    time: string;
    kind: string;
    turn_id: string;
    tool_name?: string;
    tool_use_id?: string;
    success?: boolean;
    duration_ms?: number;
    input_size?: number;
    result_size?: number;
    agent?: string;
    agent_type?: string;
    agent_source?: string;
    tool_uses?: number;
    total_tokens?: number;
    tool_input?: string;
    tool_params?: Record<string, string>;
    model?: string;
    tokens_in?: number;
    tokens_out?: number;
    cost_usd?: number;
    content?: string;
    redacted?: boolean;
    content_length?: number;
  }

  interface SessionTimeline {
    session_id: string;
    workspace: string;
    events: SessionEvent[];
    totals: {
      turns: number;
      tool_calls: number;
      failures: number;
      cost_usd: number;
      tokens_out: number;
      first_seen: string;
      last_seen: string;
    };
    truncated: boolean;
    total_events: number;
    warning?: string;
    content_available: boolean;
    tool_details_available: boolean;
  }

  const RANGES = [
    { label: '24h', value: 'now-24h' },
    { label: '7d', value: 'now-7d' },
    { label: '30d', value: 'now-30d' },
  ];

  let sessions = $state<AgentSession[]>([]);
  let loading = $state(true);
  let error = $state('');
  let range = $state('now-24h');
  let allProfiles = $state(false);

  // The detail pane tracks an ID, not a row object. Holding the object itself
  // detaches it from the list: a refresh replaces `sessions` with fresh rows
  // while the pane keeps rendering counts, cost and timestamps frozen at the
  // moment it was opened. `selectedSnapshot` is the fallback for when the row
  // legitimately leaves the list (aged out of the window, or scope narrowed),
  // so the pane shows last-known values instead of blanking.
  let selectedId = $state<string | null>(null);
  let selectedSnapshot = $state<AgentSession | null>(null);
  let timeline = $state<SessionTimeline | null>(null);
  let timelineLoading = $state(false);
  let timelineError = $state('');

  const activeProfile = $derived(profileState.active?.name ?? '');
  // An empty workspace means unfiltered on the Go side, which is exactly what
  // "All profiles" wants. With no profile selected at all there is nothing to
  // scope to, so the view is global either way.
  const scope = $derived(allProfiles ? '' : activeProfile);

  // Live row wins so the facts stay current; the snapshot covers the gap.
  const selected = $derived(
    (selectedId ? sessions.find((s) => s.session_id === selectedId) : null) ??
      selectedSnapshot,
  );

  // Two independent monotonic counters, deliberately NOT shared. A slow
  // in-flight request must not overwrite a newer one of its own kind — but the
  // list and the timeline are unrelated, so a single counter would make opening
  // a session discard an in-flight list refresh, and a list refresh discard a
  // timeline the operator just asked for.
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
      const rows = await api.ListAgentSessions(scope, range, 50);
      if (mine !== listEpoch) return;
      sessions = (rows ?? []) as AgentSession[];
    } catch (e) {
      if (mine !== listEpoch) return;
      error = String(e);
      sessions = [];
    } finally {
      if (mine === listEpoch) loading = false;
    }
  }

  // Reloads when the range, the profile scope, or the active profile changes.
  // A $effect rather than a $derived: this is a side effect, and mutating
  // state inside $derived loops.
  $effect(() => {
    void range;
    void scope;
    void load();
  });

  // A selected session from another profile must not survive a scope change —
  // otherwise the detail pane shows work the new scope excludes. Reads the
  // snapshot rather than `selected` so this cannot depend on its own effect.
  $effect(() => {
    const s = scope;
    const snap = selectedSnapshot;
    if (snap && s && snap.workspace !== s) {
      close();
    }
  });

  async function open(s: AgentSession) {
    selectedId = s.session_id;
    selectedSnapshot = s;
    timeline = null;
    timelineError = '';
    timelineLoading = true;
    expanded = new SvelteSet();
    const mine = ++timelineEpoch;
    try {
      const api = await getApi();
      if (!api) {
        timelineError = 'Wails bindings unavailable.';
        return;
      }
      const tl = await api.GetAgentSessionTimeline(s.session_id, s.workspace, 1000);
      if (mine !== timelineEpoch) return;
      timeline = tl as unknown as SessionTimeline;
    } catch (e) {
      if (mine !== timelineEpoch) return;
      timelineError = String(e);
    } finally {
      if (mine === timelineEpoch) timelineLoading = false;
    }
  }

  // Which events have had their captured text expanded. Keyed by sequence and
  // reset whenever a different session is opened.
  let expanded = $state(new SvelteSet<number>());

  function toggleExpanded(seq: number) {
    // Reassigned, not mutated in place, so the template reliably re-renders.
    const next = new SvelteSet(expanded);
    if (next.has(seq)) next.delete(seq);
    else next.add(seq);
    expanded = next;
  }

  function close() {
    timelineEpoch++;
    timelineLoading = false;
    selectedId = null;
    selectedSnapshot = null;
    timeline = null;
    timelineError = '';
  }

  // --- formatting ----------------------------------------------------------

  function shortId(id: string): string {
    return id.slice(0, 8);
  }

  function clock(iso: string): string {
    if (!iso) return '—';
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? '—' : d.toLocaleTimeString();
  }

  function ago(iso: string): string {
    if (!iso) return '—';
    const t = new Date(iso).getTime();
    if (Number.isNaN(t)) return '—';
    const s = Math.max(0, Math.round((Date.now() - t) / 1000));
    if (s < 60) return `${s}s ago`;
    if (s < 3600) return `${Math.round(s / 60)}m ago`;
    if (s < 86400) return `${Math.round(s / 3600)}h ago`;
    return `${Math.round(s / 86400)}d ago`;
  }

  function duration(a: string, b: string): string {
    const s = new Date(a).getTime();
    const e = new Date(b).getTime();
    if (Number.isNaN(s) || Number.isNaN(e) || e < s) return '—';
    const m = Math.round((e - s) / 60000);
    if (m < 60) return `${m}m`;
    return `${Math.floor(m / 60)}h ${m % 60}m`;
  }

  function ms(n?: number): string {
    if (!n) return '';
    return n < 1000 ? `${n}ms` : `${(n / 1000).toFixed(1)}s`;
  }

  function bytes(n?: number): string {
    if (!n) return '';
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / 1024 / 1024).toFixed(1)} MB`;
  }

  function usd(n: number): string {
    if (!n) return '$0.00';
    return n < 0.01 ? '<$0.01' : `$${n.toFixed(2)}`;
  }

  function compact(n: number): string {
    if (!n) return '0';
    if (n < 1000) return String(n);
    if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
    if (n < 1_000_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
    return `${(n / 1_000_000_000).toFixed(2)}B`;
  }

  const KIND_LABEL: Record<string, string> = {
    user_prompt: 'prompt',
    assistant_response: 'response',
    tool_result: 'tool',
    api_request: 'model call',
    subagent_completed: 'subagent done',
  };

  // query_source values. "repl_main_thread" is the session's own thread;
  // "agent:custom" / "agent:builtin:<Type>" are subagents; the rest is harness
  // overhead that still costs money and should not masquerade as agent work.
  function agentLabel(a?: string): string {
    if (!a || a === 'repl_main_thread') return '';
    if (a === 'agent:custom') return 'subagent';
    if (a.startsWith('agent:builtin:')) return a.slice('agent:builtin:'.length);
    return a.replace(/_/g, ' ');
  }
  const isSubagent = (a?: string) => !!a && a.startsWith('agent:');

  // Badge variants are limited to those Badge.svelte defines; a failed tool
  // call is the only thing that earns a loud one.
  function kindVariant(e: SessionEvent): string {
    if (e.kind === 'tool_result') return e.success === false ? 'failed' : 'completed';
    if (e.kind === 'subagent_completed') return 'worker';
    if (e.kind === 'api_request') return 'running';
    return '';
  }

  // Turns are the unit an operator reads a session in. Grouping by turn_id
  // preserves execution order because the events arrive ordered by sequence.
  interface Turn {
    id: string;
    events: SessionEvent[];
  }
  const turns = $derived.by<Turn[]>(() => {
    const out: Turn[] = [];
    for (const e of timeline?.events ?? []) {
      const id = e.turn_id || '(no turn)';
      const last = out[out.length - 1];
      if (last && last.id === id) last.events.push(e);
      else out.push({ id, events: [e] });
    }
    return out;
  });

  // The two capture flags are independent and reported separately: an operator
  // may well want to know what a task touched without exporting prompt text.
  // The authoritative rollups: computed over the session's whole life, so they
  // do not move when the list's range changes. Null until the timeline lands,
  // when the range-scoped row is the only thing available.
  const tot = $derived(timeline?.totals ?? null);

  const hasEvents = $derived(!!timeline && timeline.events.length > 0);
  const promptCaptureOff = $derived(hasEvents && !timeline!.content_available);
  const toolCaptureOff = $derived(hasEvents && !timeline!.tool_details_available);

  // How much of this session's work was done by subagents rather than the main
  // thread. Only model calls carry attribution, so this is a share of those,
  // not of all events — the label says so.
  const agentBreakdown = $derived.by(() => {
    const counts = new Map<string, number>();
    for (const e of timeline?.events ?? []) {
      if (!e.agent) continue;
      counts.set(e.agent, (counts.get(e.agent) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  });
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
            onclick={() => (range = r.value)}
            disabled={loading}>{r.label}</button>
        {/each}
      </div>
      <label class="toggle">
        <input type="checkbox" bind:checked={allProfiles} disabled={loading} />
        All profiles
      </label>
      <button class="btn" onclick={() => load()} disabled={loading}>
        {#if loading}<Spinner />{/if}
        Refresh
      </button>
    </div>
  </header>

  <p class="scope-note">
    {#if scope}
      Sessions stamped <code>{scope}</code>.
    {:else if activeProfile}
      All profiles. Active profile is <code>{activeProfile}</code>.
    {:else}
      All profiles. No profile is currently active.
    {/if}
    Counts and cost below cover the selected range only — a session that started
    earlier shows just the part inside it. Open one for its real totals.
  </p>

  {#if error}
    <p class="error">{error}</p>
  {/if}

  {#if loading && sessions.length === 0}
    <p class="muted"><Spinner /> Loading sessions…</p>
  {:else if !loading && !error && sessions.length === 0}
    <EmptyState
      title="No agent sessions in this window"
      message="Nothing has run in the selected range for this scope. Widen the range, or switch to all profiles." />
  {:else if sessions.length > 0}
    <div class="table-wrap">
      <table class="tbl">
        <thead>
          <tr>
            <th>Session</th>
            <th>Profile</th>
            <th>Doing</th>
            <th class="num">Turns</th>
            <th class="num">Tools</th>
            <th class="num">Failed</th>
            <th class="num">Out</th>
            <th class="num">Cost</th>
            <th class="num">Ran</th>
            <th>Last</th>
          </tr>
        </thead>
        <tbody>
          {#each sessions as s (s.session_id)}
            <tr
              class="trow"
              class:selected={selected?.session_id === s.session_id}
              aria-selected={selected?.session_id === s.session_id}
              onclick={() => open(s)}
              tabindex="0"
              onkeydown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  open(s);
                }
              }}>
              <td><code class="sid">{shortId(s.session_id)}</code></td>
              <td>{s.workspace || '—'}</td>
              <td class="doing">{s.last_tool || '—'}</td>
              <td class="num">{s.turns}</td>
              <td class="num">{s.tool_calls}</td>
              <td class="num" class:bad={s.failures > 0}>{s.failures || ''}</td>
              <td class="num">{compact(s.tokens_out)}</td>
              <td class="num">{usd(s.cost_usd)}</td>
              <td class="num">{duration(s.first_seen, s.last_seen)}</td>
              <td class="muted">{ago(s.last_seen)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}

  {#if selected}
    <section class="detail">
      <header class="detail-head">
        <div>
          <h2>Session <code>{shortId(selected.session_id)}</code></h2>
          <p class="sub">
            {selected.workspace || 'no profile'} ·
            {(selected.models ?? []).join(', ') || 'unknown model'} ·
            {clock(tot?.first_seen ?? selected.first_seen)} →
            {clock(tot?.last_seen ?? selected.last_seen)}
            {#if tot}<span class="muted">· whole session, not just the selected range</span>{/if}
          </p>
        </div>
        <button class="btn" onclick={close}>Close</button>
      </header>

      <dl class="facts">
        <div><dt>Turns</dt><dd>{tot?.turns ?? selected.turns}</dd></div>
        <div><dt>Tool calls</dt><dd>{tot?.tool_calls ?? selected.tool_calls}</dd></div>
        <div><dt>Failures</dt><dd>{tot?.failures ?? selected.failures}</dd></div>
        <div><dt>Cost</dt><dd>{usd(tot?.cost_usd ?? selected.cost_usd)}</dd></div>
        <div><dt>Output tokens</dt><dd>{compact(tot?.tokens_out ?? selected.tokens_out)}</dd></div>
        <!-- input_tokens EXCLUDES cache reads, which dominate by orders of
             magnitude on a long session. Showing it alone next to cost reads
             as a contradiction, so the cached figure is shown beside it. -->
        <div><dt>Input (fresh)</dt><dd>{compact(selected.tokens_in)}</dd></div>
        <div><dt>Input (cached)</dt><dd>{compact(selected.tokens_cache_in)}</dd></div>
        <div><dt>Tools used</dt><dd>{(selected.top_tools ?? []).join(', ') || '—'}</dd></div>
      </dl>

      {#if agentBreakdown.length > 1}
        <!-- Only api_request and assistant_response carry query_source, so this
             is a share of MODEL CALLS, not of every event. Said plainly rather
             than presented as a whole-session breakdown. -->
        <div class="agents">
          <span class="agents-label">model calls by agent</span>
          {#each agentBreakdown as [a, n] (a)}
            <span class="agent-chip" class:sub={isSubagent(a)}>
              {agentLabel(a) || 'main thread'} <span class="muted">{n}</span>
            </span>
          {/each}
        </div>
      {/if}

      {#if timelineLoading}
        <p class="muted"><Spinner /> Loading timeline…</p>
      {:else if timelineError}
        <p class="error">{timelineError}</p>
      {:else if timeline}
        {#if timeline.warning}
          <div class="notice notice-bad">
            <strong>This timeline may be incomplete.</strong>
            <p>{timeline.warning}</p>
            <p class="notice-sub">
              The search succeeded only partially, so events may be missing from
              what is shown below.
            </p>
          </div>
        {/if}
        {#if toolCaptureOff || promptCaptureOff}
          <div class="notice">
            <strong>Some capture is off for these sessions.</strong>
            {#if toolCaptureOff}
              <p>
                <strong>What a task touched</strong> — file paths, commands, MCP
                tool names — needs <code>OTEL_LOG_TOOL_DETAILS</code>. It carries
                no prompt or completion text, so it answers "what did this do"
                without exporting conversations.
              </p>
            {/if}
            {#if promptCaptureOff}
              <p>
                <strong>Prompt and completion text</strong> needs
                <code>OTEL_LOG_USER_PROMPTS</code> and
                <code>OTEL_LOG_ASSISTANT_RESPONSES</code>. The exporter writes
                those attributes as <code>&lt;REDACTED&gt;</code> until then.
              </p>
            {/if}
            <p class="notice-sub">
              Both are privacy decisions, so neither is a control in this panel.
              This view reads every one of those fields already, so whatever you
              enable shows up here on the next session — but note the cluster
              currently has no retention policy and no index-level access
              control, which is the reason the design marked this blocking.
            </p>
          </div>
        {/if}
        {#if timeline.truncated}
          <p class="muted">
            Showing the {timeline.events.length} most recent of {timeline.total_events}
            events — earlier activity is not listed.
          </p>
        {/if}

        <ol class="turns">
          {#each turns as t, i (t.id + i)}
            <li class="turn">
              <div class="turn-head">Turn {i + 1}</div>
              <ul class="events">
                {#each t.events as e, j (e.sequence + '-' + j)}
                  <li class="ev" class:sub={isSubagent(e.agent) || e.kind === 'subagent_completed'}>
                    <span class="seq">{e.sequence}</span>
                    <Badge text={KIND_LABEL[e.kind] ?? e.kind} variant={kindVariant(e)} />
                    <span class="ev-main">
                      {#if e.kind === 'subagent_completed'}
                        <!-- The child's own summary row: the only record that
                             names a subagent and totals its work. -->
                        <strong>{e.agent_type || 'subagent'}</strong>
                        <span class="muted">
                          {e.agent_source ?? ''} · {e.tool_uses ?? 0} tool calls ·
                          {compact(e.total_tokens ?? 0)} tokens
                        </span>
                      {:else}
                        {#if agentLabel(e.agent)}
                          <span class="agent">{agentLabel(e.agent)}</span>
                        {/if}
                        {#if e.tool_name}<strong>{e.tool_name}</strong>{/if}
                        {#if e.model}<span class="muted">{e.model}</span>{/if}
                        {#if e.tool_params?.full_command || e.tool_params?.bash_command}
                          <code class="touched"
                            >{e.tool_params.full_command ?? e.tool_params.bash_command}</code>
                        {:else if e.tool_params?.mcp_tool_name}
                          <code class="touched"
                            >{e.tool_params.mcp_server_name
                              ? e.tool_params.mcp_server_name + '__'
                              : ''}{e.tool_params.mcp_tool_name}</code>
                        {:else if e.tool_input}
                          <code class="touched">{e.tool_input}</code>
                        {/if}
                        {#if e.content}
                          <!-- Clamped: with the content flags on, a session's
                               full prompts and responses would otherwise render
                               in their entirety across up to 1000 events. -->
                          <button
                            type="button"
                            class="content"
                            class:expanded={expanded.has(e.sequence)}
                            title="Click to expand or collapse"
                            onclick={(ev) => {
                              ev.stopPropagation();
                              toggleExpanded(e.sequence);
                            }}>{e.content}</button>
                        {:else if e.redacted}
                          <span class="muted">redacted · {e.content_length} chars</span>
                        {/if}
                      {/if}
                    </span>
                    <span class="ev-meta">
                      {#if e.duration_ms}{ms(e.duration_ms)}{/if}
                      {#if e.result_size}· {bytes(e.result_size)}{/if}
                      {#if e.cost_usd}· {usd(e.cost_usd)}{/if}
                      {#if e.success === false}· <span class="bad">failed</span>{/if}
                    </span>
                  </li>
                {/each}
              </ul>
            </li>
          {/each}
        </ol>
      {/if}
    </section>
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
  .toggle {
    display: inline-flex;
    align-items: center;
    gap: var(--spacing-xs);
    font-size: 11px;
    color: var(--color-text-secondary);
  }
  .scope-note {
    margin: 0;
    font-size: 11px;
    color: var(--color-text-tertiary);
  }
  .error {
    font-size: 11px;
    color: var(--color-status-error-text);
  }
  .muted {
    font-size: 11px;
    color: var(--color-text-tertiary);
  }
  .bad {
    color: var(--color-status-error-text);
  }

  /* The table is the only thing allowed to scroll sideways; the panel itself
     must not. */
  .table-wrap {
    overflow-x: auto;
  }
  .tbl {
    width: 100%;
    border-collapse: collapse;
    font-size: 11px;
  }
  .tbl th {
    text-align: left;
    font-weight: 600;
    color: var(--color-text-tertiary);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    font-size: 9px;
    padding: var(--spacing-xs) var(--spacing-sm);
    border-bottom: 1px solid var(--color-border-secondary);
    white-space: nowrap;
  }
  .tbl td {
    padding: var(--spacing-xs) var(--spacing-sm);
    border-bottom: 1px solid var(--color-border-secondary);
    color: var(--color-text-secondary);
    white-space: nowrap;
  }
  .tbl .num {
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
  /* NOT `.row`: that is a global flex-card rule in styles/components.css,
     imported app-wide, and `display: flex` on a <tr> destroys the table
     layout. Svelte's scoping hashes our own selectors; it does not shield the
     element from a global class of the same name. */
  .trow {
    cursor: pointer;
  }
  .trow:hover td,
  .trow:focus-visible td {
    background: var(--color-muted-bg);
  }
  .trow.selected td {
    background: var(--color-muted-bg);
    color: var(--color-text-primary);
  }
  .sid {
    font-family: var(--font-mono);
  }
  .doing {
    color: var(--color-text-primary);
  }

  .detail {
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm);
    padding: var(--spacing-md);
    display: flex;
    flex-direction: column;
    gap: var(--spacing-sm);
  }
  .detail-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: var(--spacing-md);
  }
  .detail-head h2 {
    margin: 0;
    font-size: 13px;
  }
  .sub {
    margin: 2px 0 0;
    font-size: 11px;
    color: var(--color-text-tertiary);
  }
  .facts {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
    gap: var(--spacing-sm);
    margin: 0;
  }
  .facts dt {
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-tertiary);
  }
  .facts dd {
    margin: 2px 0 0;
    font-size: 12px;
    color: var(--color-text-primary);
    font-variant-numeric: tabular-nums;
  }

  .notice {
    border: 1px solid var(--color-warning-border);
    background: var(--color-warning-bg);
    border-radius: var(--radius-sm);
    padding: var(--spacing-md);
    font-size: 11px;
    color: var(--color-text-secondary);
  }
  .notice p {
    margin: var(--spacing-xs) 0 0;
  }
  .notice-sub {
    color: var(--color-text-tertiary);
  }

  .agents {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: var(--spacing-xs);
    font-size: 11px;
  }
  .agents-label {
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-tertiary);
  }
  .agent-chip {
    border: 1px solid var(--color-border-secondary);
    border-radius: 9999px;
    padding: 1px 8px;
    color: var(--color-text-secondary);
  }
  .agent-chip.sub,
  .agent {
    color: var(--color-text-primary);
  }
  /* Subagent rows are indented so the fan-out inside a turn is visible at a
     glance; attribution exists only on model calls, so this is a hint about
     who issued the call, not a strict tree. */
  .ev.sub {
    padding-left: var(--spacing-md);
    border-left: 2px solid var(--color-border-secondary);
  }
  .agent {
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    flex-shrink: 0;
  }
  .touched {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-primary);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .notice-bad {
    border-color: var(--color-status-error-text);
  }

  .turns {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: var(--spacing-sm);
  }
  .turn-head {
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-tertiary);
    margin-bottom: 2px;
  }
  .events {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .ev {
    display: flex;
    align-items: baseline;
    gap: var(--spacing-xs);
    font-size: 11px;
    color: var(--color-text-secondary);
    padding: 2px 0;
  }
  .seq {
    font-family: var(--font-mono);
    font-size: 9px;
    color: var(--color-text-tertiary);
    min-width: 3.5em;
    text-align: right;
    flex-shrink: 0;
  }
  .ev-main {
    flex: 1;
    min-width: 0;
    display: flex;
    gap: var(--spacing-xs);
    align-items: baseline;
    overflow: hidden;
  }
  /* Captured text can be long; it wraps inside its own cell rather than
     widening the row. */
  /* A button so it is focusable and operable from the keyboard, but styled as
     the text it contains. Clamped to three lines until expanded. */
  .content {
    white-space: pre-wrap;
    word-break: break-word;
    color: var(--color-text-primary);
    background: none;
    border: 0;
    padding: 0;
    margin: 0;
    font: inherit;
    text-align: left;
    cursor: pointer;
    overflow: hidden;
    display: -webkit-box;
    -webkit-line-clamp: 3;
    line-clamp: 3;
    -webkit-box-orient: vertical;
  }
  .content.expanded {
    display: block;
    max-height: 40vh;
    overflow-y: auto;
  }
  .ev-meta {
    font-size: 9px;
    color: var(--color-text-tertiary);
    flex-shrink: 0;
    font-variant-numeric: tabular-nums;
  }
</style>

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
    truncated: boolean;
    content_available: boolean;
  }

  const RANGES = [
    { label: '24h', value: 'now-24h' },
    { label: '7d', value: 'now-7d' },
    { label: '30d', value: 'now-30d' },
  ];

  let sessions = $state<AgentSession[]>([]);
  let loading = $state(false);
  let error = $state('');
  let range = $state('now-24h');
  let allProfiles = $state(false);

  let selected = $state<AgentSession | null>(null);
  let timeline = $state<SessionTimeline | null>(null);
  let timelineLoading = $state(false);
  let timelineError = $state('');

  const activeProfile = $derived(profileState.active?.name ?? '');
  // An empty workspace means unfiltered on the Go side, which is exactly what
  // "All profiles" wants. With no profile selected at all there is nothing to
  // scope to, so the view is global either way.
  const scope = $derived(allProfiles ? '' : activeProfile);

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
  // otherwise the detail pane shows work the new scope excludes.
  $effect(() => {
    const s = scope;
    if (selected && s && selected.workspace !== s) {
      selected = null;
      timeline = null;
    }
  });

  async function open(s: AgentSession) {
    selected = s;
    timeline = null;
    timelineError = '';
    timelineLoading = true;
    const mine = ++timelineEpoch;
    try {
      const api = await getApi();
      if (!api) {
        timelineError = 'Wails bindings unavailable.';
        return;
      }
      const tl = await api.GetAgentSessionTimeline(s.session_id, 1000);
      if (mine !== timelineEpoch) return;
      timeline = tl as unknown as SessionTimeline;
    } catch (e) {
      if (mine !== timelineEpoch) return;
      timelineError = String(e);
    } finally {
      if (mine === timelineEpoch) timelineLoading = false;
    }
  }

  function close() {
    timelineEpoch++;
    timelineLoading = false;
    selected = null;
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
  };

  // Badge variants are limited to those Badge.svelte defines; a failed tool
  // call is the only thing that earns a loud one.
  function kindVariant(e: SessionEvent): string {
    if (e.kind === 'tool_result') return e.success === false ? 'failed' : 'completed';
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

  // Events present but no text anywhere means content logging is off — an
  // actionable state, and distinct from an empty session.
  const captureOff = $derived(
    !!timeline && timeline.events.length > 0 && !timeline.content_available,
  );
</script>

<div class="agentwork">
  <header class="panel-header">
    <h1>Agent Work</h1>
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
  </p>

  {#if error}
    <p class="error">{error}</p>
  {/if}

  {#if !loading && !error && sessions.length === 0}
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
              class="row"
              class:selected={selected?.session_id === s.session_id}
              onclick={() => open(s)}
              tabindex="0"
              role="button"
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
            {selected.models.join(', ') || 'unknown model'} ·
            {clock(selected.first_seen)} → {clock(selected.last_seen)}
          </p>
        </div>
        <button class="btn" onclick={close}>Close</button>
      </header>

      <dl class="facts">
        <div><dt>Turns</dt><dd>{selected.turns}</dd></div>
        <div><dt>Tool calls</dt><dd>{selected.tool_calls}</dd></div>
        <div><dt>Failures</dt><dd>{selected.failures}</dd></div>
        <div><dt>Cost</dt><dd>{usd(selected.cost_usd)}</dd></div>
        <div><dt>Output tokens</dt><dd>{compact(selected.tokens_out)}</dd></div>
        <!-- input_tokens EXCLUDES cache reads, which dominate by orders of
             magnitude on a long session. Showing it alone next to cost reads
             as a contradiction, so the cached figure is shown beside it. -->
        <div><dt>Input (fresh)</dt><dd>{compact(selected.tokens_in)}</dd></div>
        <div><dt>Input (cached)</dt><dd>{compact(selected.tokens_cache_in)}</dd></div>
        <div><dt>Tools used</dt><dd>{selected.top_tools.join(', ') || '—'}</dd></div>
      </dl>

      {#if timelineLoading}
        <p class="muted"><Spinner /> Loading timeline…</p>
      {:else if timelineError}
        <p class="error">{timelineError}</p>
      {:else if timeline}
        {#if captureOff}
          <div class="notice">
            <strong>Prompt and completion text is not being captured.</strong>
            <p>
              Claude Code's exporter writes these attributes as
              <code>&lt;REDACTED&gt;</code> unless content logging is enabled. Message
              sizes, tools, durations, models and cost are shown below regardless.
            </p>
            <p class="notice-sub">
              Enabling it is a privacy decision, so it is deliberately not a
              control in this panel. The flags the shipped CLI reads are
              <code>OTEL_LOG_USER_PROMPTS</code>,
              <code>OTEL_LOG_ASSISTANT_RESPONSES</code> and
              <code>OTEL_LOG_TOOL_CONTENT</code>; captured text lands in the same
              records and appears here automatically.
            </p>
          </div>
        {/if}
        {#if timeline.truncated}
          <p class="muted">
            Showing the first {timeline.events.length} events — this session has more.
          </p>
        {/if}

        <ol class="turns">
          {#each turns as t, i (t.id + i)}
            <li class="turn">
              <div class="turn-head">Turn {i + 1}</div>
              <ul class="events">
                {#each t.events as e (e.sequence)}
                  <li class="ev">
                    <span class="seq">{e.sequence}</span>
                    <Badge text={KIND_LABEL[e.kind] ?? e.kind} variant={kindVariant(e)} />
                    <span class="ev-main">
                      {#if e.tool_name}<strong>{e.tool_name}</strong>{/if}
                      {#if e.model}<span class="muted">{e.model}</span>{/if}
                      {#if e.content}
                        <span class="content">{e.content}</span>
                      {:else if e.redacted}
                        <span class="muted">redacted · {e.content_length} chars</span>
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
    border: 1px solid var(--color-border);
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
    border-right: 1px solid var(--color-border);
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
    border-bottom: 1px solid var(--color-border);
    white-space: nowrap;
  }
  .tbl td {
    padding: var(--spacing-xs) var(--spacing-sm);
    border-bottom: 1px solid var(--color-border);
    color: var(--color-text-secondary);
    white-space: nowrap;
  }
  .tbl .num {
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
  .row {
    cursor: pointer;
  }
  .row:hover td,
  .row:focus-visible td {
    background: var(--color-muted-bg);
  }
  .row.selected td {
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
    border: 1px solid var(--color-border);
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
  .content {
    white-space: pre-wrap;
    word-break: break-word;
    color: var(--color-text-primary);
  }
  .ev-meta {
    font-size: 9px;
    color: var(--color-text-tertiary);
    flex-shrink: 0;
    font-variant-numeric: tabular-nums;
  }
</style>

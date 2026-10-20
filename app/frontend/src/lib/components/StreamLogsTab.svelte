<script lang="ts">
  /**
   * Live-logs tab body for the Stream panel — renders the OpenSearch telemetry
   * tail (time / severity / body / event / service / model / workspace /
   * session / duration) with the sort chip and footer. Display-only: the parent
   * owns fetching, sorting, and the feature flag; this component just renders
   * `displayLogs` and signals the two actions (clear sort, view-more) back up.
   *
   * Severity and event name arrived with ADR-007, when our own services started
   * emitting here. Before that every row came from claude-code and had neither.
   */
  import { formatClock } from '../utils/format';
  import EmptyState from './EmptyState.svelte';

  interface LogEntry {
    time: string;
    body: string;
    severity?: string;
    service?: string;
    event_name?: string;
    outcome?: string;
    run_id?: string;
    session?: string;
    workspace?: string;
    model?: string;
    duration_ms?: number;
    cost_usd?: number;
    input_tokens?: number;
    output_tokens?: number;
    cache_read_tokens?: number;
    cache_creation_tokens?: number;
    query_source?: string;
    tool_name?: string;
    hook_name?: string;
    success?: string;
    error_type?: string;
    num_hooks?: number;
    num_success?: number;
  }

  let {
    opensearchActive,
    logsError,
    logs,
    logsLoading,
    logsSortBy,
    displayLogs,
    onClearSort,
    onViewMore,
  }: {
    opensearchActive: boolean;
    logsError: string | null;
    logs: LogEntry[];
    logsLoading: boolean;
    logsSortBy: 'cost' | 'duration' | 'tokens' | null;
    displayLogs: LogEntry[];
    onClearSort: () => void;
    onViewMore: () => void;
  } = $props();

  // Compact so a row stays one line: 578304 -> 578.3k, 911520901 -> 911.5M.
  function fmtCount(n: number): string {
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
    return `${n}`;
  }

  // Sub-cent calls are the common case, so two decimals would show most rows as
  // $0.00. Three significant-ish digits keeps them distinguishable.
  function fmtCost(usd: number): string {
    return usd < 0.01 ? `$${usd.toFixed(4)}` : `$${usd.toFixed(2)}`;
  }

  // Cache reads DOMINATE the token mix (578k read vs 94 in on a measured
  // request), so folding them into one total would read as a contradiction
  // against the cost. They get their own segment.
  function fmtTokens(l: LogEntry): string {
    const io = [l.input_tokens, l.output_tokens];
    if (!io[0] && !io[1]) return '';
    return `${fmtCount(io[0] ?? 0)}→${fmtCount(io[1] ?? 0)}`;
  }

  function fmtTime(iso: string): string {
    if (!iso) return '';
    try { return formatClock(new Date(iso).getTime(), { seconds: true }); }
    catch { return iso; }
  }

  function fmtDur(ms?: number): string {
    if (!ms) return '';
    return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(2)}s`;
  }

  function sessionShort(id?: string): string {
    if (!id) return '';
    return id.length > 8 ? id.slice(0, 8) : id;
  }
</script>

<section class="tab-body">
  {#if !opensearchActive}
    <EmptyState
      title="OpenSearch not enabled"
      message="Enable the OpenSearch integration to stream live telemetry logs here." />
  {:else if logsError}
    <EmptyState title="Failed to tail logs" message={logsError} />
  {:else if logs.length === 0 && !logsLoading}
    <EmptyState
      title="No logs in the last 24h"
      message="Nothing emitted in this window. Claude Code writes here whenever a
               session runs; our own services write one record per run. Widen the
               workspace filter if you expected rows." />
  {:else}
    {#if logsSortBy}
      <div class="log-sort-chip">
        sorted by <strong>{logsSortBy === 'duration' ? 'latency' : logsSortBy}</strong> desc
        <button class="link-btn" onclick={onClearSort}>clear</button>
      </div>
    {/if}
    <ol class="log-list">
      {#each displayLogs as l, i (l.time + ':' + i)}
        <li class="log-row">
          <span class="log-time">{fmtTime(l.time)}</span>
          <!-- Unset severity renders as a dash, never as INFO. Every claude-code
               document has an empty severityText, and defaulting it would make
               this column claim a level the record does not carry. -->
          <span class="log-sev" class:sev-unset={!l.severity}
                data-sev={(l.severity || '').toLowerCase()}
                title={l.severity ? l.severity : 'no severity on this record'}
          >{l.severity || '—'}</span>
          <span class="log-body">{l.body}</span>
          <!-- Only when it adds something. claude-code's body IS its event name
               (body "claude_code.tool_result", event_name "tool_result"), so
               showing both doubles every one of those rows; an ADR-007 body is a
               human sentence and the event name is genuinely extra. -->
          {#if l.event_name && !l.body?.endsWith(l.event_name)}
            <span class="log-tag tag-event">{l.event_name}</span>
          {/if}
          {#if l.service}<span class="log-tag tag-svc">{l.service}</span>{/if}
          {#if l.model}<span class="log-tag">{l.model}</span>{/if}
          {#if l.workspace}<span class="log-tag tag-ws">{l.workspace}</span>{/if}
          {#if l.session}<span class="log-tag tag-sess">{sessionShort(l.session)}</span>{/if}
          <!-- Per-event detail. Each event type populates a different
               subset, so the row shows what that event actually knows rather
               than a fixed grid of mostly-empty columns. -->
          {#if l.tool_name}<span class="log-tag tag-tool">{l.tool_name}</span>{/if}
          {#if l.hook_name}<span class="log-tag tag-tool">{l.hook_name}</span>{/if}
          {#if l.num_hooks}
            <span class="log-tag" class:tag-bad={l.num_success !== l.num_hooks}
            >{l.num_success ?? 0}/{l.num_hooks} hooks</span>
          {/if}
          {#if l.query_source && l.query_source !== 'main'}
            <span class="log-tag tag-src">{l.query_source}</span>
          {/if}
          {#if fmtTokens(l)}<span class="log-tok">{fmtTokens(l)}</span>{/if}
          {#if l.cache_read_tokens}
            <span class="log-tok log-cache" title="cache read tokens">{fmtCount(l.cache_read_tokens)} cached</span>
          {/if}
          <!-- success is TRI-STATE: a tool_decision has none, so only an
               explicit "false" is a failure. Absent must not read as failed. -->
          {#if l.success === 'false'}
            <span class="log-tag tag-bad">{l.error_type || 'failed'}</span>
          {/if}
          {#if l.cost_usd}<span class="log-cost">{fmtCost(l.cost_usd)}</span>{/if}
          {#if l.duration_ms}<span class="log-dur">{fmtDur(l.duration_ms)}</span>{/if}
        </li>
      {/each}
    </ol>
    <div class="log-footer">
      showing {logs.length} of last 24h, newest first ·
      <button class="link-btn" onclick={onViewMore}>view more in Dashboards ↗</button>
    </div>
  {/if}
</section>

<style>
  .tab-body { flex: 1; min-height: 0; overflow-y: auto; }

  .log-list {
    list-style: none;
    margin: 0;
    padding: 0;
    font-family: var(--font-mono, ui-monospace, monospace);
    font-size: 12px;
  }
  .log-row {
    display: flex;
    gap: 10px;
    align-items: baseline;
    padding: 4px 8px;
    border-bottom: 1px solid var(--border-subtle, var(--color-border-secondary, transparent));
    white-space: nowrap;
    overflow: hidden;
  }
  .log-row:hover { background: var(--bg-hover, var(--color-surface-hover)); }
  .log-time { color: var(--text-faint, var(--color-text-tertiary)); flex-shrink: 0; }
  .log-body { color: var(--text, var(--color-text-primary)); font-weight: 600; }
  .log-tag {
    background: var(--bg-elev, var(--color-bg-tertiary));
    color: var(--text-muted, var(--color-text-secondary));
    padding: 1px 7px;
    border-radius: var(--r-sm, var(--radius-sm));
    font-size: 11px;
  }
  .tag-ws   { color: var(--accent, var(--color-accent)); }
  .tag-sess { font-family: var(--font-mono, ui-monospace, monospace); }
  /* The emitting service, and the ADR-007 event name (this store's log group).
     Both are what make a row attributable now that more than one service
     writes here. */
  .tag-svc   { font-weight: 600; }
  .tag-tool  { font-family: var(--font-mono, ui-monospace, monospace); font-weight: 600; }
  .tag-src   { font-style: italic; }
  .tag-bad   { color: var(--color-status-error-text, crimson); font-weight: 600; }
  .log-tok   { font-variant-numeric: tabular-nums; color: var(--text-muted, var(--color-text-secondary)); font-size: 11px; }
  .log-cache { opacity: 0.75; }
  /* Money earns emphasis: it is the one number on an api_request row that
     cannot be inferred from the others. */
  .log-cost  { font-variant-numeric: tabular-nums; font-weight: 600; }
  .tag-event { font-family: var(--font-mono, ui-monospace, monospace); }

  .log-sev {
    flex: 0 0 auto;
    min-width: 42px;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.02em;
  }
  /* Only levels we actually emit get a colour. An unset level is deliberately
     the faintest thing in the row: it is the absence of information, not a
     state, and it is the majority of rows. */
  .log-sev[data-sev='error'] { color: var(--color-status-error-text, crimson); }
  .log-sev[data-sev='warn']  { color: var(--color-status-warning-text, darkorange); }
  .log-sev[data-sev='info']  { color: var(--text-muted, var(--color-text-secondary)); }
  .sev-unset { color: var(--text-faint, var(--color-text-tertiary)); font-weight: 400; }
  .log-dur  { color: var(--text-faint, var(--color-text-tertiary)); margin-left: auto; }

  .log-sort-chip {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 8px;
    padding: 3px 10px;
    font-size: 11.5px;
    background: var(--bg-elev, var(--color-bg-tertiary));
    border: 1px solid var(--border, var(--color-border-primary));
    border-radius: var(--r-sm, var(--radius-sm));
    color: var(--text-muted, var(--color-text-secondary));
  }
  .log-sort-chip strong { color: var(--text, var(--color-text-primary)); font-weight: 700; }

  .log-footer {
    margin-top: 12px;
    font-size: 11.5px;
    color: var(--text-faint, var(--color-text-tertiary));
    text-align: right;
  }
  .link-btn {
    background: none;
    border: none;
    color: var(--accent, var(--color-accent));
    cursor: pointer;
    font: inherit;
    padding: 0;
  }
  .link-btn:hover { text-decoration: underline; }
</style>

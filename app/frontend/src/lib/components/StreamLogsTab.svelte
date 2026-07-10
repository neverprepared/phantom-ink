<script lang="ts">
  /**
   * Live-logs tab body for the Stream panel — renders the OpenSearch telemetry
   * tail (time / body / model / workspace / session / duration) with the sort
   * chip and footer. Display-only: the parent owns fetching, sorting, and the
   * feature flag; this component just renders `displayLogs` and signals the two
   * actions (clear sort, view-more) back up.
   */
  import { formatClock } from '../utils/format';
  import EmptyState from './EmptyState.svelte';

  interface LogEntry {
    time: string;
    body: string;
    session?: string;
    workspace?: string;
    model?: string;
    duration_ms?: number;
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
      message="Start a Claude Code session or widen your workspace filter." />
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
          <span class="log-body">{l.body}</span>
          {#if l.model}<span class="log-tag">{l.model}</span>{/if}
          {#if l.workspace}<span class="log-tag tag-ws">{l.workspace}</span>{/if}
          {#if l.session}<span class="log-tag tag-sess">{sessionShort(l.session)}</span>{/if}
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

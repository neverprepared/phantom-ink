<script lang="ts">
  /**
   * Audit-log drill-down for a stream envelope — the expandable "history" box
   * shown under a live or attention card. The markup + CSS were byte-identical
   * in both the Live and Attention tabs of StreamPanel; this is the shared
   * source of truth. The parent owns the fetch (into its history map) and the
   * expand/collapse gate; this only renders the fetched sequence.
   */
  import { formatClock } from '../utils/format';

  interface AgentEventEntry {
    seq: number;
    id: string;
    source: string;
    type: string;
    status: string;
    parent_id: string;
    ts: number;
    envelope: Record<string, any>;
  }

  let { entries, loading }: {
    entries: AgentEventEntry[] | undefined;
    loading: boolean;
  } = $props();

  function statusLabel(s: string): string { return s ? s.replace('_', ' ') : ''; }
  function fmtMs(ms: number): string {
    if (!ms) return '';
    try { return formatClock(ms, { seconds: true }); }
    catch { return ''; }
  }
</script>

<div class="history-box">
  {#if loading}
    <div class="history-loading">loading audit log…</div>
  {:else if !entries || entries.length === 0}
    <div class="history-loading">no audit entries yet</div>
  {:else}
    <ol class="history-list">
      {#each entries as ev (ev.seq)}
        <li class="history-row">
          <span class="history-time">{fmtMs(ev.ts)}</span>
          <span class="history-type">{ev.type}</span>
          {#if ev.status}<span class="history-status status-{ev.status}">{statusLabel(ev.status)}</span>{/if}
          {#if ev.envelope?.outcome}
            <span class="history-outcome" class:ok={ev.envelope.outcome.ok} class:bad={!ev.envelope.outcome.ok}>
              {ev.envelope.outcome.actor}{ev.envelope.outcome.ok ? ' · ok' : ' · failed'}
              {ev.envelope.outcome.duration_ms ? ' · ' + ev.envelope.outcome.duration_ms + 'ms' : ''}
            </span>
          {/if}
        </li>
      {/each}
    </ol>
  {/if}
</div>

<style>
  .history-box {
    margin-top: 6px;
    padding: 8px 10px;
    background: var(--bg, var(--color-bg-primary));
    border: 1px solid var(--border, var(--color-border-primary));
    border-radius: var(--r-sm, var(--radius-sm));
  }
  .history-loading {
    font-size: 12px;
    color: var(--text-faint, var(--color-text-tertiary));
    padding: 4px 0;
  }
  .history-list {
    list-style: none;
    margin: 0;
    padding: 0;
    font-family: var(--font-mono, ui-monospace, monospace);
    font-size: 11.5px;
    display: flex;
    flex-direction: column;
    gap: 3px;
  }
  .history-row {
    display: flex;
    gap: 10px;
    align-items: baseline;
    color: var(--text-muted, var(--color-text-secondary));
  }
  .history-time { color: var(--text-faint, var(--color-text-tertiary)); }
  .history-type { color: var(--text, var(--color-text-primary)); font-weight: 600; }
  .history-status {
    padding: 0 6px;
    border-radius: var(--r-sm, var(--radius-sm));
    font-size: 10.5px;
    font-weight: 700;
    text-transform: uppercase;
  }
  .history-status.status-failed       { color: var(--color-status-error-text); background: var(--color-error-bg); }
  .history-status.status-blocked      { color: var(--color-role-purple-text); background: var(--color-role-purple-bg); }
  .history-status.status-needs_action { color: var(--color-status-warning-text); background: var(--color-warning-bg); }
  .history-status.status-active       { color: var(--color-role-blue-text); background: var(--color-info-bg); }
  .history-status.status-upcoming     { color: var(--color-text-tertiary); background: var(--color-muted-bg); }
  .history-status.status-done         { color: var(--color-status-success-text); background: var(--color-success-bg); }
  .history-outcome { margin-left: auto; font-size: 11px; }
  .history-outcome.ok  { color: var(--color-status-success-text); }
  .history-outcome.bad { color: var(--color-status-error-text); }
</style>

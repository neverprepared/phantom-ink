<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { getApi } from '../utils/api';
  import { notifications } from '../notifications.svelte';
  import EmptyState from '../components/EmptyState.svelte';
  import Spinner from '../components/Spinner.svelte';
  import { linearScale, monotonePath, extent } from '../components/chartMath';

  // Slim summary shape from Go-side LiveLoopSummary
  interface LoopRun {
    id: string;
    name: string;
    status: string;
    iteration: number;
    max_iterations: number;
    parent_task_id: string;
    current_child_id?: string;
    metric_history: number[];
    stop_reason?: string;
    error?: string;
    workspace_profile?: string;
    created_at: number;
    updated_at: number;
  }

  let loops = $state<LoopRun[]>([]);
  let loading = $state(true);
  let pollHandle: ReturnType<typeof setInterval> | null = null;
  let statusFilter = $state<string>('');
  let cancellingIds = $state<Set<string>>(new Set());

  const POLL_MS = 5000;

  // ---------------------------------------------------------------------------
  // Status styling
  // ---------------------------------------------------------------------------

  const TERMINAL = new Set(['converged', 'thrashing', 'max_iter', 'stopped_by_condition', 'failed', 'cancelled']);
  const ACTIVE = new Set(['running', 'pending']);

  function statusClass(s: string): string {
    if (s === 'converged') return 'pill pill-good';
    if (ACTIVE.has(s)) return 'pill pill-running';
    if (TERMINAL.has(s)) return 'pill pill-bad';
    return 'pill';
  }

  function statusLabel(s: string): string {
    return s.toUpperCase().replace('_', ' ');
  }

  // ---------------------------------------------------------------------------
  // Polling
  // ---------------------------------------------------------------------------

  async function refresh() {
    try {
      const api = await getApi();
      const result = await api.ListLiveLoops(statusFilter || '');
      loops = (result ?? []) as LoopRun[];
    } catch (err) {
      // Brainbox unreachable or down — clear the list rather than show stale
      console.warn('LoopRunsPanel: failed to fetch loops', err);
      loops = [];
    } finally {
      loading = false;
    }
  }

  onMount(() => {
    refresh();
    pollHandle = setInterval(refresh, POLL_MS);
  });

  onDestroy(() => {
    if (pollHandle !== null) {
      clearInterval(pollHandle);
      pollHandle = null;
    }
  });

  $effect(() => {
    // Re-fetch when the filter changes
    statusFilter;
    refresh();
  });

  // ---------------------------------------------------------------------------
  // Mini sparkline
  // ---------------------------------------------------------------------------

  const SPARK_W = 120;
  const SPARK_H = 30;

  function sparklinePath(history: number[]): string {
    if (!history || history.length < 2) return '';
    const xs = history.map((_, i) => i);
    const ys = history;
    const [yMin, yMax] = extent(ys, (v) => v);
    // Add a tiny padding so a flat line still renders mid-band
    const yLo = yMin === yMax ? yMin - 0.5 : yMin;
    const yHi = yMin === yMax ? yMax + 0.5 : yMax;
    const xScale = linearScale(0, history.length - 1, 0, SPARK_W);
    const yScale = linearScale(yLo, yHi, SPARK_H - 2, 2);
    const pts = history.map((v, i) => ({ x: xScale(i), y: yScale(v) }));
    return monotonePath(pts);
  }

  function lastMetric(history: number[]): string {
    if (!history || history.length === 0) return '—';
    return history[history.length - 1].toString();
  }

  function trendDirection(history: number[]): 'down' | 'up' | 'flat' | '' {
    if (!history || history.length < 2) return '';
    const a = history[history.length - 2];
    const b = history[history.length - 1];
    if (b < a) return 'down';
    if (b > a) return 'up';
    return 'flat';
  }

  // ---------------------------------------------------------------------------
  // Cancel
  // ---------------------------------------------------------------------------

  async function cancelLoop(loop: LoopRun) {
    if (!ACTIVE.has(loop.status)) return;
    const reason = window.prompt(`Cancel loop ${loop.name} (iter ${loop.iteration})?\n\nOptional reason:`, '');
    if (reason === null) return;
    cancellingIds.add(loop.id);
    cancellingIds = cancellingIds;
    try {
      const api = await getApi();
      await api.CancelLiveLoop(loop.id, reason || 'operator cancelled');
      notifications.success(`Loop ${loop.name} cancelled`);
      await refresh();
    } catch (err) {
      notifications.error(`Cancel failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      cancellingIds.delete(loop.id);
      cancellingIds = cancellingIds;
    }
  }

  // ---------------------------------------------------------------------------
  // Time formatting
  // ---------------------------------------------------------------------------

  function fmtAge(updatedAt: number): string {
    const ageMs = Date.now() - updatedAt;
    if (ageMs < 60_000) return 'just now';
    if (ageMs < 3_600_000) return `${Math.floor(ageMs / 60_000)}m ago`;
    if (ageMs < 86_400_000) return `${Math.floor(ageMs / 3_600_000)}h ago`;
    return `${Math.floor(ageMs / 86_400_000)}d ago`;
  }
</script>

<div class="panel">
  <header class="panel-header">
    <div class="header-left">
      <h2>Loop Runs</h2>
      <span class="subtitle">live view of loop-runner instances driven by brainbox</span>
    </div>
    <div class="header-right">
      <label class="filter">
        Status
        <select bind:value={statusFilter}>
          <option value="">all</option>
          <option value="running">running</option>
          <option value="converged">converged</option>
          <option value="thrashing">thrashing</option>
          <option value="max_iter">max iter</option>
          <option value="stopped_by_condition">stopped</option>
          <option value="failed">failed</option>
          <option value="cancelled">cancelled</option>
        </select>
      </label>
    </div>
  </header>

  {#if loading}
    <div class="loading"><Spinner /></div>
  {:else if loops.length === 0}
    <EmptyState
      title="No loops {statusFilter ? `in '${statusFilter}'` : 'yet'}"
      message="Loops fire when an opted-in PR opens, syncs, or someone comments /loop on a PR. Manual trigger: POST /api/loops/start."
    />
  {:else}
    <div class="loop-list">
      {#each loops as loop (loop.id)}
        <div class="loop-card">
          <div class="card-head">
            <div class="name-block">
              <span class="loop-name">{loop.name}</span>
              <code class="loop-id">{loop.id.slice(0, 8)}</code>
            </div>
            <span class={statusClass(loop.status)}>{statusLabel(loop.status)}</span>
          </div>

          <div class="card-body">
            <div class="metric-block">
              <div class="iter-line">
                <span class="label">iter</span>
                <span class="value">{loop.iteration}<span class="dim">/ {loop.max_iterations}</span></span>
              </div>
              <div class="metric-line">
                <span class="label">metric</span>
                <span class="value">{lastMetric(loop.metric_history)}</span>
                {#if trendDirection(loop.metric_history)}
                  <span class="trend trend-{trendDirection(loop.metric_history)}">
                    {trendDirection(loop.metric_history) === 'down' ? '↓' : trendDirection(loop.metric_history) === 'up' ? '↑' : '→'}
                  </span>
                {/if}
              </div>
              {#if loop.stop_reason}
                <div class="stop-reason">stop: {loop.stop_reason}</div>
              {/if}
              {#if loop.error}
                <div class="error">{loop.error}</div>
              {/if}
            </div>

            <div class="chart-block">
              {#if loop.metric_history && loop.metric_history.length >= 2}
                <svg viewBox="0 0 {SPARK_W} {SPARK_H}" width={SPARK_W} height={SPARK_H} class="sparkline">
                  <path d={sparklinePath(loop.metric_history)} fill="none" stroke="currentColor" stroke-width="1.5" />
                </svg>
              {:else}
                <div class="no-spark">—</div>
              {/if}
            </div>

            <div class="actions">
              {#if ACTIVE.has(loop.status)}
                <button
                  class="btn-cancel"
                  onclick={() => cancelLoop(loop)}
                  disabled={cancellingIds.has(loop.id)}
                >
                  {cancellingIds.has(loop.id) ? 'cancelling…' : 'Cancel'}
                </button>
              {/if}
            </div>
          </div>

          <div class="card-foot">
            <span class="updated">{fmtAge(loop.updated_at)}</span>
            {#if loop.workspace_profile}
              <span class="profile">{loop.workspace_profile}</span>
            {/if}
          </div>
        </div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .panel {
    padding: 16px 20px;
    display: flex;
    flex-direction: column;
    gap: 12px;
    height: 100%;
    overflow: hidden;
  }

  .panel-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }

  .header-left h2 {
    margin: 0 0 2px;
    font-size: 18px;
    font-weight: 600;
  }
  .subtitle {
    font-size: 12px;
    color: var(--color-text-muted);
  }

  .filter {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    color: var(--color-text-muted);
  }
  .filter select {
    background: var(--color-surface-2, #1a1a1a);
    border: 1px solid var(--color-border, #333);
    color: var(--color-text, #ddd);
    padding: 3px 6px;
    border-radius: 4px;
    font-size: 12px;
  }

  .loading {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .loop-list {
    flex: 1;
    overflow: auto;
    display: flex;
    flex-direction: column;
    gap: 10px;
    padding-right: 4px;
  }

  .loop-card {
    background: var(--color-surface-1, #181818);
    border: 1px solid var(--color-border, #2a2a2a);
    border-radius: 6px;
    padding: 10px 12px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .card-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }

  .name-block {
    display: flex;
    align-items: baseline;
    gap: 8px;
  }
  .loop-name {
    font-weight: 600;
  }
  .loop-id {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--color-text-muted);
  }

  .pill {
    display: inline-block;
    padding: 2px 8px;
    font-size: 10px;
    font-weight: 600;
    border-radius: 10px;
    letter-spacing: 0.5px;
    background: #333;
    color: #ddd;
  }
  .pill-running {
    background: #1f3a5f;
    color: #88c1ff;
  }
  .pill-good {
    background: #1f4a2a;
    color: #95e0a8;
  }
  .pill-bad {
    background: #4a2a2a;
    color: #ff9a9a;
  }

  .card-body {
    display: grid;
    grid-template-columns: minmax(150px, 1fr) auto auto;
    align-items: center;
    gap: 16px;
  }

  .metric-block {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .iter-line,
  .metric-line {
    display: flex;
    align-items: baseline;
    gap: 6px;
    font-size: 13px;
  }
  .label {
    color: var(--color-text-muted);
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  .value {
    font-variant-numeric: tabular-nums;
    font-weight: 500;
  }
  .dim {
    color: var(--color-text-muted);
    margin-left: 2px;
  }
  .trend {
    font-size: 12px;
  }
  .trend-down {
    color: #95e0a8;
  }
  .trend-up {
    color: #ff9a9a;
  }
  .trend-flat {
    color: var(--color-text-muted);
  }
  .stop-reason {
    font-size: 11px;
    color: #ffb070;
    margin-top: 2px;
  }
  .error {
    font-size: 11px;
    color: #ff9a9a;
    margin-top: 2px;
    word-break: break-word;
  }

  .chart-block {
    color: var(--color-accent, #88c1ff);
  }
  .sparkline {
    display: block;
  }
  .no-spark {
    color: var(--color-text-muted);
    font-size: 12px;
  }

  .actions {
    display: flex;
    gap: 6px;
  }
  .btn-cancel {
    background: transparent;
    border: 1px solid #4a2a2a;
    color: #ff9a9a;
    font-size: 11px;
    padding: 4px 10px;
    border-radius: 4px;
    cursor: pointer;
    transition: background 0.15s;
  }
  .btn-cancel:hover:not(:disabled) {
    background: #2a1a1a;
  }
  .btn-cancel:disabled {
    opacity: 0.5;
    cursor: wait;
  }

  .card-foot {
    display: flex;
    gap: 12px;
    font-size: 11px;
    color: var(--color-text-muted);
  }
  .profile {
    font-family: var(--font-mono);
  }
</style>

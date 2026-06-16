<script lang="ts">
  import { dashboardDataStore, featureFlags, profileState, streamFocus } from '../stores.svelte';
  import type { OpenSearchMetricConfig } from './types';

  let { config }: { config: OpenSearchMetricConfig } = $props();

  const SORT_BY: Record<OpenSearchMetricConfig['metric'], 'cost' | 'duration' | 'tokens'> = {
    'cost-today':      'cost',
    'tokens-today':    'tokens',
    'api-requests-1h': 'duration',
    'avg-latency-1h':  'duration',
  };

  function drill() {
    streamFocus.focus({ tab: 'logs', sortBy: SORT_BY[config.metric] });
  }

  let data = $derived(dashboardDataStore.value);
  let enabled = $derived(featureFlags.isEnabled('opensearch'));
  let overview = $derived(data?.opensearch ?? null);
  let workspaceMissing = $derived(
    overview != null
      && (profileState.active?.name ?? '') !== ''
      && !overview.matched_workspace
  );

  const DEFAULTS: Record<OpenSearchMetricConfig['metric'], string> = {
    'cost-today':      'cost today',
    'tokens-today':    'tokens today',
    'api-requests-1h': 'api requests · 1h',
    'avg-latency-1h':  'avg latency · 1h',
  };

  let label = $derived(config.label?.trim() || DEFAULTS[config.metric]);

  let value = $derived.by(() => {
    if (!overview) return '—';
    switch (config.metric) {
      case 'cost-today':      return `$${(overview.cost_today_usd ?? 0).toFixed(4)}`;
      case 'tokens-today':    return (overview.tokens_today ?? 0).toLocaleString();
      case 'api-requests-1h': return (overview.api_requests_1h ?? 0).toLocaleString();
      case 'avg-latency-1h': {
        const v = overview.avg_latency_ms_1h ?? 0;
        if (!v) return '—';
        return v < 1000 ? `${Math.round(v)} ms` : `${(v / 1000).toFixed(2)} s`;
      }
    }
  });

  let colorClass = $derived(config.color && config.color !== 'default' ? config.color : '');
</script>

<div class="drag-strip widget-drag-handle" aria-hidden="true"></div>

{#if enabled && overview && !workspaceMissing}
  <button class="stat-card clickable" onclick={drill} title="Open Stream → Logs">
    <span class="stat-label">» {label}</span>
    <span class="stat-value {colorClass}">{value}</span>
  </button>
{:else}
  <div class="stat-card">
    <span class="stat-label">» {label}</span>
    {#if !enabled}
      <span class="stat-value muted">—</span>
      <span class="footnote">opensearch disabled</span>
    {:else if !overview}
      <span class="stat-value muted">…</span>
    {:else}
      <span class="stat-value muted">—</span>
      <span class="footnote">no telemetry for <code>{profileState.active?.name}</code></span>
    {/if}
  </div>
{/if}

<style>
  .drag-strip {
    height: 6px;
    cursor: grab;
    flex-shrink: 0;
  }

  .stat-card {
    width: 100%;
    height: calc(100% - 6px);
    display: flex;
    flex-direction: column;
    gap: var(--spacing-sm);
    padding: var(--spacing-lg) var(--spacing-xl);
    color: inherit;
    background: transparent;
    border: none;
    text-align: left;
  }
  .stat-card.clickable {
    cursor: pointer;
    transition: background 0.12s;
  }
  .stat-card.clickable:hover {
    background: var(--color-surface-hover, rgba(0,0,0,0.04));
  }

  .stat-label {
    font-family: var(--font-mono);
    font-size: 10px;
    letter-spacing: 0.08em;
    color: var(--color-text-tertiary);
    white-space: nowrap;
  }

  .stat-value {
    font-family: var(--font-mono);
    font-size: 28px;
    font-weight: 700;
    line-height: 1;
    color: var(--color-text-primary);
    font-variant-numeric: tabular-nums;
  }
  .stat-value.green  { color: var(--color-success); }
  .stat-value.blue   { color: var(--color-info); }
  .stat-value.red    { color: var(--color-error); }
  .stat-value.orange { color: var(--color-warning); }
  .stat-value.muted  { color: var(--color-text-muted); }

  .footnote {
    font-size: 11px;
    color: var(--color-text-tertiary);
  }
  .footnote code {
    font-family: var(--font-mono);
    background: var(--color-bg-tertiary);
    padding: 1px 4px;
    border-radius: 3px;
  }
</style>

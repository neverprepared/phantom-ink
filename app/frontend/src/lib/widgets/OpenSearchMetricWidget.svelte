<script lang="ts">
  import { dashboardDataStore, featureFlags, profileState } from '../stores.svelte';
  import type { OpenSearchMetricConfig } from './types';

  let { config }: { config: OpenSearchMetricConfig } = $props();

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

<div class="stat-card">
  <span class="stat-label">» {label}</span>

  {#if !enabled}
    <span class="stat-value muted">—</span>
    <span class="footnote">opensearch disabled</span>
  {:else if !overview}
    <span class="stat-value muted">…</span>
  {:else if workspaceMissing}
    <span class="stat-value muted">—</span>
    <span class="footnote">no telemetry for <code>{profileState.active?.name}</code></span>
  {:else}
    <span class="stat-value {colorClass}">{value}</span>
  {/if}
</div>

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

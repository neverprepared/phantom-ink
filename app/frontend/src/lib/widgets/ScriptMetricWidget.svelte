<script lang="ts">
  import { onMount } from 'svelte';
  import { getApi } from '../utils/api';
  import type { ScriptMetricConfig } from './types';

  let { config }: { config: ScriptMetricConfig } = $props();

  let count   = $state<number | null>(null);
  let error   = $state(false);
  let loading = $state(true);

  async function fetchCount() {
    const a = await getApi();
    if (!a) return;
    try {
      count = await a.RunMetricScript(config.command);
      error = false;
    } catch {
      error = true;
    } finally {
      loading = false;
    }
  }

  onMount(() => {
    void fetchCount();
    const ms = (config.interval ?? 60) * 1000;
    const interval = setInterval(fetchCount, ms);
    return () => clearInterval(interval);
  });
</script>

<div class="drag-strip widget-drag-handle" aria-hidden="true"></div>

<div class="stat-card">
  <span class="stat-label">» {config.label}</span>
  {#if loading}
    <span class="stat-value muted">…</span>
  {:else if error}
    <span class="stat-value err">!</span>
  {:else}
    <span class="stat-value" style={config.color ? `color: ${config.color}` : ''}>{count}</span>
  {/if}
  <span class="stat-sub">script · {config.interval ?? 60}s</span>
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
    gap: var(--spacing-xs);
    padding: var(--spacing-lg) var(--spacing-xl);
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
    font-size: 32px;
    font-weight: 700;
    line-height: 1;
    color: var(--color-text-primary);
  }
  .stat-value.muted { color: var(--color-text-muted); }
  .stat-value.err   { color: var(--color-error); }

  .stat-sub {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-muted);
  }
</style>

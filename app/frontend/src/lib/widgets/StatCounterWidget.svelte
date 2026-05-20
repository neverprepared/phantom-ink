<script lang="ts">
  import { dashboardDataStore, currentPanel } from '../stores.svelte';
  import type { StatCounterConfig } from './types';

  let { config }: { config: StatCounterConfig } = $props();

  let data = $derived(dashboardDataStore.value);

  let value = $derived.by(() => {
    if (!data) return 0;
    switch (config.dataKey) {
      case 'activeSessions':  return data.activeSessions;
      case 'runningTasks':    return data.runningTasks;
      case 'failedTasks':     return data.failedTasks;
      case 'scheduledFires':  return data.fires.length;
      case 'actionItems':     return data.actionItems.length;
      default:                return 0;
    }
  });

  let colorClass = $derived.by(() => {
    if (config.color === 'default') return '';
    return config.color;
  });

  function navigate() {
    if (config.navTarget) currentPanel.value = config.navTarget;
  }
</script>

<!-- invisible drag handle strip at top -->
<div class="drag-strip widget-drag-handle" aria-hidden="true"></div>

{#if config.navTarget}
  <button class="stat-card" onclick={navigate} class:urgent={config.color === 'orange' && value > 0}>
    <span class="stat-label">» {config.label}</span>
    <span class="stat-value {colorClass}" class:muted={value === 0 && (config.color === 'green' || config.color === 'muted')}>
      {value}
    </span>
  </button>
{:else}
  <div class="stat-card" class:urgent={config.color === 'orange' && value > 0}>
    <span class="stat-label">» {config.label}</span>
    <span class="stat-value {colorClass}" class:muted={value === 0}>
      {value}
    </span>
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
    background: transparent;
    border: none;
    cursor: pointer;
    text-align: left;
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
    font-size: 32px;
    font-weight: 700;
    line-height: 1;
    color: var(--color-text-primary);
  }
  .stat-value.green  { color: var(--color-success); }
  .stat-value.blue   { color: var(--color-info); }
  .stat-value.red    { color: var(--color-error); }
  .stat-value.orange { color: var(--color-warning); }
  .stat-value.muted  { color: var(--color-text-muted); }
</style>

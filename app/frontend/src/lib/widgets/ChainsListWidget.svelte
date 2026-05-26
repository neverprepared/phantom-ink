<script lang="ts">
  import { dashboardDataStore, currentPanel } from '../stores.svelte';

  let data = $derived(dashboardDataStore.value);
  let fires = $derived(data?.fires ?? []);

  function formatNextFire(iso: string): string {
    const d = new Date(iso);
    const today = new Date();
    const tomorrow = new Date(today);
    tomorrow.setDate(today.getDate() + 1);
    const time = d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false });
    if (d.toDateString() === today.toDateString()) return time;
    if (d.toDateString() === tomorrow.toDateString()) return `tomorrow ${time}`;
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) + ` ${time}`;
  }
</script>

<div class="widget">
  <div class="widget-header widget-drag-handle">
    <span class="widget-title">» SCHEDULED CHAINS</span>
    <span class="badge">{fires.length}</span>
  </div>
  <div class="widget-body">
    {#if fires.length === 0}
      <div class="empty">no upcoming fires</div>
    {:else}
      {#each fires as fire (fire.schedule_id)}
        <button class="fire-row" onclick={() => currentPanel.value = 'chains'}>
          <span class="fire-time">{formatNextFire(fire.next_fire_at)}</span>
          <span class="fire-name">{fire.chain_name || fire.chain_id?.slice(0, 12)}</span>
          <span class="fire-cron">{fire.cron_expr}</span>
        </button>
      {/each}
    {/if}
  </div>
</div>

<style>
  .widget { display: flex; flex-direction: column; height: 100%; }

  .widget-header {
    display: flex;
    align-items: center;
    gap: var(--spacing-sm);
    padding: var(--spacing-sm) var(--spacing-md);
    border-bottom: 1px solid var(--color-border-primary);
    cursor: grab;
    flex-shrink: 0;
  }

  .widget-title {
    font-family: var(--font-mono);
    font-size: 11px;
    letter-spacing: 0.08em;
    color: var(--color-text-secondary);
    flex: 1;
  }

  .badge {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-muted);
    background: var(--color-surface-subtle);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm);
    padding: 1px 6px;
  }

  .widget-body {
    flex: 1;
    overflow-y: auto;
    padding: var(--spacing-xs) 0;
  }

  .empty {
    font-size: 12px;
    color: var(--color-text-tertiary);
    padding: var(--spacing-xl) var(--spacing-md);
  }

  .fire-row {
    width: 100%;
    display: grid;
    grid-template-columns: 72px 1fr auto;
    align-items: center;
    gap: var(--spacing-sm);
    padding: var(--spacing-xs) var(--spacing-md);
    background: transparent;
    border: none;
    cursor: pointer;
    text-align: left;
    color: inherit;
    font-family: inherit;
    transition: background 100ms ease;
  }
  .fire-row:hover { background: var(--color-surface-hover); }

  .fire-time {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--color-accent);
    font-weight: 500;
    white-space: nowrap;
  }

  .fire-name {
    font-size: 12px;
    color: var(--color-text-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .fire-cron {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-muted);
    white-space: nowrap;
  }
</style>

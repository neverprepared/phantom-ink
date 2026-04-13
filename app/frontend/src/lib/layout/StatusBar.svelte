<script lang="ts">
  import { connectionState } from '../stores.svelte';
  import { brainboxEvents } from '../events.svelte';

  let lastEvent = $derived(brainboxEvents.last);
  let lastEventText = $derived(connectionState.lastEventText);
  let eventCount = $derived(brainboxEvents.log.length);
</script>

<div class="statusbar">
  <div class="status-left">
    {#if lastEventText}
      <span class="event-text">{lastEventText}</span>
    {:else}
      <span class="event-text muted">waiting for events...</span>
    {/if}
  </div>
  <div class="status-right">
    <span class="stat">{eventCount} events</span>
  </div>
</div>

<style>
  .statusbar {
    height: 26px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 var(--spacing-lg);
    background: var(--color-bg-primary);
    border-top: 1px solid var(--color-border-primary);
    flex-shrink: 0;
    font-size: 11px;
    color: var(--color-text-tertiary);
  }

  .status-left {
    display: flex;
    align-items: center;
    gap: var(--spacing-md);
    min-width: 0;
    flex: 1;
  }

  .event-text {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--color-text-secondary);
  }

  .event-text.muted {
    color: var(--color-text-muted);
  }

  .status-right {
    display: flex;
    align-items: center;
    gap: var(--spacing-lg);
    flex-shrink: 0;
  }

  .stat {
    color: var(--color-text-tertiary);
    font-variant-numeric: tabular-nums;
  }
</style>

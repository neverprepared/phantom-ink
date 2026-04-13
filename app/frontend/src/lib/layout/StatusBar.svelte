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
    height: 28px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 16px;
    background: var(--color-bg-primary);
    border-top: 1px solid var(--color-border-primary);
    flex-shrink: 0;
    font-size: 11px;
    color: var(--color-text-tertiary);
  }

  .status-left {
    display: flex;
    align-items: center;
    gap: 12px;
    min-width: 0;
    flex: 1;
  }

  .event-text {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 11px;
  }

  .event-text.muted {
    color: var(--color-text-tertiary);
    opacity: 0.5;
  }

  .status-right {
    display: flex;
    align-items: center;
    gap: 16px;
    flex-shrink: 0;
  }

  .stat {
    color: var(--color-text-tertiary);
  }
</style>

<script lang="ts">
  import { brainboxEvents } from '../events.svelte';

  let log = $derived(brainboxEvents.log);

  function formatTime(d: Date): string {
    return d.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }

  let timestamps = $state<WeakMap<object, Date>>(new WeakMap());

  $effect(() => {
    const ev = brainboxEvents.last;
    if (ev && !timestamps.has(ev)) {
      timestamps.set(ev, new Date());
    }
  });
</script>

<div class="panel">
  <header>
    <h1><span class="accent">events</span></h1>
    <span class="count">{log.length} events</span>
  </header>

  {#if log.length === 0}
    <div class="empty">
      <p>No events yet. Waiting for brainbox SSE stream...</p>
    </div>
  {:else}
    <div class="feed">
      {#each log as event, i (i)}
        <div class="event-row">
          <span class="event-raw selectable">{event.raw}</span>
        </div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .panel { padding-bottom: 24px; height: 100%; display: flex; flex-direction: column; }
  header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 16px;
    flex-shrink: 0;
  }
  h1 { font-size: 22px; font-weight: 600; }
  .accent { color: var(--color-accent); }
  .count { font-size: 12px; color: var(--color-text-tertiary); }

  .empty {
    color: var(--color-text-tertiary);
    font-size: 13px;
    padding: 40px 0;
    text-align: center;
  }

  .feed {
    flex: 1;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 2px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 11px;
  }

  .event-row {
    padding: 4px 8px;
    border-radius: var(--radius-sm);
    transition: background 0.1s;
  }
  .event-row:hover { background: rgba(255, 255, 255, 0.03); }

  .event-raw {
    color: var(--color-text-secondary);
    word-break: break-all;
  }
</style>

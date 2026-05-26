<script lang="ts">
  import { brainboxEvents } from '../events.svelte';

  let logEl: HTMLElement;
  let autoscroll = $state(true);

  let log = $derived(brainboxEvents.log);

  $effect(() => {
    if (autoscroll && log.length && logEl) {
      logEl.scrollTop = logEl.scrollHeight;
    }
  });

  function formatTime(d: Date): string {
    return d.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }

  // Events don't carry a timestamp so we record receipt time on render
  let timestamps = new WeakMap<object, Date>();
  function getTimestamp(event: object): Date {
    if (!timestamps.has(event)) timestamps.set(event, new Date());
    return timestamps.get(event)!;
  }
</script>

<div class="panel">
  <header>
    <h1><span class="accent">event log</span></h1>
    <div class="toolbar">
      <label class="toggle">
        <input type="checkbox" bind:checked={autoscroll} />
        <span>autoscroll</span>
      </label>
      <span class="count">{log.length} events</span>
    </div>
  </header>

  <div class="log" bind:this={logEl}>
    {#if log.length === 0}
      <div class="empty">no events yet — waiting for brainbox activity</div>
    {:else}
      {#each [...log].reverse() as event (event)}
        <div class="row">
          <span class="ts">{formatTime(getTimestamp(event))}</span>
          <span class="type">{event.type ?? '—'}</span>
          <span class="raw">{event.raw}</span>
        </div>
      {/each}
    {/if}
  </div>
</div>

<style>
  .panel {
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow: hidden;
    padding: var(--panel-padding);
    padding-bottom: 0;
  }

  header {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    margin-bottom: 16px;
    flex-shrink: 0;
  }

  h1 { font-size: 22px; font-weight: 600; }
  .accent { color: var(--color-accent); }

  .toolbar {
    display: flex;
    align-items: center;
    gap: 16px;
  }

  .toggle {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    color: var(--color-text-tertiary);
    cursor: pointer;
    user-select: none;
  }

  .count {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--color-text-muted);
  }

  .log {
    flex: 1;
    overflow-y: auto;
    font-family: var(--font-mono);
    font-size: 11px;
    display: flex;
    flex-direction: column;
    gap: 1px;
    padding-bottom: var(--panel-padding);
  }

  .empty {
    color: var(--color-text-muted);
    padding: 24px 0;
  }

  .row {
    display: grid;
    grid-template-columns: 64px 120px 1fr;
    gap: 12px;
    padding: 3px 6px;
    border-radius: var(--radius-sm);
    min-width: 0;
  }

  .row:hover { background: var(--color-bg-secondary); }

  .ts {
    color: var(--color-text-muted);
    flex-shrink: 0;
  }

  .type {
    color: var(--color-accent);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .raw {
    color: var(--color-text-secondary);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
</style>

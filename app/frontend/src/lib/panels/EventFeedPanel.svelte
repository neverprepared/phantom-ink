<script lang="ts">
  import { brainboxEvents } from '../events.svelte';

  interface StructuredEvent {
    action: string;
    [key: string]: unknown;
  }

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

  function parseStructured(raw: string): StructuredEvent | null {
    try {
      const obj = JSON.parse(raw);
      if (obj && typeof obj.action === 'string') return obj as StructuredEvent;
    } catch {}
    return null;
  }

  function actionCategory(action: string): 'session' | 'task' | 'repo' | 'other' {
    if (action.startsWith('session.')) return 'session';
    if (action.startsWith('task.')) return 'task';
    if (action.startsWith('repo.')) return 'repo';
    return 'other';
  }

  function eventDetails(ev: StructuredEvent): string {
    const { action, ...rest } = ev;
    return Object.entries(rest)
      .filter(([, v]) => v !== '' && v !== null && v !== undefined)
      .map(([k, v]) => `${k}=${v}`)
      .join('  ');
  }

  type FilterType = 'all' | 'session' | 'task' | 'repo';
  let activeFilter = $state<FilterType>('all');

  let filteredLog = $derived(
    activeFilter === 'all'
      ? log
      : log.filter(ev => {
          const s = parseStructured(ev.raw);
          return s ? actionCategory(s.action) === activeFilter : false;
        })
  );
</script>

<div class="panel">
  <header>
    <h1><span class="accent">events</span></h1>
    <span class="count">{log.length} events</span>
  </header>

  <div class="filters">
    <button class="filter-btn" class:active={activeFilter === 'all'} onclick={() => activeFilter = 'all'}>all</button>
    <button class="filter-btn session" class:active={activeFilter === 'session'} onclick={() => activeFilter = 'session'}>session</button>
    <button class="filter-btn task" class:active={activeFilter === 'task'} onclick={() => activeFilter = 'task'}>task</button>
    <button class="filter-btn repo" class:active={activeFilter === 'repo'} onclick={() => activeFilter = 'repo'}>repo</button>
  </div>

  {#if filteredLog.length === 0}
    <div class="empty">
      <p>{log.length === 0 ? 'No events yet. Waiting for brainbox SSE stream...' : 'No events match the current filter.'}</p>
    </div>
  {:else}
    <div class="feed">
      {#each filteredLog as event, i (i)}
        {@const structured = parseStructured(event.raw)}
        {@const ts = timestamps.get(event)}
        <div class="event-row">
          {#if structured}
            {@const cat = actionCategory(structured.action)}
            <span class="badge {cat}">{structured.action}</span>
            {#if ts}<span class="ts">{formatTime(ts)}</span>{/if}
            {@const details = eventDetails(structured)}
            {#if details}<span class="details selectable">{details}</span>{/if}
          {:else}
            {#if ts}<span class="ts">{formatTime(ts)}</span>{/if}
            <span class="event-raw selectable">{event.raw}</span>
          {/if}
        </div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .panel { padding: var(--panel-padding); height: 100%; display: flex; flex-direction: column; box-sizing: border-box; }
  header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 8px;
    flex-shrink: 0;
  }
  h1 { font-size: 22px; font-weight: 600; }
  .accent { color: var(--color-accent); }
  .count { font-size: 12px; color: var(--color-text-tertiary); }

  .filters {
    display: flex;
    gap: 6px;
    margin-bottom: 12px;
    flex-shrink: 0;
  }

  .filter-btn {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 11px;
    padding: 3px 10px;
    border-radius: var(--radius-sm, 4px);
    border: 1px solid rgba(255,255,255,0.1);
    background: transparent;
    color: var(--color-text-secondary);
    cursor: pointer;
    transition: background 0.1s, color 0.1s;
  }
  .filter-btn:hover { background: rgba(255,255,255,0.05); }
  .filter-btn.active { background: rgba(255,255,255,0.08); color: var(--color-text-primary, #fff); }

  .filter-btn.session.active { border-color: #3b82f6; color: #3b82f6; }
  .filter-btn.task.active    { border-color: #22c55e; color: #22c55e; }
  .filter-btn.repo.active    { border-color: #a855f7; color: #a855f7; }

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
    display: flex;
    align-items: baseline;
    gap: 8px;
    padding: 4px 8px;
    border-radius: var(--radius-sm, 4px);
    transition: background 0.1s;
  }
  .event-row:hover { background: rgba(255, 255, 255, 0.03); }

  .badge {
    flex-shrink: 0;
    padding: 1px 7px;
    border-radius: 3px;
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.03em;
  }
  .badge.session { background: rgba(59,130,246,0.2); color: #3b82f6; }
  .badge.task    { background: rgba(34,197,94,0.2);  color: #22c55e; }
  .badge.repo    { background: rgba(168,85,247,0.2); color: #a855f7; }
  .badge.other   { background: rgba(255,255,255,0.08); color: var(--color-text-secondary); }

  .ts {
    flex-shrink: 0;
    color: var(--color-text-tertiary);
    font-size: 10px;
  }

  .details {
    color: var(--color-text-secondary);
    word-break: break-all;
    flex: 1;
  }

  .event-raw {
    color: var(--color-text-secondary);
    word-break: break-all;
    flex: 1;
  }
</style>

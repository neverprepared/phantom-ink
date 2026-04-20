<script lang="ts">
  import { getApi } from '../utils/api';
  import { onMount } from 'svelte';
  import { brainboxEvents } from '../events.svelte';

  // --- Tab state ---
  type Tab = 'logs' | 'events';
  let activeTab = $state<Tab>('logs');

  // --- Logs ---
  let logLines = $state<string[]>([]);
  let loadingLogs = $state(false);
  let logCount = $state(200);

  async function refreshLogs() {
    loadingLogs = true;
    const a = await getApi();
    if (!a) { loadingLogs = false; return; }
    try {
      const entries = (await a.GetAPILogs(logCount)) ?? [];
      logLines = entries.map((e: any) => e.line);
    } catch { logLines = []; }
    finally { loadingLogs = false; }
  }

  onMount(() => { refreshLogs(); });

  // --- Events (SSE stream) ---
  interface StructuredEvent {
    action: string;
    [key: string]: unknown;
  }

  let log = $derived(brainboxEvents.log);
  let timestamps = $state<WeakMap<object, Date>>(new WeakMap());

  $effect(() => {
    const ev = brainboxEvents.last;
    if (ev && !timestamps.has(ev)) {
      timestamps.set(ev, new Date());
    }
  });

  function formatTime(d: Date): string {
    return d.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }

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

  // --- Log line parsing ---
  function logLevel(line: string): 'error' | 'warn' | 'info' | 'debug' | 'other' {
    if (line.includes('"level":"error"') || line.includes(' ERROR ')) return 'error';
    if (line.includes('"level":"warning"') || line.includes(' WARNING ') || line.includes(' WARN ')) return 'warn';
    if (line.includes('"level":"info"') || line.includes(' INFO ')) return 'info';
    if (line.includes('"level":"debug"') || line.includes(' DEBUG ')) return 'debug';
    return 'other';
  }
</script>

<div class="panel">
  <header>
    <h1><span class="accent">observability</span></h1>
    <div class="tabs">
      <button class="tab-btn" class:active={activeTab === 'logs'} onclick={() => activeTab = 'logs'}>
        <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>
        logs
      </button>
      <button class="tab-btn" class:active={activeTab === 'events'} onclick={() => activeTab = 'events'}>
        <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>
        events
        {#if log.length > 0}<span class="event-count">{log.length}</span>{/if}
      </button>
    </div>
  </header>

  {#if activeTab === 'logs'}
    <div class="logs-toolbar">
      <select class="log-count-select" bind:value={logCount} onchange={refreshLogs}>
        <option value={100}>last 100</option>
        <option value={200}>last 200</option>
        <option value={500}>last 500</option>
        <option value={1000}>last 1000</option>
      </select>
      <button class="btn-refresh" onclick={refreshLogs} title="Refresh logs" aria-label="Refresh logs">
        <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/></svg>
      </button>
    </div>
    {#if loadingLogs}
      <div class="empty">loading logs...</div>
    {:else if logLines.length === 0}
      <div class="empty">no log entries found</div>
    {:else}
      <div class="log-feed">
        {#each logLines as line, i (i)}
          {@const level = logLevel(line)}
          <div class="log-line {level}">{line}</div>
        {/each}
      </div>
    {/if}
  {:else}
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
  {/if}
</div>

<style>
  .panel { padding: var(--panel-padding); height: 100%; display: flex; flex-direction: column; box-sizing: border-box; }
  header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 12px;
    flex-shrink: 0;
  }
  h1 { font-size: 22px; font-weight: 600; }
  .accent { color: var(--color-accent); }

  /* Tabs */
  .tabs { display: flex; gap: 4px; }

  .tab-btn {
    display: flex;
    align-items: center;
    gap: 5px;
    font-size: 12px;
    padding: 5px 12px;
    border-radius: var(--radius-md);
    border: 1px solid var(--color-border-secondary);
    background: transparent;
    color: var(--color-text-tertiary);
    transition: all 0.15s;
  }
  .tab-btn:hover { color: var(--color-text-secondary); background: var(--color-surface-hover); }
  .tab-btn.active {
    background: var(--color-surface-active);
    color: var(--color-text-primary);
    border-color: var(--color-text-tertiary);
  }

  .event-count {
    font-size: 10px;
    background: var(--color-accent);
    color: var(--color-bg-primary);
    padding: 0 5px;
    border-radius: 9999px;
    font-weight: 600;
    min-width: 16px;
    text-align: center;
  }

  /* Logs toolbar */
  .logs-toolbar {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 10px;
    flex-shrink: 0;
  }

  .log-count-select {
    font-size: 12px;
    padding: 3px 8px;
    border-radius: var(--radius-sm);
    border: 1px solid var(--color-border-secondary);
    background: var(--color-bg-tertiary);
    color: var(--color-text-secondary);
  }

  .btn-refresh {
    background: none;
    border: 1px solid var(--color-border-secondary);
    color: var(--color-text-tertiary);
    padding: 4px;
    border-radius: var(--radius-sm);
    display: flex;
    transition: all 0.15s;
  }
  .btn-refresh:hover { color: var(--color-text-primary); background: var(--color-surface-hover); }

  /* Log feed */
  .log-feed {
    flex: 1;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 11px;
    line-height: 1.5;
  }

  .log-line {
    padding: 1px 8px;
    white-space: pre-wrap;
    word-break: break-all;
    color: var(--color-text-secondary);
  }
  .log-line:hover { background: var(--color-surface-hover); }
  .log-line.error { color: var(--color-error); }
  .log-line.warn { color: var(--color-accent); }
  .log-line.debug { color: var(--color-text-tertiary); }

  /* Events */
  .filters {
    display: flex;
    gap: 6px;
    margin-bottom: 12px;
    flex-shrink: 0;
  }

  .filter-btn {
    font-family: var(--font-mono);
    font-size: 11px;
    padding: 3px 10px;
    border-radius: var(--radius-sm);
    border: 1px solid var(--color-border-secondary);
    background: transparent;
    color: var(--color-text-secondary);
    cursor: pointer;
    transition: background 0.1s, color 0.1s;
  }
  .filter-btn:hover { background: var(--color-surface-hover); }
  .filter-btn.active { background: var(--color-surface-active); color: var(--color-text-primary); }

  .filter-btn.session.active { border-color: var(--color-role-developer); color: var(--color-role-developer); background: rgba(59, 130, 246, 0.1); }
  .filter-btn.task.active    { border-color: var(--color-role-worker);    color: var(--color-role-worker);    background: rgba(34, 197, 94, 0.1); }
  .filter-btn.repo.active    { border-color: var(--color-role-researcher); color: var(--color-role-researcher); background: rgba(168, 85, 247, 0.1); }

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
  .event-row:hover { background: var(--color-surface-hover); }

  .badge {
    flex-shrink: 0;
    padding: 1px 7px;
    border-radius: var(--radius-sm);
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.03em;
  }
  .badge.session { background: rgba(59, 130, 246, 0.15); color: var(--color-role-developer); }
  .badge.task    { background: rgba(34, 197, 94, 0.15);  color: var(--color-role-worker); }
  .badge.repo    { background: rgba(168, 85, 247, 0.15); color: var(--color-role-researcher); }
  .badge.other   { background: var(--color-surface-active); color: var(--color-text-secondary); }

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

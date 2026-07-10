<script lang="ts">
  import { brainboxEvents } from '../events.svelte';
  import { formatClock } from '../utils/format';
  import EmptyState from '../components/EmptyState.svelte';
  import { getApi } from '../utils/api';

  let logEl: HTMLElement;
  let autoscroll = $state(true);

  let log = $derived(brainboxEvents.log);

  // ── History search (daemon-served: OpenSearch when the sink is on,
  //    Postgres otherwise). Any active query switches the list from the
  //    live rolling tail to search results; clear/Esc returns to live. ──
  interface SearchItem {
    seq: number;
    id: string;
    type: string;
    status: string;
    ts: number;
    envelope: Record<string, any> | null;
  }

  let query = $state('');
  let typeFilter = $state('');
  let statusFilter = $state('');
  let searching = $state(false);
  let searchResults = $state<SearchItem[] | null>(null);
  let searchBackend = $state('');
  let searchTotal = $state<number | null>(null);
  let searchSeq = 0;
  let debounceHandle: ReturnType<typeof setTimeout> | null = null;

  const searchActive = $derived(
    query.trim() !== '' || typeFilter.trim() !== '' || statusFilter !== ''
  );

  function scheduleSearch() {
    if (debounceHandle !== null) clearTimeout(debounceHandle);
    if (!searchActive) {
      searchResults = null;
      return;
    }
    debounceHandle = setTimeout(() => void runSearch(), 300);
  }

  async function runSearch() {
    const api = await getApi();
    if (!api || !searchActive) return;
    const mySeq = ++searchSeq;
    searching = true;
    try {
      const res = await api.SearchAgentEvents({
        q: query.trim(),
        type: typeFilter.trim(),
        workspace: '',
        status: statusFilter,
        source: '',
        since_ms: 0,
        until_ms: 0,
        limit: 200,
      } as any);
      if (mySeq !== searchSeq) return; // stale response
      searchResults = ((res?.items ?? []) as any[]).map((i) => ({
        ...i,
        envelope: i.envelope ?? null,
      }));
      searchBackend = res?.backend ?? '';
      searchTotal = res?.total ?? null;
    } catch {
      if (mySeq === searchSeq) {
        searchResults = [];
        searchBackend = 'error';
      }
    } finally {
      if (mySeq === searchSeq) searching = false;
    }
  }

  function clearSearch() {
    query = '';
    typeFilter = '';
    statusFilter = '';
    searchResults = null;
  }

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') clearSearch();
  }

  $effect(() => {
    if (autoscroll && !searchActive && log.length && logEl) {
      logEl.scrollTop = logEl.scrollHeight;
    }
  });

  function formatTime(d: Date): string {
    return formatClock(d.getTime(), { seconds: true });
  }

  function formatTs(ms: number): string {
    const d = new Date(ms);
    const today = new Date().toDateString() === d.toDateString();
    return today
      ? formatTime(d)
      : d.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' ' + formatTime(d);
  }

  // Live events don't carry a timestamp so we record receipt time on render
  let timestamps = new WeakMap<object, Date>();
  function getTimestamp(event: object): Date {
    if (!timestamps.has(event)) timestamps.set(event, new Date());
    return timestamps.get(event)!;
  }
</script>

<div class="panel">
  <header class="panel-header">
    <h1 class="page-title">event log</h1>
    <div class="toolbar">
      <input
        class="search-input"
        type="text"
        placeholder="search history…"
        bind:value={query}
        oninput={scheduleSearch}
        onkeydown={handleKeydown}
      />
      <input
        class="search-input narrow"
        type="text"
        placeholder="type prefix"
        bind:value={typeFilter}
        oninput={scheduleSearch}
        onkeydown={handleKeydown}
      />
      <select class="search-select" bind:value={statusFilter} onchange={scheduleSearch}>
        <option value="">any status</option>
        {#each ['upcoming', 'active', 'done', 'failed', 'blocked', 'needs_action'] as s (s)}
          <option value={s}>{s}</option>
        {/each}
      </select>
      {#if searchActive}
        <button class="clear-btn" onclick={clearSearch}>✕ live</button>
      {:else}
        <label class="toggle">
          <input type="checkbox" bind:checked={autoscroll} />
          <span>autoscroll</span>
        </label>
        <span class="count">{log.length} events</span>
      {/if}
    </div>
  </header>

  {#if searchActive}
    <div class="search-meta">
      {#if searching}
        searching…
      {:else if searchResults !== null}
        {searchResults.length}{searchTotal !== null && searchTotal > searchResults.length ? ` of ${searchTotal}` : ''} results
        · backend: {searchBackend}
      {/if}
    </div>
    <div class="log">
      {#if searchResults === null || (searching && searchResults.length === 0)}
        <div class="empty">searching…</div>
      {:else if searchResults.length === 0}
        <EmptyState title="No matches"
          message={searchBackend === 'error' ? 'Search failed — is the daemon reachable?' : 'No events match this query.'} />
      {:else}
        {#each searchResults as item (item.seq)}
          <div class="row">
            <span class="ts" title={new Date(item.ts).toISOString()}>{formatTs(item.ts)}</span>
            <span class="type">{item.type ?? '—'}</span>
            <span class="raw" title={JSON.stringify(item.envelope)}>
              {item.envelope?.title ?? item.id}{item.status ? ` · ${item.status}` : ''}
              {#if item.envelope?.workspace}<span class="ws">[{item.envelope.workspace}]</span>{/if}
            </span>
          </div>
        {/each}
      {/if}
    </div>
  {:else}
    <div class="log" bind:this={logEl}>
      {#if log.length === 0}
        <EmptyState title="No events yet" message="Waiting for brainbox activity." />
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
  {/if}
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

  .toolbar {
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .search-input {
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm);
    padding: 4px 8px;
    font-size: 11px;
    font-family: var(--font-mono);
    color: var(--color-text-primary);
    width: 200px;
  }
  .search-input.narrow { width: 110px; }
  .search-input:focus { outline: none; border-color: var(--color-accent); }

  .search-select {
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm);
    padding: 4px 6px;
    font-size: 11px;
    font-family: var(--font-mono);
    color: var(--color-text-secondary);
    cursor: pointer;
  }

  .clear-btn {
    background: none;
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm);
    padding: 3px 8px;
    font-size: 10px;
    font-family: var(--font-mono);
    color: var(--color-text-muted);
    cursor: pointer;
  }
  .clear-btn:hover { border-color: var(--color-accent); color: var(--color-accent); }

  .search-meta {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-tertiary);
    padding: 4px 0;
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
    grid-template-columns: 110px 140px 1fr;
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

  .ws {
    color: var(--color-text-tertiary);
    margin-left: 6px;
  }
</style>

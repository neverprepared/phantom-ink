<script lang="ts">
  import { brainboxEvents } from '../events.svelte';
  import { formatClock } from '../utils/format';
  import EmptyState from '../components/EmptyState.svelte';
  import SessionSummaryCard from '../components/SessionSummaryCard.svelte';
  import EnvelopePayload from '../components/EnvelopePayload.svelte';
  import { getApi } from '../utils/api';
  import { profileState } from '../stores.svelte';
  import type { BrainboxEvent } from '../events.svelte';

  // Every row expands. session.summary gets the rich card (narrative + facts +
  // evidence); everything else gets the envelope payload -- `metadata` is where
  // the decidable data lives (llm.completion carries backend/model/tokens/cost/
  // attempts there) and the rows themselves only show title + status.
  let expandedSeq = $state<number | null>(null);
  function toggleExpand(seq: number) { expandedSeq = expandedSeq === seq ? null : seq; }

  // The live tail has no seq to key on -- the ring holds stable objects, so
  // identity is the key.
  let expandedEvent = $state<BrainboxEvent | null>(null);
  function toggleExpandLive(event: BrainboxEvent) {
    expandedEvent = expandedEvent === event ? null : event;
  }

  // A live frame is the brainbox SSE line verbatim (app.go emits it as
  // `brainbox:event`), so an agent envelope arrives wrapped as
  // {"event":"agent.event","data":{...envelope}}. Unwrap that so the payload
  // view gets the envelope and not the transport frame; other frames
  // (hub events, webhook triggers) are shown as they came.
  function livePayload(event: BrainboxEvent): unknown {
    const data = event.data as Record<string, any> | undefined;
    if (data && data.event === 'agent.event' && data.data) return data.data;
    return data ?? event.raw;
  }

  function rowKeydown(e: KeyboardEvent, activate: () => void) {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      activate();
    }
  }

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
        // Scope to the active profile. This was hard-coded to '' , which
        // returned other profiles' envelopes into a profile-scoped view with
        // only a dim [ws] suffix to tell them apart.
        workspace: profileState.active?.name ?? '',
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
    // An expanded live row pauses autoscroll: without this the next event
    // scrolls the payload you just opened off the screen.
    if (autoscroll && !expandedEvent && !searchActive && log.length && logEl) {
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

  // Oldest-first for display: the ring is newest-first, and the log pane
  // scrolls to the bottom. $derived so this reverses once per new event
  // instead of on every unrelated re-render (typing in the search box).
  const orderedLog = $derived([...log].reverse());
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
          {@const isSummary = item.type === 'session.summary' && item.envelope}
          {@const expanded = expandedSeq === item.seq}
          <div class="row clickable" onclick={() => toggleExpand(item.seq)}
            onkeydown={(e) => rowKeydown(e, () => toggleExpand(item.seq))}
            role="button" tabindex={0}>
            <span class="ts" title={new Date(item.ts).toISOString()}>{formatTs(item.ts)}</span>
            <span class="type">{item.type ?? '—'}</span>
            <span class="raw">
              <span class="disclosure">{expanded ? '▾' : '▸'}</span>
              {item.envelope?.title ?? item.id}{item.status ? ` · ${item.status}` : ''}
              {#if item.envelope?.workspace}<span class="ws">[{item.envelope.workspace}]</span>{/if}
            </span>
          </div>
          {#if expanded}
            <div class="summary-wrap">
              {#if isSummary}
                <SessionSummaryCard envelope={item.envelope} />
              {:else}
                <EnvelopePayload payload={item.envelope ?? item} />
              {/if}
            </div>
          {/if}
        {/each}
      {/if}
    </div>
  {:else}
    <div class="log" bind:this={logEl}>
      {#if log.length === 0}
        <EmptyState title="No events yet" message="Waiting for brainbox activity." />
      {:else}
        {#each orderedLog as event (event)}
          {@const expanded = expandedEvent === event}
          <div class="row clickable" onclick={() => toggleExpandLive(event)}
            onkeydown={(e) => rowKeydown(e, () => toggleExpandLive(event))}
            role="button" tabindex={0}>
            <span class="ts">{formatTime(new Date(event.receivedAt))}</span>
            <span class="type">{event.type ?? '—'}</span>
            <span class="raw">
              <span class="disclosure">{expanded ? '▾' : '▸'}</span>
              {event.raw}
            </span>
          </div>
          {#if expanded}
            <div class="summary-wrap"><EnvelopePayload payload={livePayload(event)} /></div>
          {/if}
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
    padding: 24px 0;
  }

  .row {
    display: grid;
    grid-template-columns: 110px 140px 1fr;
    padding: 3px 6px;
    border-radius: var(--radius-sm);
    min-width: 0;
  }

  .row:hover { background: var(--color-bg-secondary); }

  .ts {
    color: var(--color-text-muted);
    flex-shrink: 0;
  }

  .row.clickable { cursor: pointer; }
  .row.clickable:hover { background: var(--color-surface-hover); }
  .disclosure { color: var(--color-accent); margin-right: 4px; }
  .summary-wrap { padding: 4px 0 8px 0; }
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

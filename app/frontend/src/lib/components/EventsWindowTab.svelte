<!--
  The Events panel's "all" tab: every envelope the bus recorded in a time
  window, newest first, unfiltered.

  Display only. Every decision worth testing — window maths, the ts cursor,
  de-dupe, staleness — lives in stores/eventsWindow.svelte.ts, because this
  file cannot be tested at all (vite.config.ts sets environment: 'node' and
  @testing-library is not a dependency).

  The store is imported directly rather than taken as props, matching how
  StreamPanel uses streamLive; StreamLogsTab's prop interface exists because it
  has a second consumer, and this does not.
-->
<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { eventsWindow, WINDOW_RANGES, PAGE_LIMIT } from '../stores/eventsWindow.svelte';
  import { formatClock } from '../utils/format';
  import { onAgentEvent } from '../utils/wailsTopic';
  import EmptyState from './EmptyState.svelte';
  import EnvelopePayload from './EnvelopePayload.svelte';
  import SessionSummaryCard from './SessionSummaryCard.svelte';

  let expandedSeq = $state<number | null>(null);
  let off: (() => void) | null = null;

  function toggleExpand(seq: number) {
    expandedSeq = expandedSeq === seq ? null : seq;
  }

  function rowKeydown(e: KeyboardEvent, activate: () => void) {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      activate();
    }
  }

  function formatTs(ms: number): string {
    const d = new Date(ms);
    const today = new Date().toDateString() === d.toDateString();
    return today
      ? formatClock(ms, { seconds: true })
      : d.toLocaleDateString([], { month: 'short', day: 'numeric' }) +
          ' ' +
          formatClock(ms, { seconds: true });
  }

  // No debounce. EventLogPanel debounces because it reacts to a text input;
  // four preset buttons produce one event each, so a delay here would only add
  // latency. The store's epoch guard already discards a superseded response.
  //
  // No tab-gating either: this component only exists while `tab === 'window'`,
  // so mount IS activation (unlike AgentWorkPanel, whose tabs share one
  // mounted component and must therefore check which is active).
  onMount(() => {
    void eventsWindow.load();
    off = onAgentEvent(() => eventsWindow.noteLiveEvent());
  });

  onDestroy(() => off?.());

  // The window is frozen at load time, so state what was actually queried —
  // the "1h" label drifts from the truth once the tab has been open a while.
  const windowLabel = $derived(
    eventsWindow.startMs
      ? `${formatClock(eventsWindow.startMs)}–${formatClock(eventsWindow.endMs)}`
      : '',
  );

  // `total` is a real count only from OpenSearch, and even there the value
  // saturates at 10,000 unless track_total_hits is set — hence "≥". When
  // Postgres answered there is no count at all, so a full page is the only
  // evidence that more exists. Inventing a number here would turn an honest
  // footer into a confident lie.
  const countLabel = $derived.by(() => {
    const shown = eventsWindow.items.length;
    if (eventsWindow.total !== null && eventsWindow.total > shown) {
      return `${shown} of ≥${eventsWindow.total}`;
    }
    if (eventsWindow.total === null && shown >= PAGE_LIMIT) {
      return `${shown} (more may exist)`;
    }
    return `${shown}`;
  });
</script>

<section class="tab-body">
  <div class="bar">
    <!-- Structure and CSS copied from AgentWorkPanel's range control; .seg /
         .seg-btn are panel-local there, not in a shared stylesheet. -->
    <div class="seg" role="group" aria-label="Time window">
      {#each WINDOW_RANGES as r (r.ms)}
        <button
          class="seg-btn"
          class:active={eventsWindow.rangeMs === r.ms}
          disabled={eventsWindow.loading}
          onclick={() => void eventsWindow.setRange(r.ms)}>{r.label}</button>
      {/each}
    </div>

    {#if eventsWindow.newSinceLoad > 0}
      <button class="pill" onclick={() => void eventsWindow.load()}>
        {eventsWindow.newSinceLoad} new · refresh
      </button>
    {:else}
      <button class="pill pill-quiet" disabled={eventsWindow.loading}
        onclick={() => void eventsWindow.load()}>refresh</button>
    {/if}

    <span class="meta">
      {countLabel} events
      {#if windowLabel}· {windowLabel}{/if}
      {#if eventsWindow.backend}· backend: {eventsWindow.backend}{/if}
    </span>
  </div>

  <div class="log">
    {#if eventsWindow.error}
      <EmptyState title="Could not load events" message={eventsWindow.error} />
    {:else if eventsWindow.loading && eventsWindow.items.length === 0}
      <div class="empty">loading…</div>
    {:else if eventsWindow.items.length === 0}
      <EmptyState
        title="No events in this window"
        message="Nothing was recorded on the bus between {windowLabel}. Try a wider window." />
    {:else}
      {#each eventsWindow.items as item (item.seq)}
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

      <div class="foot">
        {#if eventsWindow.hasMore}
          <button class="pill" disabled={eventsWindow.loadingMore}
            onclick={() => void eventsWindow.loadOlder()}>
            {eventsWindow.loadingMore ? 'loading…' : 'load older'}
          </button>
        {:else if eventsWindow.pageCapReached}
          <span class="meta">page limit reached — narrow the window to see more</span>
        {:else}
          <span class="meta">complete back to {formatClock(eventsWindow.startMs)}</span>
        {/if}
      </div>
    {/if}
  </div>
</section>

<style>
  .tab-body {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .bar {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }

  .meta {
    color: var(--color-text-muted);
    font-size: 11px;
    font-family: var(--font-mono);
  }

  .pill {
    background: transparent;
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm);
    color: var(--color-text-secondary);
    font-size: 11px;
    padding: 3px 10px;
    cursor: pointer;
  }
  .pill:hover:not(:disabled) { background: var(--color-surface-hover); }
  .pill-quiet { color: var(--color-text-muted); }

  /* .seg / .seg-btn mirror AgentWorkPanel's range control. */
  .seg {
    display: inline-flex;
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm);
    overflow: hidden;
  }
  .seg-btn {
    background: transparent;
    border: 0;
    color: var(--color-text-secondary);
    font-size: 11px;
    padding: 3px 10px;
    cursor: pointer;
  }
  .seg-btn:not(:last-child) {
    border-right: 1px solid var(--color-border-primary);
  }
  .seg-btn.active {
    background: var(--color-muted-bg);
    color: var(--color-text-primary);
  }

  /* Row markup and CSS mirror EventLogPanel's search results — dense, because
     a 24h window is a firehose. The attention list's card rows are ~90px tall
     and shaped for AgentStateItem, which is the wrong shape here. */
  .log {
    flex: 1;
    overflow-y: auto;
    font-family: var(--font-mono);
    font-size: 11px;
    display: flex;
    flex-direction: column;
  }

  .row {
    display: grid;
    grid-template-columns: 110px 160px 1fr;
    padding: 3px 6px;
    border-radius: var(--radius-sm);
    min-width: 0;
  }

  .row:hover { background: var(--color-bg-secondary); }
  .row.clickable { cursor: pointer; }
  .row.clickable:hover { background: var(--color-surface-hover); }

  .ts { color: var(--color-text-muted); flex-shrink: 0; }
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

  .ws { color: var(--color-text-tertiary); margin-left: 6px; }

  .empty { color: var(--color-text-muted); padding: 8px 6px; }

  .foot {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 6px 4px;
  }
</style>

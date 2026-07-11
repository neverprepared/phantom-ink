<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { timeAgo } from '../utils/format';
  import { getApi, openInBrowser } from '../utils/api';
  import { featureFlags, profileState, currentPanel, attentionStore, streamFocus } from '../stores.svelte';
  import { streamLive, STATUS_OPTIONS } from '../stores/streamLive.svelte';
  import { notifications } from '../notifications.svelte';
  import Spinner from '../components/Spinner.svelte';
  import EmptyState from '../components/EmptyState.svelte';
  import ContextMenu from '../components/ContextMenu.svelte';
  import StreamLogsTab from '../components/StreamLogsTab.svelte';
  import EnvelopeHistory from '../components/EnvelopeHistory.svelte';

  interface CtxMenuItem {
    label: string;
    onClick: () => void;
    danger?: boolean;
    disabled?: boolean;
  }

  // ── Types ──────────────────────────────────────────────────────────────────

  interface AttentionItem {
    id: string;
    source: 'task' | 'loop' | 'entry' | 'hub' | 'bus';
    source_id: string;
    status: 'failed' | 'blocked' | 'needs_action' | '';
    title: string;
    subtitle: string;
    reason: string;
    workspace: string;
    time: number;
    url?: string;
    actions: string[]; // "retry" | "open" | "respond" | "dismiss"
    user_reply?: string;
    session_name?: string;
    runner_name?: string;
  }

  interface OpenTarget {
    panel: string;
    ref: string;
  }

  interface LogEntry {
    time: string;
    body: string;
    session?: string;
    workspace?: string;
    model?: string;
    duration_ms?: number;
  }

  type Tab = 'live' | 'attention' | 'logs';

  // ── State ──────────────────────────────────────────────────────────────────

  let tab = $state<Tab>('attention');
  // Attention list is sourced from the singleton attentionStore so the sidebar
  // badge, dashboard ActionItems widget, and this panel always agree without
  // duplicating polls.
  let attention = $derived<AttentionItem[]>(attentionStore.items as unknown as AttentionItem[]);
  let attentionLoading = $derived(!attentionStore.loaded);
  let logs = $state<LogEntry[]>([]);
  // Optional sort key for the logs tab. Set via cross-panel streamFocus signal
  // (e.g. the OpenSearch cost widget jumps in with sortBy='cost').
  let logsSortBy = $state<'cost' | 'duration' | 'tokens' | null>(null);
  let displayLogs = $derived.by(() => {
    if (!logsSortBy) return logs;
    const key = logsSortBy === 'duration' ? 'duration_ms' : logsSortBy;
    return [...logs].sort((a, b) => ((b as any)[key] ?? 0) - ((a as any)[key] ?? 0));
  });
  let logsLoading = $state(false);
  let attentionError = $state<string | null>(null);
  let logsError = $state<string | null>(null);

  // Inline respond expander state
  let respondingId = $state<string | null>(null);
  let respondText = $state('');

  // Outbox pending indicator, polled separately from list refresh.
  let outboxPending = $state(0);

  let livePoll: number | undefined;
  let logsPoll: number | undefined;
  let outboxPoll: number | undefined;
  let sseCleanup: Array<() => void> = [];

  const LIVE_POLL_MS = 5_000;     // SSE drives instant updates; this is a safety net
  const LOGS_POLL_MS = 3_000;
  const OUTBOX_POLL_MS = 5_000;
  const LOGS_LIMIT = 1000;

  let opensearchActive = $derived(featureFlags.isActive('opensearch'));
  let activeProfile    = $derived(profileState.active);
  let workspaceFilter  = $derived(activeProfile?.name ?? '');

  // ── Right-click context menu ───────────────────────────────────────────────
  let ctxOpen = $state(false);
  let ctxX = $state(0);
  let ctxY = $state(0);
  let ctxItems = $state<CtxMenuItem[]>([]);

  function openAttentionMenu(item: AttentionItem, evt: MouseEvent): void {
    evt.preventDefault();
    ctxX = evt.clientX;
    ctxY = evt.clientY;
    const supports = (a: string) => item.actions.includes(a);
    const items: CtxMenuItem[] = [
      {
        label: 'Open source',
        onClick: () => openTarget(item),
        disabled: !supports('open') && !item.url,
      },
      { label: 'Retry', onClick: () => retry(item), disabled: !supports('retry') },
      { label: 'Respond…', onClick: () => openRespond(item), disabled: !supports('respond') },
      { label: 'Copy ID', onClick: () => copyToClipboard(item.id, 'id') },
      { label: 'Copy reason', onClick: () => copyToClipboard(item.reason || item.subtitle || item.title, 'reason'), disabled: !item.reason && !item.subtitle && !item.title },
      { label: 'Dismiss', onClick: () => dismiss(item), danger: true, disabled: !supports('dismiss') },
    ];
    ctxItems = items;
    ctxOpen = true;
  }

  async function copyToClipboard(text: string, kind: string): Promise<void> {
    try {
      await navigator.clipboard.writeText(text);
      notifications.success(`copied ${kind}`);
    } catch {
      notifications.error('clipboard unavailable');
    }
  }

  $effect(() => {
    if (tab === 'logs' && !opensearchActive) tab = 'attention';
  });

  // If the user clicks an OpenSearch widget while Stream is already mounted,
  // the streamFocus store fires after onMount — pick it up reactively.
  $effect(() => {
    const f = streamFocus.value;
    if (!f) return;
    if (f.tab) tab = f.tab;
    if (f.sortBy) logsSortBy = f.sortBy;
    streamFocus.consume();
  });

  // ── Loaders ────────────────────────────────────────────────────────────────

  async function refreshAttention() {
    await attentionStore.refresh();
  }

  async function refreshOutbox() {
    const a = await getApi();
    if (!a) return;
    try {
      outboxPending = (await a.OutboxPending()) ?? 0;
    } catch {
      // outbox poll is best-effort; ignore transient errors
    }
  }

  async function refreshLogs() {
    if (!opensearchActive) return;
    const a = await getApi();
    if (!a) return;
    logsLoading = true;
    try {
      logs = ((await a.TailLogs(workspaceFilter, LOGS_LIMIT)) ?? []) as LogEntry[];
      logsError = null;
    } catch (err: any) {
      logsError = `${err?.message ?? err}`;
    } finally {
      logsLoading = false;
    }
  }

  onMount(() => {
    // Consume any cross-panel focus signal first so other tabs can land us
    // here on the right tab (e.g. an OpenSearch metric widget jumping to logs).
    const f = streamFocus.consume();
    if (f?.tab) tab = f.tab;
    if (f?.sortBy) logsSortBy = f.sortBy;

    void streamLive.refreshLive();
    void refreshOutbox();
    livePoll      = window.setInterval(() => streamLive.refreshLive(), LIVE_POLL_MS);
    outboxPoll    = window.setInterval(refreshOutbox, OUTBOX_POLL_MS);

    // SSE-driven instant updates. agent:event is the typed envelope stream we
    // emit in app.go from the brainbox /api/events SSE wrapper.
    const offAgent = (window as any).runtime?.EventsOn?.('agent:event', (env: any) => {
      streamLive.applyAgentEvent(env);
    });
    if (typeof offAgent === 'function') sseCleanup.push(offAgent);

    // Legacy events still poke a refresh so anything not yet on the bus stays
    // current (P5 retired most, but defense-in-depth is cheap).
    // NOTE: 'brainbox:event' fires for *every* raw bus frame and duplicated the
    // typed 'agent:event' → applyAgentEvent delta path above with a full
    // ListAgentState(200) + ListAttention per event; dropped. The 5s livePoll
    // backstops any gap not covered by the typed stream.
    const legacy = ['task:event', 'loop:run:event'];
    for (const ev of legacy) {
      const off = (window as any).runtime?.EventsOn?.(ev, () => {
        void refreshAttention();
        void streamLive.refreshLive();
      });
      if (typeof off === 'function') sseCleanup.push(off);
    }
  });

  onDestroy(() => {
    if (livePoll      !== undefined) window.clearInterval(livePoll);
    if (logsPoll      !== undefined) window.clearInterval(logsPoll);
    if (outboxPoll    !== undefined) window.clearInterval(outboxPoll);
    sseCleanup.forEach(fn => fn());
  });

  $effect(() => {
    if (tab === 'logs' && opensearchActive) {
      void refreshLogs();
      if (logsPoll === undefined) {
        logsPoll = window.setInterval(refreshLogs, LOGS_POLL_MS);
      }
    } else if (logsPoll !== undefined) {
      window.clearInterval(logsPoll);
      logsPoll = undefined;
    }
  });

  let lastFilter = $state(workspaceFilter);
  $effect(() => {
    if (workspaceFilter !== lastFilter) {
      lastFilter = workspaceFilter;
      void refreshAttention();
      void streamLive.refreshLive();
      if (tab === 'logs') void refreshLogs();
    }
  });

  // ── Status / formatting helpers ────────────────────────────────────────────

  function statusLabel(s: string): string { return s ? s.replace('_', ' ') : ''; }
  function shortId(id: string): string {
    if (!id) return '';
    return id.length > 40 ? id.slice(0, 39) + '…' : id;
  }

  // ── Actions ────────────────────────────────────────────────────────────────

  async function dismiss(item: AttentionItem) {
    attentionStore.removeLocal(item.id);
    const a = await getApi();
    if (!a) return;
    try {
      await a.DismissAttention(item.id);
    } catch (err: any) {
      void attentionStore.refresh(); // restore canonical state
      notifications.error(`Failed to dismiss: ${err?.message ?? err}`);
    }
  }

  async function retry(item: AttentionItem) {
    attentionStore.removeLocal(item.id);
    const a = await getApi();
    if (!a) return;
    try {
      await a.AttentionRetry(item.id);
    } catch (err: any) {
      void attentionStore.refresh();
      notifications.error(`Retry failed: ${err?.message ?? err}`);
    }
  }

  function openRespond(item: AttentionItem) {
    if (respondingId === item.id) {
      respondingId = null;
    } else {
      respondingId = item.id;
      respondText = item.user_reply ?? '';
    }
  }

  async function submitRespond(item: AttentionItem) {
    const a = await getApi();
    if (!a) return;
    try {
      await a.AttentionRespond(item.id, respondText);
      // Refresh to show the stored reply on the card.
      void refreshAttention();
      respondingId = null;
    } catch (err: any) {
      notifications.error(`Failed to respond: ${err?.message ?? err}`);
    }
  }

  async function openTarget(item: AttentionItem) {
    if (item.url) { openInBrowser(item.url); return; }
    const a = await getApi();
    if (!a) return;
    try {
      const target = (await a.AttentionOpenTarget(item.id)) as OpenTarget;
      if (target?.panel) currentPanel.value = target.panel;
    } catch (err: any) {
      notifications.error(`Navigate failed: ${err?.message ?? err}`);
    }
  }

  function openDashboardsDiscover() {
    const ws = workspaceFilter;
    const filter = ws
      ? `&_a=(filters:!((meta:(disabled:!f,index:logs-otel,key:'resource.attributes.workspace.keyword',negate:!f),query:(match_phrase:(resource.attributes.workspace.keyword:'${ws}')))))`
      : '';
    openInBrowser(`http://localhost:5601/app/data-explorer/discover#/?_g=()${filter}`);
  }

  // ── Formatters ─────────────────────────────────────────────────────────────

  function fmtAgo(ms: number): string {
    return timeAgo(ms);
  }

</script>

<div class="panel">
  <header class="panel-header">
    <h1 class="page-title">stream</h1>
    <div class="header-actions">
      {#if outboxPending > 0}
        <div class="outbox-indicator" title="Envelopes queued locally awaiting delivery to brainbox">
          ⤴ {outboxPending} pending
        </div>
      {/if}
      <div class="scope">
        scope:
        <span class="scope-value">{workspaceFilter || 'all'}</span>
      </div>
    </div>
  </header>

  <div class="tabs">
    <button
      class="tab"
      class:active={tab === 'live'}
      onclick={() => (tab = 'live')}>
      live
      {#if streamLive.live.length > 0}
        <span class="badge badge-mute">{streamLive.live.length}</span>
      {/if}
    </button>
    <button
      class="tab"
      class:active={tab === 'attention'}
      onclick={() => (tab = 'attention')}>
      attention
      {#if attention.length > 0}
        <span class="badge">{attention.length}</span>
      {/if}
    </button>
    <button
      class="tab"
      class:active={tab === 'logs'}
      class:disabled={!opensearchActive}
      disabled={!opensearchActive}
      onclick={() => (tab = 'logs')}
      title={opensearchActive ? '' : 'Enable OpenSearch to view live logs'}>
      logs
      {#if tab === 'logs' && logsLoading}
        <Spinner />
      {/if}
    </button>
  </div>

  {#if tab === 'live'}
    <section class="tab-body">
      <!-- Filter + selection control strip -->
      <div class="filter-strip">
        <div class="filter-buckets">
          <div class="filter-bucket">
            <span class="bucket-label">source</span>
            {#each streamLive.availableSources as src (src)}
              <button
                class="filter-chip"
                class:on={streamLive.liveFilters.sources.includes(src)}
                onclick={() => streamLive.toggleFilter('sources', src)}>{src}</button>
            {/each}
          </div>
          <div class="filter-bucket">
            <span class="bucket-label">status</span>
            {#each STATUS_OPTIONS as s (s)}
              <button
                class="filter-chip"
                class:on={streamLive.liveFilters.statuses.includes(s)}
                onclick={() => streamLive.toggleFilter('statuses', s)}>{s.replace('_', ' ')}</button>
            {/each}
          </div>
          {#if streamLive.availableTags.length > 0}
            <div class="filter-bucket">
              <span class="bucket-label">tag</span>
              {#each streamLive.availableTags as t (t)}
                <button
                  class="filter-chip"
                  class:on={streamLive.liveFilters.tags.includes(t)}
                  onclick={() => streamLive.toggleFilter('tags', t)}>{t}</button>
              {/each}
            </div>
          {/if}
        </div>
        <div class="filter-actions">
          {#if streamLive.activeFilterCount > 0}
            <button class="btn ghost small" onclick={() => streamLive.clearFilters()} title="Clear all filters">clear ({streamLive.activeFilterCount})</button>
            <button class="btn ghost small" onclick={() => streamLive.savePreset()} title="Save current filters as a preset">save preset</button>
          {/if}
          <button
            class="btn ghost small"
            class:active={streamLive.selectMode}
            onclick={() => streamLive.toggleSelectMode()}>{streamLive.selectMode ? 'cancel select' : 'select'}</button>
          {#if streamLive.selectMode && streamLive.selected.size > 0}
            <button class="btn ghost small accent" onclick={() => streamLive.saveAsPlaybook()}>
              save as playbook ({streamLive.selected.size})
            </button>
          {/if}
        </div>
      </div>

      {#if streamLive.presets.length > 0}
        <div class="preset-strip" title="Click to apply a saved view">
          {#each streamLive.presets as p (p.name)}
            <span class="preset-chip-wrap">
              <button class="preset-chip" onclick={() => streamLive.applyPreset(p)}>{p.name}</button>
              <button class="preset-x" onclick={() => streamLive.deletePreset(p.name)} title="Delete preset">×</button>
            </span>
          {/each}
        </div>
      {/if}

      {#if streamLive.liveError}
        <EmptyState title="Failed to load live state" message={streamLive.liveError} />
      {:else if streamLive.liveLoading && streamLive.live.length === 0}
        <div class="empty">loading…</div>
      {:else if streamLive.live.length === 0}
        <EmptyState
          title="Nothing currently running"
          message="Live shows envelopes whose status is upcoming, active, blocked, or needs_action across every machine." />
      {:else if streamLive.filteredLive.length === 0}
        <EmptyState
          title="No envelopes match your filters"
          message="{streamLive.live.length} envelope{streamLive.live.length === 1 ? '' : 's'} hidden by active filters. Clear filters above to see them." />
      {:else}
        <ul class="attn-list">
          {#each streamLive.filteredLive as item (item.id)}
            <li class="attn-row src-bus" class:selected={streamLive.selected.has(item.id)}>
              <div class="attn-meta">
                {#if streamLive.selectMode}
                  <input
                    type="checkbox"
                    class="select-box"
                    checked={streamLive.selected.has(item.id)}
                    onchange={() => streamLive.toggleSelect(item.id)}
                    aria-label="Select for playbook"
                  />
                {/if}
                <span class="attn-source">{item.source || 'bus'}</span>
                {#if item.status}
                  <span class="attn-status status-{item.status}">{statusLabel(item.status)}</span>
                {/if}
                {#if item.type}<span class="attn-type">{item.type}</span>{/if}
                {#if item.workspace}<span class="attn-ws">{item.workspace}</span>{/if}
                <span class="attn-time">{fmtAgo(item.updated_at)}</span>
              </div>
              <div class="attn-title">{item.title}</div>
              {#if item.subtitle}
                <div class="attn-sub">{item.subtitle}</div>
              {/if}
              <div class="attn-actions">
                <button class="btn ghost small" onclick={() => streamLive.toggleHistory(item.id)}>
                  {streamLive.history[item.id] ? 'hide history' : 'history'}
                </button>
              </div>
              {#if streamLive.history[item.id]}
                <EnvelopeHistory entries={streamLive.history[item.id]} loading={streamLive.historyLoading[item.id]} />
              {/if}
            </li>
          {/each}
        </ul>
      {/if}
    </section>
  {:else if tab === 'attention'}
    <section class="tab-body">
      {#if attentionError}
        <EmptyState title="Failed to load attention items" message={attentionError} />
      {:else if attentionLoading && attention.length === 0}
        <div class="empty">loading…</div>
      {:else if attention.length === 0}
        <EmptyState
          title="Nothing needs attention"
          message="Failed tasks and entries with pending actions will appear here." />
      {:else}
        <ul class="attn-list">
          {#each attention as item (item.id)}
            <li class="attn-row src-{item.source}" oncontextmenu={(e) => openAttentionMenu(item, e)}>
              <div class="attn-meta">
                <span class="attn-source">{item.source}</span>
                {#if item.status}
                  <span class="attn-status status-{item.status}">{item.status.replace('_', ' ')}</span>
                {/if}
                {#if item.reason}
                  <span class="attn-reason">{item.reason}</span>
                {/if}
                {#if item.workspace}<span class="attn-ws">{item.workspace}</span>{/if}
                {#if item.session_name}
                  <button
                    class="attn-chip chip-session"
                    title="Open session"
                    onclick={() => currentPanel.value = 'sessions'}>{item.session_name}</button>
                {/if}
                {#if item.runner_name}
                  <button
                    class="attn-chip chip-runner"
                    title="Open runners"
                    onclick={() => currentPanel.value = 'runners'}>{item.runner_name}</button>
                {/if}
                <span class="attn-time">{fmtAgo(item.time)}</span>
              </div>
              <div class="attn-title">{item.title}</div>
              {#if item.subtitle}
                <div class="attn-sub">{item.subtitle}</div>
              {/if}

              {#if item.user_reply}
                <div class="attn-reply">↩ {item.user_reply}</div>
              {/if}

              <div class="attn-actions">
                {#each item.actions as action (action)}
                  {#if action === 'retry'}
                    <button class="btn ghost small" onclick={() => retry(item)}>retry</button>
                  {:else if action === 'respond'}
                    <button
                      class="btn ghost small"
                      class:active={respondingId === item.id}
                      onclick={() => openRespond(item)}>respond</button>
                  {:else if action === 'open'}
                    <button class="btn ghost small" onclick={() => openTarget(item)}>open ↗</button>
                  {:else if action === 'dismiss'}
                    <button class="btn ghost small" onclick={() => dismiss(item)}>dismiss</button>
                  {/if}
                {/each}
                {#if item.source === 'bus'}
                  <button class="btn ghost small" onclick={() => streamLive.toggleHistory(item.id)}>
                    {streamLive.history[item.id] ? 'hide history' : 'history'}
                  </button>
                {/if}
              </div>

              {#if streamLive.history[item.id]}
                <EnvelopeHistory entries={streamLive.history[item.id]} loading={streamLive.historyLoading[item.id]} />
              {/if}

              {#if respondingId === item.id}
                <div class="respond-box">
                  <textarea
                    class="respond-input"
                    bind:value={respondText}
                    placeholder="Your reply…"
                    rows="3"></textarea>
                  <div class="respond-footer">
                    <button class="btn ghost small" onclick={() => (respondingId = null)}>cancel</button>
                    <button class="btn ghost small" onclick={() => submitRespond(item)}>submit</button>
                  </div>
                </div>
              {/if}
            </li>
          {/each}
        </ul>
      {/if}
    </section>
  {:else if tab === 'logs'}
    <StreamLogsTab
      {opensearchActive}
      {logsError}
      {logs}
      {logsLoading}
      {logsSortBy}
      {displayLogs}
      onClearSort={() => logsSortBy = null}
      onViewMore={openDashboardsDiscover}
    />
  {/if}
</div>

<ContextMenu
  open={ctxOpen}
  x={ctxX}
  y={ctxY}
  items={ctxItems}
  onClose={() => (ctxOpen = false)}
/>

<style>
  .panel {
    display: flex;
    flex-direction: column;
    height: 100%;
    padding: var(--panel-padding);
    overflow: hidden;
  }

  .panel-header {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    margin-bottom: 16px;
    gap: 16px;
    flex-shrink: 0;
  }

  .page-title {
    font-size: 18px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: lowercase;
    margin: 0;
    color: var(--text, var(--color-text-primary));
  }

  .header-actions { display: flex; gap: 12px; align-items: center; }

  .scope {
    font-size: 11px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--text-faint, var(--color-text-tertiary));
  }
  .scope-value {
    color: var(--text, var(--color-text-primary));
    font-weight: 600;
    letter-spacing: 0;
    text-transform: none;
    margin-left: 4px;
    padding: 2px 8px;
    background: var(--bg-elev, var(--color-bg-tertiary));
    border-radius: var(--r-sm, var(--radius-sm));
  }

  /* ── Tabs ────────────────────────────────────────────────────────────── */
  .tabs {
    display: flex;
    gap: 4px;
    border-bottom: 1px solid var(--border, var(--color-border-primary));
    margin-bottom: 16px;
    flex-shrink: 0;
  }

  .tab {
    background: none;
    border: none;
    color: var(--text-muted, var(--color-text-secondary));
    padding: 8px 14px;
    font-family: inherit;
    font-size: 12.5px;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: lowercase;
    cursor: pointer;
    position: relative;
    display: flex;
    align-items: center;
    gap: 8px;
    transition: color 0.15s;
  }
  .tab:hover { color: var(--text, var(--color-text-primary)); }
  .tab.active { color: var(--text, var(--color-text-primary)); }
  .tab.active::after {
    content: '';
    position: absolute;
    left: 0; right: 0; bottom: -1px;
    height: 2px;
    background: var(--accent, var(--color-accent));
  }
  .tab.disabled { color: var(--text-faint, var(--color-text-tertiary)); cursor: not-allowed; }

  .badge {
    background: var(--accent, var(--color-accent));
    color: white;
    border-radius: 999px;
    padding: 1px 7px;
    font-size: 10.5px;
    font-weight: 700;
    letter-spacing: 0;
  }
  .badge.badge-mute {
    background: var(--bg-elev, var(--color-bg-tertiary));
    color: var(--text-muted, var(--color-text-secondary));
  }

  .outbox-indicator {
    font-size: 11px;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: #ef6c00;
    background: rgba(255, 152, 0, 0.10);
    padding: 2px 8px;
    border-radius: var(--r-sm, var(--radius-sm));
    font-weight: 700;
  }

  .attn-type {
    font-family: var(--font-mono, ui-monospace, monospace);
    color: var(--text-muted, var(--color-text-secondary));
    letter-spacing: 0;
    text-transform: none;
    font-size: 10.5px;
  }


  .tab-body { flex: 1; min-height: 0; overflow-y: auto; }

  /* ── Attention ──────────────────────────────────────────────────────── */
  .attn-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }

  .attn-row {
    background: var(--bg-elev, var(--color-bg-secondary));
    border: 1px solid var(--border, var(--color-border-primary));
    border-left: 3px solid var(--accent, var(--color-accent));
    border-radius: var(--r-md, var(--radius-md));
    padding: 12px 16px;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .attn-row.src-task  { border-left-color: #e57373; }
  .attn-row.src-loop { border-left-color: #ce93d8; }
  .attn-row.src-hub   { border-left-color: #e57373; }
  .attn-row.src-entry { border-left-color: #ffb74d; }
  .attn-row.src-bus   { border-left-color: #4fc3f7; }

  .attn-meta {
    display: flex;
    gap: 10px;
    align-items: center;
    font-size: 11px;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--text-faint, var(--color-text-tertiary));
  }
  .attn-source { font-weight: 700; }
  .attn-status {
    padding: 1px 7px;
    border-radius: var(--r-sm, var(--radius-sm));
    font-weight: 700;
    letter-spacing: 0.04em;
  }
  .attn-status.status-failed       { color: var(--color-status-error-text); background: var(--color-error-bg); }
  .attn-status.status-blocked      { color: var(--color-role-purple-text); background: var(--color-role-purple-bg); }
  .attn-status.status-needs_action { color: var(--color-status-warning-text); background: var(--color-warning-bg); }
  .attn-reason { color: var(--text-muted, var(--color-text-secondary)); }
  .attn-ws {
    padding: 1px 7px;
    background: var(--bg, var(--color-bg-tertiary));
    border-radius: var(--r-sm, var(--radius-sm));
    letter-spacing: 0;
    text-transform: none;
  }
  .attn-time { margin-left: auto; }

  .attn-chip {
    background: var(--bg, var(--color-bg-primary));
    border: 1px solid var(--border, var(--color-border-primary));
    color: var(--text-muted, var(--color-text-secondary));
    padding: 1px 7px;
    border-radius: var(--r-sm, var(--radius-sm));
    font-family: var(--font-mono, ui-monospace, monospace);
    font-size: 10.5px;
    letter-spacing: 0;
    text-transform: none;
    cursor: pointer;
    transition: all 0.15s;
  }
  .attn-chip:hover {
    color: var(--text, var(--color-text-primary));
    border-color: var(--accent, var(--color-accent));
  }
  .chip-session { border-left: 2px solid var(--color-info, #1565c0); }
  .chip-runner  { border-left: 2px solid var(--color-warning, #ef6c00); }

  .attn-title { font-size: 14px; font-weight: 600; color: var(--text, var(--color-text-primary)); }
  .attn-sub   { font-size: 12.5px; color: var(--text-muted, var(--color-text-secondary)); }
  .attn-reply {
    font-size: 12px;
    color: var(--text-muted, var(--color-text-secondary));
    font-style: italic;
    padding: 4px 8px;
    background: var(--bg, var(--color-bg-primary));
    border-radius: var(--r-sm, var(--radius-sm));
    border-left: 2px solid var(--border, var(--color-border-primary));
  }
  .attn-actions { display: flex; gap: 8px; margin-top: 6px; flex-wrap: wrap; }

  /* ── Respond expander ───────────────────────────────────────────────── */
  .respond-box {
    display: flex;
    flex-direction: column;
    gap: 6px;
    margin-top: 4px;
    padding: 8px;
    background: var(--bg, var(--color-bg-primary));
    border: 1px solid var(--border, var(--color-border-primary));
    border-radius: var(--r-sm, var(--radius-sm));
  }
  .respond-input {
    width: 100%;
    font-family: inherit;
    font-size: 12.5px;
    background: var(--bg-elev, var(--color-bg-secondary));
    border: 1px solid var(--border, var(--color-border-primary));
    border-radius: var(--r-sm, var(--radius-sm));
    color: var(--text, var(--color-text-primary));
    padding: 6px 8px;
    resize: vertical;
    box-sizing: border-box;
  }
  .respond-input:focus { outline: 1px solid var(--accent, var(--color-accent)); }
  .respond-footer { display: flex; gap: 6px; justify-content: flex-end; }


  /* ── Shared ─────────────────────────────────────────────────────────── */
  .btn.ghost {
    background: none;
    border: 1px solid var(--border, var(--color-border-primary));
    color: var(--text-muted, var(--color-text-secondary));
    padding: 6px 12px;
    border-radius: var(--r-sm, var(--radius-sm));
    font-family: inherit;
    font-size: 12.5px;
    cursor: pointer;
    transition: all 0.15s;
  }
  .btn.ghost:hover {
    color: var(--text, var(--color-text-primary));
    background: var(--bg-hover, var(--color-surface-hover));
  }
  .btn.ghost.small { padding: 3px 10px; font-size: 11.5px; }
  .btn.ghost.active {
    color: var(--accent, var(--color-accent));
    border-color: var(--accent, var(--color-accent));
  }

  .empty {
    text-align: center;
    padding: 60px 20px;
    color: var(--text-faint, var(--color-text-tertiary));
    font-size: 13px;
  }

  /* ── Filter + preset strip ─────────────────────────────────────────────── */
  .filter-strip {
    display: flex;
    align-items: flex-start;
    gap: 12px;
    margin-bottom: 8px;
    padding-bottom: 8px;
    border-bottom: 1px dashed var(--border, var(--color-border-primary));
    flex-wrap: wrap;
  }
  .filter-buckets {
    flex: 1;
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    min-width: 0;
  }
  .filter-bucket {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    flex-wrap: wrap;
  }
  .bucket-label {
    font-family: var(--font-mono, ui-monospace, monospace);
    font-size: 10px;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--text-faint, var(--color-text-tertiary));
    margin-right: 4px;
  }
  .filter-chip {
    background: transparent;
    border: 1px solid var(--border, var(--color-border-primary));
    color: var(--text-muted, var(--color-text-secondary));
    border-radius: 999px;
    padding: 2px 9px;
    font-family: var(--font-mono, ui-monospace, monospace);
    font-size: 10.5px;
    cursor: pointer;
    transition: all 0.12s;
    text-transform: none;
    letter-spacing: 0;
  }
  .filter-chip:hover {
    color: var(--text, var(--color-text-primary));
    border-color: var(--text-muted, var(--color-text-secondary));
  }
  .filter-chip.on {
    background: var(--accent, var(--color-accent));
    color: white;
    border-color: var(--accent, var(--color-accent));
  }
  .filter-actions {
    display: flex;
    gap: 6px;
    align-items: center;
    flex-shrink: 0;
  }
  .btn.ghost.accent {
    color: var(--accent, var(--color-accent));
    border-color: var(--accent, var(--color-accent));
  }
  .preset-strip {
    display: flex;
    gap: 6px;
    margin-bottom: 8px;
    flex-wrap: wrap;
  }
  .preset-chip-wrap {
    display: inline-flex;
    align-items: stretch;
    border: 1px solid var(--border, var(--color-border-primary));
    border-radius: var(--r-sm, var(--radius-sm));
    overflow: hidden;
  }
  .preset-chip {
    background: var(--bg-elev, var(--color-bg-tertiary));
    border: none;
    color: var(--text-muted, var(--color-text-secondary));
    padding: 2px 10px;
    font-family: var(--font-mono, ui-monospace, monospace);
    font-size: 10.5px;
    cursor: pointer;
  }
  .preset-chip:hover { color: var(--text, var(--color-text-primary)); }
  .preset-x {
    background: var(--bg-elev, var(--color-bg-tertiary));
    border: none;
    border-left: 1px solid var(--border, var(--color-border-primary));
    color: var(--text-faint, var(--color-text-tertiary));
    padding: 0 6px;
    font-size: 12px;
    cursor: pointer;
  }
  .preset-x:hover { color: var(--color-error); }

  .attn-row.selected {
    box-shadow: 0 0 0 2px var(--accent, var(--color-accent));
  }
  .select-box {
    margin: 0 4px 0 0;
    width: 14px;
    height: 14px;
    accent-color: var(--accent, var(--color-accent));
    cursor: pointer;
  }
</style>

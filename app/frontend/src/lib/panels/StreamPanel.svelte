<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { timeAgo, formatClock } from '../utils/format';
  import { getApi, openInBrowser } from '../utils/api';
  import { featureFlags, profileState, currentPanel, attentionStore, streamFocus, playbookSeed } from '../stores.svelte';
  import { notifications } from '../notifications.svelte';
  import Spinner from '../components/Spinner.svelte';
  import EmptyState from '../components/EmptyState.svelte';
  import ContextMenu from '../components/ContextMenu.svelte';
  import StreamLogsTab from '../components/StreamLogsTab.svelte';

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

  // Bus envelope (mirrors brainbox.AgentStateItem). Used in Live tab and
  // History drill-down.
  interface AgentStateItem {
    id: string;
    kind: string;
    source: string;
    type: string;
    status: string;
    title: string;
    subtitle: string;
    workspace: string;
    parent_id: string;
    url: string;
    start_at: number | null;
    end_at: number | null;
    tags: string[];
    metadata: Record<string, any>;
    actions: Record<string, any>[];
    outcome: Record<string, any> | null;
    created_at: number;
    updated_at: number;
  }

  interface AgentEventEntry {
    seq: number;
    id: string;
    source: string;
    type: string;
    status: string;
    parent_id: string;
    ts: number;
    envelope: Record<string, any>;
  }

  type Tab = 'live' | 'attention' | 'logs';

  // ── State ──────────────────────────────────────────────────────────────────

  let tab = $state<Tab>('attention');
  // Attention list is sourced from the singleton attentionStore so the sidebar
  // badge, dashboard ActionItems widget, and this panel always agree without
  // duplicating polls.
  let attention = $derived<AttentionItem[]>(attentionStore.items as unknown as AttentionItem[]);
  let attentionLoading = $derived(!attentionStore.loaded);
  let live = $state<AgentStateItem[]>([]);
  let logs = $state<LogEntry[]>([]);
  // Optional sort key for the logs tab. Set via cross-panel streamFocus signal
  // (e.g. the OpenSearch cost widget jumps in with sortBy='cost').
  let logsSortBy = $state<'cost' | 'duration' | 'tokens' | null>(null);
  let displayLogs = $derived.by(() => {
    if (!logsSortBy) return logs;
    const key = logsSortBy === 'duration' ? 'duration_ms' : logsSortBy;
    return [...logs].sort((a, b) => ((b as any)[key] ?? 0) - ((a as any)[key] ?? 0));
  });
  let liveLoading = $state(true);
  let logsLoading = $state(false);
  let attentionError = $state<string | null>(null);
  let liveError = $state<string | null>(null);
  let logsError = $state<string | null>(null);

  // Inline respond expander state
  let respondingId = $state<string | null>(null);
  let respondText = $state('');

  // History drill-down state — keyed by envelope id, holds the fetched event
  // sequence. Card is expanded when its id is in this map.
  let history = $state<Record<string, AgentEventEntry[]>>({});
  let historyLoading = $state<Record<string, boolean>>({});

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

  // Active-state statuses queried for the Live tab.
  const LIVE_STATUSES = 'upcoming,active,blocked,needs_action';

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

  // ── Live tab filters + presets ─────────────────────────────────────────────
  // Multi-select chip filters: a row matches when each non-empty bucket has at
  // least one chip matching the envelope (AND across buckets, OR within).
  // Persisted client-side so the same operator view survives a reload.

  interface LiveFilters {
    sources: string[];   // e.g. ['task','hub']
    statuses: string[];  // 'upcoming' | 'active' | 'blocked' | 'needs_action'
    tags: string[];
  }
  interface LivePreset {
    name: string;
    filters: LiveFilters;
  }

  const FILTERS_KEY = 'pi-stream-filters-v1';
  const PRESETS_KEY = 'pi-stream-presets-v1';
  const STATUS_OPTIONS = ['upcoming', 'active', 'blocked', 'needs_action'];

  function loadFilters(): LiveFilters {
    try {
      const raw = localStorage.getItem(FILTERS_KEY);
      if (raw) return JSON.parse(raw) as LiveFilters;
    } catch {}
    return { sources: [], statuses: [], tags: [] };
  }
  function loadPresets(): LivePreset[] {
    try {
      const raw = localStorage.getItem(PRESETS_KEY);
      if (raw) return JSON.parse(raw) as LivePreset[];
    } catch {}
    return [];
  }

  let liveFilters = $state<LiveFilters>(loadFilters());
  let presets = $state<LivePreset[]>(loadPresets());

  $effect(() => {
    try { localStorage.setItem(FILTERS_KEY, JSON.stringify(liveFilters)); } catch {}
  });

  function toggleFilter(bucket: keyof LiveFilters, value: string): void {
    const cur = liveFilters[bucket];
    liveFilters = {
      ...liveFilters,
      [bucket]: cur.includes(value) ? cur.filter(v => v !== value) : [...cur, value],
    };
  }
  function clearFilters(): void {
    liveFilters = { sources: [], statuses: [], tags: [] };
  }
  let activeFilterCount = $derived(
    liveFilters.sources.length + liveFilters.statuses.length + liveFilters.tags.length
  );

  // Tag chip universe = every tag we've seen on a live envelope, plus filter
  // tags so removed-then-readded chips stick around.
  let availableTags = $derived.by(() => {
    const set = new Set<string>(liveFilters.tags);
    for (const item of live) for (const t of item.tags ?? []) set.add(t);
    return Array.from(set).sort();
  });
  let availableSources = $derived.by(() => {
    const set = new Set<string>(['task', 'loop', 'entry', 'hub', 'bus']);
    for (const item of live) if (item.source) set.add(item.source);
    return Array.from(set).sort();
  });

  function passesFilters(it: AgentStateItem): boolean {
    if (liveFilters.sources.length && !liveFilters.sources.includes(it.source)) return false;
    if (liveFilters.statuses.length && !liveFilters.statuses.includes(it.status)) return false;
    if (liveFilters.tags.length) {
      const itemTags = new Set(it.tags ?? []);
      if (!liveFilters.tags.some(t => itemTags.has(t))) return false;
    }
    return true;
  }

  let filteredLive = $derived(live.filter(passesFilters));

  function savePreset(): void {
    const name = window.prompt('Name this preset (e.g. "blocked-only"):');
    if (!name?.trim()) return;
    const next = [
      { name: name.trim(), filters: { ...liveFilters, sources: [...liveFilters.sources], statuses: [...liveFilters.statuses], tags: [...liveFilters.tags] } },
      ...presets.filter(p => p.name !== name.trim()),
    ].slice(0, 9);
    presets = next;
    try { localStorage.setItem(PRESETS_KEY, JSON.stringify(next)); } catch {}
  }
  function applyPreset(p: LivePreset): void {
    liveFilters = {
      sources: [...p.filters.sources],
      statuses: [...p.filters.statuses],
      tags: [...p.filters.tags],
    };
  }
  function deletePreset(name: string): void {
    const next = presets.filter(p => p.name !== name);
    presets = next;
    try { localStorage.setItem(PRESETS_KEY, JSON.stringify(next)); } catch {}
  }

  // ── Live-tab selection mode (drives Save-as-playbook) ──────────────────────
  let selectMode = $state(false);
  let selected = $state<Set<string>>(new Set());

  function toggleSelect(id: string): void {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id); else next.add(id);
    selected = next;
  }
  function clearSelection(): void { selected = new Set(); }
  function exitSelectMode(): void { selectMode = false; clearSelection(); }

  function saveAsPlaybook(): void {
    if (selected.size === 0) return;
    // Preserve the user's visible order so the playbook reads top-to-bottom.
    const picked = filteredLive.filter(it => selected.has(it.id));
    if (picked.length === 0) return;
    const lines = picked.map(it => {
      const title = (it.title || it.source || it.type || 'step').replace(/\s+/g, ' ').trim();
      const sub = (it.subtitle ?? '').trim();
      return sub ? `- [ ] ${title} — ${sub}` : `- [ ] ${title}`;
    });
    const seedName = `from-stream-${new Date().toISOString().slice(0, 10)}`;
    playbookSeed.seed({
      name: seedName,
      markdown: lines.join('\n'),
      scope: workspaceFilter ? 'profile' : 'global',
    });
    exitSelectMode();
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

  async function refreshLive() {
    const a = await getApi();
    if (!a) return;
    try {
      live = ((await a.ListAgentState({
        status: LIVE_STATUSES,
        workspace: workspaceFilter,
        source: '',
        parent_id: '',
        limit: 200,
      })) ?? []) as AgentStateItem[];
      liveError = null;
    } catch (err: any) {
      liveError = `${err?.message ?? err}`;
    } finally {
      liveLoading = false;
    }
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

  // Apply one bus envelope delta into the live list without a full reload.
  // Matches the brainbox upsert semantics: same id mutates in place; new ids
  // append; terminal/done statuses drop off the live view.
  function applyAgentEvent(env: any) {
    if (!env || typeof env !== 'object') return;
    if (env.workspace && workspaceFilter && env.workspace !== workspaceFilter) return;

    const activeStatuses = ['upcoming', 'active', 'blocked', 'needs_action'];
    const isActive = activeStatuses.includes(env.status);

    // Always nudge attention — it might be a failed/blocked/needs_action delta.
    void refreshAttention();

    const idx = live.findIndex(i => i.id === env.id);
    if (!isActive) {
      if (idx >= 0) live = live.filter((_, i) => i !== idx);
      return;
    }

    // ListAgentState returns rows enriched by brainbox (created_at, updated_at,
    // full metadata maps). The SSE payload is the envelope itself; merge the
    // fields we need for display and re-sort by updated_at desc.
    const merged: AgentStateItem = {
      id: env.id,
      kind: env.kind ?? 'event',
      source: env.source ?? '',
      type: env.type ?? '',
      status: env.status ?? '',
      title: env.title ?? '',
      subtitle: env.subtitle ?? '',
      workspace: env.workspace ?? '',
      parent_id: env.parent_id ?? '',
      url: env.url ?? '',
      start_at: env.start_at ?? null,
      end_at: env.end_at ?? null,
      tags: env.tags ?? [],
      metadata: env.metadata ?? {},
      actions: env.actions ?? [],
      outcome: env.outcome ?? null,
      created_at: idx >= 0 ? live[idx].created_at : Date.now(),
      updated_at: Date.now(),
    };
    if (idx >= 0) {
      const next = [...live];
      next[idx] = merged;
      live = next.sort((a, b) => b.updated_at - a.updated_at);
    } else {
      live = [merged, ...live];
    }
  }

  // Lazy-load the audit log for one envelope when the user expands a card.
  async function toggleHistory(id: string) {
    if (history[id]) {
      const next = { ...history };
      delete next[id];
      history = next;
      return;
    }
    historyLoading = { ...historyLoading, [id]: true };
    const a = await getApi();
    if (!a) return;
    try {
      const events = ((await a.ListAgentEvents(id, '', 200)) ?? []) as AgentEventEntry[];
      history = { ...history, [id]: events };
    } catch (err: any) {
      notifications.error(`Failed to load history: ${err?.message ?? err}`);
    } finally {
      historyLoading = { ...historyLoading, [id]: false };
    }
  }

  onMount(() => {
    // Consume any cross-panel focus signal first so other tabs can land us
    // here on the right tab (e.g. an OpenSearch metric widget jumping to logs).
    const f = streamFocus.consume();
    if (f?.tab) tab = f.tab;
    if (f?.sortBy) logsSortBy = f.sortBy;

    void refreshLive();
    void refreshOutbox();
    livePoll      = window.setInterval(refreshLive, LIVE_POLL_MS);
    outboxPoll    = window.setInterval(refreshOutbox, OUTBOX_POLL_MS);

    // SSE-driven instant updates. agent:event is the typed envelope stream we
    // emit in app.go from the brainbox /api/events SSE wrapper.
    const offAgent = (window as any).runtime?.EventsOn?.('agent:event', (env: any) => {
      applyAgentEvent(env);
    });
    if (typeof offAgent === 'function') sseCleanup.push(offAgent);

    // Legacy events still poke a refresh so anything not yet on the bus stays
    // current (P5 retired most, but defense-in-depth is cheap).
    const legacy = ['task:event', 'loop:run:event', 'brainbox:event'];
    for (const ev of legacy) {
      const off = (window as any).runtime?.EventsOn?.(ev, () => {
        void refreshAttention();
        void refreshLive();
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
      void refreshLive();
      if (tab === 'logs') void refreshLogs();
    }
  });

  // ── Status / formatting helpers ────────────────────────────────────────────

  function statusLabel(s: string): string { return s ? s.replace('_', ' ') : ''; }
  function shortId(id: string): string {
    if (!id) return '';
    return id.length > 40 ? id.slice(0, 39) + '…' : id;
  }
  function fmtMs(ms: number): string {
    if (!ms) return '';
    try { return formatClock(ms, { seconds: true }); }
    catch { return ''; }
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
      {#if live.length > 0}
        <span class="badge badge-mute">{live.length}</span>
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
            {#each availableSources as src (src)}
              <button
                class="filter-chip"
                class:on={liveFilters.sources.includes(src)}
                onclick={() => toggleFilter('sources', src)}>{src}</button>
            {/each}
          </div>
          <div class="filter-bucket">
            <span class="bucket-label">status</span>
            {#each STATUS_OPTIONS as s (s)}
              <button
                class="filter-chip"
                class:on={liveFilters.statuses.includes(s)}
                onclick={() => toggleFilter('statuses', s)}>{s.replace('_', ' ')}</button>
            {/each}
          </div>
          {#if availableTags.length > 0}
            <div class="filter-bucket">
              <span class="bucket-label">tag</span>
              {#each availableTags as t (t)}
                <button
                  class="filter-chip"
                  class:on={liveFilters.tags.includes(t)}
                  onclick={() => toggleFilter('tags', t)}>{t}</button>
              {/each}
            </div>
          {/if}
        </div>
        <div class="filter-actions">
          {#if activeFilterCount > 0}
            <button class="btn ghost small" onclick={clearFilters} title="Clear all filters">clear ({activeFilterCount})</button>
            <button class="btn ghost small" onclick={savePreset} title="Save current filters as a preset">save preset</button>
          {/if}
          <button
            class="btn ghost small"
            class:active={selectMode}
            onclick={() => { selectMode = !selectMode; if (!selectMode) clearSelection(); }}>{selectMode ? 'cancel select' : 'select'}</button>
          {#if selectMode && selected.size > 0}
            <button class="btn ghost small accent" onclick={saveAsPlaybook}>
              save as playbook ({selected.size})
            </button>
          {/if}
        </div>
      </div>

      {#if presets.length > 0}
        <div class="preset-strip" title="Click to apply a saved view">
          {#each presets as p (p.name)}
            <span class="preset-chip-wrap">
              <button class="preset-chip" onclick={() => applyPreset(p)}>{p.name}</button>
              <button class="preset-x" onclick={() => deletePreset(p.name)} title="Delete preset">×</button>
            </span>
          {/each}
        </div>
      {/if}

      {#if liveError}
        <EmptyState title="Failed to load live state" message={liveError} />
      {:else if liveLoading && live.length === 0}
        <div class="empty">loading…</div>
      {:else if live.length === 0}
        <EmptyState
          title="Nothing currently running"
          message="Live shows envelopes whose status is upcoming, active, blocked, or needs_action across every machine." />
      {:else if filteredLive.length === 0}
        <EmptyState
          title="No envelopes match your filters"
          message="{live.length} envelope{live.length === 1 ? '' : 's'} hidden by active filters. Clear filters above to see them." />
      {:else}
        <ul class="attn-list">
          {#each filteredLive as item (item.id)}
            <li class="attn-row src-bus" class:selected={selected.has(item.id)}>
              <div class="attn-meta">
                {#if selectMode}
                  <input
                    type="checkbox"
                    class="select-box"
                    checked={selected.has(item.id)}
                    onchange={() => toggleSelect(item.id)}
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
                <button class="btn ghost small" onclick={() => toggleHistory(item.id)}>
                  {history[item.id] ? 'hide history' : 'history'}
                </button>
              </div>
              {#if history[item.id]}
                <div class="history-box">
                  {#if historyLoading[item.id]}
                    <div class="history-loading">loading audit log…</div>
                  {:else if history[item.id].length === 0}
                    <div class="history-loading">no audit entries yet</div>
                  {:else}
                    <ol class="history-list">
                      {#each history[item.id] as ev (ev.seq)}
                        <li class="history-row">
                          <span class="history-time">{fmtMs(ev.ts)}</span>
                          <span class="history-type">{ev.type}</span>
                          {#if ev.status}<span class="history-status status-{ev.status}">{statusLabel(ev.status)}</span>{/if}
                          {#if ev.envelope?.outcome}
                            <span class="history-outcome" class:ok={ev.envelope.outcome.ok} class:bad={!ev.envelope.outcome.ok}>
                              {ev.envelope.outcome.actor}{ev.envelope.outcome.ok ? ' · ok' : ' · failed'}
                              {ev.envelope.outcome.duration_ms ? ' · ' + ev.envelope.outcome.duration_ms + 'ms' : ''}
                            </span>
                          {/if}
                        </li>
                      {/each}
                    </ol>
                  {/if}
                </div>
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
                  <button class="btn ghost small" onclick={() => toggleHistory(item.id)}>
                    {history[item.id] ? 'hide history' : 'history'}
                  </button>
                {/if}
              </div>

              {#if history[item.id]}
                <div class="history-box">
                  {#if historyLoading[item.id]}
                    <div class="history-loading">loading audit log…</div>
                  {:else if history[item.id].length === 0}
                    <div class="history-loading">no audit entries yet</div>
                  {:else}
                    <ol class="history-list">
                      {#each history[item.id] as ev (ev.seq)}
                        <li class="history-row">
                          <span class="history-time">{fmtMs(ev.ts)}</span>
                          <span class="history-type">{ev.type}</span>
                          {#if ev.status}<span class="history-status status-{ev.status}">{statusLabel(ev.status)}</span>{/if}
                          {#if ev.envelope?.outcome}
                            <span class="history-outcome" class:ok={ev.envelope.outcome.ok} class:bad={!ev.envelope.outcome.ok}>
                              {ev.envelope.outcome.actor}{ev.envelope.outcome.ok ? ' · ok' : ' · failed'}
                              {ev.envelope.outcome.duration_ms ? ' · ' + ev.envelope.outcome.duration_ms + 'ms' : ''}
                            </span>
                          {/if}
                        </li>
                      {/each}
                    </ol>
                  {/if}
                </div>
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

  .history-box {
    margin-top: 6px;
    padding: 8px 10px;
    background: var(--bg, var(--color-bg-primary));
    border: 1px solid var(--border, var(--color-border-primary));
    border-radius: var(--r-sm, var(--radius-sm));
  }
  .history-loading {
    font-size: 12px;
    color: var(--text-faint, var(--color-text-tertiary));
    padding: 4px 0;
  }
  .history-list {
    list-style: none;
    margin: 0;
    padding: 0;
    font-family: var(--font-mono, ui-monospace, monospace);
    font-size: 11.5px;
    display: flex;
    flex-direction: column;
    gap: 3px;
  }
  .history-row {
    display: flex;
    gap: 10px;
    align-items: baseline;
    color: var(--text-muted, var(--color-text-secondary));
  }
  .history-time { color: var(--text-faint, var(--color-text-tertiary)); }
  .history-type { color: var(--text, var(--color-text-primary)); font-weight: 600; }
  .history-status {
    padding: 0 6px;
    border-radius: var(--r-sm, var(--radius-sm));
    font-size: 10.5px;
    font-weight: 700;
    text-transform: uppercase;
  }
  .history-status.status-failed       { color: var(--color-status-error-text); background: var(--color-error-bg); }
  .history-status.status-blocked      { color: var(--color-role-purple-text); background: var(--color-role-purple-bg); }
  .history-status.status-needs_action { color: var(--color-status-warning-text); background: var(--color-warning-bg); }
  .history-status.status-active       { color: var(--color-role-blue-text); background: var(--color-info-bg); }
  .history-status.status-upcoming     { color: var(--color-text-tertiary); background: var(--color-muted-bg); }
  .history-status.status-done         { color: var(--color-status-success-text); background: var(--color-success-bg); }
  .history-outcome { margin-left: auto; font-size: 11px; }
  .history-outcome.ok  { color: var(--color-status-success-text); }
  .history-outcome.bad { color: var(--color-status-error-text); }

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

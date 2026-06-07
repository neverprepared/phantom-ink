<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { getApi, openInBrowser } from '../utils/api';
  import { featureFlags, profileState, currentPanel } from '../stores.svelte';
  import { notifications } from '../notifications.svelte';
  import Spinner from '../components/Spinner.svelte';
  import EmptyState from '../components/EmptyState.svelte';

  // ── Types ──────────────────────────────────────────────────────────────────

  interface AttentionItem {
    id: string;
    source: 'task' | 'chain' | 'entry' | 'hub' | 'bus';
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
  let attention = $state<AttentionItem[]>([]);
  let live = $state<AgentStateItem[]>([]);
  let logs = $state<LogEntry[]>([]);
  let attentionLoading = $state(true);
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

  let attentionPoll: number | undefined;
  let livePoll: number | undefined;
  let logsPoll: number | undefined;
  let outboxPoll: number | undefined;
  let sseCleanup: Array<() => void> = [];

  const ATTN_POLL_MS = 5_000;
  const LIVE_POLL_MS = 5_000;     // SSE drives instant updates; this is a safety net
  const LOGS_POLL_MS = 3_000;
  const OUTBOX_POLL_MS = 5_000;
  const LOGS_LIMIT = 1000;

  // Active-state statuses queried for the Live tab.
  const LIVE_STATUSES = 'upcoming,active,blocked,needs_action';

  let opensearchActive = $derived(featureFlags.isActive('opensearch'));
  let activeProfile    = $derived(profileState.active);
  let workspaceFilter  = $derived(activeProfile?.name ?? '');

  $effect(() => {
    if (tab === 'logs' && !opensearchActive) tab = 'attention';
  });

  // ── Loaders ────────────────────────────────────────────────────────────────

  async function refreshAttention() {
    const a = await getApi();
    if (!a) return;
    try {
      attention = ((await a.ListAttention(workspaceFilter)) ?? []) as AttentionItem[];
      attentionError = null;
    } catch (err: any) {
      attentionError = `${err?.message ?? err}`;
    } finally {
      attentionLoading = false;
    }
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
    void refreshAttention();
    void refreshLive();
    void refreshOutbox();
    attentionPoll = window.setInterval(refreshAttention, ATTN_POLL_MS);
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
    const legacy = ['task:event', 'chain:run:event', 'brainbox:event'];
    for (const ev of legacy) {
      const off = (window as any).runtime?.EventsOn?.(ev, () => {
        void refreshAttention();
        void refreshLive();
      });
      if (typeof off === 'function') sseCleanup.push(off);
    }
  });

  onDestroy(() => {
    if (attentionPoll !== undefined) window.clearInterval(attentionPoll);
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
    try { return new Date(ms).toLocaleTimeString(undefined, { hour12: false }); }
    catch { return ''; }
  }

  // ── Actions ────────────────────────────────────────────────────────────────

  async function dismiss(item: AttentionItem) {
    const prev = attention;
    attention = attention.filter(i => i.id !== item.id);
    const a = await getApi();
    if (!a) return;
    try {
      await a.DismissAttention(item.id);
    } catch (err: any) {
      attention = prev;
      notifications.error(`Failed to dismiss: ${err?.message ?? err}`);
    }
  }

  async function retry(item: AttentionItem) {
    const prev = attention;
    attention = attention.filter(i => i.id !== item.id);
    const a = await getApi();
    if (!a) return;
    try {
      await a.AttentionRetry(item.id);
    } catch (err: any) {
      attention = prev;
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
    if (!ms) return '';
    const diff = Date.now() - ms;
    if (diff < 60_000) return `${Math.round(diff / 1000)}s ago`;
    if (diff < 3_600_000) return `${Math.round(diff / 60_000)}m ago`;
    if (diff < 86_400_000) return `${Math.round(diff / 3_600_000)}h ago`;
    return `${Math.round(diff / 86_400_000)}d ago`;
  }

  function fmtTime(iso: string): string {
    if (!iso) return '';
    try {
      const d = new Date(iso);
      return d.toLocaleTimeString(undefined, { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
    } catch { return iso; }
  }

  function fmtDur(ms?: number): string {
    if (!ms) return '';
    return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(2)}s`;
  }

  function sessionShort(id?: string): string {
    if (!id) return '';
    return id.length > 8 ? id.slice(0, 8) : id;
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
      {#if liveError}
        <EmptyState title="Failed to load live state" message={liveError} />
      {:else if liveLoading && live.length === 0}
        <div class="empty">loading…</div>
      {:else if live.length === 0}
        <EmptyState
          title="Nothing currently running"
          message="Live shows envelopes whose status is upcoming, active, blocked, or needs_action across every machine." />
      {:else}
        <ul class="attn-list">
          {#each live as item (item.id)}
            <li class="attn-row src-bus">
              <div class="attn-meta">
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
            <li class="attn-row src-{item.source}">
              <div class="attn-meta">
                <span class="attn-source">{item.source}</span>
                {#if item.status}
                  <span class="attn-status status-{item.status}">{item.status.replace('_', ' ')}</span>
                {/if}
                {#if item.reason}
                  <span class="attn-reason">{item.reason}</span>
                {/if}
                {#if item.workspace}<span class="attn-ws">{item.workspace}</span>{/if}
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
    <section class="tab-body">
      {#if !opensearchActive}
        <EmptyState
          title="OpenSearch not enabled"
          message="Enable the OpenSearch integration to stream live telemetry logs here." />
      {:else if logsError}
        <EmptyState title="Failed to tail logs" message={logsError} />
      {:else if logs.length === 0 && !logsLoading}
        <EmptyState
          title="No logs in the last 24h"
          message="Start a Claude Code session or widen your workspace filter." />
      {:else}
        <ol class="log-list">
          {#each logs as l, i (l.time + ':' + i)}
            <li class="log-row">
              <span class="log-time">{fmtTime(l.time)}</span>
              <span class="log-body">{l.body}</span>
              {#if l.model}<span class="log-tag">{l.model}</span>{/if}
              {#if l.workspace}<span class="log-tag tag-ws">{l.workspace}</span>{/if}
              {#if l.session}<span class="log-tag tag-sess">{sessionShort(l.session)}</span>{/if}
              {#if l.duration_ms}<span class="log-dur">{fmtDur(l.duration_ms)}</span>{/if}
            </li>
          {/each}
        </ol>
        <div class="log-footer">
          showing {logs.length} of last 24h, newest first ·
          <button class="link-btn" onclick={openDashboardsDiscover}>view more in Dashboards ↗</button>
        </div>
      {/if}
    </section>
  {/if}
</div>

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
  .history-status.status-failed       { color: #b71c1c; background: rgba(244, 67, 54, 0.12); }
  .history-status.status-blocked      { color: #6a1b9a; background: rgba(156, 39, 176, 0.12); }
  .history-status.status-needs_action { color: #ef6c00; background: rgba(255, 152, 0, 0.14); }
  .history-status.status-active       { color: #1565c0; background: rgba(33, 150, 243, 0.12); }
  .history-status.status-upcoming     { color: #455a64; background: rgba(96, 125, 139, 0.12); }
  .history-status.status-done         { color: #2e7d32; background: rgba(76, 175, 80, 0.12); }
  .history-outcome { margin-left: auto; font-size: 11px; }
  .history-outcome.ok  { color: #2e7d32; }
  .history-outcome.bad { color: #b71c1c; }

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
  .attn-row.src-chain { border-left-color: #ce93d8; }
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
  .attn-status.status-failed       { color: #b71c1c; background: rgba(244, 67, 54, 0.12); }
  .attn-status.status-blocked      { color: #6a1b9a; background: rgba(156, 39, 176, 0.12); }
  .attn-status.status-needs_action { color: #ef6c00; background: rgba(255, 152, 0, 0.14); }
  .attn-reason { color: var(--text-muted, var(--color-text-secondary)); }
  .attn-ws {
    padding: 1px 7px;
    background: var(--bg, var(--color-bg-tertiary));
    border-radius: var(--r-sm, var(--radius-sm));
    letter-spacing: 0;
    text-transform: none;
  }
  .attn-time { margin-left: auto; }

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

  /* ── Live logs ──────────────────────────────────────────────────────── */
  .log-list {
    list-style: none;
    margin: 0;
    padding: 0;
    font-family: var(--font-mono, ui-monospace, monospace);
    font-size: 12px;
  }
  .log-row {
    display: flex;
    gap: 10px;
    align-items: baseline;
    padding: 4px 8px;
    border-bottom: 1px solid var(--border-subtle, var(--color-border-secondary, transparent));
    white-space: nowrap;
    overflow: hidden;
  }
  .log-row:hover { background: var(--bg-hover, var(--color-surface-hover)); }
  .log-time { color: var(--text-faint, var(--color-text-tertiary)); flex-shrink: 0; }
  .log-body { color: var(--text, var(--color-text-primary)); font-weight: 600; }
  .log-tag {
    background: var(--bg-elev, var(--color-bg-tertiary));
    color: var(--text-muted, var(--color-text-secondary));
    padding: 1px 7px;
    border-radius: var(--r-sm, var(--radius-sm));
    font-size: 11px;
  }
  .tag-ws   { color: var(--accent, var(--color-accent)); }
  .tag-sess { font-family: var(--font-mono, ui-monospace, monospace); }
  .log-dur  { color: var(--text-faint, var(--color-text-tertiary)); margin-left: auto; }

  .log-footer {
    margin-top: 12px;
    font-size: 11.5px;
    color: var(--text-faint, var(--color-text-tertiary));
    text-align: right;
  }
  .link-btn {
    background: none;
    border: none;
    color: var(--accent, var(--color-accent));
    cursor: pointer;
    font: inherit;
    padding: 0;
  }
  .link-btn:hover { text-decoration: underline; }

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
</style>

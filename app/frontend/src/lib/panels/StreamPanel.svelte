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
    source: 'task' | 'chain' | 'entry' | 'hub';
    source_id: string;
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

  type Tab = 'attention' | 'live';

  // ── State ──────────────────────────────────────────────────────────────────

  let tab = $state<Tab>('attention');
  let attention = $state<AttentionItem[]>([]);
  let logs = $state<LogEntry[]>([]);
  let attentionLoading = $state(true);
  let logsLoading = $state(false);
  let attentionError = $state<string | null>(null);
  let logsError = $state<string | null>(null);

  // Inline respond expander state
  let respondingId = $state<string | null>(null);
  let respondText = $state('');

  let attentionPoll: number | undefined;
  let logsPoll: number | undefined;
  let sseCleanup: Array<() => void> = [];

  const ATTN_POLL_MS = 5_000;
  const LOGS_POLL_MS = 3_000;
  const LOGS_LIMIT = 1000;

  let opensearchActive = $derived(featureFlags.isActive('opensearch'));
  let activeProfile    = $derived(profileState.active);
  let workspaceFilter  = $derived(activeProfile?.name ?? '');

  $effect(() => {
    if (tab === 'live' && !opensearchActive) tab = 'attention';
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
    void refreshAttention();
    attentionPoll = window.setInterval(refreshAttention, ATTN_POLL_MS);

    // SSE-driven immediate refresh — supplements the 5s safety net poll.
    const sseEvents = ['task:event', 'chain:run:event', 'brainbox:event'];
    for (const ev of sseEvents) {
      const off = (window as any).runtime?.EventsOn?.(ev, () => void refreshAttention());
      if (typeof off === 'function') sseCleanup.push(off);
    }
  });

  onDestroy(() => {
    if (attentionPoll !== undefined) window.clearInterval(attentionPoll);
    if (logsPoll      !== undefined) window.clearInterval(logsPoll);
    sseCleanup.forEach(fn => fn());
  });

  $effect(() => {
    if (tab === 'live' && opensearchActive) {
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
      if (tab === 'live') void refreshLogs();
    }
  });

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
      <div class="scope">
        scope:
        <span class="scope-value">{workspaceFilter || 'all'}</span>
      </div>
    </div>
  </header>

  <div class="tabs">
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
      class:active={tab === 'live'}
      class:disabled={!opensearchActive}
      disabled={!opensearchActive}
      onclick={() => (tab = 'live')}
      title={opensearchActive ? '' : 'Enable OpenSearch to view live logs'}>
      live
      {#if tab === 'live' && logsLoading}
        <Spinner />
      {/if}
    </button>
  </div>

  {#if tab === 'attention'}
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
                <span class="attn-reason">{item.reason}</span>
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
              </div>

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
  {:else if tab === 'live'}
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

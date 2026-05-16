<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { getApi } from '../utils/api';
  import { notifications } from '../notifications.svelte';

  interface Task {
    id: string;
    chain_id: string;
    status: 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled';
    priority: number;
    input: string;
    cwd: string;
    trigger: string;
    parent_task_id: string;
    enqueued_at: string;
    scheduled_for: string;
    started_at: string;
    finished_at: string;
    attempts: number;
    max_attempts: number;
    last_error: string;
    result_run_id: string;
  }

  interface Chain {
    id: string;
    name: string;
  }

  let tasks = $state<Task[]>([]);
  let chains = $state<Map<string, string>>(new Map());
  let filter = $state<'all' | 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled'>('all');
  let loading = $state(true);
  let expandedID = $state<string | null>(null);
  let unsubscribe: (() => void) | null = null;

  async function load() {
    const a = await getApi();
    if (!a) { loading = false; return; }
    try {
      const status = filter === 'all' ? '' : filter;
      const [t, c] = await Promise.all([
        a.ListTasks(status, 100),
        a.ListChains(),
      ]);
      tasks = (t ?? []) as Task[];
      chains = new Map(((c ?? []) as Chain[]).map(ch => [ch.id, ch.name]));
    } catch (err: any) {
      notifications.error(`Failed to load tasks: ${err?.message ?? err}`);
    } finally {
      loading = false;
    }
  }

  function subscribeEvents() {
    const rt = (window as any).runtime;
    if (!rt?.EventsOn) return;
    const handler = () => { load(); };
    rt.EventsOn('task:event', handler);
    unsubscribe = () => rt.EventsOff?.('task:event');
  }

  onMount(() => {
    load();
    subscribeEvents();
  });
  onDestroy(() => unsubscribe?.());

  $effect(() => {
    // Re-fetch when filter changes
    filter;
    load();
  });

  async function cancel(t: Task) {
    const a = await getApi();
    if (!a) return;
    try {
      await a.CancelTask(t.id);
      notifications.success(`Cancelled ${t.id}`);
    } catch (err: any) {
      notifications.error(`Cancel failed: ${err?.message ?? err}`);
    }
  }

  async function retry(t: Task) {
    const a = await getApi();
    if (!a) return;
    try {
      await a.RetryTask(t.id);
      notifications.success(`Retrying ${t.id}`);
    } catch (err: any) {
      notifications.error(`Retry failed: ${err?.message ?? err}`);
    }
  }

  function chainName(id: string): string {
    return chains.get(id) ?? id;
  }

  function formatTime(iso: string): string {
    if (!iso) return '';
    try {
      const d = new Date(iso);
      return d.toLocaleString();
    } catch {
      return iso;
    }
  }

  function duration(t: Task): string {
    if (!t.started_at) return '';
    const start = new Date(t.started_at).getTime();
    const end = t.finished_at ? new Date(t.finished_at).getTime() : Date.now();
    const ms = end - start;
    if (ms < 1000) return `${ms}ms`;
    if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
    return `${(ms / 60_000).toFixed(1)}m`;
  }
</script>

<div class="panel" aria-busy={loading}>
  <header class="panel-header">
    <h1><span class="panel-accent">tasks</span></h1>
    <button class="btn-refresh" onclick={load} title="Refresh" aria-label="Refresh">
      <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/></svg>
    </button>
  </header>

  <p class="panel-hint">Queued chain runs. The worker drains pending tasks every couple of seconds.</p>

  <div class="filter-row">
    {#each ['all', 'pending', 'running', 'succeeded', 'failed', 'cancelled'] as opt}
      <button class="filter-pill" class:active={filter === opt} onclick={() => filter = opt as any}>
        {opt}
      </button>
    {/each}
  </div>

  {#if loading}
    <div class="loading">loading...</div>
  {:else if tasks.length === 0}
    <div class="empty">no tasks {filter !== 'all' ? `in ${filter}` : ''}</div>
  {:else}
    <div class="task-list">
      {#each tasks as t (t.id)}
        <div class="task-row" class:expanded={expandedID === t.id}>
          <button class="task-summary" onclick={() => expandedID = expandedID === t.id ? null : t.id}>
            <span class="status-dot status-{t.status}"></span>
            <span class="task-status">{t.status}</span>
            <span class="task-chain">{chainName(t.chain_id)}</span>
            <span class="task-meta">
              {t.trigger}
              {#if t.attempts > 1}· try {t.attempts}/{t.max_attempts}{/if}
              {#if t.started_at}· {duration(t)}{/if}
            </span>
            <span class="task-time">{formatTime(t.started_at || t.enqueued_at)}</span>
          </button>
          {#if expandedID === t.id}
            <div class="task-detail">
              <div class="detail-row">
                <span class="detail-label">id</span>
                <code class="detail-value">{t.id}</code>
              </div>
              {#if t.input}
                <div class="detail-row">
                  <span class="detail-label">input</span>
                  <pre class="detail-pre">{t.input}</pre>
                </div>
              {/if}
              {#if t.cwd}
                <div class="detail-row">
                  <span class="detail-label">cwd</span>
                  <code class="detail-value">{t.cwd}</code>
                </div>
              {/if}
              {#if t.scheduled_for}
                <div class="detail-row">
                  <span class="detail-label">scheduled</span>
                  <span class="detail-value">{formatTime(t.scheduled_for)}</span>
                </div>
              {/if}
              {#if t.last_error}
                <div class="detail-row">
                  <span class="detail-label">error</span>
                  <pre class="detail-pre err">{t.last_error}</pre>
                </div>
              {/if}
              <div class="task-actions">
                {#if t.status === 'pending' || t.status === 'running'}
                  <button class="btn-sm btn-danger" onclick={() => cancel(t)}>cancel</button>
                {:else if t.status === 'failed' || t.status === 'cancelled'}
                  <button class="btn-sm btn-secondary" onclick={() => retry(t)}>retry</button>
                {/if}
              </div>
            </div>
          {/if}
        </div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .panel { padding: var(--panel-padding); }
  .panel-hint {
    font-size: 12px;
    color: var(--color-text-tertiary);
    margin: 0 0 14px;
  }

  .filter-row {
    display: flex; gap: 6px; flex-wrap: wrap;
    margin-bottom: 14px;
  }
  .filter-pill {
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-secondary);
    border-radius: 9999px; padding: 3px 10px;
    color: var(--color-text-tertiary);
    font-size: 11px; cursor: pointer;
    transition: all 0.15s;
  }
  .filter-pill:hover { color: var(--color-text-secondary); }
  .filter-pill.active {
    background: rgba(59, 130, 246, 0.1);
    border-color: rgba(59, 130, 246, 0.3);
    color: var(--color-accent);
  }

  .loading, .empty {
    font-size: 12px;
    color: var(--color-text-tertiary);
    padding: 24px 0;
  }

  .task-list { display: flex; flex-direction: column; gap: 4px; }

  .task-row {
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-md);
    overflow: hidden;
  }

  .task-summary {
    display: grid;
    grid-template-columns: 12px 80px 1fr auto auto;
    align-items: center;
    gap: 10px;
    background: none; border: none;
    color: inherit; cursor: pointer; font-family: inherit;
    width: 100%; text-align: left;
    padding: 8px 12px;
  }
  .task-summary:hover { background: rgba(255, 255, 255, 0.02); }

  .status-dot {
    width: 8px; height: 8px; border-radius: 50%;
    flex-shrink: 0;
  }
  .status-dot.status-pending { background: var(--color-text-tertiary); }
  .status-dot.status-running { background: var(--color-info); box-shadow: 0 0 6px rgba(59, 130, 246, 0.5); }
  .status-dot.status-succeeded { background: var(--color-success); }
  .status-dot.status-failed { background: var(--color-error); }
  .status-dot.status-cancelled { background: var(--color-warning, #d97706); }

  .task-status {
    font-size: 11px;
    color: var(--color-text-secondary);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    font-weight: 600;
  }
  .task-chain {
    font-size: 13px;
    color: var(--color-text-primary);
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .task-meta { font-size: 11px; color: var(--color-text-tertiary); }
  .task-time {
    font-size: 11px; color: var(--color-text-tertiary);
    font-family: var(--font-mono);
  }

  .task-detail {
    border-top: 1px solid var(--color-border-primary);
    padding: 10px 14px;
    background: rgba(0, 0, 0, 0.1);
    display: flex; flex-direction: column; gap: 6px;
  }
  .detail-row {
    display: flex; align-items: baseline; gap: 10px;
    font-size: 11px;
  }
  .detail-label {
    font-size: 9px; font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.04em;
    color: var(--color-text-tertiary);
    min-width: 64px; flex-shrink: 0;
  }
  .detail-value {
    font-family: var(--font-mono);
    color: var(--color-text-secondary);
    word-break: break-all;
  }
  .detail-pre {
    flex: 1;
    background: var(--color-bg-tertiary);
    border-radius: var(--radius-sm);
    padding: 6px 8px;
    font-family: var(--font-mono); font-size: 11px;
    color: var(--color-text-secondary);
    white-space: pre-wrap; word-break: break-word;
    max-height: 200px; overflow-y: auto;
    margin: 0;
  }
  .detail-pre.err { color: var(--color-error); }

  .task-actions { display: flex; gap: 6px; justify-content: flex-end; margin-top: 4px; }
</style>

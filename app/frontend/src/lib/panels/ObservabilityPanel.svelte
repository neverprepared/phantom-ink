<script lang="ts">
  import { getApi } from '../utils/api';
  import { onMount } from 'svelte';
  import EmptyState from '../components/EmptyState.svelte';
  import Badge from '../components/Badge.svelte';


  let langfuseHealth = $state<any>(null);
  let metrics = $state<any[]>([]);
  let traces = $state<any[]>([]);
  let selectedSession = $state('');
  let loading = $state(true);
  let activeTab = $state<'metrics' | 'traces'>('metrics');

  async function refresh() {
    const a = await getApi();
    if (!a) return;
    try {
      const [h, m] = await Promise.all([a.GetLangfuseHealth(), a.GetContainerMetrics()]);
      langfuseHealth = h;
      metrics = m ?? [];
    } catch (err) {
      console.error('Observability refresh failed:', err);
    } finally {
      loading = false;
    }
  }

  async function loadTraces() {
    if (!selectedSession) return;
    const a = await getApi();
    if (!a) return;
    try {
      traces = (await a.GetSessionTraces(selectedSession, 50)) ?? [];
    } catch (err) {
      console.error('Failed to load traces:', err);
    }
  }

  onMount(() => { refresh(); });

  function formatBytes(bytes: number): string {
    if (!bytes) return '–';
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  }
</script>

<div class="panel" aria-busy={loading}>
  <header>
    <h1><span class="accent">observability</span></h1>
    <div class="header-meta">
      {#if langfuseHealth}
        <span class="health-badge" class:ok={langfuseHealth.Status === 'ok' || langfuseHealth.status === 'ok'}>
          langfuse: {langfuseHealth.Status ?? langfuseHealth.status}
        </span>
      {/if}
    </div>
  </header>

  <div class="tabs">
    <button class="tab" class:active={activeTab === 'metrics'} onclick={() => activeTab = 'metrics'}>
      Container Metrics
    </button>
    <button class="tab" class:active={activeTab === 'traces'} onclick={() => { activeTab = 'traces'; }}>
      LangFuse Traces
    </button>
  </div>

  {#if loading}
    <div class="loading">loading...</div>
  {:else if activeTab === 'metrics'}
    {#if metrics.length === 0}
      <EmptyState title="No container metrics" message="Start a session to see metrics." />
    {:else}
      <div class="metrics-list">
        {#each metrics as m (m.Name ?? m.name)}
          <div class="metric-row">
            <span class="metric-name">{m.Name ?? m.name}</span>
            <div class="metric-stats">
              <span class="metric-val">
                <span class="metric-label">CPU</span>
                {(m.CPU ?? m.cpu_percent ?? 0).toFixed(1)}%
              </span>
              <span class="metric-val">
                <span class="metric-label">MEM</span>
                {formatBytes(m.Memory ?? m.memory_bytes ?? 0)}
              </span>
              {#if m.Uptime ?? m.uptime}
                <span class="metric-val">
                  <span class="metric-label">UP</span>
                  {m.Uptime ?? m.uptime}
                </span>
              {/if}
            </div>
          </div>
        {/each}
      </div>
    {/if}
  {:else}
    <div class="trace-controls">
      <input
        type="text"
        bind:value={selectedSession}
        placeholder="Session name..."
      />
      <button class="btn-load" onclick={loadTraces} disabled={!selectedSession}>load traces</button>
    </div>

    {#if traces.length === 0}
      <EmptyState title="No traces" message="Enter a session name and click load traces." />
    {:else}
      <div class="list">
        {#each traces as trace (trace.ID ?? trace.id)}
          {@const name = trace.Name ?? trace.name ?? 'trace'}
          {@const ts = trace.Timestamp ?? trace.timestamp ?? ''}
          <div class="row">
            <div class="row-main">
              <span class="row-name">{name}</span>
              <div class="row-meta">
                <span class="meta-text">{(trace.ID ?? trace.id ?? '').slice(0, 12)}</span>
                <span class="meta-text muted">{ts}</span>
              </div>
            </div>
          </div>
        {/each}
      </div>
    {/if}
  {/if}
</div>

<style>
  .panel { padding-bottom: 24px; }
  header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
  h1 { font-size: 22px; font-weight: 600; }
  .accent { color: var(--color-accent); }

  .health-badge {
    font-size: 11px;
    padding: 3px 8px;
    border-radius: 9999px;
    background: rgba(239, 68, 68, 0.1);
    color: #f87171;
    border: 1px solid rgba(239, 68, 68, 0.2);
  }
  .health-badge.ok {
    background: rgba(16, 185, 129, 0.1);
    color: #6ee7b7;
    border-color: rgba(16, 185, 129, 0.2);
  }

  .tabs {
    display: flex;
    gap: 4px;
    margin-bottom: 20px;
    border-bottom: 1px solid var(--color-border-primary);
    padding-bottom: 2px;
  }
  .tab {
    background: none;
    border: none;
    border-bottom: 2px solid transparent;
    color: var(--color-text-muted);
    padding: 7px 14px;
    font-size: 13px;
    cursor: pointer;
    transition: all 0.15s;
    position: relative;
    bottom: -2px;
  }
  .tab:hover { color: var(--color-text-primary); }
  .tab.active { color: var(--color-text-primary); border-bottom-color: var(--color-info); }

  .metrics-list { display: flex; flex-direction: column; gap: 8px; }

  .metric-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-lg);
    padding: 12px 14px;
  }

  .metric-name {
    font-size: 13px;
    font-weight: 500;
    color: var(--color-text-primary);
    font-family: ui-monospace, Menlo, monospace;
  }

  .metric-stats { display: flex; gap: 20px; }
  .metric-val { font-size: 12px; color: var(--color-text-secondary); }
  .metric-label {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--color-text-tertiary);
    margin-right: 4px;
  }

  .trace-controls {
    display: flex;
    gap: 8px;
    margin-bottom: 16px;
  }
  .trace-controls input { max-width: 280px; }

  .btn-load {
    background: rgba(59, 130, 246, 0.1);
    border: 1px solid rgba(59, 130, 246, 0.3);
    color: var(--color-info);
    padding: 7px 14px;
    border-radius: var(--radius-md);
    font-size: 13px;
    flex-shrink: 0;
    transition: all 0.15s;
  }
  .btn-load:hover { background: rgba(59, 130, 246, 0.2); }

  .list { display: flex; flex-direction: column; gap: 8px; }

  .row {
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-lg);
    padding: 10px 14px;
  }

  .row-main { min-width: 0; }
  .row-name {
    display: block;
    font-size: 13px;
    font-weight: 500;
    color: var(--color-text-primary);
    margin-bottom: 4px;
  }

  .row-meta { display: flex; gap: 10px; }
  .meta-text { font-size: 11px; font-family: ui-monospace, Menlo, monospace; color: var(--color-text-secondary); }
  .meta-text.muted { color: var(--color-text-tertiary); }
</style>

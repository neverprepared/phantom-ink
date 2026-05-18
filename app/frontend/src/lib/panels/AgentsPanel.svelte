<script lang="ts">
  import { onMount } from 'svelte';
  import { getApi } from '../utils/api';
  import { notifications } from '../notifications.svelte';

  interface Agent {
    id: string;
    binary: string;
    label: string;
    path: string;
    version: string;
    enabled: boolean;
    detected: boolean;
    detected_at: string;
  }

  let agents = $state<Agent[]>([]);
  let loading = $state(true);
  let rescanning = $state(false);

  async function load() {
    const a = await getApi();
    if (!a) { loading = false; return; }
    try {
      agents = (await a.ListAgents()) ?? [];
    } catch (err: any) {
      notifications.error(`Failed to load agents: ${err?.message ?? err}`);
    } finally {
      loading = false;
    }
  }

  async function rescan() {
    rescanning = true;
    const a = await getApi();
    if (!a) { rescanning = false; return; }
    try {
      agents = (await a.RescanAgents()) ?? [];
      notifications.success('Agents rescanned');
    } catch (err: any) {
      notifications.error(`Rescan failed: ${err?.message ?? err}`);
    } finally {
      rescanning = false;
    }
  }

  async function toggle(agent: Agent) {
    if (!agent.detected) return;
    const next = !agent.enabled;
    // Optimistic update
    agents = agents.map(a => a.id === agent.id ? { ...a, enabled: next } : a);
    const a = await getApi();
    if (!a) return;
    try {
      await a.SetAgentEnabled(agent.id, next);
    } catch (err: any) {
      // Revert
      agents = agents.map(a => a.id === agent.id ? { ...a, enabled: !next } : a);
      notifications.error(`Failed to toggle ${agent.label}: ${err?.message ?? err}`);
    }
  }

  onMount(load);

  function formatDetectedAt(iso: string): string {
    if (!iso) return '';
    try {
      const d = new Date(iso);
      return d.toLocaleString();
    } catch {
      return iso;
    }
  }
</script>

<div class="panel" aria-busy={loading}>
  <header class="panel-header">
    <h1><span class="panel-accent">agents</span></h1>
    <button class="btn-refresh" onclick={rescan} disabled={rescanning} title="Rescan PATH" aria-label="Rescan PATH">
      <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/></svg>
    </button>
  </header>

  <p class="panel-hint">
    Coding-agent CLIs detected on your PATH. Toggle to enable for use in this app.
  </p>

  {#if loading}
    <div class="loading">scanning...</div>
  {:else if agents.length === 0}
    <div class="empty">
      <p>No agents catalogued yet.</p>
      <button class="btn-primary btn-sm" onclick={rescan} disabled={rescanning}>
        {rescanning ? 'scanning...' : 'Scan now'}
      </button>
    </div>
  {:else}
    <div class="agent-list">
      {#each agents as agent (agent.id)}
        <div class="agent-card" class:undetected={!agent.detected} class:enabled={agent.enabled}>
          <div class="card-top">
            <div class="card-identity">
              <span class="status-dot" class:detected={agent.detected}></span>
              <span class="agent-label">{agent.label}</span>
              <span class="agent-binary">{agent.binary}</span>
            </div>
            {#if agent.detected}
              <label class="toggle-switch" title={agent.enabled ? 'Disable' : 'Enable'}>
                <input type="checkbox" checked={agent.enabled} onchange={() => toggle(agent)} />
                <span class="toggle-track"></span>
              </label>
            {:else}
              <span class="not-installed">not installed</span>
            {/if}
          </div>

          {#if agent.detected}
            <div class="card-detail">
              <div class="detail-row">
                <span class="detail-label">path</span>
                <code class="detail-value">{agent.path}</code>
              </div>
              {#if agent.version}
                <div class="detail-row">
                  <span class="detail-label">version</span>
                  <span class="detail-value">{agent.version}</span>
                </div>
              {/if}
              {#if agent.detected_at}
                <div class="detail-row">
                  <span class="detail-label">scanned</span>
                  <span class="detail-value muted">{formatDetectedAt(agent.detected_at)}</span>
                </div>
              {/if}
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
  .loading, .empty {
    font-size: 12px;
    color: var(--color-text-tertiary);
    padding: 24px 0;
  }
  .empty { display: flex; align-items: center; gap: 12px; }

  .agent-list { display: flex; flex-direction: column; gap: 12px; }

  .agent-card {
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-primary);
    border-left: 3px solid var(--color-border-primary);
    border-radius: var(--radius-xl);
    padding: 14px 18px;
  }
  .agent-card.enabled { border-left-color: var(--color-success); }
  .agent-card.undetected { opacity: 0.55; }

  .card-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .card-identity {
    display: flex; align-items: center; gap: 10px;
  }
  .status-dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: #374151; flex-shrink: 0;
  }
  .status-dot.detected {
    background: var(--color-success);
    box-shadow: 0 0 6px rgba(16, 185, 129, 0.4);
  }
  .agent-label { font-weight: 500; font-size: 14px; color: var(--color-text-primary); }
  .agent-binary {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--color-text-tertiary);
    background: rgba(255, 255, 255, 0.04);
    padding: 1px 6px;
    border-radius: var(--radius-sm);
  }
  .not-installed {
    font-size: 10px;
    color: var(--color-text-tertiary);
    font-style: italic;
  }

  .card-detail {
    margin-top: 10px;
    padding-top: 10px;
    border-top: 1px solid var(--color-border-primary);
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .detail-row {
    display: flex;
    align-items: baseline;
    gap: 10px;
    font-size: 11px;
  }
  .detail-label {
    font-size: 9px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-tertiary);
    min-width: 56px;
    flex-shrink: 0;
  }
  .detail-value {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--color-text-secondary);
    word-break: break-all;
  }
  .detail-value.muted { color: var(--color-text-tertiary); }

  /* Toggle switch — copied from ServicesPanel pattern */
  .toggle-switch { position: relative; display: inline-flex; cursor: pointer; }
  .toggle-switch input { opacity: 0; width: 0; height: 0; position: absolute; }
  .toggle-track {
    width: 32px; height: 18px;
    background: var(--color-bg-tertiary);
    border: 1px solid var(--color-border-secondary);
    border-radius: 9999px; position: relative; transition: all 0.2s;
  }
  .toggle-track::after {
    content: ''; width: 12px; height: 12px;
    background: var(--color-text-tertiary); border-radius: 50%;
    position: absolute; top: 2px; left: 2px; transition: all 0.2s;
  }
  .toggle-switch input:checked + .toggle-track {
    background: rgba(16, 185, 129, 0.2); border-color: rgba(16, 185, 129, 0.4);
  }
  .toggle-switch input:checked + .toggle-track::after {
    background: var(--color-success); left: 16px;
  }
</style>

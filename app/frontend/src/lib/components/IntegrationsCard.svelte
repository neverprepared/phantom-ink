<script lang="ts">
  // ADR-003 integrations: place an operator-managed compose stack (kroki, …) on
  // a chosen fleet node. The router places it via the runner; on placement the
  // app wires the endpoint into the active profile's host MCP config.
  import { getApi } from '../utils/api';
  import { notifications } from '../notifications.svelte';
  import type { brainbox } from '../../../wailsjs/go/models';

  let expanded = $state(false);
  let loading = $state(false);
  let integrations = $state<brainbox.Integration[]>([]);
  let nodes = $state<brainbox.IntegrationNode[]>([]);
  let selectedNode = $state<Record<string, string>>({});
  let busy = $state(''); // integration name currently being placed/stopped

  let placedCount = $derived(
    integrations.filter((i) => i.placement?.desired === 'on').length,
  );

  async function load() {
    loading = true;
    const a = await getApi();
    if (!a) { loading = false; return; }
    try {
      const res = await a.ListIntegrations();
      integrations = res?.integrations ?? [];
      nodes = res?.nodes ?? [];
      const sel = { ...selectedNode };
      for (const i of integrations) {
        if (!sel[i.name]) sel[i.name] = i.placement?.node ?? nodes[0]?.name ?? '';
      }
      selectedNode = sel;
    } catch (e: any) {
      notifications.error(`Failed to load integrations: ${e?.message ?? e}`);
      integrations = [];
    } finally {
      loading = false;
    }
  }

  function toggle() {
    expanded = !expanded;
    if (expanded && integrations.length === 0) void load();
  }

  function copy(text: string) {
    (window as any).runtime?.ClipboardSetText(text);
    notifications.success(`Copied ${text}`);
  }

  async function place(name: string, desired: 'on' | 'off') {
    const node = selectedNode[name];
    if (!node) { notifications.error('select a node first'); return; }
    busy = name;
    const a = await getApi();
    if (!a) { busy = ''; return; }
    try {
      const res = await a.PlaceIntegration(name, node, desired);
      if (desired === 'on') {
        let msg = `${name} placed on ${node}`;
        if (res.endpoint) msg += ` → ${res.endpoint}`;
        if (res.wired?.length) msg += ` · wired ${res.wired.length} config${res.wired.length > 1 ? 's' : ''}`;
        notifications.success(msg);
        if (res.wire_err) notifications.error(`endpoint wiring: ${res.wire_err}`);
      } else {
        notifications.success(`${name} stopped on ${node}`);
      }
      await load();
    } catch (e: any) {
      notifications.error(`${desired === 'on' ? 'place' : 'stop'} ${name} failed: ${e?.message ?? e}`);
    } finally {
      busy = '';
    }
  }
</script>

<div class="service-card int-card">
  <div class="card-top">
    <button class="card-identity" onclick={toggle}>
      <svg class="expand-chevron" class:expanded xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg>
      <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m7.5 4.27 9 5.15"/><path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="m3.3 7 8.7 5 8.7-5"/><path d="M12 22V12"/></svg>
      <span class="svc-name">On-demand integrations</span>
      {#if expanded && integrations.length}<span class="svc-status">{placedCount}/{integrations.length} placed</span>{/if}
    </button>
    {#if expanded}
      <button class="btn ghost sm" onclick={load} disabled={loading}>refresh</button>
    {/if}
  </div>

  {#if expanded}
    <div class="int-body">
      <p class="int-lead">Run a service stack on a chosen fleet node; the endpoint is wired into this profile's MCP config.</p>
      {#if loading && integrations.length === 0}
        <div class="int-note">loading…</div>
      {:else if integrations.length === 0}
        <div class="int-note">No integrations in the catalog.</div>
      {:else if nodes.length === 0}
        <div class="int-note err">No docker-capable fleet nodes registered — a runner must advertise the <code>docker</code> capability.</div>
      {:else}
        {#each integrations as i (i.name)}
          {@const placed = i.placement?.desired === 'on'}
          {@const working = busy === i.name}
          <div class="int-item">
            <div class="int-row">
              <span class="int-dot {placed ? 'up' : 'down'}"></span>
              <span class="int-name">{i.name}</span>
              <span class="int-tag">{i.capability}</span>
              <span class="int-state">
                {#if i.placement}{i.placement.desired} · {i.placement.node}{:else}not placed{/if}
              </span>
              <div class="int-actions">
                <select class="int-select" bind:value={selectedNode[i.name]} disabled={busy !== ''} aria-label="target node for {i.name}">
                  {#each nodes as n (n.name)}<option value={n.name}>{n.name}</option>{/each}
                </select>
                {#if placed}
                  <button class="btn ghost sm" disabled={busy !== ''} onclick={() => place(i.name, 'off')}>{working ? '…' : 'stop'}</button>
                {:else}
                  <button class="btn sm" disabled={busy !== '' || !selectedNode[i.name]} onclick={() => place(i.name, 'on')}>{working ? '…' : 'place'}</button>
                {/if}
              </div>
            </div>
            <div class="int-desc">{i.description}</div>
            {#if placed && i.endpoint}
              <div class="int-endpoint">endpoint <button class="int-addr" title="Click to copy" onclick={() => copy(i.endpoint)}>{i.endpoint}</button></div>
            {/if}
            {#if working}<div class="int-hint">placing may take a minute on first run (pulls images)…</div>{/if}
          </div>
        {/each}
      {/if}
    </div>
  {/if}
</div>

<style>
  .int-card {
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-primary);
    border-left: 3px solid var(--color-accent, var(--color-info));
    border-radius: var(--radius-xl);
    padding: 14px 18px;
    margin-bottom: 20px;
  }
  .card-top { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }
  .card-identity {
    display: flex; align-items: center; gap: 8px;
    background: none; border: none; padding: 0; cursor: pointer;
    color: inherit; font-family: inherit; text-align: left;
  }
  .expand-chevron { color: var(--color-text-tertiary); transition: transform 0.15s; flex-shrink: 0; }
  .expand-chevron.expanded { transform: rotate(90deg); }
  .svc-name { font-weight: 500; font-size: 14px; color: var(--color-text-primary); }
  .svc-status { font-size: 11px; color: var(--color-text-tertiary); }

  .int-body { display: flex; flex-direction: column; margin-top: 10px; }
  .int-lead { font-size: 11px; color: var(--color-text-tertiary); margin: 0 0 8px; }
  .int-note { font-size: 12px; color: var(--color-text-tertiary); padding: 4px 2px; }
  .int-note.err { color: var(--color-error); }
  .int-note code { font-family: var(--font-mono); font-size: 11px; }

  .int-item { padding: 8px 2px; border-top: 1px solid var(--color-border-primary); }
  .int-row { display: flex; align-items: center; gap: 10px; }
  .int-dot { width: 8px; height: 8px; border-radius: 50%; flex: none; background: var(--color-text-tertiary); }
  .int-dot.up { background: var(--color-success); box-shadow: 0 0 6px rgba(16,185,129,0.4); }
  .int-dot.down { background: var(--color-text-tertiary); }
  .int-name { font-family: var(--font-mono); font-size: 12px; color: var(--color-text-primary); }
  .int-tag {
    font-family: var(--font-mono); font-size: 10px; color: var(--color-text-tertiary);
    border: 1px solid var(--color-border-primary); border-radius: 3px; padding: 0 4px;
  }
  .int-state { font-size: 11px; color: var(--color-text-tertiary); }
  .int-actions { margin-left: auto; display: flex; gap: 6px; align-items: center; }
  .int-select {
    font-size: 11px; padding: 2px 6px; font-family: var(--font-mono);
    background: var(--color-bg-tertiary); color: var(--color-text-primary);
    border: 1px solid var(--color-border-secondary); border-radius: var(--radius-sm);
  }
  .int-desc { font-size: 11px; color: var(--color-text-tertiary); margin: 4px 0 0 18px; }
  .int-endpoint { font-size: 11px; color: var(--color-text-tertiary); margin: 4px 0 0 18px; }
  .int-addr {
    font-family: var(--font-mono); font-size: 10px; color: var(--color-text-secondary);
    background: transparent; border: 1px solid var(--color-border-primary);
    border-radius: 3px; padding: 0 5px; cursor: pointer;
  }
  .int-addr:hover { color: var(--color-text-primary); border-color: var(--color-text-tertiary); }
  .int-hint { font-size: 10px; color: var(--color-text-tertiary); font-style: italic; margin: 4px 0 0 18px; }
</style>

<script lang="ts">
  import { getApi, openInBrowser } from '../utils/api';
  import { notifications } from '../notifications.svelte';
  import { onDestroy } from 'svelte';

  interface Service {
    name: string; state: string; status: string; health: string; one_shot: boolean; web_url?: string; addr?: string;
  }

  function copyAddr(addr: string) {
    (window as any).runtime?.ClipboardSetText(addr);
    notifications.success(`Copied ${addr}`);
  }
  interface External {
    name: string; label: string; endpoint: string; healthy: boolean; note: string;
  }

  let expanded = $state(false);
  let services = $state<Service[]>([]);
  let externals = $state<External[]>([]);
  let loading = $state(false);
  let error = $state<string | null>(null);
  let busy = $state<string | null>(null);
  let poll: number | null = null;

  let upCount = $derived(services.filter((s) => s.state === 'running').length);

  async function load(silent = false) {
    if (!silent) loading = true;
    error = null;
    try {
      const a = await getApi();
      if (!a) return;
      services = (await a.ListPlatformServices()) ?? [];
      externals = (await a.ListPlatformExternals()) ?? [];
    } catch (e: any) {
      error = e?.message ?? String(e);
      services = [];
    } finally {
      loading = false;
    }
  }

  function toggle() {
    expanded = !expanded;
    if (expanded) {
      void load();
      poll = window.setInterval(() => void load(true), 5000);  // live health while open
    } else if (poll != null) {
      clearInterval(poll); poll = null;
    }
  }
  onDestroy(() => { if (poll != null) clearInterval(poll); });

  async function act(name: string, action: 'Start' | 'Stop' | 'Restart') {
    busy = name;
    try {
      const a = await getApi();
      if (!a) return;
      if (action === 'Start') await a.StartPlatformService(name);
      else if (action === 'Stop') await a.StopPlatformService(name);
      else await a.RestartPlatformService(name);
      notifications.success(`${action.toLowerCase()}ed ${name}`);
      await load(true);
    } catch (e: any) {
      notifications.error(`${action} ${name} failed: ${e?.message ?? e}`);
    } finally {
      busy = null;
    }
  }

  async function restartAll() {
    busy = '*';
    try {
      const a = await getApi();
      if (!a) return;
      await a.RestartAllPlatformServices();
      notifications.success('restarted all platform services');
      await load(true);
    } catch (e: any) {
      notifications.error(`Restart all failed: ${e?.message ?? e}`);
    } finally {
      busy = null;
    }
  }

  function dot(s: Service): string {
    if (s.state !== 'running') return s.one_shot ? 'done' : 'down';
    if (s.health === 'unhealthy') return 'down';
    if (s.health === 'starting') return 'starting';
    return 'up';
  }
</script>

<div class="service-card ps-card">
  <div class="card-top">
    <button class="card-identity" onclick={toggle}>
      <svg class="expand-chevron" class:expanded xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg>
      <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="2" width="20" height="8" rx="2"/><rect x="2" y="14" width="20" height="8" rx="2"/><line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/></svg>
      <span class="svc-name">Platform services</span>
      {#if expanded && services.length}<span class="svc-status">{upCount}/{services.length} up</span>{/if}
    </button>
    {#if expanded}
      <button class="btn ghost sm" onclick={() => load()} disabled={loading}>refresh</button>
    {/if}
  </div>

  {#if expanded}
    <div class="ps-body">
      {#if loading && services.length === 0}
        <div class="ps-note">loading…</div>
      {:else if error}
        <div class="ps-note err">{error}</div>
      {:else if services.length === 0}
        <div class="ps-note">No platform services found — is the stack running?</div>
      {:else}
        {#each services as s (s.name)}
          <div class="ps-row">
            <span class="ps-dot {dot(s)}" title={s.status}></span>
            <span class="ps-name">{s.name}</span>
            {#if s.one_shot}<span class="ps-tag">init</span>{/if}
            {#if s.addr}<button class="ps-addr" title="Click to copy" onclick={() => copyAddr(s.addr!)}>{s.addr}</button>{/if}
            <span class="ps-state">{s.status || s.state}</span>
            <div class="ps-actions">
              {#if s.web_url && s.state === 'running'}
                <button class="btn ghost sm" onclick={() => openInBrowser(s.web_url!)} title="Open web UI in browser">open ↗</button>
              {/if}
              {#if s.state === 'running'}
                <button class="btn ghost sm" disabled={busy !== null} onclick={() => act(s.name, 'Restart')}>{busy === s.name ? '…' : 'restart'}</button>
                <button class="btn ghost sm" disabled={busy !== null} onclick={() => act(s.name, 'Stop')}>stop</button>
              {:else}
                <button class="btn sm" disabled={busy !== null} onclick={() => act(s.name, 'Start')}>{busy === s.name ? '…' : 'start'}</button>
              {/if}
            </div>
          </div>
        {/each}
      {/if}

      {#if externals.length}
        <div class="ps-sub">external (host / not compose-managed)</div>
        {#each externals as e (e.name)}
          <div class="ps-row">
            <span class="ps-dot {e.healthy ? 'up' : 'down'}" title={e.endpoint}></span>
            <span class="ps-name">{e.label}</span>
            <span class="ps-tag">{e.note}</span>
            <span class="ps-state">{e.endpoint} · {e.healthy ? 'reachable' : 'unreachable'}</span>
          </div>
        {/each}
      {/if}

      <div class="ps-footer">
        <span class="ps-hint">restarting <code>router</code> briefly drops the app's own connection.</span>
        <button class="btn ghost sm" disabled={busy !== null || services.length === 0} onclick={restartAll}>restart all</button>
      </div>
    </div>
  {/if}
</div>

<style>
  .ps-card {
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-primary);
    border-left: 3px solid var(--color-info);
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

  .ps-body { display: flex; flex-direction: column; margin-top: 10px; }
  .ps-row {
    display: flex; align-items: center; gap: 10px;
    padding: 7px 2px; border-top: 1px solid var(--color-border-primary);
  }
  .ps-dot { width: 8px; height: 8px; border-radius: 50%; flex: none; background: var(--color-text-tertiary); }
  .ps-dot.up { background: var(--color-success); box-shadow: 0 0 6px rgba(16,185,129,0.4); }
  .ps-dot.starting { background: var(--color-warning, #d29922); }
  .ps-dot.down { background: var(--color-error); }
  .ps-dot.done { background: var(--color-text-tertiary); }
  .ps-name { font-family: var(--font-mono); font-size: 12px; color: var(--color-text-primary); }
  .ps-tag {
    font-family: var(--font-mono); font-size: 10px; color: var(--color-text-tertiary);
    border: 1px solid var(--color-border-primary); border-radius: 3px; padding: 0 4px;
  }
  .ps-addr {
    font-family: var(--font-mono); font-size: 10px; color: var(--color-text-secondary);
    background: transparent; border: 1px solid var(--color-border-primary);
    border-radius: 3px; padding: 0 5px; cursor: pointer;
  }
  .ps-addr:hover { color: var(--color-text-primary); border-color: var(--color-text-tertiary); }
  .ps-state { font-size: 11px; color: var(--color-text-tertiary); margin-left: 2px; }
  .ps-actions { margin-left: auto; display: flex; gap: 6px; }
  .ps-sub {
    font-size: 10px; text-transform: uppercase; letter-spacing: 0.04em;
    color: var(--color-text-tertiary); margin-top: 12px; padding: 4px 2px 0;
    border-top: 1px solid var(--color-border-primary);
  }
  .ps-note { font-size: 12px; color: var(--color-text-tertiary); padding: 4px 2px; }
  .ps-note.err { color: var(--color-error); white-space: pre-wrap; }
  .ps-footer {
    display: flex; align-items: center; gap: 10px; margin-top: 10px;
    padding-top: 8px; border-top: 1px solid var(--color-border-primary);
  }
  .ps-hint { font-size: 11px; color: var(--color-text-tertiary); flex: 1; }
</style>

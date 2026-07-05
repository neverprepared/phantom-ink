<script lang="ts">
  import { getApi } from '../utils/api';
  import { onMount } from 'svelte';
  import { notifications } from '../notifications.svelte';
  import EmptyState from '../components/EmptyState.svelte';

  // MCP gateway server registry (ADR-002 #152). Lists every catalog server with
  // a live enable/disable toggle. Definitions stay in mcp-catalog.json; this
  // toggles which are exposed through the gateway (DB-backed, no restart).

  type Server = { name: string; command: string; enabled: boolean };
  let servers = $state<Server[]>([]);
  let loading = $state(true);
  let toggling = $state<Record<string, boolean>>({});

  let enabledCount = $derived(servers.filter((s) => s.enabled).length);

  onMount(load);

  async function load() {
    loading = true;
    const a = await getApi();
    if (!a) { loading = false; return; }
    try {
      servers = (await a.ListGatewayServers()) ?? [];
    } catch (err: any) {
      notifications.error(`Failed to load gateway servers: ${err?.message ?? err}`);
    } finally {
      loading = false;
    }
  }

  async function toggle(s: Server) {
    const next = !s.enabled;
    toggling[s.name] = true;
    const a = await getApi();
    if (!a) { toggling[s.name] = false; return; }
    try {
      await a.SetGatewayServerEnabled(s.name, next);
      s.enabled = next; // local update; servers is $state so this is reactive
      notifications.success(`${s.name} ${next ? 'enabled' : 'disabled'}`);
    } catch (err: any) {
      notifications.error(`Failed to toggle ${s.name}: ${err?.message ?? err}`);
    } finally {
      toggling[s.name] = false;
    }
  }
</script>

<div class="gateway-panel">
  <header class="panel-header">
    <div>
      <h1>Gateway</h1>
      <p class="subtitle">MCP servers exposed through the shared gateway — toggles apply live to every profile.</p>
    </div>
    <div class="header-actions">
      <span class="count">{enabledCount}/{servers.length} enabled</span>
      <button class="btn" onclick={load} disabled={loading}>refresh</button>
    </div>
  </header>

  {#if loading}
    <p class="hint">loading…</p>
  {:else if servers.length === 0}
    <EmptyState
      title="No catalog servers"
      message="The gateway has no catalog configured. Set CL_GATEWAY__CATALOG_PATH (and CL_GATEWAY__SECRET_KEY) on the brainbox host."
    />
  {:else}
    <ul class="server-list">
      {#each servers as s (s.name)}
        <li class="server-row" class:enabled={s.enabled}>
          <button
            class="toggle"
            class:on={s.enabled}
            onclick={() => toggle(s)}
            disabled={toggling[s.name]}
            role="switch"
            aria-checked={s.enabled}
            aria-label="Toggle {s.name}"
          >
            <span class="knob"></span>
          </button>
          <div class="server-meta">
            <span class="server-name">{s.name}</span>
            <span class="server-cmd">{s.command}</span>
          </div>
          <span class="state">{s.enabled ? 'on' : 'off'}</span>
        </li>
      {/each}
    </ul>
    <p class="hint">
      Definitions live in <code>mcp-catalog.json</code>; this list controls only which are enabled.
      Per-profile credentials are set in <strong>Profiles</strong>.
    </p>
  {/if}
</div>

<style>
  .gateway-panel { padding: 1.25rem 1.5rem; max-width: 760px; }
  .panel-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem; margin-bottom: 1rem; }
  h1 { font-size: 1.1rem; margin: 0 0 0.2rem; }
  .subtitle { font-size: 0.75rem; color: var(--text-muted); margin: 0; max-width: 48ch; }
  .header-actions { display: flex; align-items: center; gap: 0.6rem; white-space: nowrap; }
  .count { font-size: 0.72rem; color: var(--text-muted); }
  .btn {
    background: var(--bg-sunken); border: 1px solid var(--border);
    border-radius: 5px; color: var(--text-muted); cursor: pointer; font-size: 0.72rem; padding: 0.25rem 0.6rem;
  }
  .btn:hover:not(:disabled) { color: var(--text); }
  .hint { font-size: 0.7rem; color: var(--text-muted); margin-top: 0.8rem; }
  .server-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.3rem; }
  .server-row {
    display: flex; align-items: center; gap: 0.75rem;
    padding: 0.55rem 0.7rem; border: 1px solid var(--border);
    border-radius: 7px; background: var(--bg-sunken);
  }
  .server-row.enabled { border-color: var(--accent, var(--border)); }
  .server-meta { display: flex; flex-direction: column; gap: 0.1rem; flex: 1; min-width: 0; }
  .server-name { font-size: 0.82rem; font-weight: 500; }
  .server-cmd { font-size: 0.68rem; color: var(--text-muted); font-family: var(--font-mono, monospace); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .state { font-size: 0.68rem; color: var(--text-muted); width: 2rem; text-align: right; }
  .toggle {
    flex: none; width: 36px; height: 20px; border-radius: 999px;
    border: 1px solid var(--border); background: var(--bg-elev);
    position: relative; cursor: pointer; padding: 0; transition: background 0.15s, border-color 0.15s;
  }
  .toggle.on { background: var(--accent, #4a9); border-color: var(--accent, #4a9); }
  .toggle:disabled { opacity: 0.5; cursor: default; }
  .knob {
    position: absolute; top: 1px; left: 1px; width: 16px; height: 16px;
    border-radius: 50%; background: #fff; transition: transform 0.15s;
  }
  .toggle.on .knob { transform: translateX(16px); }
</style>

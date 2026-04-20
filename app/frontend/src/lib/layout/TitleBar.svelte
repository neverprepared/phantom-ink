<script lang="ts">
  import { connectionState, commandPalette, profileState } from '../stores.svelte';

  import { notifications } from '../notifications.svelte';

  let connected = $derived(connectionState.connected);
  let profiles = $derived(profileState.profiles);
  let activeProfile = $derived(profileState.active);
  let restarting = $state(false);

  async function restartAPI() {
    let api: any;
    try { api = await import('../../../wailsjs/go/main/App'); } catch { return; }
    restarting = true;
    try {
      await api.RestartBrainboxAPI();
      notifications.success('API restarted');
    } catch (err: any) {
      notifications.error(`Restart failed: ${err}`);
    } finally {
      restarting = false;
    }
  }

  async function selectProfile(name: string | null) {
    let api: any;
    try { api = await import('../../../wailsjs/go/main/App'); } catch { return; }
    try {
      await api.SetActiveProfile(name ?? '');
      if (name) {
        const ap = await api.GetActiveProfile();
        profileState.active = ap?.name ? ap : null;
      } else {
        profileState.active = null;
      }
    } catch (err: any) {
      console.error('Failed to select profile:', err);
    }
  }

  async function refreshProfiles() {
    let api: any;
    try { api = await import('../../../wailsjs/go/main/App'); } catch { return; }
    try {
      const scanned = await api.ScanProfiles();
      profileState.profiles = scanned ?? [];
      const ap = await api.GetActiveProfile();
      profileState.active = ap?.name ? ap : null;
    } catch (err: any) {
      console.error('Failed to refresh profiles:', err);
    }
  }
</script>

<div class="titlebar">
  <div class="titlebar-left">
    <span class="brand">PhantomInk</span>
  </div>

  {#if profiles.length > 0}
    <div class="profile-tabs">
      <button
        class="tab"
        class:active={!activeProfile}
        onclick={() => selectProfile(null)}
      >all</button>
      {#each profiles as p (p.name)}
        <button
          class="tab"
          class:active={activeProfile?.name === p.name}
          class:no-secrets={p.secrets_mode === 'none'}
          onclick={() => selectProfile(p.name)}
          title={p.secrets_mode === 'none' ? `${p.path} (no secrets configured)` : `${p.path} (${p.secrets_mode})`}
        >{p.name}</button>
      {/each}
      <button class="tab-refresh" onclick={refreshProfiles} title="Refresh profiles" aria-label="Refresh profiles">
        <svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/></svg>
      </button>
    </div>
  {/if}

  <div class="titlebar-right">
    <button class="restart-btn" onclick={restartAPI} disabled={restarting} title="Restart brainbox API" aria-label="Restart API">
      <svg class:spinning={restarting} xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/></svg>
    </button>
    <span class="conn-status" class:connected>
      <span class="conn-dot"></span>
      {connected ? 'connected' : 'disconnected'}
    </span>
    <button class="palette-btn" onclick={() => commandPalette.toggle()} title="Command palette (⌘K)" aria-label="Command palette">
      <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
      <span>⌘K</span>
    </button>
  </div>
</div>

<style>
  .titlebar {
    height: var(--titlebar-height);
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 0 var(--titlebar-pad-right) 0 var(--titlebar-pad-left);
    background: var(--color-bg-secondary);
    border-bottom: 1px solid var(--color-border-primary);
    flex-shrink: 0;
    --wails-draggable: drag;
  }

  .titlebar-left {
    display: flex;
    align-items: center;
    flex-shrink: 0;
    --wails-draggable: no-drag;
  }

  .brand {
    font-size: 13px;
    font-weight: 600;
    color: var(--color-accent);
    letter-spacing: 0.02em;
  }

  /* Profile tabs */
  .profile-tabs {
    display: flex;
    align-items: center;
    gap: 2px;
    background: var(--color-surface-subtle);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-lg);
    padding: 2px;
    flex-shrink: 1;
    min-width: 0;
    overflow-x: auto;
    --wails-draggable: no-drag;
  }

  /* Hide scrollbar but allow scroll */
  .profile-tabs::-webkit-scrollbar { display: none; }

  .tab {
    background: transparent;
    border: none;
    border-radius: var(--radius-md);
    color: var(--color-text-tertiary);
    font-size: 11px;
    font-weight: 500;
    padding: 3px 10px;
    white-space: nowrap;
    transition: all 0.15s;
    flex-shrink: 0;
  }

  .tab:hover {
    color: var(--color-text-secondary);
    background: var(--color-surface-hover);
  }

  .tab.active {
    background: var(--color-accent-soft);
    color: var(--color-accent);
    font-weight: 600;
  }

  .tab.no-secrets {
    opacity: 0.5;
  }
  .tab.no-secrets.active {
    opacity: 0.8;
  }

  .tab-refresh {
    background: transparent;
    border: none;
    color: var(--color-text-tertiary);
    padding: 3px 4px;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: var(--radius-sm);
    flex-shrink: 0;
    transition: all 0.15s;
  }
  .tab-refresh:hover {
    color: var(--color-text-secondary);
    background: var(--color-surface-hover);
  }

  .titlebar-right {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-left: auto;
    flex-shrink: 0;
    --wails-draggable: no-drag;
  }

  .conn-status {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    color: var(--color-text-tertiary);
  }

  .conn-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--color-dot-offline);
    flex-shrink: 0;
  }

  .conn-status.connected .conn-dot {
    background: var(--color-success);
    box-shadow: var(--shadow-status-active);
  }

  .conn-status.connected {
    color: var(--color-text-secondary);
  }

  .palette-btn {
    display: flex;
    align-items: center;
    gap: 6px;
    background: var(--color-surface-hover);
    border: 1px solid var(--color-border-secondary);
    border-radius: var(--radius-md);
    color: var(--color-text-tertiary);
    padding: 4px 8px;
    font-size: 11px;
    cursor: pointer;
    transition: all 0.15s;
  }

  .palette-btn:hover {
    background: var(--color-surface-active);
    color: var(--color-text-secondary);
  }

  .restart-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    background: transparent;
    border: none;
    color: var(--color-text-tertiary);
    padding: 4px;
    border-radius: var(--radius-sm);
    cursor: pointer;
    transition: all 0.15s;
  }
  .restart-btn:hover {
    color: var(--color-text-secondary);
    background: var(--color-surface-hover);
  }
  .restart-btn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
  .restart-btn svg.spinning {
    animation: spin 1s linear infinite;
  }
  @keyframes spin {
    from { transform: rotate(0deg); }
    to { transform: rotate(360deg); }
  }
</style>

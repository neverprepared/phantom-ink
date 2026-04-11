<script lang="ts">
  import { connectionState, commandPalette, profileState } from '../stores.svelte';

  let connected = $derived(connectionState.connected);
  let profiles = $derived(profileState.profiles);
  let activeProfile = $derived(profileState.active);

  async function selectProfile(name: string | null) {
    let api: any;
    try { api = await import('../../../wailsjs/go/main/App'); } catch { return; }
    await api.SetActiveProfile(name ?? '');
    if (name) {
      const ap = await api.GetActiveProfile();
      profileState.active = ap?.name ? ap : null;
    } else {
      profileState.active = null;
    }
  }

  async function refreshProfiles() {
    let api: any;
    try { api = await import('../../../wailsjs/go/main/App'); } catch { return; }
    const scanned = await api.ScanProfiles();
    profileState.profiles = scanned ?? [];
    const ap = await api.GetActiveProfile();
    profileState.active = ap?.name ? ap : null;
  }
</script>

<div class="titlebar">
  <div class="titlebar-left">
    <span class="brand">phantom-ink</span>
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
      <button class="tab-refresh" onclick={refreshProfiles} title="Refresh profiles">
        <svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/></svg>
      </button>
    </div>
  {/if}

  <div class="titlebar-right">
    <span class="conn-status" class:connected>
      <span class="conn-dot"></span>
      {connected ? 'connected' : 'disconnected'}
    </span>
    <button class="palette-btn" onclick={() => commandPalette.toggle()} title="Command palette (⌘K)">
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
    background: rgba(255, 255, 255, 0.03);
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
    background: rgba(255, 255, 255, 0.05);
  }

  .tab.active {
    background: rgba(245, 158, 11, 0.12);
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
    background: rgba(255, 255, 255, 0.05);
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
    background: #374151;
    flex-shrink: 0;
  }

  .conn-status.connected .conn-dot {
    background: var(--color-success);
    box-shadow: 0 0 4px rgba(16, 185, 129, 0.4);
  }

  .conn-status.connected {
    color: var(--color-text-secondary);
  }

  .palette-btn {
    display: flex;
    align-items: center;
    gap: 6px;
    background: rgba(255, 255, 255, 0.05);
    border: 1px solid var(--color-border-secondary);
    border-radius: var(--radius-md);
    color: var(--color-text-tertiary);
    padding: 4px 8px;
    font-size: 11px;
    cursor: pointer;
    transition: all 0.15s;
  }

  .palette-btn:hover {
    background: rgba(255, 255, 255, 0.08);
    color: var(--color-text-secondary);
  }
</style>

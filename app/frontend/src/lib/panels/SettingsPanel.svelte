<script lang="ts">
  import { getApi } from '../utils/api';
  import { onMount } from 'svelte';
  import { notifications } from '../notifications.svelte';
  import { profileState, type Profile } from '../stores.svelte';


  // --- Connection settings ---
  let baseURL = $state('http://127.0.0.1:9999');
  let apiKey = $state('');

  // --- Workspace settings ---
  let workspacesRoot = $state('');
  let profiles = $derived(profileState.profiles);
  let activeProfile = $derived(profileState.active);
  let scanning = $state(false);

  // --- Create profile ---
  let showCreateProfile = $state(false);
  let newProfileName = $state('');
  let creating = $state(false);

  // --- Delete / backup / restore ---
  let confirmDelete = $state<string | null>(null);
  let backups = $state<string[]>([]);

  // --- General ---
  let platform = $state('');
  let saving = $state(false);
  let loaded = $state(false);
  let theme = $state('dark');

  onMount(async () => {
    const a = await getApi();
    if (!a) return;
    try {
      const [cfg, plat] = await Promise.all([a.GetConfig(), a.GetPlatform()]);
      baseURL = cfg.base_url ?? 'http://127.0.0.1:9999';
      apiKey = cfg.api_key ?? '';
      workspacesRoot = cfg.workspaces_root ?? '';
      theme = cfg.theme ?? 'dark';
      platform = plat ?? 'unknown';
      await Promise.all([refreshProfiles(), loadBackups()]);
    } catch (err) {
      console.error('Failed to load config:', err);
    } finally {
      loaded = true;
    }
  });

  async function refreshProfiles() {
    scanning = true;
    const a = await getApi();
    if (!a) { scanning = false; return; }
    try {
      const scanned: Profile[] = await a.ScanProfiles();
      profileState.profiles = scanned ?? [];
      const ap = await a.GetActiveProfile();
      profileState.active = ap?.name ? ap : null;
    } catch (err) {
      console.error('Failed to scan profiles:', err);
    } finally {
      scanning = false;
    }
  }

  async function selectProfile(name: string) {
    const a = await getApi();
    if (!a) return;
    try {
      await a.SetActiveProfile(name);
      const ap = await a.GetActiveProfile();
      profileState.active = ap?.name ? ap : null;
      notifications.success(`Switched to ${name}`);
    } catch (err: any) {
      notifications.error(`Failed to switch profile: ${err}`);
    }
  }

  async function clearProfile() {
    const a = await getApi();
    if (!a) return;
    try {
      await a.SetActiveProfile('');
      profileState.active = null;
      notifications.success('Profile cleared — showing all');
    } catch (err: any) {
      notifications.error(`Failed to clear profile: ${err}`);
    }
  }

  async function handleCreateProfile() {
    if (!newProfileName.trim()) return;
    creating = true;
    const a = await getApi();
    if (!a) { creating = false; return; }
    try {
      const p = await a.CreateProfile(newProfileName.trim());
      notifications.success(`Profile "${p.name}" created — activate it in a terminal with cd ${p.path}`);
      newProfileName = '';
      showCreateProfile = false;
      await refreshProfiles();
    } catch (err: any) {
      notifications.error(`Failed to create profile: ${err}`);
    } finally {
      creating = false;
    }
  }

  async function loadBackups() {
    const a = await getApi();
    if (!a) return;
    try { backups = (await a.ListBackups()) ?? []; } catch { backups = []; }
  }

  async function handleDelete(name: string, backup: boolean) {
    const a = await getApi();
    if (!a) return;
    try {
      await a.DeleteProfile(name, backup);
      confirmDelete = null;
      notifications.success(backup ? `"${name}" backed up and removed` : `"${name}" permanently deleted`);
      await refreshProfiles();
      await loadBackups();
    } catch (err: any) {
      notifications.error(`Failed to delete: ${err}`);
    }
  }

  async function handleRestore(name: string) {
    const a = await getApi();
    if (!a) return;
    try {
      await a.RestoreProfile(name);
      notifications.success(`"${name}" restored`);
      await refreshProfiles();
      await loadBackups();
    } catch (err: any) {
      notifications.error(`Failed to restore: ${err}`);
    }
  }

  async function handlePurge(name: string) {
    const a = await getApi();
    if (!a) return;
    try {
      await a.PurgeBackup(name);
      notifications.success(`Backup "${name}" permanently deleted`);
      await loadBackups();
    } catch (err: any) {
      notifications.error(`Failed to purge: ${err}`);
    }
  }

  async function toggleTheme() {
    const newTheme = theme === 'dark' ? 'light' : 'dark';
    const a = await getApi();
    if (!a) return;
    try {
      await a.SetTheme(newTheme);
      theme = newTheme;
      document.documentElement.dataset.theme = newTheme;
    } catch (err: any) {
      notifications.error(`Failed to set theme: ${err}`);
    }
  }

  async function handleSave() {
    saving = true;
    const a = await getApi();
    if (!a) { saving = false; return; }
    try {
      await a.SetConfig(baseURL, apiKey, workspacesRoot);
      notifications.success('Settings saved');
      await refreshProfiles();
    } catch (err: any) {
      notifications.error(`Failed to save: ${err}`);
    } finally {
      saving = false;
    }
  }
</script>

<div class="panel">
  <header>
    <h1><span class="accent">settings</span></h1>
  </header>

  {#if !loaded}
    <div class="loading">loading settings...</div>
  {:else}
    <div class="settings-form">

      <!-- Appearance -->
      <div class="section">
        <h2>appearance</h2>
        <div class="theme-toggle">
          <button class="theme-opt" class:active={theme === 'dark'} onclick={() => { if (theme !== 'dark') toggleTheme(); }}>
            <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>
            dark
          </button>
          <button class="theme-opt" class:active={theme === 'light'} onclick={() => { if (theme !== 'light') toggleTheme(); }}>
            <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>
            light
          </button>
        </div>
      </div>

      <!-- Brainbox Connection -->
      <div class="section">
        <h2>brainbox connection</h2>

        <div class="field">
          <label for="url">api url</label>
          <input id="url" type="url" bind:value={baseURL} placeholder="http://127.0.0.1:9999" />
          <p class="hint">the brainbox API server address</p>
        </div>

        <div class="field">
          <label for="key">api key</label>
          <input id="key" type="password" bind:value={apiKey} placeholder="loaded from ~/.config/phantom-ink/brainbox/.api-key" autocomplete="off" />
          <p class="hint">leave blank to use the key from ~/.config/phantom-ink/brainbox/.api-key</p>
        </div>
      </div>

      <!-- Workspaces -->
      <div class="section">
        <h2>workspaces</h2>

        <div class="field">
          <label for="wsroot">profiles directory</label>
          <input id="wsroot" type="text" bind:value={workspacesRoot} placeholder="~/workspaces/profiles" />
          <p class="hint">root directory to scan for shell-profiler workspaces (reads ~/.profile-manager by default)</p>
        </div>

        <div class="profiles-header">
          <h3>profiles</h3>
          <button class="btn-icon" onclick={refreshProfiles} disabled={scanning} title="Scan for profiles" aria-label="Scan for profiles">
            <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class:spinning={scanning} aria-hidden="true"><path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/></svg>
          </button>
          <button class="btn-icon" onclick={() => showCreateProfile = !showCreateProfile} title="Create new profile" aria-label="Create new profile">
            <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 5v14"/><path d="M5 12h14"/></svg>
          </button>
        </div>

        {#if showCreateProfile}
          <div class="create-profile">
            <input type="text" bind:value={newProfileName} placeholder="profile name" onkeydown={(e) => e.key === 'Enter' && handleCreateProfile()} />
            <button class="btn-small" onclick={handleCreateProfile} disabled={creating || !newProfileName.trim()}>
              {creating ? 'creating...' : 'create'}
            </button>
          </div>
        {/if}

        <div class="profile-list">
          {#if profiles.length === 0}
            <p class="empty">no profiles found — check profiles directory above</p>
          {:else}
            <button class="profile-item" class:active={!activeProfile} onclick={clearProfile}>
              <span class="profile-name">all profiles</span>
              <span class="profile-meta">no filter</span>
            </button>
            {#each profiles as p}
              <div class="profile-row">
                <button class="profile-item" class:active={activeProfile?.name === p.name} onclick={() => selectProfile(p.name)}>
                  <span class="profile-name">{p.name}</span>
                  <span class="profile-meta">
                    {#if p.secrets_mode === '1password'}
                      <span class="secrets-badge op">1Password</span>
                    {:else if p.secrets_mode === 'plaintext'}
                      <span class="secrets-badge plain">plaintext</span>
                    {:else}
                      <span class="secrets-badge none">no secrets</span>
                    {/if}
                    {#if p.has_backup}
                      <span class="secrets-badge plain">backup</span>
                    {/if}
                  </span>
                </button>
                <button class="btn-delete-profile" onclick={() => confirmDelete = confirmDelete === p.name ? null : p.name} title="Delete profile" aria-label="Delete profile {p.name}">
                  <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                </button>
              </div>
              {#if confirmDelete === p.name}
                <div class="confirm-delete">
                  <span class="confirm-text">delete "{p.name}"?</span>
                  <button class="btn-confirm backup" onclick={() => handleDelete(p.name, true)}>backup + delete</button>
                  <button class="btn-confirm danger" onclick={() => handleDelete(p.name, false)}>permanently delete</button>
                  <button class="btn-confirm cancel" onclick={() => confirmDelete = null}>cancel</button>
                </div>
              {/if}
            {/each}
          {/if}
        </div>

        <!-- Backups -->
        {#if backups.length > 0}
          <div class="backups-section">
            <h3>backups</h3>
            {#each backups as b}
              <div class="backup-row">
                <span class="backup-name">{b}</span>
                <button class="btn-confirm backup" onclick={() => handleRestore(b)}>restore</button>
                <button class="btn-confirm danger" onclick={() => handlePurge(b)}>purge</button>
              </div>
            {/each}
          </div>
        {/if}
      </div>

      <!-- Save -->
      <div class="form-actions">
        <button class="btn-save" onclick={handleSave} disabled={saving}>
          {saving ? 'saving...' : 'save settings'}
        </button>
      </div>

      <!-- About -->
      <div class="section info-section">
        <h2>about</h2>
        <div class="info-row">
          <span class="info-label">version</span>
          <span class="info-val">0.1.0</span>
        </div>
        <div class="info-row">
          <span class="info-label">platform</span>
          <span class="info-val">{platform}</span>
        </div>
        <div class="info-row">
          <span class="info-label">config</span>
          <span class="info-val">~/.config/phantom-ink/config.json</span>
        </div>
        <div class="info-row">
          <span class="info-label">api key source</span>
          <span class="info-val">~/.config/phantom-ink/brainbox/.api-key</span>
        </div>
      </div>
    </div>
  {/if}
</div>

<style>
  .panel { padding: var(--panel-padding); }
  header { margin-bottom: 24px; }
  h1 { font-size: 22px; font-weight: 600; }
  .accent { color: var(--color-accent); }

  .loading { color: var(--color-text-tertiary); font-size: 13px; padding: 24px 0; }

  .settings-form { max-width: 560px; }

  .section { margin-bottom: 32px; }

  h2 {
    font-size: 13px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--color-text-tertiary);
    margin-bottom: 16px;
  }

  h3 {
    font-size: 12px;
    font-weight: 600;
    color: var(--color-text-secondary);
  }

  .field { margin-bottom: 16px; }

  .hint {
    font-size: 11px;
    color: var(--color-text-tertiary);
    margin-top: 4px;
  }

  /* Profiles header with scan + create buttons */
  .profiles-header {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 12px;
  }
  .profiles-header h3 { flex: 1; }

  .btn-icon {
    background: rgba(255, 255, 255, 0.05);
    border: 1px solid var(--color-border-secondary);
    border-radius: var(--radius-md);
    color: var(--color-text-tertiary);
    padding: 4px;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all 0.15s;
  }
  .btn-icon:hover { background: rgba(255, 255, 255, 0.1); color: var(--color-text-secondary); }

  .spinning { animation: spin 0.8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* Create profile inline form */
  .create-profile {
    display: flex;
    gap: 8px;
    margin-bottom: 12px;
  }
  .create-profile input { flex: 1; }

  .btn-small {
    background: rgba(16, 185, 129, 0.1);
    border: 1px solid rgba(16, 185, 129, 0.3);
    color: var(--color-success);
    padding: 6px 14px;
    border-radius: var(--radius-md);
    font-size: 12px;
    font-weight: 500;
    white-space: nowrap;
    transition: all 0.15s;
  }
  .btn-small:hover { background: rgba(16, 185, 129, 0.2); border-color: var(--color-success); }

  /* Profile list */
  .profile-list {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .empty {
    font-size: 12px;
    color: var(--color-text-tertiary);
    padding: 12px 0;
  }

  .profile-item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    background: transparent;
    border: 1px solid transparent;
    border-radius: var(--radius-md);
    padding: 8px 12px;
    text-align: left;
    transition: all 0.15s;
    width: 100%;
  }
  .profile-item:hover {
    background: var(--color-bg-secondary);
    border-color: var(--color-border-primary);
  }
  .profile-item.active {
    background: rgba(245, 158, 11, 0.08);
    border-color: rgba(245, 158, 11, 0.25);
  }

  .profile-name {
    font-size: 13px;
    font-weight: 500;
    color: var(--color-text-primary);
  }
  .profile-item.active .profile-name {
    color: var(--color-accent);
  }

  .profile-meta {
    font-size: 11px;
    color: var(--color-text-tertiary);
    font-family: var(--font-mono);
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .secrets-badge {
    font-size: 10px;
    padding: 1px 6px;
    border-radius: var(--radius-sm);
  }
  .secrets-badge.op {
    background: rgba(59, 130, 246, 0.1);
    border: 1px solid rgba(59, 130, 246, 0.2);
    color: var(--color-info);
  }
  .secrets-badge.plain {
    background: rgba(245, 158, 11, 0.1);
    border: 1px solid rgba(245, 158, 11, 0.2);
    color: var(--color-accent);
  }
  .secrets-badge.none {
    background: rgba(239, 68, 68, 0.1);
    border: 1px solid rgba(239, 68, 68, 0.2);
    color: var(--color-error);
  }

  /* Theme toggle */
  .theme-toggle {
    display: inline-flex;
    gap: 4px;
    background: var(--color-bg-tertiary);
    border-radius: var(--radius-lg);
    padding: 4px;
  }

  .theme-opt {
    display: flex;
    align-items: center;
    gap: 6px;
    background: transparent;
    border: none;
    border-radius: var(--radius-md);
    color: var(--color-text-tertiary);
    font-size: 12px;
    padding: 6px 14px;
    transition: all 0.2s;
  }
  .theme-opt:hover { color: var(--color-text-secondary); }
  .theme-opt.active {
    background: var(--color-bg-secondary);
    color: var(--color-text-primary);
    box-shadow: var(--shadow-button);
    font-weight: 500;
  }

  /* Profile row with delete */
  .profile-row {
    display: flex;
    align-items: center;
    gap: 4px;
  }
  .profile-row .profile-item { flex: 1; }

  .btn-delete-profile {
    background: transparent;
    border: none;
    color: var(--color-text-tertiary);
    padding: 6px;
    border-radius: var(--radius-sm);
    display: flex;
    opacity: 0;
    transition: all 0.15s;
  }
  .profile-row:hover .btn-delete-profile { opacity: 1; }
  .btn-delete-profile:hover { color: var(--color-error); background: rgba(239, 68, 68, 0.1); }

  /* Delete confirmation */
  .confirm-delete {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 6px 12px;
    margin-bottom: 4px;
    background: rgba(239, 68, 68, 0.04);
    border: 1px solid rgba(239, 68, 68, 0.12);
    border-radius: var(--radius-md);
  }

  .confirm-text {
    font-size: 12px;
    color: var(--color-text-secondary);
    flex: 1;
  }

  .btn-confirm {
    font-size: 10px;
    padding: 3px 8px;
    border-radius: var(--radius-sm);
    white-space: nowrap;
    transition: all 0.15s;
  }
  .btn-confirm.backup {
    background: rgba(59, 130, 246, 0.1);
    border: 1px solid rgba(59, 130, 246, 0.25);
    color: var(--color-info);
  }
  .btn-confirm.backup:hover { background: rgba(59, 130, 246, 0.2); }
  .btn-confirm.danger {
    background: rgba(239, 68, 68, 0.1);
    border: 1px solid rgba(239, 68, 68, 0.25);
    color: var(--color-error);
  }
  .btn-confirm.danger:hover { background: rgba(239, 68, 68, 0.2); }
  .btn-confirm.cancel {
    background: transparent;
    border: 1px solid var(--color-border-secondary);
    color: var(--color-text-tertiary);
  }
  .btn-confirm.cancel:hover { color: var(--color-text-secondary); }

  /* Backups section */
  .backups-section {
    margin-top: 16px;
    padding-top: 12px;
    border-top: 1px solid var(--color-border-primary);
  }
  .backups-section h3 {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--color-text-tertiary);
    margin-bottom: 8px;
  }

  .backup-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 8px;
    border-radius: var(--radius-sm);
  }
  .backup-row:nth-child(odd) { background: rgba(255, 255, 255, 0.015); }

  .backup-name {
    flex: 1;
    font-size: 13px;
    font-weight: 500;
    color: var(--color-text-secondary);
  }

  /* Save button */
  .form-actions { margin-bottom: 32px; }

  .btn-save {
    background: rgba(59, 130, 246, 0.1);
    border: 1px solid rgba(59, 130, 246, 0.3);
    color: var(--color-info);
    padding: 9px 20px;
    border-radius: var(--radius-lg);
    font-size: 13px;
    font-weight: 500;
    transition: all 0.15s;
  }
  .btn-save:hover { background: rgba(59, 130, 246, 0.2); border-color: var(--color-info); }

  /* About section */
  .info-section {
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-xl);
    padding: 16px;
  }

  .info-section h2 { margin-bottom: 12px; }

  .info-row {
    display: flex;
    gap: 16px;
    padding: 6px 0;
    border-bottom: 1px solid var(--color-border-primary);
    font-size: 12px;
  }
  .info-row:last-child { border-bottom: none; }

  .info-label {
    color: var(--color-text-tertiary);
    min-width: 120px;
    flex-shrink: 0;
  }

  .info-val {
    color: var(--color-text-secondary);
    font-family: var(--font-mono);
    font-size: 11px;
  }
</style>

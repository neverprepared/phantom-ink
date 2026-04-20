<script lang="ts">
  import { getApi } from '../utils/api';
  import { onMount } from 'svelte';
  import { notifications } from '../notifications.svelte';
  import { profileState, type Profile } from '../stores.svelte';
  import PieChart from '../components/PieChart.svelte';

  let profiles = $derived(profileState.profiles);
  let activeProfile = $derived(profileState.active);
  let scanning = $state(false);
  let diskOverview = $state<any | null>(null);
  let loadingDisk = $state(true);

  // Create profile
  let showCreateProfile = $state(false);
  let newProfileName = $state('');
  let creating = $state(false);

  // Delete / backup / restore
  let confirmDelete = $state<string | null>(null);
  let backups = $state<string[]>([]);

  onMount(async () => {
    await Promise.all([refreshProfiles(), loadBackups(), refreshDisk()]);
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

  async function refreshDisk() {
    loadingDisk = true;
    const a = await getApi();
    if (!a) { loadingDisk = false; return; }
    try {
      diskOverview = await a.GetDiskOverview();
    } catch { diskOverview = null; }
    finally { loadingDisk = false; }
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
      await Promise.all([refreshProfiles(), refreshDisk()]);
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
      await Promise.all([refreshProfiles(), loadBackups(), refreshDisk()]);
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
      await Promise.all([refreshProfiles(), loadBackups(), refreshDisk()]);
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

  // Build pie slices from disk overview
  let pieSlices = $derived.by(() => {
    if (!diskOverview) return [];
    const slices: { name: string; value: number; label: string }[] = [];
    for (const p of (diskOverview.profiles ?? [])) {
      if (p.bytes > 0) {
        slices.push({ name: p.name, value: p.bytes, label: p.label });
      }
    }
    if (diskOverview.os_bytes > 0) {
      slices.push({ name: 'OS + other', value: diskOverview.os_bytes, label: diskOverview.os_label });
    }
    return slices;
  });

  // Disk usage per profile name → label for inline display
  let profileDiskMap = $derived(
    new Map((diskOverview?.profiles ?? []).map((p: any) => [p.name, p.label]))
  );
</script>

<div class="panel">
  <header>
    <h1><span class="accent">profiles</span></h1>
    <div class="header-actions">
      <button class="btn-icon" onclick={() => { refreshProfiles(); refreshDisk(); }} disabled={scanning} title="Refresh" aria-label="Refresh profiles">
        <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class:spinning={scanning} aria-hidden="true"><path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/></svg>
      </button>
      <button class="btn-icon" onclick={() => showCreateProfile = !showCreateProfile} title="Create new profile" aria-label="Create new profile">
        <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 5v14"/><path d="M5 12h14"/></svg>
      </button>
    </div>
  </header>

  <!-- Disk overview pie chart -->
  {#if diskOverview && pieSlices.length > 0}
    <div class="disk-overview">
      <PieChart slices={pieSlices} size={160} />
      <div class="disk-info">
        <div class="disk-total-row">
          <span class="disk-used">{diskOverview.used_label}</span>
          <span class="disk-sep">of</span>
          <span class="disk-cap">{diskOverview.total_label}</span>
          <span class="disk-label">used</span>
        </div>
        <div class="disk-legend">
          {#each pieSlices as s (s.name)}
            <span class="legend-item">
              <span class="legend-dot" style="background: var(--legend-color-{s.name.replace(/[^a-z0-9]/gi, '-').toLowerCase()})"></span>
              <span class="legend-name">{s.name}</span>
              <span class="legend-val">{s.label}</span>
            </span>
          {/each}
        </div>
      </div>
    </div>
  {/if}

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
      <p class="empty">no profiles found</p>
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
              {#if profileDiskMap.has(p.name)}
                <span class="disk-badge">{profileDiskMap.get(p.name)}</span>
              {/if}
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

<style>
  .panel { padding: var(--panel-padding); }
  header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; }
  h1 { font-size: 22px; font-weight: 600; }
  .accent { color: var(--color-accent); }
  .header-actions { display: flex; gap: 8px; }

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

  /* Disk overview */
  .disk-overview {
    display: flex;
    align-items: center;
    gap: 24px;
    margin-bottom: 24px;
    padding: 16px;
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-xl);
  }

  .disk-info {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .disk-total-row {
    display: flex;
    align-items: baseline;
    gap: 6px;
  }

  .disk-used {
    font-size: 22px;
    font-weight: 700;
    color: var(--color-text-primary);
    font-family: var(--font-mono);
  }

  .disk-sep, .disk-label {
    font-size: 12px;
    color: var(--color-text-tertiary);
  }

  .disk-cap {
    font-size: 14px;
    font-weight: 600;
    color: var(--color-text-secondary);
    font-family: var(--font-mono);
  }

  .disk-legend {
    display: flex;
    flex-wrap: wrap;
    gap: 8px 16px;
  }

  .legend-item {
    display: flex;
    align-items: center;
    gap: 5px;
    font-size: 12px;
    color: var(--color-text-tertiary);
  }

  .legend-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
    background: var(--color-text-tertiary);
  }

  .legend-name { font-weight: 500; color: var(--color-text-secondary); }
  .legend-val { font-family: var(--font-mono); font-size: 11px; }

  /* Create profile */
  .create-profile {
    display: flex;
    gap: 8px;
    margin-bottom: 16px;
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
  .profile-list { display: flex; flex-direction: column; gap: 2px; max-width: 560px; }
  .empty { font-size: 12px; color: var(--color-text-tertiary); padding: 12px 0; }

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
  .profile-item:hover { background: var(--color-bg-secondary); border-color: var(--color-border-primary); }
  .profile-item.active { background: rgba(245, 158, 11, 0.08); border-color: rgba(245, 158, 11, 0.25); }

  .profile-name { font-size: 13px; font-weight: 500; color: var(--color-text-primary); }
  .profile-item.active .profile-name { color: var(--color-accent); }

  .profile-meta {
    font-size: 11px;
    color: var(--color-text-tertiary);
    font-family: var(--font-mono);
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .disk-badge {
    font-size: 10px;
    padding: 1px 6px;
    border-radius: var(--radius-sm);
    background: rgba(148, 163, 184, 0.1);
    border: 1px solid rgba(148, 163, 184, 0.2);
    color: var(--color-text-secondary);
    font-family: var(--font-mono);
  }

  .secrets-badge { font-size: 10px; padding: 1px 6px; border-radius: var(--radius-sm); }
  .secrets-badge.op { background: rgba(59, 130, 246, 0.1); border: 1px solid rgba(59, 130, 246, 0.2); color: var(--color-info); }
  .secrets-badge.plain { background: rgba(245, 158, 11, 0.1); border: 1px solid rgba(245, 158, 11, 0.2); color: var(--color-accent); }
  .secrets-badge.none { background: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239, 68, 68, 0.2); color: var(--color-error); }

  .profile-row { display: flex; align-items: center; gap: 4px; }
  .profile-row .profile-item { flex: 1; }

  .btn-delete-profile {
    background: transparent; border: none; color: var(--color-text-tertiary);
    padding: 6px; border-radius: var(--radius-sm); display: flex;
    opacity: 0; transition: all 0.15s;
  }
  .profile-row:hover .btn-delete-profile { opacity: 1; }
  .btn-delete-profile:hover { color: var(--color-error); background: rgba(239, 68, 68, 0.1); }

  .confirm-delete {
    display: flex; align-items: center; gap: 6px; padding: 6px 12px; margin-bottom: 4px;
    background: rgba(239, 68, 68, 0.04); border: 1px solid rgba(239, 68, 68, 0.12); border-radius: var(--radius-md);
  }
  .confirm-text { font-size: 12px; color: var(--color-text-secondary); flex: 1; }

  .btn-confirm { font-size: 10px; padding: 3px 8px; border-radius: var(--radius-sm); white-space: nowrap; transition: all 0.15s; }
  .btn-confirm.backup { background: rgba(59, 130, 246, 0.1); border: 1px solid rgba(59, 130, 246, 0.25); color: var(--color-info); }
  .btn-confirm.backup:hover { background: rgba(59, 130, 246, 0.2); }
  .btn-confirm.danger { background: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239, 68, 68, 0.25); color: var(--color-error); }
  .btn-confirm.danger:hover { background: rgba(239, 68, 68, 0.2); }
  .btn-confirm.cancel { background: transparent; border: 1px solid var(--color-border-secondary); color: var(--color-text-tertiary); }
  .btn-confirm.cancel:hover { color: var(--color-text-secondary); }

  .backups-section { margin-top: 24px; padding-top: 16px; border-top: 1px solid var(--color-border-primary); }
  .backups-section h3 { font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; color: var(--color-text-tertiary); margin-bottom: 8px; }

  .backup-row { display: flex; align-items: center; gap: 8px; padding: 6px 8px; border-radius: var(--radius-sm); }
  .backup-row:nth-child(odd) { background: rgba(255, 255, 255, 0.015); }
  .backup-name { flex: 1; font-size: 13px; font-weight: 500; color: var(--color-text-secondary); }
</style>

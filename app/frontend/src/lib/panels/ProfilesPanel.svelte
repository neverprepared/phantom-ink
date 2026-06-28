<script lang="ts">
  import { getApi } from '../utils/api';
  import { onMount } from 'svelte';
  import { notifications } from '../notifications.svelte';
  import { profileState, profileColorStore, type Profile } from '../stores.svelte';
  import { getProfileColor, profileColorStyle, PROFILE_PALETTE } from '../utils/profileColors';
  import PieChart from '../components/PieChart.svelte';
  import EmptyState from '../components/EmptyState.svelte';

  // --- Profile image state ---
  type ImageStatus = { configured: boolean; exists: boolean; tag: string; digest: string; error?: string; built_at?: string };

  function fmtBuiltAt(iso: string | undefined): string {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return '';
    const now = Date.now();
    const diff = Math.floor((now - d.getTime()) / 1000);
    if (diff < 60) return 'just now';
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    if (diff < 7 * 86400) return `${Math.floor(diff / 86400)}d ago`;
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
  }
  let imageStatuses = $state<Record<string, ImageStatus>>({});
  let imageBuilding = $state<Record<string, boolean>>({});
  let imageLogs = $state<Record<string, string[]>>({});
  let imageLogsOpen = $state<Record<string, boolean>>({});
  let noCache = $state<Record<string, boolean>>({});
  let baseBuilding = $state<Record<string, boolean>>({});

  let profiles = $derived(profileState.profiles);
  let activeProfile = $derived(profileState.active);
  let scanning = $state(false);
  let diskOverview = $state<any | null>(null);
  let loadingDisk = $state(true);
  let scanningDisk = $state(false);

  // Create profile
  let showCreateProfile = $state(false);
  let newProfileName = $state('');
  let creating = $state(false);

  // Delete / backup / restore
  let confirmDelete = $state<string | null>(null);
  let backups = $state<string[]>([]);

  onMount(async () => {
    await Promise.all([refreshProfiles(), loadBackups(), refreshDisk(), loadProfileColors()]);
    for (const p of profileState.profiles) {
      checkImageStatus(p.name);
    }
  });

  async function loadProfileColors() {
    const a = await getApi();
    if (!a) return;
    try {
      const colors = await a.GetProfileColors();
      profileColorStore.overrides = colors ?? {};
    } catch { /* first run — no overrides yet */ }
  }

  async function setColor(name: string, idx: number) {
    const current = profileColorStore.getOverride(name);
    const currentIdx = current !== '' ? parseInt(current, 10) : getProfileColor(name).index;
    // Clicking the already-active swatch clears the override (reset to hash)
    const newVal = idx === currentIdx && current !== '' ? '' : String(idx);
    profileColorStore.setOverride(name, newVal);
    const a = await getApi();
    if (a) await a.SetProfileColor(name, newVal);
  }

  async function refreshProfiles() {
    scanning = true;
    const a = await getApi();
    if (!a) { scanning = false; return; }
    try {
      const scanned: Profile[] = await a.ScanProfiles();
      profileState.profiles = scanned ?? [];
      const ap = await a.GetActiveProfile();
      profileState.active = ap?.name ? ap : null;
    } catch (err: any) {
      notifications.error(`Failed to scan profiles: ${err?.message ?? err}`);
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

  async function scanDisk() {
    scanningDisk = true;
    const a = await getApi();
    if (!a) { scanningDisk = false; return; }
    try {
      diskOverview = await a.ScanDiskUsage();
      notifications.success('Disk scan complete');
    } catch (err: any) {
      notifications.error(`Disk scan failed: ${err?.message ?? err}`);
    } finally {
      scanningDisk = false;
    }
  }

  function formatScanTime(iso: string): string {
    if (!iso) return 'never';
    const d = new Date(iso);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const mins = Math.floor(diffMs / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.floor(hrs / 24)}d ago`;
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

  async function toggleHidden(name: string) {
    const next = profileState.setHidden(name, !profileState.isHidden(name));
    const a = await getApi();
    if (!a) return;
    try {
      await (a as any).SetHiddenProfiles?.(Array.from(next));
    } catch (err: any) {
      notifications.error(`Failed to update profile visibility: ${err?.message ?? err}`);
    }
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

  // NFS exports
  // --- Profile image functions ---

  async function checkImageStatus(profile: string) {
    const a = await getApi();
    if (!a) return;
    try {
      const s = await a.GetRemoteProfileImageStatus(profile);
      imageStatuses[profile] = s;
    } catch {
      imageStatuses[profile] = { configured: false, exists: false, tag: '', digest: '' };
    }
  }

  async function buildImage(profile: string) {
    const a = await getApi();
    if (!a) return;
    imageBuilding[profile] = true;
    imageLogs[profile] = [];
    imageLogsOpen[profile] = true;

    const rt = (window as any).runtime;
    let unsub: (() => void) | null = null;
    if (rt?.EventsOn) {
      rt.EventsOn('profile-image:progress', (evt: any) => {
        if (evt?.profile !== profile) return;
        imageLogs[profile] = [...(imageLogs[profile] ?? []), evt.step];
        if (evt.done) {
          imageBuilding[profile] = false;
          if (evt.error) {
            notifications.error(`Build failed: ${evt.error}`);
          } else {
            notifications.success(`Profile image built: ${evt.tag}`);
            checkImageStatus(profile);
          }
          if (rt?.EventsOff) rt.EventsOff('profile-image:progress');
        }
      });
    }

    try {
      await a.BuildProfileImage({ profile, base_image: '', registry_url: '' });
    } catch (err: any) {
      imageBuilding[profile] = false;
      notifications.error(`Build error: ${err?.message ?? err}`);
      if (rt?.EventsOff) rt.EventsOff('profile-image:progress');
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
  <header class="panel-header">
    <h1 class="page-title">profiles</h1>
    <div class="header-actions">
      <button class="btn-refresh" onclick={() => { refreshProfiles(); refreshDisk(); }} disabled={scanning} title="Refresh" aria-label="Refresh profiles">
        <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class:spinning={scanning} aria-hidden="true"><path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/></svg>
      </button>
      <button class="btn primary" onclick={() => showCreateProfile = !showCreateProfile}>+ new profile</button>
    </div>
  </header>

  <!-- Disk overview pie chart -->
  {#if diskOverview}
    <div class="disk-overview">
      {#if pieSlices.length > 0}
        <PieChart slices={pieSlices} size={160} />
      {/if}
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
        <div class="disk-scan-row">
          <button class="btn-scan" onclick={scanDisk} disabled={scanningDisk}>
            <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class:spinning={scanningDisk} aria-hidden="true"><path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/></svg>
            {scanningDisk ? 'scanning...' : 'scan disk'}
          </button>
          <span class="scan-time">
            {#if diskOverview.scanned_at}
              scanned {formatScanTime(diskOverview.scanned_at)}
            {:else}
              not yet scanned
            {/if}
          </span>
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
      <EmptyState title="No profiles found" />
    {:else}
      <button class="profile-item" class:active={!activeProfile} onclick={clearProfile}>
        <span class="profile-name">all profiles</span>
        <span class="profile-meta">no filter</span>
      </button>
      {#each profiles as p}
        {@const pColor = getProfileColor(p.name, profileColorStore.getOverride(p.name))}
        {@const isActive = activeProfile?.name === p.name}
        {@const status = imageStatuses[p.name]}
        {@const building = imageBuilding[p.name] ?? false}
        {@const logs = imageLogs[p.name] ?? []}
        {@const logsOpen = imageLogsOpen[p.name] ?? false}
        {@const isHidden = profileState.isHidden(p.name)}
        <div class="profile-card" class:active={isActive} class:hidden={isHidden}>
          <div class="profile-row">
            <span class="profile-dot" style="background: {pColor.text};"></span>
            <button
              class="profile-item"
              class:active={isActive}
              style={isActive ? `background: ${pColor.bg}; border-color: ${pColor.border};` : ''}
              onclick={() => selectProfile(p.name)}
            >
              <span class="profile-name" style={isActive ? `color: ${pColor.text};` : ''}>{p.name}</span>
              <span class="profile-meta">
                {#if isHidden}
                  <span class="secrets-badge plain">hidden</span>
                {/if}
                {#if profileDiskMap.has(p.name)}
                  <span class="disk-badge">{profileDiskMap.get(p.name)}</span>
                {/if}
                {#if p.has_backup}
                  <span class="secrets-badge plain">backup</span>
                {/if}
              </span>
            </button>
            <button class="btn-hide-profile" onclick={() => toggleHidden(p.name)} title={isHidden ? `Show ${p.name} in the picker` : `Hide ${p.name} from the picker`} aria-label="{isHidden ? 'Show' : 'Hide'} profile {p.name}">
              {#if isHidden}
                <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
              {:else}
                <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>
              {/if}
            </button>
            <button class="btn-delete-profile" onclick={() => confirmDelete = confirmDelete === p.name ? null : p.name} title="Delete profile" aria-label="Delete profile {p.name}">
              <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
            </button>
          </div>

          <div class="card-footer">
            <div class="color-swatches">
              {#each PROFILE_PALETTE as swatch (swatch.index)}
                <button
                  class="swatch"
                  class:selected={pColor.index === swatch.index}
                  style="background: {swatch.text};"
                  onclick={() => setColor(p.name, swatch.index)}
                  title="Color {swatch.index}"
                  aria-label="Set color {swatch.index} for {p.name}"
                ></button>
              {/each}
            </div>

            <div class="card-image-actions">
              {#if !status}
                <span class="image-badge checking">checking…</span>
              {:else if !status.configured}
                <span class="image-badge unconfigured">no registry</span>
              {:else if status.exists}
                <span class="image-badge ok" title={status.built_at ? new Date(status.built_at).toLocaleString() : ''}>
                  <svg xmlns="http://www.w3.org/2000/svg" width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>
                  image ready{status.built_at ? ` · ${fmtBuiltAt(status.built_at)}` : ''}
                </span>
              {:else}
                <span class="image-badge missing" title={status?.error ?? ''}>no image{status?.error ? ' ⚠' : ''}</span>
              {/if}

              {#if status?.configured}
                <button
                  class="btn-build"
                  class:building
                  onclick={() => buildImage(p.name)}
                  disabled={building}
                  title={building ? 'Building…' : status?.exists ? 'Rebuild image' : 'Build image'}
                >
                  {#if building}
                    <svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="spin" aria-hidden="true"><path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/></svg>
                    building…
                  {:else if status?.exists}
                    rebuild
                  {:else}
                    build
                  {/if}
                </button>
                <button class="btn-icon-sm" onclick={() => checkImageStatus(p.name)} title="Refresh status" aria-label="Refresh image status for {p.name}">
                  <svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/></svg>
                </button>
              {/if}
            </div>
          </div>

          {#if status?.exists && status.digest}
            <div class="image-digest" title={status.digest}>{status.digest.replace(/^sha256:/, '').slice(0, 12)}</div>
          {/if}

          {#if logs.length > 0}
            <button class="logs-toggle" onclick={() => imageLogsOpen[p.name] = !logsOpen}>
              {logsOpen ? '▾' : '▸'} build log ({logs.length})
            </button>
            {#if logsOpen}
              <div class="build-log">
                {#each logs as line}
                  <div class="log-line">{line}</div>
                {/each}
              </div>
            {/if}
          {/if}

          {#if confirmDelete === p.name}
            <div class="confirm-delete">
              <span class="confirm-text">delete "{p.name}"?</span>
              <button class="btn-confirm backup" onclick={() => handleDelete(p.name, true)}>backup + delete</button>
              <button class="btn-confirm danger" onclick={() => handleDelete(p.name, false)}>permanently delete</button>
              <button class="btn-confirm cancel" onclick={() => confirmDelete = null}>cancel</button>
            </div>
          {/if}
        </div>
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
  .header-actions { display: flex; align-items: center; gap: 8px; }

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

  .disk-scan-row {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-top: 4px;
  }

  .btn-scan {
    display: flex;
    align-items: center;
    gap: 5px;
    background: rgba(255, 255, 255, 0.05);
    border: 1px solid var(--color-border-secondary);
    border-radius: var(--radius-md);
    color: var(--color-text-tertiary);
    font-size: 11px;
    padding: 4px 10px;
    transition: all 0.15s;
  }
  .btn-scan:hover:not(:disabled) { background: rgba(255, 255, 255, 0.1); color: var(--color-text-secondary); }
  .btn-scan:disabled { opacity: 0.5; cursor: not-allowed; }

  .scan-time {
    font-size: 11px;
    color: var(--color-text-tertiary);
    font-style: italic;
  }

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
  .profile-list { display: flex; flex-direction: column; gap: 4px; }
  .empty { font-size: 12px; color: var(--color-text-tertiary); padding: 12px 0; }

  .profile-card {
    border: 1px solid transparent;
    border-radius: var(--radius-md);
    padding: 2px 0;
    transition: border-color 0.15s;
  }
  .profile-card:hover { border-color: var(--color-border-primary); }

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

  .profile-name { font-size: 13px; font-weight: 500; color: var(--color-text-primary); }

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
  .secrets-badge.plain { background: rgba(245, 158, 11, 0.1); border: 1px solid rgba(245, 158, 11, 0.2); color: var(--color-accent); }

  .profile-row { display: flex; align-items: center; gap: 4px; }
  .profile-row .profile-item { flex: 1; }

  .profile-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
  }

  .card-footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 2px 6px 6px 16px;
    gap: 8px;
  }

  .color-swatches {
    display: flex;
    gap: 4px;
  }

  .card-image-actions {
    display: flex;
    align-items: center;
    gap: 5px;
    flex-shrink: 0;
  }

  .swatch {
    width: 14px;
    height: 14px;
    border-radius: 50%;
    border: 2px solid transparent;
    padding: 0;
    cursor: pointer;
    transition: all 0.15s;
    opacity: 0.6;
  }
  .swatch:hover { opacity: 1; transform: scale(1.2); }
  .swatch.selected {
    opacity: 1;
    border-color: var(--color-text-primary);
    transform: scale(1.15);
  }

  .btn-delete-profile {
    background: transparent; border: none; color: var(--color-text-tertiary);
    padding: 6px; border-radius: var(--radius-sm); display: flex;
    opacity: 0; transition: all 0.15s;
  }
  .profile-row:hover .btn-delete-profile { opacity: 1; }
  .btn-delete-profile:hover { color: var(--color-error); background: rgba(239, 68, 68, 0.1); }

  .btn-hide-profile {
    background: transparent; border: none; color: var(--color-text-tertiary);
    padding: 6px; border-radius: var(--radius-sm); display: flex;
    opacity: 0; transition: all 0.15s;
  }
  .profile-row:hover .btn-hide-profile { opacity: 1; }
  .btn-hide-profile:hover {
    color: var(--accent, var(--color-accent));
    background: var(--accent-soft, color-mix(in srgb, var(--accent, var(--color-accent)) 10%, transparent));
  }
  .profile-card.hidden .btn-hide-profile { opacity: 0.6; }

  /* Hidden profiles get dimmed but stay manageable. */
  .profile-card.hidden .profile-item { opacity: 0.55; }
  .profile-card.hidden .profile-name { font-style: italic; }

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


  .image-badge {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    font-size: 10px;
    padding: 2px 7px;
    border-radius: var(--radius-sm);
    white-space: nowrap;
    font-weight: 500;
  }
  .image-badge.checking {
    background: rgba(148, 163, 184, 0.08);
    border: 1px solid rgba(148, 163, 184, 0.15);
    color: var(--color-text-muted);
    font-style: italic;
  }
  .image-badge.unconfigured {
    background: rgba(245, 158, 11, 0.07);
    border: 1px solid rgba(245, 158, 11, 0.18);
    color: var(--color-accent);
  }
  .image-badge.ok {
    background: rgba(16, 185, 129, 0.08);
    border: 1px solid rgba(16, 185, 129, 0.2);
    color: var(--color-success);
  }
  .image-badge.missing {
    background: rgba(148, 163, 184, 0.05);
    border: 1px solid rgba(148, 163, 184, 0.12);
    color: var(--color-text-tertiary);
  }

  .btn-build {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    font-size: 11px;
    font-weight: 500;
    padding: 3px 10px;
    border-radius: var(--radius-md);
    background: rgba(99, 102, 241, 0.1);
    border: 1px solid rgba(99, 102, 241, 0.25);
    color: var(--color-info);
    transition: all 0.15s;
    white-space: nowrap;
  }
  .btn-build:hover:not(:disabled) {
    background: rgba(99, 102, 241, 0.2);
    border-color: rgba(99, 102, 241, 0.4);
  }
  .btn-build:disabled { opacity: 0.6; cursor: not-allowed; }
  .btn-build.building {
    background: rgba(99, 102, 241, 0.06);
    border-color: rgba(99, 102, 241, 0.15);
    color: var(--color-text-tertiary);
  }

  .btn-icon-sm {
    background: transparent;
    border: none;
    color: var(--color-text-muted);
    padding: 3px;
    border-radius: var(--radius-sm);
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all 0.15s;
  }
  .btn-icon-sm:hover { color: var(--color-text-secondary); background: rgba(255, 255, 255, 0.06); }

  .image-digest {
    font-size: 10px;
    font-family: var(--font-mono);
    color: var(--color-text-muted);
    padding: 0 6px 4px 16px;
  }

  .logs-toggle {
    background: none;
    border: none;
    font-size: 11px;
    color: var(--color-text-tertiary);
    padding: 4px 2px 2px;
    cursor: pointer;
    transition: color 0.15s;
  }
  .logs-toggle:hover { color: var(--color-text-secondary); }

  .build-log {
    margin-top: 4px;
    padding: 8px 10px;
    background: #1a1a1a;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: var(--radius-sm);
    max-height: 180px;
    overflow-y: auto;
  }

  .log-line {
    font-size: 11px;
    font-family: var(--font-mono);
    color: #d4d4d4;
    line-height: 1.6;
    white-space: pre-wrap;
    word-break: break-all;
  }

  @keyframes spin { to { transform: rotate(360deg); } }
  .spin { animation: spin 0.8s linear infinite; }
</style>

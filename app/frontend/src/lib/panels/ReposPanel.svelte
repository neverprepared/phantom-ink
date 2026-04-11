<script lang="ts">
  import { getApi, openInBrowser } from '../utils/api';
  import { onMount } from 'svelte';
  import { notifications } from '../notifications.svelte';
  import { profileState } from '../stores.svelte';
  import EmptyState from '../components/EmptyState.svelte';
  import Modal from '../components/Modal.svelte';
  import ProfilePicker from '../components/ProfilePicker.svelte';


  let allRepos = $state<any[]>([]);
  let loading = $state(true);
  let showAddModal = $state(false);
  let repoURL = $state('');
  let repoName = $state('');
  let mergeQueue = $state(false);
  let prShepherd = $state(false);
  let selectedProfile = $state('');
  let isAdding = $state(false);

  let activeProfile = $derived(profileState.active);
  let profiles = $derived(profileState.profiles);

  // Filter: active profile shows only that profile's repos; "all" shows everything
  let filteredRepos = $derived.by(() => {
    if (!activeProfile) return allRepos;
    return allRepos.filter(r => {
      const rp = (r.workspace_profile ?? '').toLowerCase();
      return rp === activeProfile.name.toLowerCase();
    });
  });

  // Group repos by profile for display
  let groupedRepos = $derived.by(() => {
    const groups: Record<string, any[]> = {};
    for (const r of filteredRepos) {
      const key = r.workspace_profile || 'unassigned';
      (groups[key] ??= []).push(r);
    }
    return groups;
  });

  async function refresh() {
    const a = await getApi();
    if (!a) return;
    try {
      allRepos = (await a.ListRepos()) ?? [];
    } catch (err) {
      console.error('Failed to fetch repos:', err);
    } finally {
      loading = false;
    }
  }

  onMount(() => { refresh(); });

  function openAddModal() {
    selectedProfile = activeProfile?.name ?? profiles[0]?.name ?? '';
    showAddModal = true;
  }

  async function handleAdd() {
    if (!repoURL.trim() || !selectedProfile) return;
    isAdding = true;
    const a = await getApi();
    if (!a) { isAdding = false; return; }
    try {
      const profile = profiles.find(p => p.name === selectedProfile);
      await a.AddRepo({
        url: repoURL,
        name: repoName || undefined,
        merge_queue: mergeQueue,
        pr_shepherd: prShepherd,
        workspace_profile: selectedProfile,
        workspace_home: profile?.workspace_home ?? '',
      });
      notifications.success('Repo added');
      showAddModal = false;
      repoURL = ''; repoName = ''; mergeQueue = false; prShepherd = false;
      refresh();
    } catch (err: any) {
      notifications.error(`Failed to add repo: ${err}`);
    } finally {
      isAdding = false;
    }
  }

  async function handleDelete(name: string) {
    const a = await getApi();
    if (!a) return;
    try {
      await a.DeleteRepo(name);
      notifications.success(`Removed: ${name}`);
      refresh();
    } catch (err: any) {
      notifications.error(`Failed to remove: ${err}`);
    }
  }
</script>

<div class="panel" aria-busy={loading}>
  <header>
    <h1><span class="accent">repos</span></h1>
    <button class="new-btn" onclick={openAddModal}>+ add repo</button>
  </header>

  {#if loading}
    <div class="loading">loading repos...</div>
  {:else if filteredRepos.length === 0}
    <EmptyState title="No repos tracked" message="Add a GitHub repo to enable multi-agent automation." />
  {:else}
    {#each Object.entries(groupedRepos) as [profileName, repos] (profileName)}
      <div class="group-header">
        <span class="group-label">{profileName}</span>
        <span class="group-count">{repos.length}</span>
      </div>
      <div class="list">
        {#each repos as repo (repo.name)}
          {@const mq = repo.merge_queue_enabled}
          {@const ps = repo.pr_shepherd_enabled}
          <div class="row">
            <div class="row-main">
              <div class="row-title">
                <button class="repo-name" onclick={() => openInBrowser(repo.url)}>{repo.name}</button>
                {#if !activeProfile}
                  <span class="scope-badge">{repo.workspace_profile}</span>
                {/if}
              </div>
              <div class="row-meta">
                {#if mq}<span class="badge mq">merge-queue</span>{/if}
                {#if ps}<span class="badge ps">pr-shepherd</span>{/if}
                <span class="meta-url">{repo.url}</span>
              </div>
            </div>
            <button class="btn-remove" onclick={() => handleDelete(repo.name)}>remove</button>
          </div>
        {/each}
      </div>
    {/each}
  {/if}
</div>

{#if showAddModal}
  <Modal onClose={() => showAddModal = false}>
    {#snippet children()}
      <h2>add repo</h2>
      <p class="modal-sub">register a GitHub repo for multi-agent automation</p>

      <div class="field">
        <label for="rurl">github url</label>
        <input id="rurl" type="url" bind:value={repoURL} placeholder="https://github.com/org/repo" />
      </div>

      <div class="field">
        <label for="rname">short name (optional)</label>
        <input id="rname" type="text" bind:value={repoName} placeholder="auto-derived from URL" />
      </div>

      <div class="checkboxes">
        <label class="checkbox">
          <input type="checkbox" bind:checked={mergeQueue} />
          enable merge-queue agent
        </label>
        <label class="checkbox">
          <input type="checkbox" bind:checked={prShepherd} />
          enable pr-shepherd agent
        </label>
      </div>

      <!-- Profile picker -->
      <ProfilePicker bind:selected={selectedProfile} />

      <div class="modal-actions">
        <button class="btn-cancel" onclick={() => showAddModal = false} disabled={isAdding}>cancel</button>
        <button class="btn-submit" onclick={handleAdd} disabled={isAdding || !repoURL.trim() || !selectedProfile}>
          {isAdding ? 'adding...' : 'add'}
        </button>
      </div>
    {/snippet}
  </Modal>
{/if}

<style>
  .panel { padding-bottom: 24px; }
  header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
  h1 { font-size: 22px; font-weight: 600; }
  .accent { color: var(--color-accent); }

  .new-btn {
    background: rgba(59, 130, 246, 0.1);
    border: 1px solid rgba(59, 130, 246, 0.3);
    color: var(--color-info);
    padding: 7px 14px;
    border-radius: var(--radius-lg);
    font-size: 13px;
    font-weight: 500;
    transition: all 0.15s;
  }
  .new-btn:hover { background: rgba(59, 130, 246, 0.2); }

  .loading { color: var(--color-text-tertiary); font-size: 13px; padding: 24px 0; }

  /* Group headers */
  .group-header {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: 16px;
    margin-bottom: 8px;
    padding-bottom: 6px;
    border-bottom: 1px solid var(--color-border-primary);
  }
  .group-header:first-of-type { margin-top: 0; }

  .group-label {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--color-text-tertiary);
  }

  .group-count {
    font-size: 10px;
    background: var(--color-bg-tertiary);
    color: var(--color-text-tertiary);
    padding: 1px 6px;
    border-radius: 9999px;
  }

  .list { display: flex; flex-direction: column; gap: 8px; margin-bottom: 16px; }

  .row {
    display: flex;
    align-items: center;
    gap: 12px;
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-lg);
    padding: 12px 14px;
  }

  .row-main { flex: 1; min-width: 0; }

  .row-title {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 6px;
  }

  .repo-name {
    font-size: 14px;
    font-weight: 500;
    color: var(--color-text-primary);
    background: none;
    border: none;
    padding: 0;
    text-align: left;
  }
  .repo-name:hover { color: var(--color-accent); }

  .scope-badge {
    font-size: 9px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    padding: 1px 6px;
    border-radius: 9999px;
    background: rgba(245, 158, 11, 0.1);
    color: var(--color-accent);
    border: 1px solid rgba(245, 158, 11, 0.2);
  }

  .row-meta { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }

  .badge {
    font-size: 10px;
    padding: 2px 6px;
    border-radius: 9999px;
    font-weight: 500;
  }
  .badge.mq {
    background: rgba(16, 185, 129, 0.1);
    color: #6ee7b7;
    border: 1px solid rgba(16, 185, 129, 0.2);
  }
  .badge.ps {
    background: rgba(168, 85, 247, 0.1);
    color: #d8b4fe;
    border: 1px solid rgba(168, 85, 247, 0.2);
  }

  .meta-url {
    font-size: 11px;
    color: var(--color-text-tertiary);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .btn-remove {
    background: rgba(239, 68, 68, 0.05);
    border: 1px solid rgba(239, 68, 68, 0.15);
    color: #f87171;
    padding: 4px 10px;
    border-radius: var(--radius-md);
    font-size: 11px;
    flex-shrink: 0;
    transition: all 0.15s;
  }
  .btn-remove:hover { background: rgba(239, 68, 68, 0.15); }

  /* Modal */
  h2 { font-size: 18px; font-weight: 600; margin-bottom: 4px; }
  .modal-sub { font-size: 12px; color: var(--color-text-muted); margin-bottom: 20px; }
  .field { margin-bottom: 14px; }

  .hint {
    font-size: 11px;
    color: var(--color-text-tertiary);
    margin-top: 4px;
  }

  .checkboxes { display: flex; flex-direction: column; gap: 10px; margin-bottom: 14px; }
  .checkbox {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 13px;
    color: var(--color-text-secondary);
    cursor: pointer;
  }
  .checkbox input[type="checkbox"] { width: auto; }

  /* Profile picker */
  .btn-submit {
    background: rgba(59, 130, 246, 0.1);
    border: 1px solid rgba(59, 130, 246, 0.3);
    color: var(--color-info);
    padding: 7px 16px;
    border-radius: var(--radius-md);
    font-size: 13px;
    font-weight: 500;
    transition: all 0.15s;
  }
  .btn-submit:hover { background: rgba(59, 130, 246, 0.2); }
</style>

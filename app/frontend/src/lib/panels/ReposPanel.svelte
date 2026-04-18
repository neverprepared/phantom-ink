<script lang="ts">
  import { getApi, openInBrowser } from '../utils/api';
  import { onMount } from 'svelte';
  import { notifications } from '../notifications.svelte';
  import { profileState } from '../stores.svelte';
  import { brainboxEvents } from '../events.svelte';
  import EmptyState from '../components/EmptyState.svelte';
  import Modal from '../components/Modal.svelte';
  import ProfilePicker from '../components/ProfilePicker.svelte';

  interface Worktree {
    id: string;
    repo_name: string;
    branch: string;
    worktree_path: string;
    session_name?: string;
    status: string; // "ready", "in_use", "error"
    created_at: number;
    error?: string;
  }

  let allRepos = $state<any[]>([]);
  let loading = $state(true);
  let showAddModal = $state(false);
  let repoURL = $state('');
  let repoName = $state('');
  let mergeQueue = $state(false);
  let prShepherd = $state(false);
  let selectedProfile = $state('');
  let isAdding = $state(false);

  // Worktree state
  let worktrees = $state<Record<string, Worktree[]>>({});       // repo_name -> worktrees
  let expandedRepos = $state<Set<string>>(new Set());
  let newBranch = $state<Record<string, string>>({});            // repo_name -> branch input
  let creatingWorktree = $state<Record<string, boolean>>({});
  let launchingSession = $state<Record<string, boolean>>({});    // worktree_id -> loading
  let confirmDeleteWt = $state<string | null>(null);             // worktree id

  let activeProfile = $derived(profileState.active);
  let profiles = $derived(profileState.profiles);

  let filteredRepos = $derived.by(() => {
    if (!activeProfile) return allRepos;
    return allRepos.filter(r => {
      const rp = (r.workspace_profile ?? '').toLowerCase();
      return rp === activeProfile.name.toLowerCase();
    });
  });

  let groupedRepos = $derived.by(() => {
    const groups: Record<string, any[]> = {};
    for (const r of filteredRepos) {
      const key = r.workspace_profile || 'unassigned';
      (groups[key] ??= []).push(r);
    }
    return groups;
  });

  // SSE: reload worktrees on relevant events
  $effect(() => {
    const lastEvent = brainboxEvents.last;
    if (!lastEvent) return;
    try {
      const parsed = typeof lastEvent === 'string' ? JSON.parse(lastEvent) : lastEvent;
      const action = parsed?.action;
      if (action === 'worktree.created' || action === 'worktree.deleted' || action === 'worktree.updated') {
        const repoName = parsed?.data?.repo_name;
        if (repoName && expandedRepos.has(repoName)) {
          loadWorktrees(repoName);
        }
      }
    } catch {}
  });

  async function refresh() {
    const a = await getApi();
    if (!a) { loading = false; return; }
    try {
      allRepos = (await a.ListRepos()) ?? [];
    } catch (err: any) {
      notifications.error(`Failed to load repos: ${err?.message ?? err}`);
    } finally {
      loading = false;
    }
  }

  async function loadWorktrees(repoName: string) {
    const a = await getApi();
    if (!a) return;
    try {
      const result = await a.ListWorktrees(repoName);
      worktrees = { ...worktrees, [repoName]: result ?? [] };
    } catch (err) {
      console.error('Failed to fetch worktrees:', err);
    }
  }

  function toggleExpand(repoName: string) {
    const next = new Set(expandedRepos);
    if (next.has(repoName)) {
      next.delete(repoName);
    } else {
      next.add(repoName);
      loadWorktrees(repoName);
    }
    expandedRepos = next;
  }

  async function createWorktree(repoName: string) {
    const branch = (newBranch[repoName] ?? '').trim();
    if (!branch) return;
    creatingWorktree = { ...creatingWorktree, [repoName]: true };
    const a = await getApi();
    if (!a) { creatingWorktree = { ...creatingWorktree, [repoName]: false }; return; }
    try {
      await a.CreateWorktree({ repo_name: repoName, branch });
      newBranch = { ...newBranch, [repoName]: '' };
      await loadWorktrees(repoName);
    } catch (err: any) {
      notifications.error(`Failed to create worktree: ${err?.message ?? err}`);
    } finally {
      creatingWorktree = { ...creatingWorktree, [repoName]: false };
    }
  }

  async function deleteWorktree(id: string, repoName: string) {
    confirmDeleteWt = null;
    const a = await getApi();
    if (!a) return;
    try {
      await a.DeleteWorktree(id);
      await loadWorktrees(repoName);
    } catch (err: any) {
      notifications.error(`Failed to delete worktree: ${err?.message ?? err}`);
    }
  }

  async function launchSession(wt: Worktree) {
    launchingSession = { ...launchingSession, [wt.id]: true };
    const a = await getApi();
    if (!a) { launchingSession = { ...launchingSession, [wt.id]: false }; return; }
    try {
      const resp = await a.CreateWorktreeSession(wt.id);
      notifications.success(`Session started: ${resp.session}`);
      await loadWorktrees(wt.repo_name);
    } catch (err: any) {
      notifications.error(`Failed to launch session: ${err?.message ?? err}`);
    } finally {
      launchingSession = { ...launchingSession, [wt.id]: false };
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

  function wtStatusClass(status: string) {
    if (status === 'in_use') return 'wt-in-use';
    if (status === 'error') return 'wt-error';
    return 'wt-ready';
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
          {@const expanded = expandedRepos.has(repo.name)}
          {@const repoWorktrees = worktrees[repo.name] ?? []}
          <div class="row" class:expanded>
            <div class="row-main">
              <div class="row-title">
                <button class="expand-btn" onclick={() => toggleExpand(repo.name)} title={expanded ? 'Collapse' : 'Expand worktrees'}>
                  {expanded ? '▼' : '▶'}
                </button>
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

          {#if expanded}
            <div class="worktrees-section">
              <!-- New worktree input -->
              <div class="wt-new-row">
                <input
                  class="wt-branch-input"
                  type="text"
                  placeholder="branch name (e.g. feature/my-task)"
                  bind:value={newBranch[repo.name]}
                  onkeydown={(e) => e.key === 'Enter' && createWorktree(repo.name)}
                />
                <button
                  class="btn-wt-add"
                  onclick={() => createWorktree(repo.name)}
                  disabled={creatingWorktree[repo.name] || !(newBranch[repo.name] ?? '').trim()}
                >
                  {creatingWorktree[repo.name] ? '…' : '+ worktree'}
                </button>
              </div>

              {#if repoWorktrees.length === 0}
                <div class="wt-empty">No worktrees — create one above</div>
              {:else}
                {#each repoWorktrees as wt (wt.id)}
                  <div class="wt-row">
                    <span class="wt-dot {wtStatusClass(wt.status)}"></span>
                    <span class="wt-branch">{wt.branch}</span>
                    <span class="wt-status-label {wtStatusClass(wt.status)}">{wt.status}</span>
                    {#if wt.session_name}
                      <span class="wt-session">{wt.session_name}</span>
                    {/if}
                    <div class="wt-actions">
                      {#if confirmDeleteWt === wt.id}
                        <span class="wt-confirm-label">delete?</span>
                        <button class="btn-wt-confirm-yes" onclick={() => deleteWorktree(wt.id, repo.name)}>yes</button>
                        <button class="btn-wt-cancel" onclick={() => (confirmDeleteWt = null)}>no</button>
                      {:else}
                        {#if wt.status !== 'in_use'}
                          <button
                            class="btn-wt-launch"
                            onclick={() => launchSession(wt)}
                            disabled={launchingSession[wt.id]}
                          >
                            {launchingSession[wt.id] ? '…' : 'Launch'}
                          </button>
                        {/if}
                        <button class="btn-wt-delete" onclick={() => (confirmDeleteWt = wt.id)}>✕</button>
                      {/if}
                    </div>
                  </div>
                {/each}
              {/if}
            </div>
          {/if}
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
  .panel { padding: var(--panel-padding); }
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

  .list { display: flex; flex-direction: column; gap: 0; margin-bottom: 16px; }

  .row {
    display: flex;
    align-items: center;
    gap: 12px;
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-lg);
    padding: 12px 14px;
    margin-bottom: 4px;
  }
  .row.expanded {
    border-bottom-left-radius: 0;
    border-bottom-right-radius: 0;
    border-bottom-color: transparent;
    margin-bottom: 0;
  }

  .row-main { flex: 1; min-width: 0; }

  .row-title {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 6px;
  }

  .expand-btn {
    background: none;
    border: none;
    padding: 0 2px;
    font-size: 10px;
    color: var(--color-text-tertiary);
    cursor: pointer;
    flex-shrink: 0;
    line-height: 1;
  }
  .expand-btn:hover { color: var(--color-text-primary); }

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

  /* Worktrees section */
  .worktrees-section {
    background: var(--color-bg-tertiary);
    border: 1px solid var(--color-border-primary);
    border-top: none;
    border-bottom-left-radius: var(--radius-lg);
    border-bottom-right-radius: var(--radius-lg);
    padding: 10px 14px 12px;
    margin-bottom: 4px;
  }

  .wt-new-row {
    display: flex;
    gap: 8px;
    margin-bottom: 10px;
  }

  .wt-branch-input {
    flex: 1;
    padding: 5px 9px;
    border: 1px solid var(--color-border-secondary);
    border-radius: var(--radius-md);
    background: var(--color-bg-secondary);
    color: var(--color-text-primary);
    font-size: 12px;
    font-family: var(--font-mono, monospace);
  }

  .btn-wt-add {
    padding: 5px 12px;
    border-radius: var(--radius-md);
    border: 1px solid rgba(59, 130, 246, 0.3);
    background: rgba(59, 130, 246, 0.08);
    color: var(--color-info);
    font-size: 12px;
    white-space: nowrap;
    flex-shrink: 0;
  }
  .btn-wt-add:hover:not(:disabled) { background: rgba(59, 130, 246, 0.15); }
  .btn-wt-add:disabled { opacity: 0.4; cursor: not-allowed; }

  .wt-empty {
    font-size: 12px;
    color: var(--color-text-tertiary);
    padding: 4px 0;
  }

  .wt-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 0;
    border-top: 1px solid var(--color-border-primary);
  }
  .wt-row:first-of-type { border-top: none; }

  .wt-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    flex-shrink: 0;
  }
  .wt-dot.wt-ready   { background: #6ee7b7; }
  .wt-dot.wt-in-use  { background: var(--color-info); }
  .wt-dot.wt-error   { background: #f87171; }

  .wt-branch {
    font-size: 12px;
    font-family: var(--font-mono, monospace);
    color: var(--color-text-primary);
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .wt-status-label {
    font-size: 10px;
    flex-shrink: 0;
  }
  .wt-status-label.wt-ready   { color: #6ee7b7; }
  .wt-status-label.wt-in-use  { color: var(--color-info); }
  .wt-status-label.wt-error   { color: #f87171; }

  .wt-session {
    font-size: 10px;
    color: var(--color-text-tertiary);
    font-family: var(--font-mono, monospace);
    flex-shrink: 0;
  }

  .wt-actions {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-shrink: 0;
  }

  .btn-wt-launch {
    padding: 3px 10px;
    border-radius: var(--radius-md);
    border: 1px solid rgba(59, 130, 246, 0.3);
    background: rgba(59, 130, 246, 0.08);
    color: var(--color-info);
    font-size: 11px;
  }
  .btn-wt-launch:hover:not(:disabled) { background: rgba(59, 130, 246, 0.15); }
  .btn-wt-launch:disabled { opacity: 0.4; cursor: not-allowed; }

  .btn-wt-delete {
    background: none;
    border: none;
    color: var(--color-text-tertiary);
    font-size: 12px;
    padding: 2px 4px;
    cursor: pointer;
    border-radius: 3px;
  }
  .btn-wt-delete:hover { color: #f87171; background: rgba(239, 68, 68, 0.1); }

  .wt-confirm-label {
    font-size: 11px;
    color: var(--color-text-secondary);
  }

  .btn-wt-confirm-yes {
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 3px;
    border: 1px solid rgba(239, 68, 68, 0.4);
    background: rgba(239, 68, 68, 0.1);
    color: #f87171;
    cursor: pointer;
  }

  .btn-wt-cancel {
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 3px;
    border: 1px solid var(--color-border-primary);
    background: none;
    color: var(--color-text-tertiary);
    cursor: pointer;
  }

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

  .modal-actions {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    margin-top: 20px;
  }

  .btn-cancel {
    background: none;
    border: 1px solid var(--color-border-primary);
    color: var(--color-text-secondary);
    padding: 7px 14px;
    border-radius: var(--radius-md);
    font-size: 13px;
  }

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

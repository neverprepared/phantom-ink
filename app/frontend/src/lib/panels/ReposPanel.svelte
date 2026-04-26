<script lang="ts">
  import { getApi, openInBrowser } from '../utils/api';
  import { onMount } from 'svelte';
  import { notifications } from '../notifications.svelte';
  import { profileState, profileColorStore } from '../stores.svelte';
  import { getProfileColor, profileColorStyle } from '../utils/profileColors';
  import { brainboxEvents } from '../events.svelte';
  import EmptyState from '../components/EmptyState.svelte';
  import Modal from '../components/Modal.svelte';
  import ProfilePicker from '../components/ProfilePicker.svelte';
  import Badge from '../components/Badge.svelte';

  interface Worktree {
    id: string;
    repo_name: string;
    branch: string;
    worktree_path: string;
    session_name?: string;
    status: string;
    created_at: number;
    error?: string;
  }

  interface RepoPR {
    number: number;
    title: string;
    branch: string;
    author: string;
    url: string;
    is_draft: boolean;
    created_at: string;
    ci_status: string; // "passing", "failing", "pending", ""
    labels: string[];
  }

  let allRepos = $state<any[]>([]);
  let hubTasks = $state<any[]>([]);
  let loading = $state(true);
  let showAddModal = $state(false);
  let repoURL = $state('');
  let repoName = $state('');
  let mergeQueue = $state(false);
  let prShepherd = $state(false);
  let selectedProfile = $state('');
  let isAdding = $state(false);

  // PR state
  let repoPRs = $state<Record<string, RepoPR[]>>({});

  // Branch count state
  let branchCounts = $state<Record<string, number>>({});

  // Worktree state
  let worktrees = $state<Record<string, Worktree[]>>({});
  let newBranch = $state<Record<string, string>>({});
  let creatingWorktree = $state<Record<string, boolean>>({});
  let launchingSession = $state<Record<string, boolean>>({});
  let confirmDeleteWt = $state<string | null>(null);

  // Card-level collapse: collapsed by default
  let expandedCards = $state<Set<string>>(new Set());

  // Expanded sections per repo: repoName → Set<'prs' | 'workers' | 'worktrees'>
  let expandedSections = $state<Record<string, Set<string>>>({});

  // Toggle in-flight state
  let togglingMQ = $state<Record<string, boolean>>({});
  let togglingPS = $state<Record<string, boolean>>({});

  let activeProfile = $derived(profileState.active);
  let profiles = $derived(profileState.profiles);

  let filteredRepos = $derived.by(() => {
    if (!activeProfile) return allRepos;
    return allRepos.filter(r =>
      (r.workspace_profile ?? '').toLowerCase() === activeProfile.name.toLowerCase()
    );
  });

  function tasksForRepo(repoUrl: string): any[] {
    return hubTasks.filter(t => {
      const turl = typeof t.repo_url === 'string' ? t.repo_url : '';
      return turl === repoUrl && (t.status === 'running' || t.status === 'pending');
    });
  }

  $effect(() => {
    const lastEvent = brainboxEvents.last;
    if (!lastEvent) return;
    try {
      const parsed = typeof lastEvent === 'string' ? JSON.parse(lastEvent) : lastEvent;
      const action = parsed?.action ?? parsed?.raw ?? '';
      if (
        action.startsWith('worktree.') ||
        action.startsWith('task.') ||
        action.startsWith('repo.') ||
        action === 'hub'
      ) {
        refresh();
      }
    } catch {}
  });

  async function refresh() {
    const a = await getApi();
    if (!a) { loading = false; return; }
    try {
      const [repos, hs] = await Promise.all([
        a.ListRepos(),
        a.GetHubState(),
      ]);
      allRepos = repos ?? [];
      hubTasks = hs?.tasks ?? [];
      // Eagerly load worktrees and PRs for badge counts
      const repoList = repos ?? [];
      const [wtResults, prResults, branchResults] = await Promise.all([
        Promise.all(
          repoList.map(async (r: any) => {
            try { return { name: r.name, wts: await a.ListWorktrees(r.name) ?? [] }; }
            catch { return { name: r.name, wts: [] }; }
          })
        ),
        Promise.all(
          repoList.map(async (r: any) => {
            try { return { name: r.name, prs: await a.GetRepoPRs(r.url) ?? [] }; }
            catch { return { name: r.name, prs: [] }; }
          })
        ),
        Promise.all(
          repoList.map(async (r: any) => {
            try {
              const branches = await a.GetRepoBranches(r.url) ?? [];
              return { name: r.name, count: branches.length };
            } catch { return { name: r.name, count: 0 }; }
          })
        ),
      ]);
      const wtMap: Record<string, Worktree[]> = {};
      for (const { name, wts } of wtResults) wtMap[name] = wts;
      worktrees = wtMap;
      const prMap: Record<string, RepoPR[]> = {};
      for (const { name, prs } of prResults) prMap[name] = prs;
      repoPRs = prMap;
      const bcMap: Record<string, number> = {};
      for (const { name, count } of branchResults) bcMap[name] = count;
      branchCounts = bcMap;
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
    } catch (err: any) {
      notifications.error('Failed to fetch worktrees: ' + (err?.message ?? err));
    }
  }

  function toggleCard(rname: string) {
    const next = new Set(expandedCards);
    if (next.has(rname)) next.delete(rname);
    else {
      next.add(rname);
      // Load worktrees on first expand if not already loaded
      if (!worktrees[rname]) loadWorktrees(rname);
    }
    expandedCards = next;
  }

  function isSectionExpanded(rname: string, section: string): boolean {
    return expandedSections[rname]?.has(section) ?? false;
  }

  function toggleSection(rname: string, section: string) {
    const current = expandedSections[rname] ?? new Set<string>();
    const next = new Set(current);
    if (next.has(section)) {
      next.delete(section);
    } else {
      next.add(section);
      if (section === 'worktrees') loadWorktrees(rname);
    }
    expandedSections = { ...expandedSections, [rname]: next };
  }

  async function createWorktree(rname: string) {
    const branch = (newBranch[rname] ?? '').trim();
    if (!branch) return;
    creatingWorktree = { ...creatingWorktree, [rname]: true };
    const a = await getApi();
    if (!a) { creatingWorktree = { ...creatingWorktree, [rname]: false }; return; }
    try {
      await a.CreateWorktree({ repo_name: rname, branch });
      newBranch = { ...newBranch, [rname]: '' };
      await loadWorktrees(rname);
    } catch (err: any) {
      notifications.error(`Failed to create worktree: ${err?.message ?? err}`);
    } finally {
      creatingWorktree = { ...creatingWorktree, [rname]: false };
    }
  }

  async function deleteWorktree(id: string, rname: string) {
    confirmDeleteWt = null;
    const a = await getApi();
    if (!a) return;
    try {
      await a.DeleteWorktree(id);
      await loadWorktrees(rname);
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

  async function toggleMergeQueue(repo: any) {
    togglingMQ = { ...togglingMQ, [repo.name]: true };
    const a = await getApi();
    if (!a) { togglingMQ = { ...togglingMQ, [repo.name]: false }; return; }
    try {
      const val = !repo.merge_queue_enabled;
      await a.UpdateRepo(repo.name, { merge_queue: val });
      await refresh();
    } catch (err: any) {
      notifications.error(`Failed to update merge queue: ${err?.message ?? err}`);
    } finally {
      togglingMQ = { ...togglingMQ, [repo.name]: false };
    }
  }

  async function togglePRShepherd(repo: any) {
    togglingPS = { ...togglingPS, [repo.name]: true };
    const a = await getApi();
    if (!a) { togglingPS = { ...togglingPS, [repo.name]: false }; return; }
    try {
      const val = !repo.pr_shepherd_enabled;
      await a.UpdateRepo(repo.name, { pr_shepherd: val });
      await refresh();
    } catch (err: any) {
      notifications.error(`Failed to update PR shepherd: ${err?.message ?? err}`);
    } finally {
      togglingPS = { ...togglingPS, [repo.name]: false };
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

  function shortId(id: string): string {
    return id ? id.substring(0, 8) : '—';
  }

  function extractOwnerRepo(url: string): string {
    try {
      const u = new URL(url);
      return u.pathname.replace(/^\//, '').replace(/\.git$/, '');
    } catch {
      return url;
    }
  }
</script>

<div class="panel" aria-busy={loading}>
  <header>
    <h1><span class="accent">repos</span></h1>
    <div class="header-actions">
      <button class="refresh-btn" onclick={refresh} title="Refresh" aria-label="Refresh">
        <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/></svg>
      </button>
      <button class="new-btn" onclick={openAddModal}>+ add repo</button>
    </div>
  </header>

  {#if loading}
    <div class="loading">loading repos...</div>
  {:else if filteredRepos.length === 0}
    <EmptyState title="No repos tracked" message="Add a GitHub repo to enable multi-agent automation." />
  {:else}
    <div class="cards">
      {#each filteredRepos as repo (repo.name)}
        {@const workers = tasksForRepo(repo.url)}
        {@const workerCount = workers.length}
        {@const repoWorktrees = worktrees[repo.name] ?? []}
        {@const openPRs = repoPRs[repo.name] ?? []}
        {@const prCount = openPRs.length}
        {@const branchCount = branchCounts[repo.name] ?? 0}
        {@const cardExpanded = expandedCards.has(repo.name)}
        {@const wtCount = repoWorktrees.length}
        {@const prExpanded = isSectionExpanded(repo.name, 'prs')}
        {@const workersExpanded = isSectionExpanded(repo.name, 'workers')}
        {@const worktreesExpanded = isSectionExpanded(repo.name, 'worktrees')}

        <div class="repo-card">
          <!-- Card header -->
          <div class="card-header">
            <button class="card-expand-btn" onclick={() => toggleCard(repo.name)} aria-expanded={cardExpanded}>
              <svg class="card-chevron" class:expanded={cardExpanded} xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg>
            </button>
            <span class="status-dot" class:active={workerCount > 0}></span>
            <button class="repo-name-btn" onclick={() => openInBrowser(repo.url)}>
              {repo.name}
              <svg class="ext-icon" xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
            </button>
            {#if repo.is_fork}
              <span class="tag fork-tag">fork</span>
            {/if}
            {#if !activeProfile && repo.workspace_profile}
              {@const rwp = repo.workspace_profile.toLowerCase()}
              <span class="tag profile-tag" style={profileColorStyle(getProfileColor(rwp, profileColorStore.getOverride(rwp)))}>{rwp}</span>
            {/if}
            <div class="header-spacer"></div>
            <!-- Badge counts (always visible) -->
            <span class="header-badge" class:has-count={prCount > 0} title="Open PRs">
              <svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="18" cy="18" r="3"/><circle cx="6" cy="6" r="3"/><path d="M13 6h3a2 2 0 0 1 2 2v7"/><line x1="6" y1="9" x2="6" y2="21"/></svg>
              {prCount}
            </span>
            <span class="header-badge" class:has-count={branchCount > 0} title="Branches">
              <svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="6" y1="3" x2="6" y2="15"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/></svg>
              {branchCount}
            </span>
            <span class="header-badge" class:has-count={workerCount > 0} title="Active workers">
              <svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="3" width="20" height="14" rx="2" ry="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
              {workerCount}
            </span>
            <span class="header-badge" class:has-count={wtCount > 0} title="Worktrees">
              <svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="6" y1="3" x2="6" y2="15"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/></svg>
              {wtCount}
            </span>
            <button class="btn-remove" onclick={() => handleDelete(repo.name)}>remove</button>
          </div>

          {#if cardExpanded}
          <!-- Repo meta -->
          <div class="card-meta">
            <span class="meta-item">
              <svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="6" y1="3" x2="6" y2="15"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/></svg>
              {repo.target_branch || 'main'}
            </span>
            {#if repo.is_fork && repo.upstream_url}
              <span class="meta-item meta-upstream">↑ {extractOwnerRepo(repo.upstream_url)}</span>
            {/if}
            <span class="meta-url">{repo.url}</span>
          </div>

          <!-- Toggle switches: merge-queue, pr-shepherd -->
          <div class="toggle-row">
            <button
              class="toggle-switch"
              class:on={repo.merge_queue_enabled}
              class:off={!repo.merge_queue_enabled}
              disabled={togglingMQ[repo.name]}
              onclick={() => toggleMergeQueue(repo)}
              title="Toggle merge-queue agent"
            >
              <span class="switch-track">
                <span class="switch-knob"></span>
              </span>
              <span class="switch-label">merge-queue</span>
            </button>

            <button
              class="toggle-switch"
              class:on={repo.pr_shepherd_enabled}
              class:off={!repo.pr_shepherd_enabled}
              disabled={togglingPS[repo.name]}
              onclick={() => togglePRShepherd(repo)}
              title="Toggle PR shepherd agent"
            >
              <span class="switch-track">
                <span class="switch-knob"></span>
              </span>
              <span class="switch-label">pr-shepherd</span>
            </button>
          </div>

          <!-- Collapsible sections -->
          <div class="sections">

            <!-- Open PRs -->
            <div class="section">
              <button
                class="section-toggle"
                class:expanded={prExpanded}
                onclick={() => toggleSection(repo.name, 'prs')}
              >
                <svg class="chevron" xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg>
                <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="18" cy="18" r="3"/><circle cx="6" cy="6" r="3"/><path d="M13 6h3a2 2 0 0 1 2 2v7"/><line x1="6" y1="9" x2="6" y2="21"/></svg>
                open prs
                {#if prCount > 0}
                  <span class="section-count">{prCount}</span>
                {/if}
              </button>
              {#if prExpanded}
                <div class="section-body">
                  {#if openPRs.length === 0}
                    <div class="empty-section">No open PRs</div>
                  {:else}
                    {#each openPRs as pr (pr.number)}
                      <div class="pr-row">
                        <span class="pr-ci {pr.ci_status || 'none'}" title={pr.ci_status || 'no checks'}></span>
                        <button class="pr-title-btn" onclick={() => openInBrowser(pr.url)}>
                          <span class="pr-number">#{pr.number}</span>
                          {pr.title}
                        </button>
                        {#if pr.is_draft}
                          <span class="tag draft-tag">draft</span>
                        {/if}
                        <div class="header-spacer"></div>
                        <span class="pr-branch">{pr.branch}</span>
                        <span class="pr-author">{pr.author}</span>
                      </div>
                    {/each}
                  {/if}
                </div>
              {/if}
            </div>

            <!-- Active Workers -->
            <div class="section">
              <button
                class="section-toggle"
                class:expanded={workersExpanded}
                onclick={() => toggleSection(repo.name, 'workers')}
              >
                <svg class="chevron" xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg>
                <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="3" width="20" height="14" rx="2" ry="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
                active workers
                {#if workerCount > 0}
                  <span class="section-count">{workerCount}</span>
                {/if}
              </button>
              {#if workersExpanded}
                <div class="section-body">
                  {#if workers.length === 0}
                    <div class="empty-section">No active workers</div>
                  {:else}
                    {#each workers as task (task.id)}
                      <div class="worker-row">
                        <span class="worker-id">{shortId(task.id)}</span>
                        <span class="worker-agent">{task.agent_name || '—'}</span>
                        <Badge text={task.status} variant={task.status} />
                        {#if task.session_name}
                          <span class="worker-session">{task.session_name}</span>
                        {/if}
                      </div>
                    {/each}
                  {/if}
                </div>
              {/if}
            </div>

            <!-- Worktrees / Branch Overview -->
            <div class="section">
              <button
                class="section-toggle"
                class:expanded={worktreesExpanded}
                onclick={() => toggleSection(repo.name, 'worktrees')}
              >
                <svg class="chevron" xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg>
                <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="6" y1="3" x2="6" y2="15"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/></svg>
                worktrees
                {#if repoWorktrees.length > 0}
                  <span class="section-count">{repoWorktrees.length}</span>
                {/if}
              </button>
              {#if worktreesExpanded}
                <div class="section-body">
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
                    <div class="empty-section">No worktrees — create one above</div>
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
                                {launchingSession[wt.id] ? '…' : 'launch'}
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
            </div>

          </div><!-- /sections -->
          {/if}
        </div><!-- /repo-card -->
      {/each}
    </div>
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

  header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 20px;
  }

  h1 { font-size: 22px; font-weight: 600; }
  .accent { color: var(--color-accent); }

  .header-actions { display: flex; gap: 8px; align-items: center; }

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

  .refresh-btn {
    background: none;
    border: 1px solid var(--color-border-secondary);
    color: var(--color-text-tertiary);
    padding: 6px;
    border-radius: var(--radius-md);
    display: flex;
    transition: all 0.15s;
  }
  .refresh-btn:hover { color: var(--color-text-primary); border-color: var(--color-text-tertiary); }

  .loading { color: var(--color-text-tertiary); font-size: 13px; padding: 24px 0; }

  /* Cards grid */
  .cards {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .repo-card {
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-primary);
    border-left: 3px solid var(--color-border-primary);
    border-radius: var(--radius-xl);
    padding: 14px 18px;
    transition: border-left-color 0.2s;
  }

  .repo-card:has(.status-dot.active) {
    border-left-color: var(--color-success);
  }

  /* Card header */
  .card-header {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .repo-card:has(.card-meta) .card-header {
    margin-bottom: 8px;
  }

  .card-expand-btn {
    background: none;
    border: none;
    padding: 2px;
    cursor: pointer;
    display: flex;
    align-items: center;
    color: var(--color-text-tertiary);
    flex-shrink: 0;
    transition: color 0.15s;
  }
  .card-expand-btn:hover { color: var(--color-text-secondary); }

  .card-chevron {
    transition: transform 0.15s;
  }
  .card-chevron.expanded {
    transform: rotate(90deg);
  }

  .header-badge {
    display: flex;
    align-items: center;
    gap: 3px;
    font-size: 11px;
    color: var(--color-text-muted);
    padding: 2px 6px;
    border-radius: 9999px;
    background: var(--color-bg-tertiary);
    border: 1px solid var(--color-border-primary);
    flex-shrink: 0;
    font-variant-numeric: tabular-nums;
  }
  .header-badge.has-count {
    color: var(--color-text-secondary);
    border-color: var(--color-border-secondary);
  }

  .status-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--color-text-muted);
    flex-shrink: 0;
  }
  .status-dot.active {
    background: var(--color-success);
    box-shadow: 0 0 6px rgba(16, 185, 129, 0.4);
  }

  .repo-name-btn {
    display: flex;
    align-items: center;
    gap: 4px;
    font-size: 15px;
    font-weight: 600;
    color: var(--color-text-primary);
    background: none;
    border: none;
    padding: 0;
    text-align: left;
    transition: color 0.15s;
  }
  .repo-name-btn:hover { color: var(--color-accent); }

  .ext-icon { opacity: 0.4; flex-shrink: 0; }
  .repo-name-btn:hover .ext-icon { opacity: 0.8; }

  .header-spacer { flex: 1; }

  .tag {
    font-size: 9px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    padding: 1px 6px;
    border-radius: 9999px;
    flex-shrink: 0;
  }

  .fork-tag {
    background: rgba(168, 85, 247, 0.1);
    color: #d8b4fe;
    border: 1px solid rgba(168, 85, 247, 0.2);
  }

  .profile-tag {
    border: 1px solid transparent;
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

  /* Card meta */
  .card-meta {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 10px;
    flex-wrap: wrap;
  }

  .meta-item {
    display: flex;
    align-items: center;
    gap: 4px;
    font-size: 12px;
    color: var(--color-text-tertiary);
    font-family: var(--font-mono, monospace);
  }

  .meta-upstream {
    color: var(--color-text-tertiary);
    font-size: 11px;
  }

  .meta-url {
    font-size: 11px;
    color: var(--color-text-tertiary);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    flex: 1;
    min-width: 0;
  }

  /* Toggle switches */
  .toggle-row {
    display: flex;
    gap: 10px;
    margin-bottom: 10px;
    flex-wrap: wrap;
  }

  .toggle-switch {
    display: flex;
    align-items: center;
    gap: 7px;
    background: none;
    border: none;
    padding: 3px 0;
    cursor: pointer;
    transition: opacity 0.15s;
  }
  .toggle-switch:disabled { opacity: 0.5; cursor: not-allowed; }

  .switch-track {
    position: relative;
    display: inline-flex;
    align-items: center;
    width: 28px;
    height: 16px;
    border-radius: 8px;
    background: var(--color-bg-tertiary);
    border: 1px solid var(--color-border-secondary);
    transition: background 0.2s, border-color 0.2s;
    flex-shrink: 0;
  }

  .toggle-switch.on .switch-track {
    background: rgba(16, 185, 129, 0.25);
    border-color: rgba(16, 185, 129, 0.5);
  }

  .switch-knob {
    position: absolute;
    left: 2px;
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: var(--color-text-tertiary);
    transition: transform 0.2s, background 0.2s;
  }

  .toggle-switch.on .switch-knob {
    transform: translateX(12px);
    background: var(--color-success);
  }

  .switch-label {
    font-size: 12px;
    color: var(--color-text-tertiary);
    transition: color 0.15s;
  }
  .toggle-switch.on .switch-label { color: var(--color-text-secondary); }

  /* Collapsible sections */
  .sections {
    border-top: 1px solid var(--color-border-primary);
    margin-top: 2px;
  }

  .section {
    border-bottom: 1px solid var(--color-border-primary);
  }
  .section:last-child { border-bottom: none; }

  .section-toggle {
    display: flex;
    align-items: center;
    gap: 6px;
    width: 100%;
    background: none;
    border: none;
    padding: 8px 0;
    font-size: 12px;
    color: var(--color-text-tertiary);
    text-align: left;
    cursor: pointer;
    transition: color 0.15s;
    text-transform: none;
    letter-spacing: normal;
    font-weight: normal;
  }
  .section-toggle:hover { color: var(--color-text-secondary); }

  .chevron {
    transition: transform 0.15s;
    flex-shrink: 0;
    color: var(--color-text-tertiary);
  }
  .section-toggle.expanded .chevron { transform: rotate(90deg); }

  .section-count {
    font-size: 10px;
    background: var(--color-bg-tertiary);
    color: var(--color-text-tertiary);
    border: 1px solid var(--color-border-secondary);
    border-radius: 9999px;
    padding: 0 5px;
    line-height: 1.5;
    margin-left: 2px;
  }

  .section-body {
    padding: 4px 0 10px 18px;
  }

  /* Empty section */
  .empty-section {
    font-size: 12px;
    color: var(--color-text-tertiary);
    padding: 2px 0;
  }

  /* PRs */
  .pr-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 5px 0;
    border-top: 1px solid var(--color-border-primary);
    font-size: 12px;
  }
  .pr-row:first-child { border-top: none; }

  .pr-ci {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    flex-shrink: 0;
  }
  .pr-ci.passing  { background: #6ee7b7; }
  .pr-ci.failing  { background: #f87171; }
  .pr-ci.pending  { background: var(--color-warning, #fbbf24); }
  .pr-ci.none     { background: var(--color-text-muted); }

  .pr-title-btn {
    display: flex;
    align-items: center;
    gap: 5px;
    background: none;
    border: none;
    padding: 0;
    font-size: 12px;
    color: var(--color-text-primary);
    text-align: left;
    cursor: pointer;
    transition: color 0.15s;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
  }
  .pr-title-btn:hover { color: var(--color-accent); }

  .pr-number {
    color: var(--color-text-tertiary);
    font-family: var(--font-mono, monospace);
    font-size: 11px;
    flex-shrink: 0;
  }

  .draft-tag {
    background: rgba(148, 163, 184, 0.1);
    color: var(--color-text-tertiary);
    border: 1px solid rgba(148, 163, 184, 0.2);
  }

  .pr-branch {
    font-size: 11px;
    font-family: var(--font-mono, monospace);
    color: var(--color-text-tertiary);
    flex-shrink: 0;
    max-width: 140px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .pr-author {
    font-size: 11px;
    color: var(--color-text-tertiary);
    flex-shrink: 0;
  }

  /* Workers */
  .worker-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 5px 0;
    border-top: 1px solid var(--color-border-primary);
    font-size: 12px;
  }
  .worker-row:first-child { border-top: none; }

  .worker-id {
    font-family: var(--font-mono, monospace);
    font-size: 11px;
    color: var(--color-text-tertiary);
    flex-shrink: 0;
  }

  .worker-agent {
    font-size: 12px;
    color: var(--color-text-primary);
    font-weight: 500;
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .worker-session {
    font-size: 10px;
    color: var(--color-text-tertiary);
    font-family: var(--font-mono, monospace);
    flex-shrink: 0;
  }

  /* Worktrees */
  .wt-new-row {
    display: flex;
    gap: 8px;
    margin-bottom: 8px;
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
    transition: all 0.15s;
  }
  .btn-wt-add:hover:not(:disabled) { background: rgba(59, 130, 246, 0.15); }
  .btn-wt-add:disabled { opacity: 0.4; cursor: not-allowed; }

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
    transition: all 0.15s;
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
    transition: all 0.15s;
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
    transition: all 0.15s;
  }

  .btn-wt-cancel {
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 3px;
    border: 1px solid var(--color-border-primary);
    background: none;
    color: var(--color-text-tertiary);
    cursor: pointer;
    transition: all 0.15s;
  }

  /* Modal */
  h2 { font-size: 18px; font-weight: 600; margin-bottom: 4px; }
  .modal-sub { font-size: 12px; color: var(--color-text-muted); margin-bottom: 20px; }
  .field { margin-bottom: 14px; }

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
    transition: all 0.15s;
  }
  .btn-cancel:hover { background: rgba(255,255,255,0.05); }

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
  .btn-submit:hover:not(:disabled) { background: rgba(59, 130, 246, 0.2); }
  .btn-submit:disabled { opacity: 0.5; cursor: not-allowed; }
</style>

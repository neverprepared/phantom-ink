<script lang="ts">
  // Code — the active profile's GitHub launchpad. One pane covering what's
  // waiting on you (PRs, assigned issues), your notifications, and your
  // repositories, with a one-click hand-off from any row into work: an
  // autonomous fleet task, an interactive container session, or a clone on this
  // host with a terminal open in it.
  //
  // Read-only and profile-scoped: the backend resolves the profile's EXISTING
  // curated GITHUB_TOKEN per call and nothing is cached, so switching profiles
  // switches accounts. State lives in codeState so the sections and the
  // dispatch modal share one source.
  import { onMount } from 'svelte';
  import { profileState } from '../stores.svelte';
  import { currentPanel, settingsState, jobsState } from '../stores.svelte';
  import { codeState, itemCloneURL, type Issue, type Repo } from '../stores/code.svelte';
  import { openInBrowser } from '../utils/api';
  import { timeAgoOrDate } from '../utils/format';
  import CardExpander from '../components/CardExpander.svelte';
  import DispatchModal from '../components/DispatchModal.svelte';
  import MarkdownRenderer from '../components/MarkdownRenderer.svelte';
  import EmptyState from '../components/EmptyState.svelte';
  import Spinner from '../components/Spinner.svelte';

  const activeProfile = $derived(profileState.active?.name ?? '');

  const PROVIDER_LABEL: Record<string, string> = { github: 'GitHub', ado: 'Azure DevOps' };
  const providerLabel = (p: string) => PROVIDER_LABEL[p] ?? p;

  let attentionOpen = $state(true);
  let notificationsOpen = $state(false);
  let reposOpen = $state(false);

  // Detail-view section state. Branches and commits open by default — they are
  // what "what's going on in this repo" usually means.
  let branchesOpen = $state(true);
  let commitsOpen = $state(true);
  let detailPRsOpen = $state(false);
  let detailIssuesOpen = $state(false);
  let readmeOpen = $state(true);

  onMount(() => { void codeState.loadAgents(); });

  // Re-fetch on mount AND on every profile switch — the whole page is one
  // profile's account, so a switch is a full reload, not a filter.
  $effect(() => {
    const p = activeProfile;
    codeState.reset();
    void codeState.refresh(p);
  });

  /** epoch-ms for the shared time formatters; GitHub sends RFC3339. */
  function ts(iso: string): number {
    const t = Date.parse(iso);
    return Number.isNaN(t) ? 0 : t;
  }

  // GITHUB_TOKEN is curated in the Profiles tab's gateway-env editor.
  function openProfiles() {
    settingsState.open('profiles');
  }

  /** The dispatch target for a repo row — shared by both of its lanes. */
  function repoTarget(r: Repo) {
    return {
      kind: 'repo' as const,
      repoFullName: r.full_name,
      repoURL: r.clone_url,
      number: 0,
      title: '',
      htmlURL: r.html_url,
    };
  }

  function beginWorkRepo(r: Repo) {
    codeState.openDispatch(repoTarget(r));
  }

  // The host lane, no modal: clone into this profile's workspace (or open an
  // existing checkout untouched) and drop a terminal running claude in it.
  function cloneAndOpen(r: Repo) {
    void codeState.cloneAndOpen(activeProfile, repoTarget(r));
  }

  // ── Detail view ──────────────────────────────────────────────────────────

  function openDetail(r: Repo) {
    void codeState.openDetail(activeProfile, r);
  }

  /**
   * The first line of a commit message. The backend keeps the whole message on
   * purpose; a row only has space for the subject.
   */
  function commitSubject(message: string): string {
    const first = (message ?? '').split('\n', 1)[0];
    return first.trim() || '(no message)';
  }

  function shortSHA(sha: string): string {
    return (sha ?? '').slice(0, 7);
  }

  function beginWorkIssue(i: Issue) {
    codeState.openDispatch({
      kind: i.is_pull_request ? 'pr' : 'issue',
      repoFullName: i.repo_full_name,
      // Provider-aware: derives the correct clone URL for github vs ado rows.
      repoURL: itemCloneURL(i),
      number: i.number,
      title: i.title,
      htmlURL: i.html_url,
    });
  }

  // ADO work items live at .../_workitems/edit/{id} — not a cloneable repo, so
  // there is no dispatch/session/clone target for them. ADO PRs and all GitHub
  // rows still resolve to a real repo and keep the begin-work lane.
  function canBeginWork(row: Issue): boolean {
    return !(row.provider === 'ado' && !row.is_pull_request);
  }
</script>

<!-- Provider logo for the far-left column of overview rows. Inline SVG (no
     network); GitHub takes the theme text color, Azure DevOps its brand blue. -->
{#snippet providerLogo(provider: string)}
  {#if provider === 'ado'}
    <svg class="provider-logo ado" viewBox="0 0 24 24" role="img" aria-label={providerLabel(provider)}>
      <title>{providerLabel(provider)}</title>
      <path fill="currentColor" d="M0 8.877L2.247 5.91l8.405-3.416V.022l7.37 5.393L2.966 8.338v8.225L0 15.707zm24-4.45v14.651l-5.753 4.9-9.303-3.057v3.056l-5.978-7.416 15.057 1.798V5.415z" />
    </svg>
  {:else}
    <svg class="provider-logo github" viewBox="0 0 24 24" role="img" aria-label={providerLabel(provider)}>
      <title>{providerLabel(provider)}</title>
      <path fill="currentColor" d="M12 .5C5.37.5 0 5.78 0 12.29c0 5.2 3.44 9.6 8.2 11.16.6.11.82-.25.82-.56 0-.28-.01-1.02-.02-2-3.34.7-4.04-1.58-4.04-1.58-.55-1.36-1.33-1.73-1.33-1.73-1.09-.73.08-.71.08-.71 1.2.08 1.83 1.21 1.83 1.21 1.07 1.79 2.81 1.27 3.5.97.11-.76.42-1.27.76-1.56-2.67-.3-5.47-1.31-5.47-5.84 0-1.29.47-2.34 1.24-3.17-.12-.3-.54-1.52.12-3.16 0 0 1.01-.32 3.3 1.21.96-.26 1.98-.39 3-.4 1.02.01 2.04.14 3 .4 2.28-1.53 3.29-1.21 3.29-1.21.66 1.64.24 2.86.12 3.16.77.83 1.24 1.88 1.24 3.17 0 4.54-2.81 5.53-5.49 5.83.43.36.81 1.09.81 2.2 0 1.59-.01 2.87-.01 3.26 0 .31.22.68.83.56A12.02 12.02 0 0024 12.29C24 5.78 18.63.5 12 .5z" />
    </svg>
  {/if}
{/snippet}

<div class="code">
  <header class="head">
    <div>
      <h1>Code</h1>
      <p class="sub">
        {#if codeState.detailRepo}
          <code class="repo">{codeState.detailRepo.full_name}</code> in
          <strong>{activeProfile || 'no profile'}</strong>
        {:else}
          GitHub for <strong>{activeProfile || 'no profile'}</strong> — what's waiting on you, and a
          one-click hand-off to an agent.
        {/if}
      </p>
    </div>
    <!-- Refresh re-runs whichever view is showing, not always the overview. -->
    {#if codeState.detailRepo}
      <button
        class="refresh"
        title="Refresh this repository"
        disabled={codeState.detailLoading}
        onclick={() => openDetail(codeState.detailRepo!)}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12a9 9 0 1 1-3-6.7"/><polyline points="21 3 21 9 15 9"/></svg>
        {codeState.detailLoading ? 'Refreshing…' : 'Refresh'}
      </button>
    {:else}
      <button class="refresh" title="Refresh" disabled={codeState.loading} onclick={() => codeState.refresh(activeProfile)}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12a9 9 0 1 1-3-6.7"/><polyline points="21 3 21 9 15 9"/></svg>
        {codeState.loading ? 'Refreshing…' : 'Refresh'}
      </button>
    {/if}
  </header>

  {#if !activeProfile}
    <EmptyState title="No active profile" message="Select a profile to see its GitHub account." />
  {:else if codeState.tokenMissing || codeState.tokenInvalid}
    <!-- Token state is actionable, not an error: point at where it's edited. -->
    <div class="banner">
      <div>
        <strong>
          {codeState.tokenInvalid ? 'GitHub rejected this profile’s token' : 'No GitHub token for this profile'}
        </strong>
        <p>
          {codeState.tokenInvalid
            ? 'The stored GITHUB_TOKEN is expired or wrong. Re-curate it to bring this page back.'
            : 'Curate a GITHUB_TOKEN for this profile and its repositories, PRs, and notifications appear here.'}
        </p>
      </div>
      <button class="banner-action" onclick={openProfiles}>Open Profiles</button>
    </div>
  {/if}

  <!-- The two views are one top-level branch. The overview's DATA lives in
       codeState, not in this component, so swapping back is instant: there is
       no second GitHub round-trip for a list that was on screen a click ago. -->
  {#if codeState.detailRepo}
    {@const repo = codeState.detailRepo}
    <div class="detail-head">
      <button class="link back" onclick={() => codeState.closeDetail()}>← back</button>
      {@render providerLogo(repo.provider)}
      <code class="repo strong">{repo.full_name}</code>
      {#if repo.default_branch}<span class="badge">{repo.default_branch}</span>{/if}
      <div class="detail-actions">
        <button class="link" onclick={() => openInBrowser(repo.html_url)}>open ↗</button>
        <button
          class="link"
          disabled={codeState.cloningRepo !== ''}
          title="Clone into this profile's workspace and open a terminal"
          onclick={() => cloneAndOpen(repo)}
        >
          {codeState.cloningRepo === repo.full_name ? 'Cloning…' : 'Clone + terminal'}
        </button>
        <button class="link accent" onclick={() => beginWorkRepo(repo)}>Begin work ▾</button>
      </div>
    </div>
    {#if repo.description}<p class="detail-desc">{repo.description}</p>{/if}

    {#if codeState.detailError}
      <EmptyState title="Couldn't load this repository" message={codeState.detailError} />
    {/if}

    <!-- D1 · Branches -->
    <section class="card">
      <CardExpander
        label="branches"
        count={String(codeState.repoBranches.length)}
        bind:open={branchesOpen}
      >
        {#if codeState.branchesError}
          <p class="section-error">{codeState.branchesError}</p>
        {:else if codeState.detailLoading && codeState.repoBranches.length === 0}
          <div class="skeleton"><span class="bar w-50"></span><span class="bar w-35"></span></div>
        {:else if codeState.repoBranches.length === 0}
          <p class="muted-note">No branches visible.</p>
        {:else}
          <ul class="rows">
            {#each codeState.repoBranches as b (b.name)}
              <li class="row compact">
                <div class="row-meta">
                  <code class="repo">{b.name}</code>
                  {#if b.name === repo.default_branch}<span class="badge why">default</span>{/if}
                  <span class="ago"><code class="repo">{shortSHA(b.sha)}</code></span>
                </div>
              </li>
            {/each}
          </ul>
        {/if}
      </CardExpander>
    </section>

    <!-- D2 · Recent commits -->
    <section class="card">
      <CardExpander
        label="recent commits"
        count={String(codeState.repoCommits.length)}
        hint="newest first"
        bind:open={commitsOpen}
      >
        {#if codeState.commitsError}
          <p class="section-error">{codeState.commitsError}</p>
        {:else if codeState.detailLoading && codeState.repoCommits.length === 0}
          <div class="skeleton"><span class="bar w-80"></span><span class="bar w-65"></span><span class="bar w-50"></span></div>
        {:else if codeState.repoCommits.length === 0}
          <p class="muted-note">No commits.</p>
        {:else}
          <ul class="rows">
            {#each codeState.repoCommits as c (c.sha)}
              <li class="row">
                <div class="row-main">
                  <span class="title">{commitSubject(c.message)}</span>
                </div>
                <div class="row-meta">
                  <code class="repo">{shortSHA(c.sha)}</code>
                  {#if c.author}<span class="by">{c.author}</span>{/if}
                  <span class="ago">{timeAgoOrDate(ts(c.date))}</span>
                  {#if c.html_url}
                    <button class="link" onclick={() => openInBrowser(c.html_url)}>open ↗</button>
                  {/if}
                </div>
              </li>
            {/each}
          </ul>
        {/if}
      </CardExpander>
    </section>

    <!-- D3 · Open PRs -->
    <section class="card">
      <CardExpander label="open pull requests" count={String(codeState.repoPRs.length)} bind:open={detailPRsOpen}>
        {#if codeState.prsError}
          <p class="section-error">{codeState.prsError}</p>
        {:else if codeState.detailLoading && codeState.repoPRs.length === 0}
          <div class="skeleton"><span class="bar w-80"></span><span class="bar w-50"></span></div>
        {:else if codeState.repoPRs.length === 0}
          <p class="muted-note">No open pull requests.</p>
        {:else}
          <ul class="rows">
            {#each codeState.repoPRs as row (row.html_url)}
              <li class="row">
                <div class="row-main">
                  <span class="num">#{row.number}</span>
                  <span class="title">{row.title}</span>
                </div>
                <div class="row-meta">
                  {#if row.draft}<span class="badge muted">draft</span>{/if}
                  {#if row.user}<span class="by">@{row.user}</span>{/if}
                  <span class="ago">{timeAgoOrDate(ts(row.updated_at))}</span>
                  <button class="link" onclick={() => openInBrowser(row.html_url)}>open ↗</button>
                  <button class="link accent" onclick={() => beginWorkIssue(row)}>Begin work ▾</button>
                </div>
              </li>
            {/each}
          </ul>
        {/if}
      </CardExpander>
    </section>

    <!-- D4 · Open issues -->
    <section class="card">
      <CardExpander label="open issues" count={String(codeState.repoIssues.length)} bind:open={detailIssuesOpen}>
        {#if codeState.repoIssuesError}
          <p class="section-error">{codeState.repoIssuesError}</p>
        {:else if codeState.detailLoading && codeState.repoIssues.length === 0}
          <div class="skeleton"><span class="bar w-80"></span><span class="bar w-50"></span></div>
        {:else if codeState.repoIssues.length === 0}
          <p class="muted-note">No open issues.</p>
        {:else}
          <ul class="rows">
            {#each codeState.repoIssues as row (row.html_url)}
              <li class="row">
                <div class="row-main">
                  <span class="num">#{row.number}</span>
                  <span class="title">{row.title}</span>
                </div>
                <div class="row-meta">
                  {#if row.user}<span class="by">@{row.user}</span>{/if}
                  <span class="ago">{timeAgoOrDate(ts(row.updated_at))}</span>
                  <button class="link" onclick={() => openInBrowser(row.html_url)}>open ↗</button>
                  <button class="link accent" onclick={() => beginWorkIssue(row)}>Begin work ▾</button>
                </div>
              </li>
            {/each}
          </ul>
        {/if}
      </CardExpander>
    </section>

    <!-- D5 · README. Untrusted repo content — MarkdownRenderer sanitizes. -->
    <section class="card">
      <CardExpander label="readme" bind:open={readmeOpen}>
        {#if codeState.readmeError}
          <p class="section-error">{codeState.readmeError}</p>
        {:else}
          {#if codeState.readmeURL}
            <p class="readme-link">
              <button class="link" onclick={() => openInBrowser(codeState.readmeURL)}>view on GitHub ↗</button>
            </p>
          {/if}
          <MarkdownRenderer
            content={codeState.readme}
            loading={codeState.detailLoading && !codeState.readme}
          />
        {/if}
      </CardExpander>
    </section>
  {:else}
  {#if codeState.loadError}
    <EmptyState title="Couldn't load GitHub" message={codeState.loadError} />
  {:else if codeState.loading && codeState.repos.length === 0 && codeState.attentionCount === 0}
    <Spinner />
  {/if}

  {#if codeState.showProviderFilter}
    <div class="provider-filter" role="group" aria-label="Filter by provider">
      <button class="pf-btn" class:active={codeState.providerFilter === 'all'} onclick={() => (codeState.providerFilter = 'all')}>All</button>
      <button class="pf-btn" class:active={codeState.providerFilter === 'github'} onclick={() => (codeState.providerFilter = 'github')}>
        {@render providerLogo('github')}<span>GitHub</span>
      </button>
      <button class="pf-btn" class:active={codeState.providerFilter === 'ado'} onclick={() => (codeState.providerFilter = 'ado')}>
        {@render providerLogo('ado')}<span>Azure DevOps</span>
      </button>
    </div>
  {/if}

  <!-- 1 · Needs attention: open PRs (mine + awaiting my review) + my issues -->
  <section class="card">
    <CardExpander
      label="needs attention"
      count={String(codeState.attentionCount)}
      hint="open PRs and assigned issues"
      bind:open={attentionOpen}
    >
      {#if codeState.pullRequestsError}
        <p class="section-error">pull requests: {codeState.pullRequestsError}</p>
      {/if}
      {#if codeState.issuesError}
        <p class="section-error">issues: {codeState.issuesError}</p>
      {/if}
      {#if codeState.jiraError}
        <p class="section-error">jira unavailable — issue status hidden. {codeState.jiraError}</p>
      {/if}
      {#if codeState.reasonsPresent.length > 1}
        <div class="reason-chips" role="group" aria-label="Filter by reason">
          <button class="chip" class:active={codeState.reasonFilter === 'all'} onclick={() => (codeState.reasonFilter = 'all')}>all</button>
          {#each codeState.reasonsPresent as reason}
            <button class="chip" class:active={codeState.reasonFilter === reason} onclick={() => (codeState.reasonFilter = reason)}>{reason}</button>
          {/each}
        </div>
      {/if}
      {#if codeState.attentionCount === 0}
        <p class="muted-note">Nothing open against you. Enjoy it.</p>
      {:else if codeState.filteredAttention.length === 0}
        <p class="muted-note">No items match these filters.</p>
      {:else}
        <ul class="rows">
          {#each codeState.filteredAttention as row (row.html_url)}
            <li class="row with-logo">
              {@render providerLogo(row.provider)}
              <div class="row-body">
                <div class="row-main">
                  <span class="num">#{row.number}</span>
                  <span class="title">{row.title}</span>
                </div>
                {#if codeState.jiraFor(row).length > 0}
                  <div class="jira-chips">
                    {#each codeState.jiraFor(row) as issue (issue.key)}
                      <button
                        class="jira-chip jira-{issue.status_category || 'new'}"
                        title={issue.summary}
                        onclick={() => openInBrowser(issue.url)}
                      >
                        <span class="jira-key">{issue.key}</span>
                        <span class="jira-status">{issue.status}</span>
                        {#if issue.assignee}<span class="jira-who">{issue.assignee}</span>{/if}
                        {#if issue.manual}<span class="jira-manual" title="linked by hand, not derived from the title">·</span>{/if}
                      </button>
                    {/each}
                  </div>
                {/if}
                <code class="repo">{row.repo_full_name}</code>
                <div class="row-meta">
                  <span class="badge">{row.is_pull_request ? 'pr' : (row.provider === 'ado' ? 'work item' : 'issue')}</span>
                  {#if row.draft}<span class="badge muted">draft</span>{/if}
                  <span class="badge why">{row.reason}</span>
                  {#if row.user}<span class="by">@{row.user}</span>{/if}
                  <span class="ago">{timeAgoOrDate(ts(row.updated_at))}</span>
                  <button class="link" onclick={() => openInBrowser(row.html_url)}>open ↗</button>
                  {#if canBeginWork(row)}
                    <button class="link accent" onclick={() => beginWorkIssue(row)}>Begin work ▾</button>
                  {/if}
                </div>
              </div>
            </li>
          {/each}
        </ul>
      {/if}
    </CardExpander>
  </section>

  <!-- 2 · Notifications -->
  <section class="card">
    <CardExpander
      label="notifications"
      count={String(codeState.notifications.length)}
      bind:open={notificationsOpen}
    >
      {#if codeState.notificationsError}
        <p class="section-error">{codeState.notificationsError}</p>
      {/if}
      {#if codeState.filteredNotifications.length === 0}
        <p class="muted-note">Inbox clear.</p>
      {:else}
        <div class="card-actions">
          <button class="link" onclick={() => codeState.markAllNotificationsRead()}>mark all read</button>
        </div>
        <ul class="rows">
          {#each codeState.filteredNotifications as n (n.id)}
            <li class="row with-logo">
              {@render providerLogo('github')}
              <div class="row-body">
                <div class="row-main">
                  <span class="title">{n.subject_title}</span>
                </div>
                <code class="repo">{n.repo_full_name}</code>
                <div class="row-meta">
                  <span class="badge">{n.subject_type}</span>
                  <span class="badge why">{n.reason}</span>
                  <span class="ago">{timeAgoOrDate(ts(n.updated_at))}</span>
                  {#if n.url}
                    <button class="link" onclick={() => openInBrowser(n.url)}>open ↗</button>
                  {/if}
                  <button class="link accent" title="Mark this notification read" onclick={() => codeState.markNotificationRead(n.id)}>✓ read</button>
                </div>
              </div>
            </li>
          {/each}
        </ul>
      {/if}
    </CardExpander>
  </section>

  <!-- 3 · Repositories, pushed-desc, with a client-side filter -->
  <section class="card">
    <CardExpander
      label="repositories"
      count={String(codeState.repos.length)}
      hint="most recently pushed first"
      bind:open={reposOpen}
    >
      {#if codeState.reposError}
        <p class="section-error">{codeState.reposError}</p>
      {/if}
      {#if codeState.repos.length === 0}
        <p class="muted-note">No repositories visible to this token.</p>
      {:else}
        <input class="filter" placeholder="Filter repositories…" bind:value={codeState.repoFilter} />
        <ul class="rows">
          {#each codeState.filteredRepos as r (r.full_name)}
            <li class="row with-logo">
              {@render providerLogo(r.provider)}
              <div class="row-body">
                <div class="row-main">
                  <!-- The repo name IS the link into the detail view; the row's
                       existing actions keep working alongside it. -->
                  <button class="repo-link" title="Open this repository" onclick={() => openDetail(r)}>
                    <code class="repo">{r.full_name}</code>
                  </button>
                  {#if r.description}<span class="desc">{r.description}</span>{/if}
                </div>
                <div class="row-meta">
                <span class="badge">{r.default_branch}</span>
                <span class="stars">★ {r.stars}</span>
                {#if r.open_issues}<span class="muted-small">{r.open_issues} open</span>{/if}
                <span class="ago">{timeAgoOrDate(ts(r.pushed_at))}</span>
                <button class="link" onclick={() => openInBrowser(r.html_url)}>open ↗</button>
                <button
                  class="link"
                  disabled={codeState.cloningRepo !== ''}
                  title="Clone into this profile's workspace and open a terminal"
                  onclick={() => cloneAndOpen(r)}
                >
                  {codeState.cloningRepo === r.full_name ? 'Cloning…' : 'Clone + terminal'}
                </button>
                <button class="link accent" onclick={() => beginWorkRepo(r)}>Begin work ▾</button>
                <button class="link" title="Branches, commits, PRs, issues, README" onclick={() => openDetail(r)}>details →</button>
                </div>
              </div>
            </li>
          {/each}
        </ul>
        {#if codeState.filteredRepos.length === 0}
          <p class="muted-note">No repository matches “{codeState.repoFilter}”.</p>
        {/if}
      {/if}
    </CardExpander>
  </section>

  {/if}

  <!-- Lane results belong to both views: work can be started from either. -->
  {#if codeState.lastTaskID}
    <p class="dispatched">
      Dispatched <code>{codeState.lastTaskID.slice(0, 8)}</code> —
      <button class="link accent" onclick={() => jobsState.open('jobs')}>watch it in Jobs</button>
    </p>
  {/if}
  {#if codeState.lastSessionURL}
    <p class="dispatched">
      Interactive session up —
      <button class="link accent" onclick={() => openInBrowser(codeState.lastSessionURL)}>attach ↗</button>
    </p>
  {/if}
  {#if codeState.lastClonePath}
    <p class="dispatched">
      Terminal open in <code>{codeState.lastClonePath}</code>
    </p>
  {/if}
  {#if codeState.cloneError}
    <!-- git's own stderr — on a private repo that wording is the whole answer. -->
    <p class="section-error">{codeState.cloneError}</p>
  {/if}
</div>

<DispatchModal profile={activeProfile} />

<style>
  .code { padding: var(--panel-padding); color: var(--color-text-primary); }
  .head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: var(--spacing-lg); }
  h1 { font-size: 1.4rem; margin: 0; }
  .sub { color: var(--color-text-muted); margin: 2px 0 0; font-size: 0.85rem; }
  .sub strong { color: var(--color-text-secondary); }

  .refresh {
    display: inline-flex; align-items: center; gap: 6px;
    background: transparent; color: var(--color-text-secondary);
    border: 1px solid var(--color-border-secondary); border-radius: var(--radius-sm);
    padding: 5px 10px; font-size: 0.8rem; cursor: pointer;
  }
  .refresh:disabled { opacity: 0.6; cursor: progress; }

  .card {
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-md);
    padding: var(--spacing-md) var(--spacing-lg);
    margin-bottom: var(--spacing-md);
  }

  .banner {
    display: flex; align-items: center; justify-content: space-between; gap: var(--spacing-lg);
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-warning, var(--color-border-primary));
    border-radius: var(--radius-md);
    padding: var(--spacing-md) var(--spacing-lg);
    margin-bottom: var(--spacing-md);
  }
  .banner p { margin: 3px 0 0; font-size: 0.82rem; color: var(--color-text-muted); }
  .banner-action {
    background: transparent; color: var(--color-accent);
    border: 1px solid var(--color-accent); border-radius: var(--radius-sm);
    padding: 5px 12px; font-size: 0.8rem; cursor: pointer; white-space: nowrap;
  }

  .section-error {
    margin: 0 0 var(--spacing-sm);
    font-size: 0.78rem;
    color: var(--color-error);
    font-family: var(--font-mono);
  }
  .muted-note { color: var(--color-text-muted); font-size: 0.82rem; margin: 0; }
  .muted-small { color: var(--color-text-muted); font-size: 0.72rem; }

  .filter {
    width: 100%;
    background: var(--color-bg-primary);
    border: 1px solid var(--color-border-secondary);
    border-radius: var(--radius-sm);
    color: var(--color-text-primary);
    padding: 6px 8px; font-size: 0.82rem; font-family: inherit;
    margin-bottom: var(--spacing-sm);
  }

  .rows { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
  .row {
    border: 1px solid var(--color-border-secondary);
    border-radius: var(--radius-sm);
    padding: 7px 10px;
    display: flex; flex-direction: column; gap: 5px;
  }
  .row-main { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
  .row-meta { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .repo { font-family: var(--font-mono); font-size: 0.76rem; color: var(--color-text-muted); }
  .num { font-family: var(--font-mono); font-size: 0.76rem; color: var(--color-text-tertiary); }
  .title { font-size: 0.87rem; }
  .desc { font-size: 0.78rem; color: var(--color-text-muted); }
  .by { font-size: 0.72rem; color: var(--color-text-muted); }
  .stars { font-size: 0.72rem; color: var(--color-text-muted); }
  .ago { font-size: 0.72rem; color: var(--color-text-muted); margin-left: auto; }

  .badge {
    font-size: 0.68rem; padding: 2px 7px; border-radius: 999px;
    border: 1px solid var(--color-border-secondary); color: var(--color-text-secondary);
    text-transform: lowercase; white-space: nowrap;
  }
  .badge.muted { color: var(--color-text-tertiary); }
  .badge.why { color: var(--color-accent); border-color: var(--color-accent); }

  /* Overview rows carry a provider logo in a fixed far-left column, so every
     row's content aligns to the same left edge — a uniform grid across the list. */
  .row.with-logo {
    display: grid;
    grid-template-columns: 20px 1fr;
    align-items: center;
    column-gap: 10px;
  }
  .row-body { display: flex; flex-direction: column; gap: 5px; min-width: 0; }
  .provider-logo { width: 18px; height: 18px; display: block; flex: none; }
  .provider-logo.github { color: var(--color-text-primary); }
  .provider-logo.ado { color: #2b88d8; }

  /* Provider segmented control (top of overview) */
  .provider-filter {
    display: flex; gap: 2px; margin-bottom: var(--spacing-sm);
    background: var(--color-bg-primary);
    border: 1px solid var(--color-border-secondary);
    border-radius: var(--radius-sm);
    padding: 2px; width: fit-content;
  }
  .pf-btn {
    display: flex; align-items: center; gap: 6px;
    background: transparent; border: none; cursor: pointer;
    color: var(--color-text-secondary); font-size: 0.78rem; font-family: inherit;
    padding: 4px 10px; border-radius: calc(var(--radius-sm) - 2px);
  }
  .pf-btn:hover { color: var(--color-text-primary); }
  .pf-btn.active { background: var(--color-bg-elev, var(--color-bg-secondary)); color: var(--color-text-primary); }
  .pf-btn .provider-logo { width: 14px; height: 14px; }

  /* Reason filter chips (needs-attention card) */
  .reason-chips { display: flex; flex-wrap: wrap; gap: 5px; margin-bottom: var(--spacing-sm); }
  .chip {
    background: transparent; cursor: pointer; font-family: inherit;
    border: 1px solid var(--color-border-secondary); color: var(--color-text-secondary);
    font-size: 0.7rem; padding: 2px 9px; border-radius: 999px; text-transform: lowercase;
  }
  .chip:hover { color: var(--color-text-primary); }
  .chip.active { background: var(--color-accent); border-color: var(--color-accent); color: #fff; }

  .card-actions { display: flex; justify-content: flex-end; margin-bottom: 4px; }

  .link {
    background: transparent; border: none; padding: 0;
    color: var(--color-text-secondary); font-size: 0.75rem; cursor: pointer;
  }
  .link:hover { text-decoration: underline; }
  .link:disabled { opacity: 0.5; cursor: progress; text-decoration: none; }
  .link.accent { color: var(--color-accent); }

  /* ── Detail view ──────────────────────────────────────────────────────── */
  .detail-head {
    display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
    margin-bottom: 4px;
  }
  .detail-actions { display: flex; align-items: center; gap: 10px; margin-left: auto; }
  .back { color: var(--color-accent); font-size: 0.8rem; }
  .repo.strong { color: var(--color-text-primary); font-size: 0.9rem; }
  .detail-desc { margin: 0 0 var(--spacing-md); font-size: 0.8rem; color: var(--color-text-muted); }
  .readme-link { margin: 0 0 var(--spacing-sm); }

  /* A repo name that navigates. Styled as text, not a button, so the row still
     reads as a row. */
  .repo-link {
    background: transparent; border: none; padding: 0; cursor: pointer;
    font: inherit; color: inherit; text-align: left;
  }
  .repo-link:hover .repo { color: var(--color-accent); text-decoration: underline; }

  .row.compact { padding: 4px 10px; }

  /* Per-section skeleton while a detail fetch is in flight. */
  .skeleton { display: flex; flex-direction: column; gap: 8px; }
  .bar {
    height: 10px; border-radius: var(--radius-sm);
    background: var(--color-bg-tertiary, var(--color-bg-primary));
    opacity: 0.6;
  }
  .w-35 { width: 35%; }
  .w-50 { width: 50%; }
  .w-65 { width: 65%; }
  .w-80 { width: 80%; }

  .dispatched { font-size: 0.8rem; color: var(--color-text-muted); }
  .dispatched code { font-family: var(--font-mono); }

  /* Jira issue chips. Namespaced away from .chip, which is already the
     reason-filter button in this panel. */
  .jira-chips {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    margin: 2px 0 4px;
  }
  .jira-chip {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 1px 7px;
    border-radius: 10px;
    border: 1px solid var(--border);
    background: transparent;
    color: var(--text);
    font: inherit;
    font-size: 11px;
    cursor: pointer;
  }
  .jira-chip:hover { border-color: var(--accent); }
  .jira-key { font-weight: 600; }
  .jira-status { opacity: 0.85; }
  .jira-who { opacity: 0.6; }
  /* A manual link is marked so it is distinguishable from one the title
     produced — removing it is only possible on the jira tab. */
  .jira-manual { opacity: 0.55; font-weight: 700; }
  /* Jira's statusCategory is a closed 3-value enum, so this mapping is total. */
  .jira-done { border-color: #3fb950; }
  .jira-indeterminate { border-color: #d29922; }
  .jira-new { opacity: 0.8; }
</style>

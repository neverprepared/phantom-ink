<script lang="ts">
  // Code — the active profile's GitHub launchpad. One pane covering what's
  // waiting on you (PRs, assigned issues), your notifications, and your
  // repositories, with a one-click hand-off from any row to a fleet agent.
  //
  // Read-only and profile-scoped: the backend resolves the profile's EXISTING
  // curated GITHUB_TOKEN per call and nothing is cached, so switching profiles
  // switches accounts. State lives in codeState so the sections and the
  // dispatch modal share one source.
  import { onMount } from 'svelte';
  import { profileState } from '../stores.svelte';
  import { currentPanel, settingsState } from '../stores.svelte';
  import { codeState, type Issue, type Repo } from '../stores/code.svelte';
  import { openInBrowser } from '../utils/api';
  import { timeAgoOrDate } from '../utils/format';
  import CardExpander from '../components/CardExpander.svelte';
  import DispatchModal from '../components/DispatchModal.svelte';
  import EmptyState from '../components/EmptyState.svelte';
  import Spinner from '../components/Spinner.svelte';

  const activeProfile = $derived(profileState.active?.name ?? '');

  let attentionOpen = $state(true);
  let notificationsOpen = $state(false);
  let reposOpen = $state(false);

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

  function dispatchRepo(r: Repo) {
    codeState.openDispatch({
      kind: 'repo',
      repoFullName: r.full_name,
      repoURL: r.clone_url || r.html_url,
      number: 0,
      title: '',
      htmlURL: r.html_url,
    });
  }

  function dispatchIssue(i: Issue) {
    codeState.openDispatch({
      kind: i.is_pull_request ? 'pr' : 'issue',
      repoFullName: i.repo_full_name,
      // Search hits carry no clone_url; derive the repo page and let the agent
      // clone from it (git accepts the html URL).
      repoURL: `https://github.com/${i.repo_full_name}.git`,
      number: i.number,
      title: i.title,
      htmlURL: i.html_url,
    });
  }
</script>

<div class="code">
  <header class="head">
    <div>
      <h1>Code</h1>
      <p class="sub">
        GitHub for <strong>{activeProfile || 'no profile'}</strong> — what's waiting on you, and a
        one-click hand-off to an agent.
      </p>
    </div>
    <button class="refresh" title="Refresh" disabled={codeState.loading} onclick={() => codeState.refresh(activeProfile)}>
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12a9 9 0 1 1-3-6.7"/><polyline points="21 3 21 9 15 9"/></svg>
      {codeState.loading ? 'Refreshing…' : 'Refresh'}
    </button>
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

  {#if codeState.loadError}
    <EmptyState title="Couldn't load GitHub" message={codeState.loadError} />
  {:else if codeState.loading && codeState.repos.length === 0 && codeState.attentionCount === 0}
    <Spinner />
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
      {#if codeState.attentionCount === 0}
        <p class="muted-note">Nothing open against you. Enjoy it.</p>
      {:else}
        <ul class="rows">
          {#each [...codeState.pullRequests, ...codeState.issues] as row (row.html_url)}
            <li class="row">
              <div class="row-main">
                <code class="repo">{row.repo_full_name}</code>
                <span class="num">#{row.number}</span>
                <span class="title">{row.title}</span>
              </div>
              <div class="row-meta">
                <span class="badge">{row.is_pull_request ? 'pr' : 'issue'}</span>
                {#if row.draft}<span class="badge muted">draft</span>{/if}
                <span class="badge why">{row.reason}</span>
                {#if row.user}<span class="by">@{row.user}</span>{/if}
                <span class="ago">{timeAgoOrDate(ts(row.updated_at))}</span>
                <button class="link" onclick={() => openInBrowser(row.html_url)}>open ↗</button>
                <button class="link accent" onclick={() => dispatchIssue(row)}>⚡ Dispatch</button>
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
      {#if codeState.notifications.length === 0}
        <p class="muted-note">Inbox clear.</p>
      {:else}
        <ul class="rows">
          {#each codeState.notifications as n (n.id)}
            <li class="row">
              <div class="row-main">
                <span class="title">{n.subject_title}</span>
              </div>
              <div class="row-meta">
                <span class="badge">{n.subject_type}</span>
                <span class="badge why">{n.reason}</span>
                <code class="repo">{n.repo_full_name}</code>
                <span class="ago">{timeAgoOrDate(ts(n.updated_at))}</span>
                {#if n.url}
                  <button class="link" onclick={() => openInBrowser(n.url)}>open ↗</button>
                {/if}
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
            <li class="row">
              <div class="row-main">
                <code class="repo">{r.full_name}</code>
                {#if r.description}<span class="desc">{r.description}</span>{/if}
              </div>
              <div class="row-meta">
                <span class="badge">{r.default_branch}</span>
                <span class="stars">★ {r.stars}</span>
                {#if r.open_issues}<span class="muted-small">{r.open_issues} open</span>{/if}
                <span class="ago">{timeAgoOrDate(ts(r.pushed_at))}</span>
                <button class="link" onclick={() => openInBrowser(r.html_url)}>open ↗</button>
                <button class="link accent" onclick={() => dispatchRepo(r)}>⚡ Dispatch</button>
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

  {#if codeState.lastTaskID}
    <p class="dispatched">
      Dispatched <code>{codeState.lastTaskID.slice(0, 8)}</code> —
      <button class="link accent" onclick={() => (currentPanel.value = 'jobs')}>watch it in Jobs</button>
    </p>
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

  .link {
    background: transparent; border: none; padding: 0;
    color: var(--color-text-secondary); font-size: 0.75rem; cursor: pointer;
  }
  .link:hover { text-decoration: underline; }
  .link.accent { color: var(--color-accent); }

  .dispatched { font-size: 0.8rem; color: var(--color-text-muted); }
  .dispatched code { font-family: var(--font-mono); }
</style>

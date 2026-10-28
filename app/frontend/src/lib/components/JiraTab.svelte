<script lang="ts">
  // The Jira tab: this profile's tickets from its own JQL, each showing the
  // code that references it. Conventional links are DERIVED from PR titles;
  // the "link code" picker stores an override for the ones that aren't.
  import { getApi } from '../utils/api';
  import { notifications } from '../notifications.svelte';
  import { profileState } from '../stores.svelte';
  import { codeState } from '../stores/code.svelte';
  import { jiraState, jiraDispatchTarget } from '../stores/jira.svelte';
  import { timeAgo } from '../utils/format';
  import DispatchModal from './DispatchModal.svelte';
  import type { main } from '../../../wailsjs/go/models';

  // Editor state, deliberately component-local: the JQL draft and the link
  // picker describe one visit, not the profile. The TICKETS live in
  // jiraState, which is module-scoped so they survive this component being
  // destroyed on every tab and profile switch.
  let jqlDraft = $state('');
  let editingJql = $state(false);
  let linkingKey = $state(''); // ticket currently showing the PR picker
  let pickingKey = $state(''); // ticket currently showing the repo picker

  let profile = $derived(profileState.active?.name ?? '');
  let tickets = $derived(jiraState.rows);

  /** Reload, then re-seed the draft unless the operator is mid-edit. */
  async function load(force = false) {
    await jiraState.refresh(profile, force);
    if (!editingJql) jqlDraft = jiraState.jql;
  }

  // Re-read on mount and on every profile change. Cached tickets stay on
  // screen while this revalidates behind them.
  $effect(() => {
    if (profile) void load();
  });

  // The code overview and the agent roster are what this tab's PICKERS are
  // built from — repos for "which repo does this ticket's work happen in", and
  // pull requests for the link picker, which until now told the operator to
  // "open the code tab first". Both stores are cached per profile, so warming
  // them here costs one round trip at most and nothing on a revisit.
  $effect(() => {
    if (!profile) return;
    void codeState.refresh(profile);
    void codeState.loadAgents();
  });

  // Live age for the freshness label; one tick a minute while mounted.
  let now = $state(Date.now());
  $effect(() => {
    const t = setInterval(() => (now = Date.now()), 60_000);
    return () => clearInterval(t);
  });
  const fetchedAgo = $derived(
    jiraState.fetchedAt ? timeAgo(Date.parse(jiraState.fetchedAt), now) : ''
  );

  /**
   * Run a write that changes what the query returns, then reload.
   *
   * The backend drops its own memo on these, but the frontend cache has to be
   * dropped too — otherwise the resource keeps serving the value it already
   * holds and the new link never appears.
   */
  async function afterWrite(fn: () => Promise<void>, ok?: string) {
    try {
      await fn();
      jiraState.invalidate(profile);
      if (ok) notifications.success(ok);
      await load();
    } catch (e: any) {
      notifications.error(`${e?.message ?? e}`);
    }
  }

  async function saveJql() {
    await afterWrite(async () => {
      const a = await getApi();
      if (!a) throw new Error('API bindings unavailable');
      await a.SetJiraJQL(profile, jqlDraft);
      editingJql = false;
    });
  }

  async function link(issueKey: string, rowKey: string) {
    await afterWrite(async () => {
      const a = await getApi();
      if (!a) throw new Error('API bindings unavailable');
      await a.LinkJiraIssue(profile, issueKey, rowKey);
      linkingKey = '';
    }, `${issueKey} linked`);
  }

  async function unlink(issueKey: string, rowKey: string) {
    await afterWrite(async () => {
      const a = await getApi();
      if (!a) throw new Error('API bindings unavailable');
      await a.UnlinkJiraIssue(profile, issueKey, rowKey);
    }, `${issueKey} unlinked`);
  }

  function openInBrowser(url: string) {
    (window as any).runtime?.BrowserOpenURL(url);
  }

  // ── Begin work ───────────────────────────────────────────────────────────
  //
  // The same two lanes a repo row gets in the code tab, with one extra step: a
  // ticket carries no repo, so it is resolved from the ticket's code refs and
  // only asked for when there are none.

  function target(tk: main.JiraTicket, chosen?: string) {
    return jiraDispatchTarget(tk, codeState.repos, chosen);
  }

  /** True when this ticket can start work without asking which repo. */
  function hasRepo(tk: main.JiraTicket): boolean {
    return target(tk) !== null;
  }

  /** Open the dispatch modal, asking for a repo first if we cannot infer one. */
  function beginWork(tk: main.JiraTicket, chosen?: string) {
    const t = target(tk, chosen);
    if (!t) {
      pickingKey = tk.key;
      return;
    }
    pickingKey = '';
    codeState.openDispatch(t);
  }

  /** The host lane: clone into this profile's workspace and open a terminal. */
  function cloneAndOpen(tk: main.JiraTicket, chosen?: string) {
    const t = target(tk, chosen);
    if (!t) {
      pickingKey = tk.key;
      return;
    }
    pickingKey = '';
    void codeState.cloneAndOpen(profile, t);
  }

  // Which lane the repo picker is standing in for, so choosing a repo
  // continues what the operator actually clicked.
  let pickingLane = $state<'work' | 'clone'>('work');

  function askRepo(tk: main.JiraTicket, lane: 'work' | 'clone') {
    pickingLane = lane;
    pickingKey = tk.key;
  }

  function chooseRepo(tk: main.JiraTicket, fullName: string) {
    if (!fullName) return;
    if (pickingLane === 'clone') cloneAndOpen(tk, fullName);
    else beginWork(tk, fullName);
  }

  function chipClass(cat: string): string {
    if (cat === 'done') return 'tk-status done';
    if (cat === 'indeterminate') return 'tk-status progress';
    return 'tk-status todo';
  }
</script>

<div class="jira-tab">
  <div class="panel-header">
    <h1 class="page-title">jira</h1>
    <div class="head-actions">
      <button class="btn ghost sm" onclick={() => (editingJql = !editingJql)}>
        {editingJql ? 'cancel' : 'edit query'}
      </button>
      <!-- Disabled only on a cold load: during a background check there ARE
           tickets on screen, so asking again is reasonable. -->
      <button
        class="btn ghost sm"
        onclick={() => load(true)}
        disabled={jiraState.loading}
      >{jiraState.loading || jiraState.backgroundBusy ? 'refreshing…' : 'refresh'}</button>
    </div>
  </div>

  <!-- Same freshness affordance as the code tab: tickets kept across a tab or
       profile switch must not be mistaken for live ones. -->
  {#if fetchedAgo || jiraState.backgroundBusy}
    <p class="freshness" aria-live="polite">
      {#if jiraState.backgroundBusy}
        <span class="dot" aria-hidden="true"></span>
        <span>Checking for updates…</span>
        {#if fetchedAgo}<span class="sep">·</span><span>showing tickets from {fetchedAgo}</span>{/if}
      {:else}
        <span>Updated {fetchedAgo}</span>
      {/if}
    </p>
  {/if}

  {#if editingJql}
    <div class="jql-edit">
      <textarea bind:value={jqlDraft} rows="2" spellcheck="false" aria-label="JQL query"></textarea>
      <button class="btn sm" onclick={saveJql}>save</button>
    </div>
  {:else if jiraState.jql}
    <code class="jql-view">{jiraState.jql}</code>
  {/if}

  {#if jiraState.jiraError}
    <!-- A JQL typo is a 400. Say so; an empty list would read as "no tickets". -->
    <p class="section-error">{jiraState.jiraError}</p>
  {:else if jiraState.loadError}
    <!-- Distinct from the above: Jira was never reached at all. -->
    <p class="section-error">{jiraState.loadError}</p>
  {/if}

  {#if jiraState.loading && tickets.length === 0}
    <p class="muted-note">loading…</p>
  {:else if !jiraState.jiraError && tickets.length === 0}
    <p class="muted-note">No tickets match this query.</p>
  {:else}
    <ul class="tickets">
      {#each tickets as tk (tk.key)}
        <li class="ticket">
          <div class="tk-main">
            <button class="tk-key" onclick={() => openInBrowser(tk.url)}>{tk.key}</button>
            <span class="tk-summary">{tk.summary}</span>
            <span class={chipClass(tk.status_category)}>{tk.status}</span>
            {#if tk.assignee}<span class="tk-who">{tk.assignee}</span>{/if}
          </div>

          {#each tk.refs ?? [] as ref (ref.row_key)}
            <div class="tk-ref">
              <span class="tk-arrow">↳</span>
              <button class="tk-pr" onclick={() => openInBrowser(ref.url)}>#{ref.number} {ref.title}</button>
              <code class="tk-repo">{ref.repo}</code>
              {#if ref.manual}
                <span class="tk-badge">linked</span>
                <button class="tk-unlink" title="remove this manual link" onclick={() => unlink(tk.key, ref.row_key)}>×</button>
              {:else}
                <span class="tk-badge muted">derived</span>
              {/if}
            </div>
          {/each}

          {#each tk.orphan_rows ?? [] as orphan (orphan)}
            <div class="tk-ref orphan">
              <span class="tk-arrow">↳</span>
              <code class="tk-repo">{orphan}</code>
              <span class="tk-badge warn">not in current results</span>
              <button class="tk-unlink" title="remove this stale link" onclick={() => unlink(tk.key, orphan)}>×</button>
            </div>
          {/each}

          <!-- Begin work. The lanes are the code tab's, so the labels match:
               the modal lane (autonomous task / interactive session) and the
               host lane (clone + terminal) that skips the modal entirely. -->
          <div class="tk-lanes">
            <button class="link accent" onclick={() => (hasRepo(tk) ? beginWork(tk) : askRepo(tk, 'work'))}>
              Begin work ▾
            </button>
            <button
              class="link"
              disabled={codeState.cloningRepo !== ''}
              onclick={() => (hasRepo(tk) ? cloneAndOpen(tk) : askRepo(tk, 'clone'))}
            >
              {codeState.cloningRepo !== '' ? 'cloning…' : 'Clone + terminal'}
            </button>
            {#if hasRepo(tk)}
              <!-- Which repo was inferred, so an agent is never sent at a
                   repository the operator did not realise they had chosen. -->
              <code class="tk-inferred">{target(tk)?.repoFullName}</code>
            {/if}
          </div>

          {#if pickingKey === tk.key}
            <div class="tk-picker">
              {#if codeState.repos.length === 0}
                <span class="muted-note">No repositories loaded for this profile.</span>
              {:else}
                <select
                  aria-label="choose a repository for {tk.key}"
                  onchange={(e) => chooseRepo(tk, (e.currentTarget as HTMLSelectElement).value)}
                >
                  <option value="">choose a repository…</option>
                  {#each codeState.repos as r (r.full_name)}
                    <option value={r.full_name}>{r.full_name}</option>
                  {/each}
                </select>
              {/if}
              <button class="btn ghost sm" onclick={() => (pickingKey = '')}>cancel</button>
            </div>
          {/if}

          {#if (tk.refs ?? []).length === 0 && (tk.orphan_rows ?? []).length === 0}
            {#if linkingKey === tk.key}
              <div class="tk-picker">
                {#if codeState.pullRequests.length === 0}
                  <span class="muted-note">No pull requests loaded — open the code tab first.</span>
                {:else}
                  <select
                    aria-label="link a pull request to {tk.key}"
                    onchange={(e) => link(tk.key, (e.currentTarget as HTMLSelectElement).value)}
                  >
                    <option value="">choose a pull request…</option>
                    {#each codeState.pullRequests as pr (pr.html_url)}
                      <option value={`${pr.provider}:${pr.repo_full_name}#${pr.number}`}>
                        #{pr.number} {pr.title} · {pr.repo_full_name}
                      </option>
                    {/each}
                  </select>
                {/if}
                <button class="btn ghost sm" onclick={() => (linkingKey = '')}>cancel</button>
              </div>
            {:else}
              <button class="tk-link-btn" onclick={() => (linkingKey = tk.key)}>link code …</button>
            {/if}
          {/if}
        </li>
      {/each}
    </ul>
  {/if}
</div>

<!-- Same modal the code tab uses. Driven by module-scoped dispatch state, and
     the Work hub renders one tab at a time, so the two mounts never coexist. -->
<DispatchModal {profile} />

<style>
  .jira-tab { padding: var(--panel-padding); display: flex; flex-direction: column; gap: var(--spacing-sm); }
  .head-actions { display: flex; gap: 6px; }
  /* Freshness line — quieter than any content, matching the code tab's. */
  .freshness {
    display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
    margin: 0; font-size: 0.75rem; color: var(--color-text-muted);
  }
  .freshness .sep { opacity: 0.5; }
  .freshness .dot {
    width: 6px; height: 6px; border-radius: 50%;
    background: var(--color-accent, var(--color-text-secondary));
    animation: fresh-pulse 1.4s ease-in-out infinite;
  }
  @keyframes fresh-pulse {
    0%, 100% { opacity: 0.25; }
    50% { opacity: 1; }
  }
  /* Respect the OS setting: a persistent pulse is what this hurts. */
  @media (prefers-reduced-motion: reduce) {
    .freshness .dot { animation: none; opacity: 0.8; }
  }
  .jql-view { font-family: var(--font-mono); font-size: 10px; color: var(--color-text-tertiary); }
  .jql-edit { display: flex; gap: 6px; align-items: flex-start; }
  .jql-edit textarea {
    flex: 1; font-family: var(--font-mono); font-size: 11px; padding: 6px 8px;
    background: var(--color-bg-tertiary); color: var(--color-text-primary);
    border: 1px solid var(--color-border-secondary); border-radius: var(--radius-sm);
    resize: vertical;
  }
  .section-error { color: var(--color-error); font-size: 12px; margin: 0; }
  .muted-note { color: var(--color-text-tertiary); font-size: 12px; }

  .tickets { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; }
  .ticket { padding: 8px 2px; border-top: 1px solid var(--color-border-primary); }
  .tk-main { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .tk-key {
    font-family: var(--font-mono); font-size: 12px; font-weight: 600;
    background: none; border: none; padding: 0; cursor: pointer; color: var(--color-text-primary);
  }
  .tk-key:hover { text-decoration: underline; }
  .tk-summary { font-size: 12px; color: var(--color-text-primary); }
  .tk-status { font-size: 10px; padding: 1px 6px; border-radius: 10px; border: 1px solid var(--color-border-primary); }
  .tk-status.done { border-color: #3fb950; }
  .tk-status.progress { border-color: #d29922; }
  .tk-status.todo { opacity: 0.75; }
  .tk-who { font-size: 11px; color: var(--color-text-tertiary); }

  .tk-ref { display: flex; align-items: center; gap: 6px; margin: 3px 0 0 14px; font-size: 11px; }
  .tk-ref.orphan { opacity: 0.7; }
  .tk-arrow { color: var(--color-text-tertiary); }
  .tk-pr { background: none; border: none; padding: 0; cursor: pointer; color: var(--color-text-secondary); font: inherit; text-align: left; }
  .tk-pr:hover { color: var(--color-text-primary); text-decoration: underline; }
  .tk-repo { font-family: var(--font-mono); font-size: 10px; color: var(--color-text-tertiary); }
  .tk-badge { font-size: 9px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--color-text-tertiary); border: 1px solid var(--color-border-primary); border-radius: 3px; padding: 0 4px; }
  .tk-badge.warn { color: var(--color-error); border-color: var(--color-error); }
  .tk-unlink { background: none; border: none; cursor: pointer; color: var(--color-text-tertiary); font-size: 13px; line-height: 1; padding: 0 2px; }
  .tk-unlink:hover { color: var(--color-error); }

  .tk-picker { display: flex; gap: 6px; align-items: center; margin: 4px 0 0 14px; }
  .tk-picker select {
    font-size: 11px; padding: 2px 6px; max-width: 460px;
    background: var(--color-bg-tertiary); color: var(--color-text-primary);
    border: 1px solid var(--color-border-secondary); border-radius: var(--radius-sm);
  }
  .tk-link-btn {
    margin: 4px 0 0 14px; font-size: 11px; background: none; cursor: pointer;
    color: var(--color-text-tertiary); border: 1px dashed var(--color-border-secondary);
    border-radius: var(--radius-sm); padding: 2px 8px;
  }
  .tk-link-btn:hover { color: var(--color-text-primary); border-color: var(--color-text-tertiary); }

  .tk-lanes { display: flex; align-items: center; gap: 12px; margin: 5px 0 0 14px; }
  .tk-lanes .link {
    background: none; border: none; padding: 0; cursor: pointer;
    font: inherit; font-size: 11px; color: var(--color-text-secondary);
  }
  .tk-lanes .link:hover:not(:disabled) { color: var(--color-text-primary); text-decoration: underline; }
  .tk-lanes .link:disabled { cursor: default; opacity: 0.5; }
  .tk-lanes .link.accent { color: var(--color-accent, var(--color-info)); }
  .tk-inferred { font-family: var(--font-mono); font-size: 10px; color: var(--color-text-tertiary); }
</style>

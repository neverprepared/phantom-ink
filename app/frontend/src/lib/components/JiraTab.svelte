<script lang="ts">
  // The Jira tab: this profile's tickets from its own JQL, each showing the
  // code that references it. Conventional links are DERIVED from PR titles;
  // the "link code" picker stores an override for the ones that aren't.
  import { getApi } from '../utils/api';
  import { notifications } from '../notifications.svelte';
  import { profileState } from '../stores.svelte';
  import { codeState } from '../stores/code.svelte';
  import type { main } from '../../../wailsjs/go/models';

  let loading = $state(false);
  let result = $state<main.JiraTicketsResult | null>(null);
  let jqlDraft = $state('');
  let editingJql = $state(false);
  let linkingKey = $state(''); // ticket currently showing the PR picker

  let profile = $derived(profileState.active?.name ?? '');
  let tickets = $derived(result?.tickets ?? []);

  async function load() {
    if (!profile) return;
    loading = true;
    const a = await getApi();
    if (!a) { loading = false; return; }
    try {
      const res = await a.JiraTickets(profile);
      result = res;
      if (!editingJql) jqlDraft = res.jql;
    } catch (e: any) {
      notifications.error(`Jira: ${e?.message ?? e}`);
      result = null;
    } finally {
      loading = false;
    }
  }

  // Reload when the profile changes — tickets are per profile, like everything
  // else here.
  $effect(() => {
    if (profile) void load();
  });

  async function saveJql() {
    const a = await getApi();
    if (!a) return;
    try {
      await a.SetJiraJQL(profile, jqlDraft);
      editingJql = false;
      await load();
    } catch (e: any) {
      notifications.error(`${e?.message ?? e}`);
    }
  }

  async function link(issueKey: string, rowKey: string) {
    const a = await getApi();
    if (!a) return;
    try {
      await a.LinkJiraIssue(profile, issueKey, rowKey);
      linkingKey = '';
      notifications.success(`${issueKey} linked`);
      await load();
    } catch (e: any) {
      notifications.error(`${e?.message ?? e}`);
    }
  }

  async function unlink(issueKey: string, rowKey: string) {
    const a = await getApi();
    if (!a) return;
    try {
      await a.UnlinkJiraIssue(profile, issueKey, rowKey);
      notifications.success(`${issueKey} unlinked`);
      await load();
    } catch (e: any) {
      notifications.error(`${e?.message ?? e}`);
    }
  }

  function openInBrowser(url: string) {
    (window as any).runtime?.BrowserOpenURL(url);
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
      <button class="btn ghost sm" onclick={load} disabled={loading}>refresh</button>
    </div>
  </div>

  {#if editingJql}
    <div class="jql-edit">
      <textarea bind:value={jqlDraft} rows="2" spellcheck="false" aria-label="JQL query"></textarea>
      <button class="btn sm" onclick={saveJql}>save</button>
    </div>
  {:else if result?.jql}
    <code class="jql-view">{result.jql}</code>
  {/if}

  {#if result?.error}
    <!-- A JQL typo is a 400. Say so; an empty list would read as "no tickets". -->
    <p class="section-error">{result.error}</p>
  {/if}

  {#if loading && tickets.length === 0}
    <p class="muted-note">loading…</p>
  {:else if !result?.error && tickets.length === 0}
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

<style>
  .jira-tab { padding: var(--panel-padding); display: flex; flex-direction: column; gap: var(--spacing-sm); }
  .panel-header { display: flex; align-items: center; justify-content: space-between; }
  .head-actions { display: flex; gap: 6px; }
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
</style>

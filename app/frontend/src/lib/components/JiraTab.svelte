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

  // Preset editor state, component-local for the same reason the JQL draft is:
  // a draft list describes one visit. The SAVED list lives in jiraState.
  let managingPresets = $state(false);
  let presetDraft = $state<main.JiraJQLPreset[]>([]);
  let savingPresets = $state(false);
  let switchingTo = $state(''); // preset name mid-apply, for the busy chip

  let profile = $derived(profileState.active?.name ?? '');
  let tickets = $derived(jiraState.rows);

  let presets = $derived(jiraState.presets);
  /**
   * The chip currently in force. Matched on the QUERY, not a stored "active"
   * flag — the active query is whatever SetJiraJQL last wrote, and the
   * edit-query box can change that without going through a chip at all.
   */
  let activePreset = $derived(
    presets.find((p) => (p.jql ?? '').trim() === jiraState.jql.trim())?.name ?? '',
  );

  async function load(force = false) {
    await jiraState.refresh(profile, force);
  }

  /**
   * Open the editor on the query that is actually in force, or close it.
   *
   * The draft is seeded HERE rather than after a load: this component is
   * destroyed on every tab and profile switch, so a draft seeded by a load
   * only survives until the next switch, and the tickets that come back from
   * cache do not re-seed it. Opening the box is the one moment the saved
   * query is certainly known, so that is where it is read.
   */
  function toggleJqlEditor() {
    if (editingJql) {
      editingJql = false;
      return;
    }
    jqlDraft = jiraState.jql;
    editingJql = true;
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

  // The chip row is its own read — see the store: presets change only when
  // edited, so they must not ride the tickets' 45s TTL.
  $effect(() => {
    if (profile) void jiraState.loadPresets(profile);
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

  // ── Presets ──────────────────────────────────────────────────────────────
  //
  // A preset click goes through the SAVED query, so switching views is the
  // same write the edit-query box makes. That keeps the memo, the freshness
  // line and the displayed JQL all describing one thing.

  async function applyPreset(name: string) {
    if (name === activePreset || switchingTo) return;
    switchingTo = name;
    try {
      await jiraState.applyPreset(profile, name);
      editingJql = false;
      await load();
    } catch (e: any) {
      notifications.error(`${e?.message ?? e}`);
    } finally {
      switchingTo = '';
    }
  }

  /** Open the editor on a COPY, so cancelling really discards. */
  function manage() {
    presetDraft = presets.map((p) => ({ ...p }) as main.JiraJQLPreset);
    managingPresets = true;
  }

  function addPresetRow(jql = '') {
    presetDraft = [...presetDraft, { name: '', jql } as main.JiraJQLPreset];
  }

  /** Capture the query on screen as a new, unnamed preset row. */
  function captureCurrent() {
    manage();
    // The draft counts only while the box is open; once it is closed the
    // query on screen is the saved one, and a discarded draft must not win.
    addPresetRow((editingJql && jqlDraft.trim()) || jiraState.jql);
  }

  function removePresetRow(i: number) {
    presetDraft = presetDraft.filter((_, n) => n !== i);
  }

  /** Reorder by one step; the stored order is the chip order. */
  function movePresetRow(i: number, delta: number) {
    const to = i + delta;
    if (to < 0 || to >= presetDraft.length) return;
    const next = [...presetDraft];
    [next[i], next[to]] = [next[to], next[i]];
    presetDraft = next;
  }

  async function savePresets() {
    savingPresets = true;
    try {
      await jiraState.savePresets(profile, presetDraft);
      managingPresets = false;
      notifications.success('presets saved');
    } catch (e: any) {
      // Validation lives in the backend, so a blank name or a duplicate
      // arrives here as a message and the editor stays open on the draft.
      notifications.error(`${e?.message ?? e}`);
    } finally {
      savingPresets = false;
    }
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
      <button class="btn ghost sm" onclick={() => (managingPresets ? (managingPresets = false) : manage())}>
        {managingPresets ? 'done' : 'presets'}
      </button>
      <button class="btn ghost sm" onclick={toggleJqlEditor}>
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

  <!-- Preset chips: one saved query per button, so the common views are one
       click rather than a re-typed JQL. The active one is matched on the
       query itself — see activePreset. -->
  {#if presets.length > 0 || managingPresets}
    <div class="presets" role="group" aria-label="saved queries">
      {#each presets as p (p.name)}
        <button
          class="chip"
          class:active={p.name === activePreset}
          aria-pressed={p.name === activePreset}
          title={p.jql}
          disabled={switchingTo !== ''}
          onclick={() => applyPreset(p.name)}
        >{switchingTo === p.name ? '…' : p.name}</button>
      {/each}
      {#if !managingPresets}
        <button class="chip add" title="save the query on screen as a preset" onclick={captureCurrent}>+ save current</button>
      {/if}
    </div>
  {/if}

  {#if managingPresets}
    <!-- The whole list is edited and saved at once, which is also what the
         backend takes: a rename, an edit, a delete and a reorder are one
         write, so a half-applied list is not a state that can exist. -->
    <div class="preset-edit">
      {#if presetDraft.length === 0}
        <p class="muted-note">No presets. Add one, or save none to keep the chip row empty.</p>
      {/if}
      {#each presetDraft as row, i (i)}
        <div class="preset-row">
          <input
            class="p-name"
            bind:value={presetDraft[i].name}
            placeholder="name"
            aria-label="preset {i + 1} name"
            spellcheck="false"
          />
          <input
            class="p-jql"
            bind:value={presetDraft[i].jql}
            placeholder="JQL"
            aria-label="preset {i + 1} query"
            spellcheck="false"
          />
          <button class="p-btn" title="move up" disabled={i === 0} onclick={() => movePresetRow(i, -1)}>↑</button>
          <button class="p-btn" title="move down" disabled={i === presetDraft.length - 1} onclick={() => movePresetRow(i, 1)}>↓</button>
          <button class="p-btn del" title="remove this preset" onclick={() => removePresetRow(i)}>×</button>
        </div>
      {/each}
      <div class="preset-actions">
        <button class="btn ghost sm" onclick={() => addPresetRow(jiraState.jql)}>+ add</button>
        <button class="btn sm" disabled={savingPresets} onclick={savePresets}>
          {savingPresets ? 'saving…' : 'save presets'}
        </button>
        <button class="btn ghost sm" onclick={() => (managingPresets = false)}>cancel</button>
      </div>
    </div>
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
  /* Preset chips. Quiet by default so the active one reads as the selection
     rather than the row reading as a toolbar. */
  .presets { display: flex; flex-wrap: wrap; gap: 5px; align-items: center; }
  .chip {
    font: inherit; font-size: 11px; line-height: 1.6; padding: 1px 9px; cursor: pointer;
    background: var(--color-bg-tertiary); color: var(--color-text-secondary);
    border: 1px solid var(--color-border-secondary); border-radius: 11px;
  }
  .chip:hover:not(:disabled) { color: var(--color-text-primary); border-color: var(--color-text-tertiary); }
  .chip:disabled { cursor: default; opacity: 0.55; }
  .chip.active {
    color: var(--color-text-primary); font-weight: 600;
    border-color: var(--color-accent, var(--color-info));
    background: var(--color-bg-secondary);
  }
  .chip.add { background: none; border-style: dashed; color: var(--color-text-tertiary); }

  .preset-edit { display: flex; flex-direction: column; gap: 4px; }
  .preset-row { display: flex; gap: 5px; align-items: center; }
  .preset-row input {
    font-family: var(--font-mono); font-size: 11px; padding: 3px 6px; min-width: 0;
    background: var(--color-bg-tertiary); color: var(--color-text-primary);
    border: 1px solid var(--color-border-secondary); border-radius: var(--radius-sm);
  }
  .preset-row .p-name { flex: 0 0 150px; }
  .preset-row .p-jql { flex: 1; }
  .p-btn {
    background: none; border: 1px solid var(--color-border-secondary); border-radius: var(--radius-sm);
    color: var(--color-text-tertiary); cursor: pointer; font-size: 11px; line-height: 1;
    padding: 4px 6px;
  }
  .p-btn:hover:not(:disabled) { color: var(--color-text-primary); }
  .p-btn:disabled { opacity: 0.35; cursor: default; }
  .p-btn.del:hover { color: var(--color-error); border-color: var(--color-error); }
  .preset-actions { display: flex; gap: 6px; }

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

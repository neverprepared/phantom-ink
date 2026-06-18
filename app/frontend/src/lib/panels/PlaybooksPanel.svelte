<script lang="ts">
  import { onMount } from 'svelte';
  import { getApi } from '../utils/api';
  import { notifications } from '../notifications.svelte';
  import { profileState, refreshTick, playbookSeed } from '../stores.svelte';
  import Modal from '../components/Modal.svelte';
  import Spinner from '../components/Spinner.svelte';

  interface PlaybookTask {
    id: string;
    index: number;
    content: string;
    status: string;
    output?: string;
    error?: string;
    started_at?: number;
    finished_at?: number;
  }
  interface Playbook {
    id: string;
    name: string;
    markdown: string;
    tasks: PlaybookTask[];
    status: string;
    workspace_profile: string;
    runner?: string;
    created_at: number;
    started_at?: number;
    finished_at?: number;
  }

  // ----- state -----
  let playbooks = $state<Playbook[]>([]);
  let loading = $state(true);
  let query = $state('');
  let activeId = $state<string | null>(null);

  // available runners (loaded on mount)
  let runners = $state<string[]>([]);

  // create modal
  let showCreate = $state(false);
  let newName = $state('');
  let newMarkdown = $state('- [ ] Step one\n- [ ] Step two');
  let newScope = $state<'profile' | 'global'>('profile');
  let newRunner = $state('');

  // delete confirmation
  let pendingDelete = $state<Playbook | null>(null);

  // list expansion
  let expandedId = $state<string | null>(null);

  // profile vs global tab — only meaningful when a profile is active
  let scopeTab = $state<'profile' | 'global'>('profile');

  const activeProfileName = $derived(profileState.active?.name ?? '');
  const scopeLabel = $derived(activeProfileName || 'all');
  const hasProfile = $derived(!!activeProfileName);

  const active = $derived(activeId ? playbooks.find((p) => p.id === activeId) ?? null : null);

  const filtered = $derived(
    (() => {
      const q = query.trim().toLowerCase();
      const all = !q ? playbooks : playbooks.filter(
        (p) => (p.name + ' ' + (p.markdown ?? '') + ' ' + (p.workspace_profile ?? '')).toLowerCase().includes(q),
      );
      if (!hasProfile) return all;
      return scopeTab === 'global'
        ? all.filter((p) => p.workspace_profile === 'global')
        : all.filter((p) => p.workspace_profile !== 'global');
    })(),
  );

  const profileCount = $derived(playbooks.filter((p) => p.workspace_profile !== 'global').length);
  const globalCount = $derived(playbooks.filter((p) => p.workspace_profile === 'global').length);

  async function load(silent = false) {
    if (!silent) loading = true;
    try {
      const api = await getApi();
      const result = await api.ListPlaybooks(activeProfileName);
      playbooks = (result ?? []) as Playbook[];
    } catch (e: any) {
      if (!silent) notifications.error(`Failed to load playbooks: ${e?.message ?? e}`);
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    const _ = activeProfileName;
    void load(true);
  });

  $effect(() => {
    refreshTick.count;
    void load(true);
  });

  onMount(async () => {
    const api = await getApi();
    if (!api) return;
    try {
      const rs = await api.ListRunners();
      runners = (rs ?? []).map((r: any) => r.name);
    } catch {}

    // Consume any one-shot seed left by Stream's "save as playbook" flow.
    const seed = playbookSeed.consume();
    if (seed) {
      newName = seed.name;
      newMarkdown = seed.markdown;
      newScope = seed.scope ?? (activeProfileName ? 'profile' : 'global');
      showCreate = true;
    }
  });

  // If the user is already on the Playbooks panel when the seed arrives, the
  // onMount above won't fire — pick it up reactively too.
  $effect(() => {
    const seed = playbookSeed.value;
    if (!seed) return;
    playbookSeed.consume();
    newName = seed.name;
    newMarkdown = seed.markdown;
    newScope = seed.scope ?? (activeProfileName ? 'profile' : 'global');
    showCreate = true;
  });

  async function createPlaybook() {
    if (!newName.trim()) return;
    try {
      const api = await getApi();
      const profile = newScope === 'global' ? 'global' : activeProfileName || 'global';
      const pb = await api.CreatePlaybook({
        name: newName.trim(),
        markdown: newMarkdown,
        workspace_profile: profile,
        runner: newRunner || undefined,
      });
      playbooks = [pb, ...playbooks];
      showCreate = false;
      newName = '';
      newMarkdown = '- [ ] Step one\n- [ ] Step two';
      newRunner = '';
      activeId = pb.id;
    } catch (e: any) {
      notifications.error(`Failed to create playbook: ${e?.message ?? e}`);
    }
  }

  async function runPlaybook(pb: Playbook, runnerOverride?: string) {
    try {
      const api = await getApi();
      const profile = pb.workspace_profile === 'global' ? activeProfileName || '' : pb.workspace_profile;
      const runner = runnerOverride !== undefined ? runnerOverride : (pb.runner ?? '');
      const updated = await api.RunPlaybook(pb.id, profile, runner);
      const idx = playbooks.findIndex((p) => p.id === pb.id);
      if (idx >= 0) {
        const next = [...playbooks];
        next[idx] = updated;
        playbooks = next;
      }
      notifications.success(`running · ${pb.name}${runner ? ` on ${runner}` : ''}`);
    } catch (e: any) {
      notifications.error(`Failed to run: ${e?.message ?? e}`);
    }
  }

  async function cancelPlaybook(pb: Playbook) {
    try {
      const api = await getApi();
      await api.CancelPlaybook(pb.id);
      await load();
      notifications.success(`cancelled · ${pb.name}`);
    } catch (e: any) {
      notifications.error(`Failed to cancel: ${e?.message ?? e}`);
    }
  }

  async function confirmDelete() {
    const pb = pendingDelete;
    if (!pb) return;
    pendingDelete = null;
    try {
      const api = await getApi();
      await api.DeletePlaybook(pb.id);
      playbooks = playbooks.filter((p) => p.id !== pb.id);
      if (activeId === pb.id) activeId = null;
      notifications.success(`deleted · ${pb.name}`);
    } catch (e: any) {
      notifications.error(`Failed to delete: ${e?.message ?? e}`);
    }
  }

  // ----- editor -----
  let editMarkdown = $state('');
  let editRunner = $state('');
  let saving = $state(false);
  let lastEditorId: string | null = null;

  $effect(() => {
    const pb = active;
    if (!pb) { lastEditorId = null; return; }
    if (lastEditorId === pb.id) return;
    lastEditorId = pb.id;
    editMarkdown = pb.markdown ?? '';
    editRunner = pb.runner ?? '';
  });

  async function saveInstructions() {
    if (!active || saving) return;
    saving = true;
    try {
      const api = await getApi();
      const runnerVal = editRunner || '';
      const updated = await api.UpdatePlaybook(active.id, {
        markdown: editMarkdown,
        runner: runnerVal,
      });
      const idx = playbooks.findIndex((p) => p.id === active!.id);
      if (idx >= 0) {
        const next = [...playbooks];
        next[idx] = updated ?? { ...active, markdown: editMarkdown, runner: runnerVal };
        playbooks = next;
      }
      notifications.success('saved');
    } catch (e: any) {
      notifications.error(`Failed to save: ${e?.message ?? e}`);
    } finally {
      saving = false;
    }
  }

  function stepCount(pb: Playbook): number {
    return pb.tasks?.length ?? 0;
  }
  const taskStatusColor: Record<string, string> = {
    done: 'var(--run)',
    running: 'var(--accent)',
    failed: 'var(--fail)',
    pending: 'var(--text-faint)',
  };
</script>

{#if !active}
  <!-- ===== LIBRARY ===== -->
  <div class="pi-main-inner" style="padding: var(--panel-padding);">
    <div class="section-row" style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px;">
      <h1 class="page-title" style="display:flex;align-items:center;gap:10px;">
        playbooks
        <span class="scope-chip mono">{scopeLabel}</span>
        {#if loading}<Spinner />{/if}
      </h1>
      <div style="display:flex;gap:10px;align-items:center;">
        <div class="filter" style="margin:0;width:260px;">
          <input bind:value={query} placeholder="search recipes…" />
        </div>
        <button class="btn primary" onclick={() => { showCreate = true; newScope = activeProfileName ? (scopeTab === 'global' ? 'global' : 'profile') : 'global'; }}>+ new playbook</button>
      </div>
    </div>
    <p style="color: var(--text-faint); font-size: 13px; margin: -4px 0 16px;">
      Reusable recipes — one unit of work each. Compose several into a pipeline over in Loops.
    </p>

    {#if hasProfile}
      <div class="pb-tabs">
        <button
          class="pb-tab"
          class:pb-tab--active={scopeTab === 'profile'}
          onclick={() => { scopeTab = 'profile'; expandedId = null; }}
        >{activeProfileName} <span class="pb-tab-count">{profileCount}</span></button>
        <button
          class="pb-tab"
          class:pb-tab--active={scopeTab === 'global'}
          onclick={() => { scopeTab = 'global'; expandedId = null; }}
        >global <span class="pb-tab-count">{globalCount}</span></button>
      </div>
    {/if}

    {#if loading}
      <p style="color: var(--text-faint);">loading…</p>
    {:else if filtered.length === 0}
      <div class="card" style="padding: 48px; text-align: center; color: var(--text-faint);">
        <div style="margin-top: 12px; font-size: 14px; color: var(--text-muted);">
          {query ? `no playbooks match "${query}"` : `no ${scopeTab === 'global' ? 'global' : activeProfileName} playbooks yet`}
        </div>
      </div>
    {:else}
      <div class="pb-list">
        {#each filtered as pb (pb.id)}
          {@const expanded = expandedId === pb.id}
          <div class="pb-row" class:pb-row--expanded={expanded}>
            <div
              class="pb-row-header"
              role="button"
              tabindex="0"
              onclick={() => (expandedId = expanded ? null : pb.id)}
              onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') expandedId = expanded ? null : pb.id; }}
            >
              <span class="pb-chevron mono">{expanded ? '⌄' : '›'}</span>
              <span class="pb-check">✓</span>
              <span class="pb-name">{pb.name}</span>
              <div class="pb-tags">
                <span class="mono tag" style="color: var(--run); border-color: color-mix(in srgb, var(--run) 35%, var(--border));">{pb.status}</span>
                {#if pb.runner}
                  <span class="mono tag" title="runs on {pb.runner}">⚙ {pb.runner}</span>
                {/if}
                <span class="mono tag">{stepCount(pb)} steps</span>
              </div>
              <div class="pb-actions">
                <button class="btn ghost sm" onclick={(e) => { e.stopPropagation(); activeId = pb.id; }}>edit</button>
                {#if pb.status === 'running'}
                  <button class="btn danger sm" onclick={(e) => { e.stopPropagation(); cancelPlaybook(pb); }}>■ stop</button>
                {:else}
                  <button class="btn primary sm" onclick={(e) => { e.stopPropagation(); runPlaybook(pb); }}>▶ run</button>
                {/if}
                <button class="btn ghost sm" onclick={(e) => { e.stopPropagation(); pendingDelete = pb; }}>×</button>
              </div>
            </div>
            {#if expanded}
              <div class="pb-detail">
                <p class="mono" style="font-size:11.5px;color:var(--text-muted);line-height:1.7;margin:0;white-space:pre-wrap;word-break:break-word;">{(pb.markdown ?? '').trim()}</p>
                {#if pb.started_at}
                  <p class="mono" style="font-size:11px;color:var(--text-faint);margin:10px 0 0;">last run: {new Date(pb.started_at).toLocaleString()}</p>
                {/if}
              </div>
            {/if}
          </div>
        {/each}
      </div>
    {/if}
  </div>
{:else}
  <!-- ===== EDITOR ===== -->
  <div class="pi-main-inner" style="padding: var(--panel-padding); max-width: 860px;">
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px;">
      <button class="btn ghost sm" onclick={() => (activeId = null)}>←</button>
      <span style="color: var(--task);font-size:18px;">✓</span>
      <h1 class="page-title" style="font-size:26px;">{active.name}</h1>
      <span class="mono" style="font-size:11px;color: var(--text-faint);margin-left:8px;">{active.workspace_profile || 'global'}</span>
      {#if active.runner}
        <span class="mono" style="font-size:11px;color:var(--text-faint);">⚙ {active.runner}</span>
      {/if}
      <div style="margin-left:auto;display:flex;gap:8px;align-items:center;">
        <select
          bind:value={editRunner}
          style="font-size:12px;padding:4px 8px;border:1px solid var(--border);border-radius:var(--r-md);background:var(--bg);color:var(--text);height:28px;"
          title="run on"
        >
          <option value="">in-process (API host)</option>
          {#each runners as r (r)}
            <option value={r}>{r}</option>
          {/each}
        </select>
        <button class="btn ghost sm" onclick={() => (pendingDelete = active!)}>delete</button>
        {#if active.status === 'running'}
          <button class="btn danger sm" onclick={() => cancelPlaybook(active!)}>■ stop</button>
        {:else}
          <button class="btn primary sm" onclick={() => runPlaybook(active!, editRunner)}>▶ run</button>
        {/if}
      </div>
    </div>
    <p style="color: var(--text-faint); font-size: 13px; margin: 0 0 22px;">
      Write agent instructions as a markdown checklist. Each checked item becomes a task when the playbook runs.
    </p>

    <div style="display:flex;flex-direction:column;gap:18px;">
      <!-- instructions editor -->
      <div class="card" style="padding:0;overflow:hidden;">
        <div style="display:flex;align-items:center;gap:10px;padding:12px 16px;border-bottom:1px solid var(--border);">
          <span class="mono-head">» agent instructions</span>
          <div style="margin-left:auto;display:flex;gap:8px;align-items:center;">
            <button class="btn sm primary" onclick={saveInstructions} disabled={saving}>
              {saving ? 'saving…' : 'save'}
            </button>
          </div>
        </div>
        <textarea
          bind:value={editMarkdown}
          rows="16"
          class="mono"
          placeholder="- [ ] Step one&#10;- [ ] Step two"
          style="width:100%;resize:vertical;font-size:12.5px;padding:16px;border:none;background:var(--bg-sunken);color:var(--text);outline:none;line-height:1.65;box-sizing:border-box;display:block;"
        ></textarea>
      </div>

      <!-- task run history -->
      {#if active.tasks && active.tasks.length > 0}
        <div class="card" style="padding:0;overflow:hidden;">
          <div style="padding:12px 16px;border-bottom:1px solid var(--border);">
            <span class="mono-head">» last run · {active.tasks.length} tasks</span>
          </div>
          <div style="display:flex;flex-direction:column;">
            {#each active.tasks as t (t.id)}
              <div style="padding:11px 16px;border-bottom:1px solid var(--border);display:flex;align-items:flex-start;gap:10px;">
                <span class="mono" style="font-size:11px;min-width:18px;color: var(--text-faint);">{t.index + 1}</span>
                <span
                  class="mono"
                  style="font-size:10px;padding:2px 7px;border-radius:99px;background: color-mix(in srgb, {taskStatusColor[t.status] ?? 'var(--text-faint)'} 15%, var(--bg-elev));color:{taskStatusColor[t.status] ?? 'var(--text-faint)'};white-space:nowrap;"
                >{t.status}</span>
                <span style="font-size:13px;color:var(--text);flex:1;">{t.content}</span>
              </div>
            {/each}
          </div>
        </div>
      {/if}
    </div>
  </div>
{/if}

{#if pendingDelete}
  <Modal onClose={() => (pendingDelete = null)}>
    <div style="display:flex;flex-direction:column;gap:16px;">
      <h3 style="font-size:15px;font-weight:600;color:var(--text);margin:0;">Delete playbook?</h3>
      <p style="font-size:13px;color:var(--text-muted);margin:0;">
        <strong style="color:var(--text);">{pendingDelete.name}</strong> will be permanently removed.
      </p>
      <div style="display:flex;justify-content:flex-end;gap:8px;">
        <button class="btn ghost" onclick={() => (pendingDelete = null)}>cancel</button>
        <button class="btn danger" onclick={confirmDelete}>delete</button>
      </div>
    </div>
  </Modal>
{/if}

{#if showCreate}
  <Modal onClose={() => (showCreate = false)}>
    <div style="display:flex;flex-direction:column;gap:14px;">
      <h3 style="font-size:15px;font-weight:600;color:var(--text);margin:0;">New Playbook</h3>
      <label style="display:flex;flex-direction:column;gap:6px;font-size:13px;color:var(--text-muted);">
        Name
        <input class="filter-input" type="text" bind:value={newName} placeholder="e.g. nightly-triage" style="padding:7px 10px;border:1px solid var(--border);border-radius:var(--r-md);background:var(--bg);color:var(--text);font-size:13px;" />
      </label>
      <label style="display:flex;flex-direction:column;gap:6px;font-size:13px;color:var(--text-muted);">
        Instructions (markdown checklist)
        <textarea
          bind:value={newMarkdown}
          rows="6"
          style="padding:7px 10px;border:1px solid var(--border);border-radius:var(--r-md);background:var(--bg);color:var(--text);font-size:12px;font-family:var(--font-mono);resize:vertical;"
        ></textarea>
      </label>
      <div style="display:flex;gap:6px;">
        <button class="btn sm {newScope === 'profile' ? 'primary' : ''}" onclick={() => (newScope = 'profile')} disabled={!activeProfileName}>{activeProfileName || 'no profile'}</button>
        <button class="btn sm {newScope === 'global' ? 'primary' : ''}" onclick={() => (newScope = 'global')}>global</button>
      </div>
      <label style="display:flex;flex-direction:column;gap:6px;font-size:13px;color:var(--text-muted);">
        Run on
        <select bind:value={newRunner} style="padding:7px 10px;border:1px solid var(--border);border-radius:var(--r-md);background:var(--bg);color:var(--text);font-size:13px;">
          <option value="">in-process (API host)</option>
          {#each runners as r (r)}
            <option value={r}>{r}</option>
          {/each}
        </select>
      </label>
      <div style="display:flex;justify-content:flex-end;gap:8px;">
        <button class="btn ghost" onclick={() => (showCreate = false)}>cancel</button>
        <button class="btn primary" onclick={createPlaybook} disabled={!newName.trim()}>create</button>
      </div>
    </div>
  </Modal>
{/if}

<style>
  .pi-main-inner { max-width: 1280px; margin: 0 auto; }
  .scope-chip {
    font-size: 11px;
    color: var(--text-faint);
    border: 1px solid var(--border);
    background: var(--bg-elev);
    padding: 2px 9px;
    border-radius: 99px;
    text-transform: lowercase;
    letter-spacing: .04em;
  }

  /* scope tabs */
  .pb-tabs {
    display: flex;
    gap: 2px;
    margin-bottom: 12px;
    border-bottom: 1px solid var(--border);
  }
  .pb-tab {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 6px 14px;
    font-size: 12px;
    font-family: var(--font-mono);
    color: var(--text-muted);
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    cursor: pointer;
    margin-bottom: -1px;
    transition: color 0.1s, border-color 0.1s;
  }
  .pb-tab:hover {
    color: var(--text);
  }
  .pb-tab--active {
    color: var(--text);
    border-bottom-color: var(--accent);
  }
  .pb-tab-count {
    font-size: 10px;
    padding: 1px 5px;
    border-radius: 99px;
    background: var(--bg-sunken);
    color: var(--text-faint);
  }
  .pb-tab--active .pb-tab-count {
    background: color-mix(in srgb, var(--accent) 15%, var(--bg-sunken));
    color: var(--accent);
  }

  /* compact list layout */
  .pb-list {
    display: flex;
    flex-direction: column;
    border: 1px solid var(--border);
    border-radius: var(--r-md);
    overflow: hidden;
  }
  .pb-row {
    border-bottom: 1px solid var(--border);
  }
  .pb-row:last-child {
    border-bottom: none;
  }
  .pb-row-header {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 9px 12px;
    cursor: pointer;
    user-select: none;
    background: var(--bg-elev);
    transition: background 0.1s;
  }
  .pb-row-header:hover {
    background: color-mix(in srgb, var(--accent) 6%, var(--bg-elev));
  }
  .pb-row--expanded .pb-row-header {
    background: color-mix(in srgb, var(--accent) 8%, var(--bg-elev));
    border-bottom: 1px solid var(--border);
  }
  .pb-chevron {
    font-size: 13px;
    color: var(--text-faint);
    min-width: 14px;
    text-align: center;
    line-height: 1;
  }
  .pb-check {
    font-size: 11px;
    color: var(--run);
    min-width: 14px;
    text-align: center;
    opacity: 0.7;
  }
  .pb-name {
    font-size: 13px;
    color: var(--text);
    flex: 1;
    min-width: 0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .pb-tags {
    display: flex;
    gap: 5px;
    align-items: center;
    flex-shrink: 0;
  }
  .pb-actions {
    display: flex;
    gap: 4px;
    align-items: center;
    flex-shrink: 0;
    margin-left: 4px;
  }
  .pb-detail {
    padding: 12px 40px 14px;
    background: var(--bg-sunken);
  }
  .tag {
    font-size: 10px;
    padding: 1px 7px;
    border: 1px solid var(--border);
    border-radius: 99px;
    color: var(--text-faint);
    white-space: nowrap;
  }
</style>

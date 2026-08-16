<script lang="ts">
  import { getApi } from '../utils/api';
  import { notifications } from '../notifications.svelte';
  import CardExpander from './CardExpander.svelte';
  import type { brainbox } from '../../../wailsjs/go/models';

  // Per-profile skills manager. The brain `skills` vault is the source of truth
  // (rendered to SKILL.md); this is the operator surface for it — list, view,
  // create, edit, and (gated) delete. All calls proxy the router's
  // /api/brain/profiles/{profile}/skills facade via Go. Create/edit are safe;
  // delete is the operator act, so it sits behind an inline confirm.

  let { profile }: { profile: string } = $props();

  type Mode = 'list' | 'view' | 'create';

  const TEMPLATE = `---
name: my-skill
description: Use when … (a concrete trigger — "use when doing X / working with Y")
---

# My skill

Tight, imperative instructions with at least one concrete example.
`;

  let loading = $state(false);
  let loaded = $state(false);
  let unavailable = $state(false);
  let skills = $state<brainbox.BrainSkill[]>([]);

  let mode = $state<Mode>('list');
  let selected = $state('');       // skill name being viewed/edited
  let draft = $state('');          // editable body (view or create)
  let original = $state('');       // pristine body for dirty-check in view mode
  let busy = $state(false);        // a write is in flight
  let confirmDelete = $state('');  // skill name pending delete confirmation

  let dirty = $derived(mode === 'view' && draft !== original);

  async function load() {
    loading = true;
    const a = await getApi();
    if (!a) { loading = false; return; }
    try {
      skills = (await a.ListBrainSkills(profile)) ?? [];
      unavailable = false;
    } catch (e) {
      // A missing binding / unconfigured facade reads as unavailable, matching
      // the memory editor's tone rather than a scary error toast on open.
      unavailable = true;
    } finally {
      loading = false;
      loaded = true;
    }
  }

  async function openSkill(name: string) {
    busy = true;
    const a = await getApi();
    if (!a) { busy = false; return; }
    try {
      const d = await a.GetBrainSkill(profile, name);
      selected = name;
      original = d?.body ?? '';
      draft = original;
      mode = 'view';
    } catch (e) {
      notifications.error(`load skill failed: ${e}`);
    } finally {
      busy = false;
    }
  }

  function startCreate() {
    selected = '';
    draft = TEMPLATE;
    original = '';
    mode = 'create';
  }

  function cancel() {
    mode = 'list';
    selected = '';
    draft = '';
    original = '';
  }

  async function save() {
    busy = true;
    const a = await getApi();
    if (!a) { busy = false; return; }
    try {
      if (mode === 'create') {
        const res = await a.CreateBrainSkill(profile, draft);
        notifications.success(`skill "${res?.name}" created`);
      } else {
        const res = await a.UpdateBrainSkill(profile, selected, draft);
        notifications.success(
          res?.replaced ? `skill "${selected}" updated` : `skill "${selected}" saved`,
        );
      }
      await load();
      cancel();
    } catch (e) {
      notifications.error(`save skill failed: ${e}`);
    } finally {
      busy = false;
    }
  }

  async function doDelete(name: string) {
    busy = true;
    const a = await getApi();
    if (!a) { busy = false; return; }
    try {
      const res = await a.DeleteBrainSkill(profile, name);
      notifications.success(`skill "${name}" deleted (${res?.deleted ?? 0} version(s))`);
      confirmDelete = '';
      if (selected === name) cancel();
      await load();
    } catch (e) {
      notifications.error(`delete skill failed: ${e}`);
    } finally {
      busy = false;
    }
  }

  function shortDate(iso: string): string {
    return iso ? iso.slice(0, 10) : '';
  }
</script>

<CardExpander label="skills" count={loaded && !unavailable ? `(${skills.length})` : ''}
  hint="reusable SKILL.md capabilities (brain is the source of truth)"
  description="The operator surface for this profile's brain skills vault (rendered to SKILL.md for agents): list, view, create, edit, and — when enabled — delete. Create and edit are safe; delete is gated."
  onOpen={() => { if (!loaded) void load(); }}>
  <div class="sk-body">
    {#if loading}
      <p class="sk-hint">loading…</p>
    {:else if unavailable}
      <p class="sk-hint">skills facade not reachable — needs the router brain facade (CL_BRAIN__ADMIN_URL + PB_ADMIN_KEY) and a provisioned memory binding.</p>
    {:else if mode === 'list'}
      <div class="sk-head">
        <span class="sk-hint">{skills.length} skill{skills.length === 1 ? '' : 's'} in this profile's vault — rendered to SKILL.md on sync.</span>
        <button class="sk-btn primary" disabled={busy} onclick={startCreate}>+ new</button>
      </div>
      {#if skills.length === 0}
        <p class="sk-hint">no skills yet. “+ new” authors one (frontmatter <code>name</code> + <code>description</code> + instructions).</p>
      {:else}
        <div class="sk-list">
          {#each skills as s (s.name)}
            <div class="sk-item">
              <button class="sk-name" disabled={busy} onclick={() => openSkill(s.name)} title="view / edit">{s.name}</button>
              <span class="sk-date">{shortDate(s.updated_at)}</span>
              {#if confirmDelete === s.name}
                <span class="sk-confirm">delete?</span>
                <button class="sk-x danger" disabled={busy} onclick={() => doDelete(s.name)}>confirm</button>
                <button class="sk-x" disabled={busy} onclick={() => (confirmDelete = '')}>cancel</button>
              {:else}
                <button class="sk-x" disabled={busy} onclick={() => (confirmDelete = s.name)}>delete</button>
              {/if}
            </div>
          {/each}
        </div>
      {/if}
    {:else}
      <!-- view (edit existing) or create -->
      <div class="sk-head">
        <span class="sk-editing">{mode === 'create' ? 'new skill' : selected}</span>
        <span class="sk-hint">full SKILL.md — frontmatter <code>name</code>{mode === 'view' ? ' must stay ' + selected : ''} + <code>description</code> + body</span>
      </div>
      <textarea class="sk-editor" bind:value={draft} spellcheck="false" disabled={busy}></textarea>
      <div class="sk-actions">
        <button
          class="sk-btn primary"
          disabled={busy || !draft.trim() || (mode === 'view' && !dirty)}
          onclick={save}
        >
          {busy ? 'saving…' : mode === 'create' ? 'create' : 'save'}
        </button>
        <button class="sk-btn" disabled={busy} onclick={cancel}>cancel</button>
        {#if mode === 'view'}
          {#if confirmDelete === selected}
            <span class="sk-confirm">delete “{selected}”?</span>
            <button class="sk-x danger" disabled={busy} onclick={() => doDelete(selected)}>confirm</button>
            <button class="sk-x" disabled={busy} onclick={() => (confirmDelete = '')}>cancel</button>
          {:else}
            <button class="sk-x danger-outline" disabled={busy} onclick={() => (confirmDelete = selected)}>delete skill</button>
          {/if}
        {/if}
      </div>
    {/if}
  </div>
</CardExpander>

<style>
  .sk-body { display: flex; flex-direction: column; gap: 8px; padding: 4px 2px; }
  .sk-hint { color: var(--color-text-tertiary); font-size: 0.85em; margin: 0; }
  .sk-hint code { font-family: var(--font-mono); }
  .sk-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .sk-editing { font-family: var(--font-mono); color: var(--color-text-primary); font-size: 0.9em; }

  .sk-list { display: flex; flex-direction: column; gap: 2px; }
  .sk-item { display: flex; align-items: center; gap: 8px; font-size: 0.85em; padding: 2px 0; }
  .sk-name {
    flex: 1; text-align: left; background: none; border: none; padding: 2px 0;
    color: var(--color-text-primary); font-family: var(--font-mono); cursor: pointer;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .sk-name:hover { color: var(--card-accent, var(--color-text-primary)); text-decoration: underline; }
  .sk-date { color: var(--color-text-tertiary); font-size: 0.9em; min-width: 74px; text-align: right; }
  .sk-confirm { color: var(--color-text-secondary); font-size: 0.85em; }

  .sk-editor {
    width: 100%; min-height: 260px; resize: vertical; box-sizing: border-box;
    padding: 8px; border-radius: var(--radius-md, 4px);
    border: 1px solid var(--color-border-primary); background: var(--color-bg-secondary);
    color: var(--color-text-primary); font-family: var(--font-mono); font-size: 0.8em;
    line-height: 1.5; white-space: pre; overflow-wrap: normal; overflow-x: auto;
  }
  .sk-actions { display: flex; align-items: center; gap: 6px; }

  .sk-btn {
    padding: 4px 10px; border-radius: var(--radius-md, 4px);
    border: 1px solid var(--color-border-primary); background: var(--color-bg-secondary);
    color: var(--color-text-primary); cursor: pointer; font-size: 0.85em;
  }
  .sk-btn.primary { border-color: var(--card-accent, var(--color-border-primary)); }
  .sk-btn:disabled { opacity: 0.6; cursor: default; }
  .sk-x {
    padding: 2px 8px; border-radius: var(--radius-md, 4px); border: 1px solid var(--color-border-primary);
    background: var(--color-bg-secondary); color: var(--color-text-secondary); cursor: pointer; font-size: 0.8em;
  }
  .sk-x:disabled { opacity: 0.6; cursor: default; }
  .sk-x.danger { border-color: var(--color-danger, #c0392b); color: var(--color-danger, #c0392b); }
  .sk-x.danger-outline { color: var(--color-text-tertiary); }
  .sk-x.danger-outline:hover { border-color: var(--color-danger, #c0392b); color: var(--color-danger, #c0392b); }
</style>

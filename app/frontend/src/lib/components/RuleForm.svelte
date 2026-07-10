<script lang="ts">
  import { onMount } from 'svelte';
  import { getApi, safe } from '../utils/api';
  import RulePatternEditor from './RulePatternEditor.svelte';
  import RuleActionsEditor from './RuleActionsEditor.svelte';
  import {
    draftFromAction,
    draftToAction,
    draftValid,
    newActionDraft,
    parseSaveError,
    type ActionDraft,
    type Rule,
  } from '../rules';

  // Create/edit form for one server-side rule.
  let {
    rule,
    activeProfile,
    allProfiles,
    onSaved,
    onCancel,
  }: {
    rule: Rule | null; // null = create
    activeProfile: string;
    allProfiles: string[];
    onSaved: () => void;
    onCancel: () => void;
  } = $props();

  let draft = $state({
    id: rule?.id ?? '',
    name: rule?.name ?? '',
    description: rule?.description ?? '',
    profile: rule?.profile ?? activeProfile,
    enabled: rule?.enabled ?? true,
  });
  let actionDrafts = $state<ActionDraft[]>(
    rule?.actions?.length
      ? rule.actions.map(draftFromAction)
      : [newActionDraft('submit_task')]
  );
  let saving = $state(false);
  let saveError = $state('');
  let patternErrors = $state<string[]>([]);
  let patternEditor = $state<RulePatternEditor | null>(null);

  // Pickers
  let agents = $state<string[]>([]);
  let playbooks = $state<{ id: string; name: string }[]>([]);
  let loopTemplates = $state<string[]>([]);

  onMount(async () => {
    const api = await getApi();
    if (!api) return;
    const [ag, pb, lt] = await Promise.all([
      safe(api.ListAgentRoles(), [], 'ListAgentRoles'),
      safe(api.ListPlaybooks(''), [], 'ListPlaybooks'),
      safe(api.ListLoopTemplates(), [], 'ListLoopTemplates'),
    ]);
    agents = ((ag ?? []) as any[]).map((a) => a.name).filter(Boolean);
    playbooks = ((pb ?? []) as any[]).map((p) => ({ id: p.id, name: p.name }));
    loopTemplates = (lt ?? []) as string[];
  });

  const formValid = $derived(
    draft.name.trim() !== '' && actionDrafts.length > 0 && actionDrafts.every(draftValid)
  );

  async function save() {
    const api = await getApi();
    if (!api) return;
    saveError = '';
    patternErrors = [];

    const pattern = patternEditor?.getPattern();
    if (pattern == null) return; // raw JSON parse error shown inline

    let actions: Record<string, any>[];
    try {
      actions = actionDrafts.map(draftToAction);
    } catch (e: any) {
      saveError = String(e?.message ?? e);
      return;
    }

    saving = true;
    try {
      await api.SaveRule({
        id: draft.id,
        name: draft.name.trim(),
        profile: draft.profile,
        enabled: draft.enabled,
        description: draft.description.trim(),
        pattern,
        actions,
      } as any);
      onSaved();
    } catch (e: any) {
      const parsed = parseSaveError(e);
      if (parsed.patternErrors.length) patternErrors = parsed.patternErrors;
      else saveError = parsed.message;
    } finally {
      saving = false;
    }
  }
</script>

<div class="rule-form">
  <div class="form-title">{draft.id ? 'edit rule' : 'new rule'}</div>

  <label class="form-row">
    <span class="form-label">name</span>
    <input class="form-input" bind:value={draft.name} placeholder="triage failed tasks" />
  </label>

  <label class="form-row">
    <span class="form-label">description (optional)</span>
    <input class="form-input" bind:value={draft.description} />
  </label>

  <label class="form-row">
    <span class="form-label">profile</span>
    <select class="form-select narrow" bind:value={draft.profile}>
      <option value="">global (all profiles)</option>
      {#each allProfiles as p (p)}
        <option value={p}>{p}</option>
      {/each}
    </select>
  </label>

  <RulePatternEditor
    bind:this={patternEditor}
    initial={rule?.pattern ?? null}
    defaultWorkspace={draft.profile}
    bind:patternErrors
  />

  <div class="form-row">
    <span class="form-label">actions</span>
    <RuleActionsEditor
      bind:drafts={actionDrafts}
      {agents}
      {playbooks}
      {loopTemplates}
      profiles={allProfiles}
    />
  </div>

  <label class="form-row form-row-inline">
    <input type="checkbox" bind:checked={draft.enabled} />
    <span class="form-label">enabled</span>
  </label>

  {#if saveError}
    <p class="save-error">{saveError}</p>
  {/if}

  <div class="form-actions">
    <button class="btn sm primary" onclick={save} disabled={!formValid || saving}>
      {saving ? 'saving…' : 'save'}
    </button>
    <button class="btn sm ghost" onclick={onCancel}>cancel</button>
  </div>
</div>

<style>
  .rule-form {
    display: flex; flex-direction: column; gap: var(--spacing-md);
    padding: var(--spacing-lg);
    border: 1px solid var(--color-accent);
    border-radius: var(--radius-md);
    background: var(--color-bg-secondary);
  }
  .form-title { font-size: 12px; font-weight: 600; color: var(--color-text-secondary); }
  .form-row { display: flex; flex-direction: column; gap: 4px; }
  .form-row-inline { flex-direction: row; align-items: center; gap: var(--spacing-sm); }
  .form-label { font-family: var(--font-mono); font-size: 10px; color: var(--color-text-muted); }

  .form-input {
    background: var(--color-bg-primary); border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm); padding: 6px 8px;
    font-size: 12px; color: var(--color-text-primary); font-family: inherit; width: 100%;
  }
  .form-input:focus { outline: none; border-color: var(--color-accent); }

  .form-select {
    background: var(--color-bg-primary); border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm); padding: 6px 8px;
    font-size: 12px; color: var(--color-text-primary); cursor: pointer;
  }
  .form-select.narrow { width: auto; min-width: 160px; }

  .save-error { font-family: var(--font-mono); font-size: 10px; color: var(--color-error); margin: 0; }
  .form-actions { display: flex; gap: var(--spacing-sm); }
</style>

<script lang="ts">
  import KeyValueRows from './KeyValueRows.svelte';
  import {
    ACTION_TYPES,
    newActionDraft,
    type ActionDraft,
    type RuleActionType,
  } from '../rules';

  // Editable list of rule actions. Pickers are supplied by RuleForm so this
  // component stays dumb about data loading.
  let {
    drafts = $bindable([] as ActionDraft[]),
    agents = [],
    playbooks = [],
    loopTemplates = [],
    profiles = [],
  }: {
    drafts?: ActionDraft[];
    agents?: string[];
    playbooks?: { id: string; name: string }[];
    loopTemplates?: string[];
    profiles?: string[];
  } = $props();

  const PLACEHOLDER_HINT =
    '{title} {type} {source} {status} {workspace} {metadata.<path>} {outcome.error} {envelope} …';

  function addAction(type: RuleActionType) {
    drafts = [...drafts, newActionDraft(type)];
  }
  function removeAction(i: number) {
    drafts = drafts.filter((_, idx) => idx !== i);
  }
  function addArgv(d: ActionDraft) {
    d.argv = [...d.argv, ''];
  }
  function removeArgv(d: ActionDraft, i: number) {
    d.argv = d.argv.filter((_, idx) => idx !== i);
    if (d.argv.length === 0) d.argv = [''];
  }
</script>

<div class="actions-editor">
  {#each drafts as d, i (i)}
    <div class="action-card">
      <div class="action-head">
        <span class="action-type-badge type-{d.type}">{d.type.replace('_', ' ')}</span>
        <button class="action-remove" onclick={() => removeAction(i)} title="remove action">✕</button>
      </div>

      {#if d.type === 'submit_task'}
        <label class="form-row">
          <span class="form-label">agent</span>
          <select class="form-select narrow" bind:value={d.agentName}>
            <option value="">— select —</option>
            {#each agents as a (a)}<option value={a}>{a}</option>{/each}
          </select>
        </label>
        <label class="form-row">
          <span class="form-label">task description</span>
          <textarea class="form-input ta" rows="2" bind:value={d.description}
            placeholder="Triage: {'{title}'} ({'{outcome.error}'})"></textarea>
          <span class="hint">placeholders: {PLACEHOLDER_HINT}</span>
        </label>
        <div class="form-grid">
          <label class="form-row">
            <span class="form-label">priority</span>
            <input class="form-input num" bind:value={d.priority} />
          </label>
          <label class="form-row">
            <span class="form-label">workspace</span>
            <select class="form-select narrow" bind:value={d.workspaceProfile}>
              <option value="">inherit event workspace</option>
              {#each profiles as p (p)}<option value={p}>{p}</option>{/each}
            </select>
          </label>
          <label class="form-row">
            <span class="form-label">repo url (optional, templated)</span>
            <input class="form-input" bind:value={d.repoUrl} />
          </label>
        </div>

      {:else if d.type === 'run_playbook'}
        <div class="form-grid">
          <label class="form-row">
            <span class="form-label">playbook</span>
            <select class="form-select narrow" bind:value={d.playbook}>
              <option value="">— select —</option>
              {#each playbooks as p (p.id)}<option value={p.id}>{p.name}</option>{/each}
            </select>
          </label>
          <label class="form-row">
            <span class="form-label">workspace</span>
            <select class="form-select narrow" bind:value={d.workspaceProfile}>
              <option value="">inherit event workspace</option>
              {#each profiles as p (p)}<option value={p}>{p}</option>{/each}
            </select>
          </label>
        </div>

      {:else if d.type === 'start_loop'}
        <div class="form-grid">
          <label class="form-row">
            <span class="form-label">loop template</span>
            <select class="form-select narrow" bind:value={d.templateName}>
              <option value="">— select —</option>
              {#each loopTemplates as t (t)}<option value={t}>{t}</option>{/each}
            </select>
          </label>
          <label class="form-row">
            <span class="form-label">workspace</span>
            <select class="form-select narrow" bind:value={d.workspaceProfile}>
              <option value="">inherit event workspace</option>
              {#each profiles as p (p)}<option value={p}>{p}</option>{/each}
            </select>
          </label>
        </div>
        <div class="form-row">
          <span class="form-label">artifact refs (string values templated)</span>
          {#if d.refsRawMode}
            <textarea class="form-input ta mono" rows="3" bind:value={d.refsRaw} spellcheck="false"></textarea>
            <span class="hint">contains non-string values — editing as raw JSON</span>
          {:else}
            <KeyValueRows bind:rows={d.refRows} keyPlaceholder="ref name" valuePlaceholder={'value or {placeholder}'} />
          {/if}
        </div>

      {:else if d.type === 'webhook'}
        <label class="form-row">
          <span class="form-label">url</span>
          <input class="form-input mono" bind:value={d.url} placeholder="https://hooks.example.com/…" />
        </label>
        <div class="form-row">
          <span class="form-label">headers (values templated)</span>
          <KeyValueRows bind:rows={d.headerRows} keyPlaceholder="X-Header" valuePlaceholder="value" />
        </div>
        <div class="form-row">
          <span class="form-label">body</span>
          <div class="seg-ctrl">
            <button class="seg-btn" class:active={d.bodyMode === 'envelope'}
              onclick={() => (d.bodyMode = 'envelope')}>full envelope</button>
            <button class="seg-btn" class:active={d.bodyMode === 'custom'}
              onclick={() => (d.bodyMode = 'custom')}>custom JSON</button>
          </div>
          {#if d.bodyMode === 'custom'}
            <textarea class="form-input ta mono" rows="3" bind:value={d.bodyRaw} spellcheck="false"></textarea>
            <span class="hint">string values templated; a _brainbox provenance stamp is always added</span>
          {/if}
        </div>
        <label class="form-row">
          <span class="form-label">timeout (s, optional)</span>
          <input class="form-input num" bind:value={d.timeoutS} placeholder="15" />
        </label>

      {:else if d.type === 'run_script'}
        <div class="form-row">
          <span class="form-label">command (fixed argv — event arrives via stdin JSON + BRAINBOX_* env)</span>
          {#each d.argv as _, ai (ai)}
            <div class="argv-row">
              <input class="form-input mono" bind:value={d.argv[ai]}
                placeholder={ai === 0 ? '/usr/local/bin/my-script' : 'arg'} />
              {#if d.argv.length > 1}
                <button class="action-remove" onclick={() => removeArgv(d, ai)} title="remove">✕</button>
              {/if}
            </div>
          {/each}
          <button class="add-link" onclick={() => addArgv(d)}>+ arg</button>
        </div>
        <div class="form-grid">
          <label class="form-row">
            <span class="form-label">cwd (optional)</span>
            <input class="form-input mono" bind:value={d.cwd} />
          </label>
          <label class="form-row">
            <span class="form-label">timeout (s, optional)</span>
            <input class="form-input num" bind:value={d.timeoutS} placeholder="60" />
          </label>
        </div>
        <p class="warn">requires CL_RULES__ALLOW_RUN_SCRIPT=true on the daemon — save will be rejected otherwise</p>
      {/if}
    </div>
  {/each}

  <div class="add-row">
    <span class="form-label">add action</span>
    <div class="seg-ctrl">
      {#each ACTION_TYPES as t (t)}
        <button class="seg-btn" onclick={() => addAction(t)}>{t.replace('_', ' ')}</button>
      {/each}
    </div>
  </div>
</div>

<style>
  .actions-editor { display: flex; flex-direction: column; gap: var(--spacing-sm); }

  .action-card {
    display: flex; flex-direction: column; gap: var(--spacing-sm);
    padding: var(--spacing-md);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-md);
    background: var(--color-bg-primary);
  }
  .action-head { display: flex; align-items: center; justify-content: space-between; }
  .action-type-badge {
    font-family: var(--font-mono); font-size: 10px;
    padding: 1px 8px; border-radius: 999px;
    border: 1px solid var(--color-border-primary);
    color: var(--color-text-muted);
  }
  .action-type-badge.type-submit_task  { color: var(--color-accent); border-color: rgba(234,179,8,0.3); background: rgba(234,179,8,0.06); }
  .action-type-badge.type-run_playbook { color: #10b981; border-color: rgba(16,185,129,0.3); background: rgba(16,185,129,0.06); }
  .action-type-badge.type-start_loop   { color: #f59e0b; border-color: rgba(245,158,11,0.3); background: rgba(245,158,11,0.06); }
  .action-type-badge.type-webhook      { color: #60a5fa; border-color: rgba(96,165,250,0.3); background: rgba(96,165,250,0.06); }
  .action-type-badge.type-run_script   { color: #f472b6; border-color: rgba(244,114,182,0.3); background: rgba(244,114,182,0.06); }

  .action-remove {
    background: none; border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm); padding: 2px 6px;
    font-size: 10px; cursor: pointer; color: var(--color-text-muted);
    font-family: var(--font-mono);
  }
  .action-remove:hover { border-color: var(--color-error); color: var(--color-error); }

  .form-grid { display: flex; gap: var(--spacing-md); flex-wrap: wrap; }
  .form-grid .form-row { min-width: 140px; }
  .form-row { display: flex; flex-direction: column; gap: 4px; }
  .form-label { font-family: var(--font-mono); font-size: 10px; color: var(--color-text-muted); }
  .hint { font-family: var(--font-mono); font-size: 9px; color: var(--color-text-tertiary); line-height: 1.5; }
  .warn { font-family: var(--font-mono); font-size: 10px; color: var(--color-warning, #f59e0b); margin: 0; }

  .form-input {
    background: var(--color-bg-secondary); border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm); padding: 6px 8px;
    font-size: 12px; color: var(--color-text-primary); font-family: inherit; width: 100%;
  }
  .form-input:focus { outline: none; border-color: var(--color-accent); }
  .form-input.ta { resize: vertical; line-height: 1.5; }
  .form-input.mono { font-family: var(--font-mono); font-size: 11px; }
  .form-input.num { width: 90px; font-family: var(--font-mono); }

  .form-select {
    background: var(--color-bg-secondary); border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm); padding: 6px 8px;
    font-size: 12px; color: var(--color-text-primary); cursor: pointer;
  }
  .form-select.narrow { width: auto; min-width: 160px; }

  .argv-row { display: flex; gap: 6px; align-items: center; }
  .add-link {
    align-self: flex-start; background: none; border: none;
    color: var(--color-text-tertiary); font-size: 10px;
    font-family: var(--font-mono); cursor: pointer; padding: 2px 0;
  }
  .add-link:hover { color: var(--color-accent); }

  .add-row { display: flex; flex-direction: column; gap: 4px; }

  .seg-ctrl { display: flex; flex-wrap: wrap; }
  .seg-btn {
    font-family: var(--font-mono); font-size: 10px;
    padding: 3px 8px; background: none;
    border: 1px solid var(--color-border-primary);
    cursor: pointer; color: var(--color-text-muted);
    transition: all 100ms; margin-left: -1px;
  }
  .seg-btn:first-child { border-radius: var(--radius-sm) 0 0 var(--radius-sm); margin-left: 0; }
  .seg-btn:last-child { border-radius: 0 var(--radius-sm) var(--radius-sm) 0; }
  .seg-btn.active { background: rgba(234,179,8,0.08); border-color: var(--color-accent); color: var(--color-accent); z-index: 1; position: relative; }
  .seg-btn:hover:not(.active) { color: var(--color-text-secondary); }
</style>

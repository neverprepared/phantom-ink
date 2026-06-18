<script lang="ts">
  /**
   * Three-tab Loops panel — the main operator surface for the loop-engineering
   * runtime. Replaces the standalone Loop Runs sidebar entry.
   *
   *   Templates — list of templates + raw YAML preview (CodeMirror editor
   *               lands in PR 5).
   *   Runs      — live + history view of LoopInstance records (lifted as
   *               LoopRunsPanel child component).
   *   Trigger   — pick a template, fill its required_refs, fire start_loop.
   */
  import { onMount } from 'svelte';
  import yaml from 'js-yaml';
  import { getApi } from '../utils/api';
  import { notifications } from '../notifications.svelte';
  import EmptyState from '../components/EmptyState.svelte';
  import Spinner from '../components/Spinner.svelte';
  import LoopRunsPanel from './LoopRunsPanel.svelte';

  type TabId = 'templates' | 'runs' | 'trigger';
  let activeTab = $state<TabId>('templates');

  // ---------------------------------------------------------------------------
  // Templates tab
  // ---------------------------------------------------------------------------

  interface LoopTemplate {
    name: string;
    origin: 'built-in' | 'user';
    version: string;
    hash: string;
    yaml: string;
  }

  let templateNames = $state<string[]>([]);
  let templatesLoading = $state(true);
  let selectedName = $state<string | null>(null);
  let selectedTemplate = $state<LoopTemplate | null>(null);
  let templateError = $state<string | null>(null);

  async function loadTemplateList() {
    templatesLoading = true;
    try {
      const api = await getApi();
      templateNames = (await api.ListLoopTemplates()) ?? [];
      if (templateNames.length > 0 && selectedName === null) {
        selectName(templateNames[0]);
      }
    } catch (err) {
      console.warn('LoopsPanel: failed to list templates', err);
      templateNames = [];
    } finally {
      templatesLoading = false;
    }
  }

  async function selectName(name: string) {
    selectedName = name;
    selectedTemplate = null;
    templateError = null;
    try {
      const api = await getApi();
      selectedTemplate = (await api.GetLoopTemplate(name)) as LoopTemplate;
    } catch (err) {
      templateError = err instanceof Error ? err.message : String(err);
    }
  }

  // ---------------------------------------------------------------------------
  // Trigger tab
  // ---------------------------------------------------------------------------

  interface RequiredRef {
    name: string;
    type: 'int' | 'string' | 'sha';
    description: string;
    required: boolean;
  }

  let triggerTemplateName = $state<string>('');
  let triggerRequiredRefs = $state<RequiredRef[]>([]);
  let triggerValues = $state<Record<string, string>>({});
  let triggerStarting = $state(false);
  let triggerError = $state<string | null>(null);

  $effect(() => {
    // When the operator picks a template in Trigger, fetch it and parse
    // required_refs out of the frontmatter.
    if (!triggerTemplateName) {
      triggerRequiredRefs = [];
      triggerValues = {};
      return;
    }
    void loadTriggerTemplate(triggerTemplateName);
  });

  async function loadTriggerTemplate(name: string) {
    triggerError = null;
    try {
      const api = await getApi();
      const tpl = (await api.GetLoopTemplate(name)) as LoopTemplate;
      const refs = parseRequiredRefs(tpl.yaml);
      triggerRequiredRefs = refs;
      // Reset values, preserving any the operator already typed for keys
      // that still exist in the new template.
      const next: Record<string, string> = {};
      for (const r of refs) {
        next[r.name] = triggerValues[r.name] ?? '';
      }
      triggerValues = next;
    } catch (err) {
      triggerError = err instanceof Error ? err.message : String(err);
    }
  }

  function parseRequiredRefs(rawYaml: string): RequiredRef[] {
    // Strip the markdown body so js-yaml only sees frontmatter.
    const fm = extractFrontmatter(rawYaml);
    if (!fm) return [];
    try {
      const parsed = yaml.load(fm) as Record<string, unknown> | null;
      const raw = parsed?.required_refs;
      if (!Array.isArray(raw)) return [];
      return raw.map((r): RequiredRef => ({
        name: String((r as Record<string, unknown>).name ?? ''),
        type: ((r as Record<string, unknown>).type as RequiredRef['type']) ?? 'string',
        description: String((r as Record<string, unknown>).description ?? ''),
        required: (r as Record<string, unknown>).required !== false,
      })).filter((r) => r.name);
    } catch {
      return [];
    }
  }

  function extractFrontmatter(content: string): string | null {
    if (!content.startsWith('---')) return null;
    const after = content.slice(3).replace(/^\n+/, '');
    const idx = after.indexOf('\n---');
    if (idx === -1) return null;
    return after.slice(0, idx);
  }

  function inputTypeFor(ref: RequiredRef): string {
    if (ref.type === 'int') return 'number';
    return 'text';
  }

  function coerceValue(ref: RequiredRef, raw: string): unknown {
    if (ref.type === 'int' && raw !== '') {
      const n = Number(raw);
      return Number.isFinite(n) ? n : raw;
    }
    return raw;
  }

  async function startTrigger() {
    if (!triggerTemplateName) return;
    triggerStarting = true;
    triggerError = null;
    try {
      const api = await getApi();
      const refs: Record<string, unknown> = {};
      for (const ref of triggerRequiredRefs) {
        const value = triggerValues[ref.name] ?? '';
        if (value === '' && !ref.required) continue;
        refs[ref.name] = coerceValue(ref, value);
      }
      const result = await api.StartLiveLoop(triggerTemplateName, refs);
      notifications.success(`Loop started: ${(result as { id: string }).id.slice(0, 8)}`);
      activeTab = 'runs';
    } catch (err) {
      triggerError = err instanceof Error ? err.message : String(err);
    } finally {
      triggerStarting = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Mount
  // ---------------------------------------------------------------------------

  onMount(() => {
    void loadTemplateList();
  });

  function originBadge(origin: string): string {
    if (origin === 'user') return 'badge badge-user';
    return 'badge badge-built';
  }
</script>

<div class="panel">
  <header class="panel-header">
    <h2>Loops</h2>
    <nav class="tabs">
      <button
        class="tab"
        class:active={activeTab === 'templates'}
        onclick={() => (activeTab = 'templates')}
      >
        Templates
      </button>
      <button
        class="tab"
        class:active={activeTab === 'runs'}
        onclick={() => (activeTab = 'runs')}
      >
        Runs
      </button>
      <button
        class="tab"
        class:active={activeTab === 'trigger'}
        onclick={() => (activeTab = 'trigger')}
      >
        Trigger
      </button>
    </nav>
  </header>

  {#if activeTab === 'templates'}
    <div class="templates-tab">
      <aside class="template-list">
        {#if templatesLoading}
          <div class="loading"><Spinner /></div>
        {:else if templateNames.length === 0}
          <EmptyState
            title="No templates"
            message="Loop templates live in brainbox/loop-templates/. Add one there or via PUT /api/loops/templates/<name>."
          />
        {:else}
          {#each templateNames as name (name)}
            <button
              class="template-item"
              class:active={selectedName === name}
              onclick={() => selectName(name)}
            >
              <span class="template-name">{name}</span>
            </button>
          {/each}
        {/if}
      </aside>
      <section class="template-detail">
        {#if templateError}
          <div class="error">{templateError}</div>
        {:else if selectedTemplate}
          <div class="template-head">
            <div class="title-row">
              <span class="template-title">{selectedTemplate.name}</span>
              <span class={originBadge(selectedTemplate.origin)}>{selectedTemplate.origin}</span>
              {#if selectedTemplate.version}
                <span class="dim">v{selectedTemplate.version}</span>
              {/if}
            </div>
            <div class="hash">hash {selectedTemplate.hash}</div>
          </div>
          <pre class="yaml-view">{selectedTemplate.yaml}</pre>
          <div class="editor-note">
            Read-only view — CodeMirror editor + save / fork / delete land in the next PR.
          </div>
        {:else}
          <div class="dim">Select a template to view.</div>
        {/if}
      </section>
    </div>
  {:else if activeTab === 'runs'}
    <div class="runs-tab">
      <LoopRunsPanel />
    </div>
  {:else if activeTab === 'trigger'}
    <div class="trigger-tab">
      <div class="trigger-form">
        <label class="field">
          Template
          <select bind:value={triggerTemplateName}>
            <option value="">— pick one —</option>
            {#each templateNames as name (name)}
              <option value={name}>{name}</option>
            {/each}
          </select>
        </label>

        {#if triggerRequiredRefs.length > 0}
          <div class="refs">
            <div class="refs-heading">Required artifact refs</div>
            {#each triggerRequiredRefs as ref (ref.name)}
              <label class="field">
                <span class="ref-label">
                  {ref.name}
                  <span class="ref-type">{ref.type}</span>
                  {#if !ref.required}<span class="ref-optional">optional</span>{/if}
                </span>
                <input
                  type={inputTypeFor(ref)}
                  bind:value={triggerValues[ref.name]}
                  placeholder={ref.description || ''}
                />
                {#if ref.description}
                  <span class="ref-description">{ref.description}</span>
                {/if}
              </label>
            {/each}
          </div>
        {:else if triggerTemplateName}
          <div class="dim">This template declares no required refs.</div>
        {/if}

        {#if triggerError}
          <div class="error">{triggerError}</div>
        {/if}

        <div class="actions">
          <button
            class="btn-start"
            disabled={!triggerTemplateName || triggerStarting}
            onclick={startTrigger}
          >
            {triggerStarting ? 'Starting…' : 'Start loop'}
          </button>
        </div>
      </div>
    </div>
  {/if}
</div>

<style>
  .panel {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
  }

  .panel-header {
    padding: 12px 20px 0;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .panel-header h2 {
    margin: 0;
    font-size: 18px;
    font-weight: 600;
  }

  .tabs {
    display: flex;
    gap: 4px;
    border-bottom: 1px solid var(--color-border, #2a2a2a);
  }
  .tab {
    background: transparent;
    border: none;
    color: var(--color-text-muted, #888);
    padding: 8px 12px;
    cursor: pointer;
    font-size: 13px;
    border-bottom: 2px solid transparent;
    transition: color 0.15s, border-color 0.15s;
  }
  .tab:hover {
    color: var(--color-text, #ddd);
  }
  .tab.active {
    color: var(--color-text, #ddd);
    border-bottom-color: var(--color-accent, #88c1ff);
  }

  /* Templates tab — split view */

  .templates-tab {
    flex: 1;
    min-height: 0;
    display: grid;
    grid-template-columns: 220px 1fr;
    gap: 12px;
    padding: 12px 20px;
    overflow: hidden;
  }
  .template-list {
    overflow: auto;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .template-item {
    background: transparent;
    border: 1px solid transparent;
    color: var(--color-text, #ddd);
    text-align: left;
    padding: 6px 10px;
    border-radius: 4px;
    cursor: pointer;
    font-size: 13px;
  }
  .template-item:hover {
    background: var(--color-surface-2, #1a1a1a);
  }
  .template-item.active {
    background: var(--color-surface-2, #1a1a1a);
    border-color: var(--color-border, #2a2a2a);
  }
  .template-name {
    font-family: var(--font-mono, monospace);
  }
  .template-detail {
    overflow: auto;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .template-head {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .title-row {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .template-title {
    font-weight: 600;
    font-size: 14px;
  }
  .badge {
    font-size: 10px;
    padding: 1px 6px;
    border-radius: 8px;
    background: #2a2a2a;
    color: #aaa;
    letter-spacing: 0.5px;
    text-transform: uppercase;
    font-weight: 600;
  }
  .badge-built {
    background: #2a2f4a;
    color: #b8c0e0;
  }
  .badge-user {
    background: #1f3a2a;
    color: #95e0a8;
  }
  .dim {
    color: var(--color-text-muted, #888);
    font-size: 12px;
  }
  .hash {
    font-family: var(--font-mono, monospace);
    font-size: 11px;
    color: var(--color-text-muted, #888);
  }
  .yaml-view {
    background: var(--color-surface-1, #181818);
    border: 1px solid var(--color-border, #2a2a2a);
    border-radius: 4px;
    padding: 10px;
    font-family: var(--font-mono, monospace);
    font-size: 12px;
    line-height: 1.5;
    color: var(--color-text, #ddd);
    overflow: auto;
    margin: 0;
    flex: 1;
    min-height: 0;
    white-space: pre;
  }
  .editor-note {
    font-size: 11px;
    color: var(--color-text-muted, #888);
    font-style: italic;
  }

  /* Runs tab — child panel takes over */
  .runs-tab {
    flex: 1;
    min-height: 0;
    overflow: hidden;
  }

  /* Trigger tab — form layout */

  .trigger-tab {
    flex: 1;
    overflow: auto;
    padding: 16px 20px;
  }
  .trigger-form {
    max-width: 540px;
    display: flex;
    flex-direction: column;
    gap: 14px;
  }
  .field {
    display: flex;
    flex-direction: column;
    gap: 4px;
    font-size: 12px;
    color: var(--color-text-muted, #888);
  }
  .field select,
  .field input {
    background: var(--color-surface-2, #1a1a1a);
    border: 1px solid var(--color-border, #2a2a2a);
    color: var(--color-text, #ddd);
    padding: 6px 10px;
    border-radius: 4px;
    font-size: 13px;
  }
  .refs {
    display: flex;
    flex-direction: column;
    gap: 12px;
    padding: 12px;
    background: var(--color-surface-1, #181818);
    border: 1px solid var(--color-border, #2a2a2a);
    border-radius: 4px;
  }
  .refs-heading {
    font-size: 11px;
    color: var(--color-text-muted, #888);
    text-transform: uppercase;
    letter-spacing: 0.5px;
    font-weight: 600;
  }
  .ref-label {
    display: flex;
    align-items: center;
    gap: 6px;
    color: var(--color-text, #ddd);
    font-size: 12px;
    font-family: var(--font-mono, monospace);
  }
  .ref-type {
    background: #2a2a2a;
    color: #aaa;
    font-size: 9px;
    padding: 1px 4px;
    border-radius: 3px;
    text-transform: uppercase;
  }
  .ref-optional {
    color: #ffb070;
    font-size: 10px;
    text-transform: uppercase;
  }
  .ref-description {
    font-size: 11px;
    color: var(--color-text-muted, #888);
    font-family: var(--font-sans, sans-serif);
  }
  .actions {
    display: flex;
    gap: 8px;
    margin-top: 4px;
  }
  .btn-start {
    background: var(--color-accent, #88c1ff);
    color: #111;
    border: none;
    padding: 8px 18px;
    border-radius: 4px;
    font-weight: 600;
    cursor: pointer;
    font-size: 13px;
  }
  .btn-start:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .loading {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .error {
    color: #ff9a9a;
    font-size: 12px;
    padding: 6px 10px;
    background: rgba(255, 0, 0, 0.05);
    border-left: 2px solid #ff9a9a;
  }
</style>

<script lang="ts">
  /**
   * Three-tab Loops panel — the main operator surface for the loop-engineering
   * runtime. Replaces the standalone Loop Runs sidebar entry.
   *
   *   Templates — list of templates + markdown editor + mermaid preview.
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
  import MarkdownEditor from '../components/MarkdownEditor.svelte';
  import MermaidDiagram from '../components/MermaidDiagram.svelte';
  import LoopRunsPanel from './LoopRunsPanel.svelte';

  type TabId = 'templates' | 'runs' | 'trigger';
  let activeTab = $state<TabId>('templates');

  // ---------------------------------------------------------------------------
  // Templates tab
  // ---------------------------------------------------------------------------

  interface LoopTemplate {
    name: string;
    origin: 'built-in' | 'user';
    hash: string;
    markdown: string;
  }

  let templateNames = $state<string[]>([]);
  let templatesLoading = $state(true);
  let selectedName = $state<string | null>(null);
  let selectedTemplate = $state<LoopTemplate | null>(null);
  let templateError = $state<string | null>(null);
  let editorValue = $state('');     // editor buffer (drifts from selectedTemplate.markdown when dirty)
  let savedMarkdown = $state('');   // last persisted text — diff against editorValue = dirty
  let templateBusy = $state(false); // true while save/fork/delete is in flight

  // Mermaid diagram preview state — recomputed on template change / save
  let diagramMermaid = $state<string>('');
  let diagramError = $state<string | null>(null);
  let diagramBusy = $state(false);

  // AI Assist state
  let assistPrompt = $state('');
  let assistBusy = $state(false);
  let assistError = $state<string | null>(null);
  let assistModel = $state('');
  let sessionCost = $state(0);   // cumulative USD across this editor session
  let editorSelection = $state<{ startLine: number; endLine: number; isEmpty: boolean }>({
    startLine: 1,
    endLine: 1,
    isEmpty: true,
  });
  let explanation = $state<string | null>(null);

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

  function isDirty(): boolean {
    return editorValue !== savedMarkdown;
  }

  async function selectName(name: string) {
    if (isDirty()) {
      const ok = window.confirm(
        `${selectedName} has unsaved changes. Discard them?`,
      );
      if (!ok) return;
    }
    selectedName = name;
    selectedTemplate = null;
    templateError = null;
    try {
      const api = await getApi();
      const tpl = (await api.GetLoopTemplate(name)) as LoopTemplate;
      selectedTemplate = tpl;
      savedMarkdown = tpl.markdown;
      editorValue = tpl.markdown;
      void refreshDiagram(name);
    } catch (err) {
      templateError = err instanceof Error ? err.message : String(err);
    }
  }

  // Fetch the mermaid diagram for the currently-saved template via dry-run.
  // Dry-run returns a structured plan with the rendered "mermaid" field.
  async function refreshDiagram(name: string) {
    diagramBusy = true;
    diagramError = null;
    try {
      const api = await getApi();
      const result = (await api.DryRunLoopTemplate(name, {})) as { mermaid?: string };
      diagramMermaid = result?.mermaid ?? '';
    } catch (err) {
      diagramError = err instanceof Error ? err.message : String(err);
      diagramMermaid = '';
    } finally {
      diagramBusy = false;
    }
  }

  // Editor → buffer
  function handleEditorChange(next: string) {
    editorValue = next;
  }

  // Debounced server-side validation: the editor passes the current doc to
  // this function on each lint pass (CodeMirror handles the debounce);
  // we hit /api/loops/templates/validate and return the structured errors.
  async function lintTemplate(text: string) {
    try {
      const api = await getApi();
      const result = await api.ValidateLoopTemplate(text);
      if (result?.ok) return [];
      return (result?.errors ?? []).map((e) => ({
        line: e.line ?? null,
        col: e.col ?? null,
        field: e.field ?? null,
        message: e.message,
      }));
    } catch (err) {
      console.warn('lint validation failed', err);
      return [];
    }
  }

  // ---------------------------------------------------------------------------
  // Save / Fork / Delete
  // ---------------------------------------------------------------------------

  async function saveTemplate() {
    if (!selectedTemplate) return;
    if (selectedTemplate.origin === 'built-in') {
      notifications.error('Built-in templates can\'t be saved. Use Fork.');
      return;
    }
    templateBusy = true;
    try {
      const api = await getApi();
      const updated = (await api.PutLoopTemplate(
        selectedTemplate.name,
        editorValue,
        false,
      )) as LoopTemplate;
      selectedTemplate = updated;
      savedMarkdown = updated.markdown;
      editorValue = updated.markdown;
      notifications.success(`${updated.name} saved`);
      void refreshDiagram(updated.name);
    } catch (err) {
      notifications.error(`Save failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      templateBusy = false;
    }
  }

  async function forkTemplate() {
    if (!selectedTemplate) return;
    templateBusy = true;
    try {
      const api = await getApi();
      const updated = (await api.PutLoopTemplate(
        selectedTemplate.name,
        editorValue,
        true,
      )) as LoopTemplate;
      // Re-list so the new user-origin copy appears as a fresh option;
      // re-select it so the editor reflects origin: "user" and Save is enabled.
      await loadTemplateList();
      selectedTemplate = updated;
      savedMarkdown = updated.markdown;
      editorValue = updated.markdown;
      selectedName = updated.name;
      notifications.success(`Forked ${updated.name} to user dir`);
      void refreshDiagram(updated.name);
    } catch (err) {
      notifications.error(`Fork failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      templateBusy = false;
    }
  }

  // ---------------------------------------------------------------------------
  // AI Assist — Generate / Refine / Explain
  // ---------------------------------------------------------------------------

  async function runAssist(mode: 'generate' | 'refine' | 'explain') {
    if (assistBusy) return;
    const prompt = assistPrompt.trim();
    if (!prompt) {
      assistError = 'Type what you want before firing.';
      return;
    }
    if (mode === 'refine' && editorSelection.isEmpty) {
      assistError = 'Highlight a markdown range in the editor first.';
      return;
    }
    assistError = null;
    assistBusy = true;
    explanation = null;
    try {
      const api = await getApi();
      const result = (await api.AssistLoopTemplate({
        mode,
        prompt,
        // Field name preserved on the wire as `current_yaml` for one
        // release for server-side compat; semantically it's markdown.
        current_yaml: editorValue,
        selection: mode === 'generate' ? {} : {
          start_line: editorSelection.startLine,
          end_line: editorSelection.endLine,
        },
      })) as unknown as {
        // Wire key still `yaml` for one release; payload is markdown.
        yaml: string;
        explanation: string;
        model: string;
        tokens: { input?: number; output?: number };
        cost_usd: number;
        warnings: { field: string | null; message: string }[];
      };

      assistModel = result.model;
      sessionCost += Number(result.cost_usd ?? 0);

      if (mode === 'explain') {
        explanation = result.explanation;
      } else {
        // Generate / Refine replace the editor doc. Confirm if dirty.
        if (isDirty()) {
          const ok = window.confirm('Editor has unsaved changes. Replace with AI output?');
          if (!ok) return;
        }
        editorValue = result.yaml;
        if (result.warnings && result.warnings.length > 0) {
          notifications.error(
            `AI output had ${result.warnings.length} validation warning(s) — review before saving`,
          );
        } else {
          notifications.success(`${mode === 'generate' ? 'Generated' : 'Refined'} via ${result.model}`);
        }
      }
      assistPrompt = '';
    } catch (err) {
      assistError = err instanceof Error ? err.message : String(err);
    } finally {
      assistBusy = false;
    }
  }

  function onAssistKeydown(ev: KeyboardEvent) {
    const meta = ev.metaKey || ev.ctrlKey;
    if (!meta) return;
    if (ev.key === 'Enter' && !ev.shiftKey) {
      ev.preventDefault();
      void runAssist('generate');
    } else if (ev.key === 'Enter' && ev.shiftKey) {
      ev.preventDefault();
      void runAssist('refine');
    } else if (ev.key === '/') {
      ev.preventDefault();
      void runAssist('explain');
    }
  }

  function fmtCost(usd: number): string {
    if (usd < 0.01) return `$${usd.toFixed(4)}`;
    return `$${usd.toFixed(3)}`;
  }

  async function deleteTemplate() {
    if (!selectedTemplate) return;
    if (selectedTemplate.origin === 'built-in') {
      notifications.error('Built-in templates can\'t be deleted.');
      return;
    }
    const ok = window.confirm(`Delete user template ${selectedTemplate.name}?`);
    if (!ok) return;
    templateBusy = true;
    try {
      const api = await getApi();
      await api.DeleteLoopTemplate(selectedTemplate.name);
      notifications.success(`${selectedTemplate.name} deleted`);
      selectedTemplate = null;
      savedMarkdown = '';
      editorValue = '';
      selectedName = null;
      diagramMermaid = '';
      await loadTemplateList();
    } catch (err) {
      notifications.error(`Delete failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      templateBusy = false;
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
      const refs = parseRequiredRefs(frontmatterFromMarkdown(tpl.markdown));
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

  function parseRequiredRefs(fm: string | null): RequiredRef[] {
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

  // Returns the YAML frontmatter slice (between opening and closing ---)
  // from a markdown template, or null if no frontmatter is present.
  function frontmatterFromMarkdown(text: string): string | null {
    if (!text || !text.startsWith('---')) return null;
    const after = text.slice(3).replace(/^\n+/, '');
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
              {#if isDirty()}
                <span class="dirty-dot" title="Unsaved changes"></span>
              {/if}
            </div>
            <div class="hash">hash {selectedTemplate.hash}</div>
          </div>
          <div class="assist-box">
            <div class="assist-head">
              <span class="assist-label">AI Assist</span>
              <span class="assist-meta">
                {#if assistModel}<span class="assist-model">{assistModel}</span>{/if}
                {#if sessionCost > 0}<span class="assist-cost">Cost: {fmtCost(sessionCost)}</span>{/if}
              </span>
            </div>
            <textarea
              class="assist-prompt"
              bind:value={assistPrompt}
              onkeydown={onAssistKeydown}
              placeholder="Describe what you want, or ask a question about the selected markdown. ⌘↵ Generate · ⌘⇧↵ Refine · ⌘/ Explain"
              rows="2"
              disabled={assistBusy}
            ></textarea>
            <div class="assist-actions">
              <button
                class="btn-generate"
                onclick={() => runAssist('generate')}
                disabled={assistBusy || !assistPrompt.trim()}
              >
                {assistBusy ? '…' : '✨ Generate'}
              </button>
              <button
                class="btn-refine"
                onclick={() => runAssist('refine')}
                disabled={assistBusy || !assistPrompt.trim() || editorSelection.isEmpty}
                title={editorSelection.isEmpty ? 'Highlight a markdown range first' : ''}
              >
                Refine selection
              </button>
              <button
                class="btn-explain"
                onclick={() => runAssist('explain')}
                disabled={assistBusy || !assistPrompt.trim()}
              >
                Explain
              </button>
            </div>
            {#if assistError}
              <div class="error">{assistError}</div>
            {/if}
            {#if explanation}
              <div class="explanation">
                <div class="explanation-head">
                  <span>Explanation</span>
                  <button class="explanation-close" onclick={() => (explanation = null)}>×</button>
                </div>
                <div class="explanation-body">{explanation}</div>
              </div>
            {/if}
          </div>
          <div class="diagram-section">
            <div class="diagram-head">
              <span class="diagram-label">Diagram</span>
              {#if diagramBusy}<span class="dim">rendering…</span>{/if}
            </div>
            {#if diagramError}
              <div class="error">{diagramError}</div>
            {:else}
              <MermaidDiagram source={diagramMermaid} scale={0.5} />
            {/if}
          </div>
          <details class="markdown-collapse">
            <summary class="markdown-summary">
              <span class="markdown-label">Markdown source</span>
              {#if isDirty()}
                <span class="dirty-dot" title="Unsaved changes"></span>
              {/if}
              <span class="dim summary-hint">click to expand</span>
            </summary>
            <div class="editor-wrap">
              <MarkdownEditor
                value={editorValue || selectedTemplate.markdown}
                onChange={handleEditorChange}
                lintRequest={lintTemplate}
                onSelectionChange={(sel) => (editorSelection = sel)}
              />
            </div>
          </details>
          <div class="editor-actions">
            {#if selectedTemplate.origin === 'user'}
              <button
                class="btn-save"
                onclick={saveTemplate}
                disabled={templateBusy || !isDirty()}
              >
                {templateBusy ? 'Saving…' : 'Save'}
              </button>
              <button
                class="btn-delete"
                onclick={deleteTemplate}
                disabled={templateBusy}
              >
                Delete
              </button>
            {:else}
              <button
                class="btn-fork"
                onclick={forkTemplate}
                disabled={templateBusy}
              >
                {templateBusy ? 'Forking…' : 'Fork to user'}
              </button>
              <span class="dim">Built-in templates are read-only. Fork to make changes.</span>
            {/if}
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
  .diagram-section {
    /* Dominant visual — claims the largest share of the detail pane.
       flex:1 alongside the (closed) markdown <details> gives it
       virtually the whole height; when the operator expands the
       markdown, both regions split available space. */
    flex: 1;
    min-height: 220px;
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 12px 14px;
    background: var(--bg-elev);
    border: 1px solid var(--border);
    border-radius: var(--r-sm);
    overflow: auto;
  }
  .diagram-head {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 11px;
  }
  .diagram-label {
    font-weight: 600;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  /* Collapsible markdown — keeps the editor available without it
     occupying chrome when the operator just wants to see the loop. */
  .markdown-collapse {
    display: flex;
    flex-direction: column;
    min-height: 0;
    background: var(--bg-elev);
    border: 1px solid var(--border);
    border-radius: var(--r-sm);
  }
  .markdown-collapse[open] {
    /* When expanded, claim space alongside the diagram. */
    flex: 1;
    min-height: 220px;
  }
  .markdown-summary {
    list-style: none;
    cursor: pointer;
    user-select: none;
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 14px;
    font-size: 11px;
    color: var(--text-muted);
  }
  .markdown-summary::-webkit-details-marker { display: none; }
  .markdown-summary::before {
    content: '▸';
    display: inline-block;
    color: var(--text-faint);
    transition: transform 0.15s ease;
  }
  .markdown-collapse[open] .markdown-summary::before {
    transform: rotate(90deg);
    color: var(--accent);
  }
  .markdown-label {
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  .summary-hint {
    margin-left: auto;
    font-style: italic;
  }
  .markdown-collapse[open] .summary-hint { display: none; }
  .editor-wrap {
    flex: 1;
    min-height: 200px;
    display: flex;
    flex-direction: column;
    padding: 0 8px 8px;
  }
  .editor-actions {
    display: flex;
    gap: 8px;
    align-items: center;
    padding-top: 4px;
  }
  .btn-save,
  .btn-fork,
  .btn-delete {
    border: 1px solid var(--color-border, #2a2a2a);
    background: var(--color-surface-2, #1a1a1a);
    color: var(--color-text, #ddd);
    padding: 5px 14px;
    font-size: 12px;
    border-radius: 4px;
    cursor: pointer;
  }
  .btn-save {
    background: var(--color-accent, #88c1ff);
    color: #111;
    border-color: var(--color-accent, #88c1ff);
    font-weight: 600;
  }
  .btn-fork {
    background: #1f3a2a;
    color: #95e0a8;
    border-color: #1f3a2a;
  }
  .btn-delete {
    background: transparent;
    color: #ff9a9a;
    border-color: #4a2a2a;
  }
  .btn-save:disabled,
  .btn-fork:disabled,
  .btn-delete:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .dirty-dot {
    display: inline-block;
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: #ffb070;
    margin-left: 4px;
  }

  /* AI Assist box */
  .assist-box {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 10px 12px;
    background: var(--color-surface-1, #181818);
    border: 1px solid var(--color-border, #2a2a2a);
    border-radius: 4px;
  }
  .assist-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 11px;
  }
  .assist-label {
    font-weight: 600;
    color: var(--color-text-muted, #888);
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  .assist-meta {
    display: flex;
    gap: 10px;
    align-items: center;
  }
  .assist-model {
    color: var(--color-accent, #88c1ff);
    font-family: var(--font-mono, monospace);
  }
  .assist-cost {
    color: var(--color-text-muted, #888);
  }
  .assist-prompt {
    background: var(--color-surface-2, #1a1a1a);
    border: 1px solid var(--color-border, #2a2a2a);
    color: var(--color-text, #ddd);
    padding: 6px 10px;
    border-radius: 4px;
    font-size: 12px;
    font-family: var(--font-sans, sans-serif);
    resize: vertical;
    min-height: 38px;
  }
  .assist-prompt:focus {
    outline: 1px solid var(--color-accent, #88c1ff);
    outline-offset: -1px;
  }
  .assist-actions {
    display: flex;
    gap: 6px;
  }
  .btn-generate,
  .btn-refine,
  .btn-explain {
    background: var(--color-surface-2, #1a1a1a);
    border: 1px solid var(--color-border, #2a2a2a);
    color: var(--color-text, #ddd);
    padding: 4px 10px;
    font-size: 11px;
    border-radius: 3px;
    cursor: pointer;
  }
  .btn-generate {
    background: var(--color-accent, #88c1ff);
    color: #111;
    border-color: var(--color-accent, #88c1ff);
    font-weight: 600;
  }
  .btn-generate:disabled,
  .btn-refine:disabled,
  .btn-explain:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
  .explanation {
    background: var(--color-surface-2, #1a1a1a);
    border: 1px solid var(--color-accent, #88c1ff);
    border-radius: 4px;
    padding: 8px 10px;
  }
  .explanation-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--color-text-muted, #888);
    margin-bottom: 4px;
  }
  .explanation-close {
    background: transparent;
    border: none;
    color: var(--color-text-muted, #888);
    cursor: pointer;
    font-size: 14px;
    padding: 0 4px;
  }
  .explanation-body {
    font-size: 12px;
    color: var(--color-text, #ddd);
    white-space: pre-wrap;
    line-height: 1.5;
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

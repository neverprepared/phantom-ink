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
  import Modal from '../components/Modal.svelte';
  import Spinner from '../components/Spinner.svelte';
  import MarkdownEditor from '../components/MarkdownEditor.svelte';
  import MermaidDiagram from '../components/MermaidDiagram.svelte';
  import LoopRunsPanel from './LoopRunsPanel.svelte';

  type TabId = 'templates' | 'runs' | 'trigger';
  let activeTab = $state<TabId>('templates');

  // Sub-tabs inside the Templates → detail pane.
  type DetailTabId = 'diagram' | 'markdown' | 'assist';
  let detailTab = $state<DetailTabId>('diagram');

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

  // Unsaved-changes guard. window.confirm is unreliable in some Wails
  // webviews (the + New and Delete flows already route through the shared
  // Modal), so the discard confirmations go through it too. Holds the pending
  // action to run if the operator confirms the discard.
  let discardConfirm = $state<null | { message: string; onConfirm: () => void }>(null);

  function guardUnsaved(message: string, proceed: () => void) {
    if (isDirty()) {
      discardConfirm = { message, onConfirm: proceed };
    } else {
      proceed();
    }
  }
  function closeDiscardConfirm() {
    discardConfirm = null;
  }
  function acceptDiscard() {
    const action = discardConfirm?.onConfirm;
    discardConfirm = null;
    action?.();
  }

  function selectName(name: string) {
    guardUnsaved(`${selectedName} has unsaved changes. Discard them?`, () => void openTemplate(name));
  }

  async function openTemplate(name: string) {
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
      // Refresh the sidebar list if this was a brand-new draft so the
      // new name appears there. Cheap — reuses the existing loader.
      if (!templateNames.includes(updated.name)) {
        await loadTemplateList();
        selectedName = updated.name;
      }
      notifications.success(`${updated.name} saved`);
      void refreshDiagram(updated.name);
    } catch (err) {
      notifications.error(`Save failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      templateBusy = false;
    }
  }

  // Seed for a brand-new template. Minimal valid LoopMarkdown — every
  // required key + section so the operator's first Save doesn't 422.
  function _newTemplateSkeleton(name: string): string {
    return `---
name: ${name}
trigger: manual
max_iterations: 3
---

# Role

Describe what the agent does each iteration.

# When to stop

- The goal is reached.

# When to escalate

- A blocker persists across iterations.
- The budget is exhausted.
`;
  }

  // Modal state for the "+ New" flow. window.prompt is unreliable in
  // some Wails webviews, so we use the existing Modal component for the
  // name input.
  let newTemplateModalOpen = $state(false);
  let newTemplateName = $state('my-loop');
  let newTemplateError = $state<string | null>(null);

  function openNewTemplateModal() {
    guardUnsaved(
      `${selectedName ?? 'Current template'} has unsaved changes. Discard them?`,
      () => {
        newTemplateName = 'my-loop';
        newTemplateError = null;
        newTemplateModalOpen = true;
      },
    );
  }

  function closeNewTemplateModal() {
    newTemplateModalOpen = false;
    newTemplateError = null;
  }

  function confirmNewTemplate() {
    const name = newTemplateName.trim();
    if (!/^[a-z0-9][a-z0-9-]*$/.test(name)) {
      newTemplateError = 'Use lowercase letters, digits, and hyphens; must start with a letter or digit.';
      return;
    }
    if (templateNames.includes(name)) {
      newTemplateError = `A template named "${name}" already exists.`;
      return;
    }
    const skeleton = _newTemplateSkeleton(name);
    // Synthetic in-memory draft. origin='user' so Save is enabled;
    // empty savedMarkdown keeps the dirty-dot lit until the first save
    // persists the file.
    selectedTemplate = {
      name,
      origin: 'user',
      hash: '',
      markdown: skeleton,
    } as LoopTemplate;
    selectedName = name;
    editorValue = skeleton;
    savedMarkdown = '';
    templateError = null;
    detailTab = 'markdown';
    diagramMermaid = '';
    diagramError = null;
    closeNewTemplateModal();
  }

  function onNewTemplateKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter') {
      e.preventDefault();
      confirmNewTemplate();
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
      // Persist the produced markdown server-side on every Generate
      // for a user-owned template (including fresh drafts AND existing
      // user templates the operator is regenerating). The operator's
      // work shouldn't be lost just because they navigate away. The
      // backend uses validate=False so even an imperfect draft lands
      // on disk — they can fix issues in-editor.
      const saveAs =
        mode === 'generate' && selectedTemplate && selectedTemplate.origin === 'user'
          ? selectedTemplate.name
          : '';

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
        save_as: saveAs,
      })) as unknown as {
        // Wire key still `yaml` for one release; payload is markdown.
        yaml: string;
        explanation: string;
        model: string;
        tokens: { input?: number; output?: number };
        cost_usd: number;
        warnings: { field: string | null; message: string }[];
        saved_to: string;
        save_error: string;
      };

      assistModel = result.model;
      sessionCost += Number(result.cost_usd ?? 0);

      if (mode === 'explain') {
        explanation = result.explanation;
        assistPrompt = '';
      } else {
        // Generate / Refine replace the editor doc. Confirm via the shared
        // Modal if the editor has unsaved changes.
        guardUnsaved(
          'Editor has unsaved changes. Replace with AI output?',
          () => void applyAiOutput(result, mode),
        );
      }
    } catch (err) {
      assistError = err instanceof Error ? err.message : String(err);
    } finally {
      assistBusy = false;
    }
  }

  // Replace the editor doc with AI output and surface the server-side persist
  // outcome. Deferred behind guardUnsaved so a dirty editor can confirm first;
  // runs immediately when the editor is clean.
  async function applyAiOutput(
    result: {
      yaml: string;
      model: string;
      warnings: { field: string | null; message: string }[];
      saved_to: string;
      save_error: string;
    },
    mode: 'generate' | 'refine',
  ) {
    editorValue = result.yaml;
    if (result.warnings && result.warnings.length > 0) {
      notifications.error(
        `AI output had ${result.warnings.length} validation warning(s) — review before saving`,
      );
    } else {
      notifications.success(`${mode === 'generate' ? 'Generated' : 'Refined'} via ${result.model}`);
    }

    // Surface the server-side persist outcome so the operator knows whether
    // their work is on disk regardless of what they do next.
    if (mode === 'generate') {
      if (result.saved_to) {
        notifications.success(`Saved as ${result.saved_to}`);
        savedMarkdown = result.yaml;
        // Refresh sidebar so the new file appears immediately.
        if (!templateNames.includes(result.saved_to)) {
          await loadTemplateList();
          selectedName = result.saved_to;
        }
        // selectedTemplate.hash is stale after a save — reload to pick up the
        // new hash so the dirty-dot doesn't lie.
        if (selectedTemplate) {
          try {
            const api2 = await getApi();
            const reloaded = (await api2.GetLoopTemplate(result.saved_to)) as LoopTemplate;
            selectedTemplate = reloaded;
            savedMarkdown = reloaded.markdown;
            editorValue = reloaded.markdown;
          } catch {
            // Best-effort reload; ignore.
          }
        }
        void refreshDiagram(result.saved_to);
      } else if (result.save_error) {
        notifications.error(`Persist failed: ${result.save_error}`);
      }
    }
    assistPrompt = '';
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

  // Delete confirmation modal — window.confirm is unreliable in Wails
  // webviews (same root cause as the + New / window.prompt bug).
  let deleteModalOpen = $state(false);
  let deleteBusy = $state(false);

  function openDeleteModal() {
    if (!selectedTemplate) return;
    if (selectedTemplate.origin === 'built-in') {
      notifications.error('Built-in templates can\'t be deleted.');
      return;
    }
    deleteModalOpen = true;
  }

  function closeDeleteModal() {
    if (deleteBusy) return;
    deleteModalOpen = false;
  }

  async function confirmDeleteTemplate() {
    if (!selectedTemplate) return;
    deleteBusy = true;
    try {
      const api = await getApi();
      await api.DeleteLoopTemplate(selectedTemplate.name);
      notifications.success(`${selectedTemplate.name} deleted`);
      selectedTemplate = null;
      savedMarkdown = '';
      editorValue = '';
      selectedName = null;
      diagramMermaid = '';
      deleteModalOpen = false;
      await loadTemplateList();
    } catch (err) {
      notifications.error(`Delete failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      deleteBusy = false;
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

  // Count of active (pending/running) loops — surfaced as a badge on
  // the Runs tab so the operator sees in-flight work the moment they
  // open the panel.
  let runningLoopCount = $state(0);
  let runningLoopByName = $state<Record<string, number>>({});
  let runningPollHandle: number | null = null;

  async function refreshRunningLoops() {
    try {
      const api = await getApi();
      // No status filter → returns all; we count the active ones
      // client-side so we get a stable badge across the two active
      // statuses (pending + running). Cheap; payload is slim.
      const summaries =
        ((await api.ListLiveLoops('')) ?? []) as Array<{ status: string; name: string }>;
      let total = 0;
      const byName: Record<string, number> = {};
      for (const s of summaries) {
        if (s.status === 'running' || s.status === 'pending') {
          total += 1;
          byName[s.name] = (byName[s.name] ?? 0) + 1;
        }
      }
      runningLoopCount = total;
      runningLoopByName = byName;
    } catch (err) {
      console.warn('LoopsPanel: failed to fetch running loops', err);
    }
  }

  onMount(() => {
    void loadTemplateList();
    void refreshRunningLoops();
    // Poll every 15s — the count is informational, not a control
    // surface, so we don't need SSE here.
    runningPollHandle = window.setInterval(refreshRunningLoops, 15_000);
    return () => {
      if (runningPollHandle !== null) {
        window.clearInterval(runningPollHandle);
        runningPollHandle = null;
      }
    };
  });

  function originBadge(origin: string): string {
    if (origin === 'user') return 'badge badge-user';
    return 'badge badge-built';
  }
</script>

<div class="panel">
  <header class="panel-header">
    <h1 class="page-title">loops</h1>
    <nav class="tabs">
      <button
        class="tab"
        class:active={activeTab === 'templates'}
        onclick={() => (activeTab = 'templates')}
      >
        templates
      </button>
      <button
        class="tab"
        class:active={activeTab === 'runs'}
        onclick={() => (activeTab = 'runs')}
      >
        runs
        {#if runningLoopCount > 0}
          <span class="running-badge" title="{runningLoopCount} loop{runningLoopCount === 1 ? '' : 's'} in flight">
            {runningLoopCount}
          </span>
        {/if}
      </button>
      <button
        class="tab"
        class:active={activeTab === 'trigger'}
        onclick={() => (activeTab = 'trigger')}
      >
        trigger
      </button>
    </nav>
  </header>

  {#if activeTab === 'templates'}
    <div class="templates-tab">
      <aside class="template-list">
        <div class="template-list-head">
          <span class="template-list-label">templates</span>
          <button class="btn-new" onclick={openNewTemplateModal} title="Create a new loop template">
            + new
          </button>
        </div>
        {#if templatesLoading}
          <div class="loading"><Spinner /></div>
        {:else if templateNames.length === 0}
          <EmptyState
            title="No templates"
            message="Click + New to create your first loop, or add one under brainbox/loop-templates/."
          />
        {:else}
          {#each templateNames as name (name)}
            <button
              class="template-item"
              class:active={selectedName === name}
              onclick={() => selectName(name)}
            >
              <span class="template-name">{name}</span>
              {#if runningLoopByName[name]}
                <span
                  class="running-badge sidebar"
                  title="{runningLoopByName[name]} in flight"
                >
                  {runningLoopByName[name]}
                </span>
              {/if}
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

          <nav class="sub-tabs" aria-label="Template view">
            <button
              class="sub-tab"
              class:active={detailTab === 'diagram'}
              onclick={() => (detailTab = 'diagram')}
            >
              diagram
            </button>
            <button
              class="sub-tab"
              class:active={detailTab === 'markdown'}
              onclick={() => (detailTab = 'markdown')}
            >
              markdown
              {#if isDirty()}<span class="dirty-dot inline" title="Unsaved changes"></span>{/if}
            </button>
            <button
              class="sub-tab"
              class:active={detailTab === 'assist'}
              onclick={() => (detailTab = 'assist')}
            >
              ai assist
              {#if assistBusy}<span class="dim sub-tab-hint">…</span>{/if}
            </button>
          </nav>

          {#if detailTab === 'diagram'}
            <div class="diagram-section">
              <div class="diagram-head">
                <span class="diagram-label">diagram</span>
                {#if diagramBusy}<span class="dim">rendering…</span>{/if}
              </div>
              {#if diagramError}
                <div class="error">{diagramError}</div>
              {:else}
                <MermaidDiagram source={diagramMermaid} initialZoom={0.25} />
              {/if}
            </div>
          {:else if detailTab === 'markdown'}
            <div class="editor-wrap">
              <MarkdownEditor
                value={editorValue || selectedTemplate.markdown}
                onChange={handleEditorChange}
                lintRequest={lintTemplate}
                onSelectionChange={(sel) => (editorSelection = sel)}
              />
            </div>
          {:else if detailTab === 'assist'}
            <div class="assist-box">
              <div class="assist-head">
                <span class="assist-label">ai assist</span>
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
                rows="3"
                disabled={assistBusy}
              ></textarea>
              <div class="assist-actions">
                <button
                  class="btn-generate"
                  onclick={() => runAssist('generate')}
                  disabled={assistBusy || !assistPrompt.trim()}
                >
                  {assistBusy ? '…' : '✨ generate'}
                </button>
                <button
                  class="btn-refine"
                  onclick={() => runAssist('refine')}
                  disabled={assistBusy || !assistPrompt.trim() || editorSelection.isEmpty}
                  title={editorSelection.isEmpty ? 'Highlight a markdown range in the Markdown tab first' : ''}
                >
                  refine selection
                </button>
                <button
                  class="btn-explain"
                  onclick={() => runAssist('explain')}
                  disabled={assistBusy || !assistPrompt.trim()}
                >
                  explain
                </button>
              </div>
              {#if assistError}
                <div class="error">{assistError}</div>
              {/if}
              {#if explanation}
                <div class="explanation">
                  <div class="explanation-head">
                    <span>explanation</span>
                    <button class="explanation-close" onclick={() => (explanation = null)} aria-label="Close explanation" title="Close explanation">×</button>
                  </div>
                  <div class="explanation-body">{explanation}</div>
                </div>
              {/if}
              <div class="dim assist-hint">
                Refine needs a highlighted range in the
                <button
                  class="link-btn"
                  type="button"
                  onclick={() => (detailTab = 'markdown')}
                >Markdown</button> tab.
              </div>
            </div>
          {/if}
          <div class="editor-actions">
            {#if selectedTemplate.origin === 'user'}
              <button
                class="btn-save"
                onclick={saveTemplate}
                disabled={templateBusy || !isDirty()}
              >
                {templateBusy ? 'saving…' : 'save'}
              </button>
              <button
                class="btn-delete"
                onclick={openDeleteModal}
                disabled={templateBusy}
              >
                delete
              </button>
            {:else}
              <button
                class="btn-fork"
                onclick={forkTemplate}
                disabled={templateBusy}
              >
                {templateBusy ? 'forking…' : 'fork to user'}
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
      <LoopRunsPanel embedded />
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
            {triggerStarting ? 'starting…' : 'start loop'}
          </button>
        </div>
      </div>
    </div>
  {/if}
</div>

{#if deleteModalOpen && selectedTemplate}
  <Modal onClose={closeDeleteModal} maxWidth="420px">
    <div class="new-modal">
      <h3>Delete template?</h3>
      <p class="dim modal-help">
        This permanently removes the user template
        <code class="modal-code">{selectedTemplate.name}</code>.
        In-flight loops continue running with their frozen
        template_text; only new triggers from the Templates list are
        affected.
      </p>
      <div class="modal-actions">
        <button
          class="btn-modal-cancel"
          onclick={closeDeleteModal}
          disabled={deleteBusy}
        >cancel</button>
        <button
          class="btn-modal-delete"
          onclick={confirmDeleteTemplate}
          disabled={deleteBusy}
        >
          {deleteBusy ? 'deleting…' : 'delete'}
        </button>
      </div>
    </div>
  </Modal>
{/if}

{#if newTemplateModalOpen}
  <Modal onClose={closeNewTemplateModal} maxWidth="420px">
    <div class="new-modal">
      <h3>New loop template</h3>
      <p class="dim modal-help">
        Pick a slug: lowercase letters, digits, and hyphens. Must start
        with a letter or digit.
      </p>
      <label class="modal-label">
        Template name
        <input
          type="text"
          class="modal-input"
          bind:value={newTemplateName}
          onkeydown={onNewTemplateKeydown}
          placeholder="my-loop"
          autocomplete="off"
          spellcheck="false"
        />
      </label>
      {#if newTemplateError}
        <div class="modal-error">{newTemplateError}</div>
      {/if}
      <div class="modal-actions">
        <button class="btn-modal-cancel" onclick={closeNewTemplateModal}>cancel</button>
        <button class="btn-modal-create" onclick={confirmNewTemplate}>create</button>
      </div>
    </div>
  </Modal>
{/if}

{#if discardConfirm}
  <Modal onClose={closeDiscardConfirm} maxWidth="400px">
    <div class="new-modal">
      <h3>Unsaved changes</h3>
      <p class="dim modal-help">{discardConfirm.message}</p>
      <div class="modal-actions">
        <button class="btn-modal-cancel" onclick={closeDiscardConfirm}>keep editing</button>
        <button class="btn-modal-create" onclick={acceptDiscard}>discard</button>
      </div>
    </div>
  </Modal>
{/if}

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
    border-bottom: 1px solid var(--color-border-primary);
  }
  .tab {
    background: transparent;
    border: none;
    color: var(--color-text-muted);
    padding: 8px 12px;
    cursor: pointer;
    font-size: 13px;
    border-bottom: 2px solid transparent;
    transition: color 0.15s, border-color 0.15s;
  }
  .tab:hover {
    color: var(--color-text-primary);
  }
  .tab.active {
    color: var(--color-text-primary);
    border-bottom-color: var(--color-accent);
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
  .template-list-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 2px 4px 8px;
    margin-bottom: 4px;
    border-bottom: 1px solid var(--border);
  }
  .template-list-label {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--text-muted);
  }
  .btn-new {
    background: var(--accent);
    color: #fff;
    border: none;
    border-radius: var(--r-sm);
    padding: 4px 10px;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    transition: background-color 0.12s;
  }
  .btn-new:hover {
    background: color-mix(in srgb, var(--accent) 88%, #000);
  }
  .template-item {
    background: transparent;
    border: 1px solid transparent;
    color: var(--color-text-primary);
    text-align: left;
    padding: 6px 10px;
    border-radius: 4px;
    cursor: pointer;
    font-size: 13px;
    display: flex;
    align-items: center;
    gap: 8px;
    justify-content: space-between;
  }
  /* Active-run count chip — reused on the Runs tab and per-template in
     the sidebar so the operator sees in-flight loops at a glance. */
  .running-badge {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 18px;
    height: 18px;
    padding: 0 6px;
    margin-left: 6px;
    border-radius: 99px;
    background: var(--run);
    color: #fff;
    font-size: 10px;
    font-weight: 700;
    font-family: var(--font-mono);
    line-height: 1;
    /* Pulse so the operator catches in-flight work peripherally. */
    animation: pulse 2.2s ease-in-out infinite;
  }
  .running-badge.sidebar {
    margin-left: 0;
  }
  .template-item:hover {
    background: var(--color-bg-secondary);
  }
  .template-item.active {
    background: var(--color-bg-secondary);
    border-color: var(--color-border-primary);
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
    background: var(--color-bg-tertiary);
    color: var(--color-text-muted);
    letter-spacing: 0.5px;
    text-transform: uppercase;
    font-weight: 600;
  }
  .badge-built {
    background: var(--task-soft);
    color: var(--task);
  }
  .badge-user {
    background: var(--run-soft);
    color: var(--run);
  }
  .dim {
    color: var(--color-text-muted);
    font-size: 12px;
  }
  .hash {
    font-family: var(--font-mono, monospace);
    font-size: 11px;
    color: var(--color-text-muted);
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
  /* Sub-tabs inside the Templates → detail pane. Visually distinct
     from the top tabs (smaller, no underline strip) so the operator
     can tell which level they're navigating. */
  .sub-tabs {
    display: flex;
    gap: 2px;
    padding: 2px;
    background: var(--bg-sunken);
    border: 1px solid var(--border);
    border-radius: var(--r-sm);
    align-self: flex-start;
  }
  .sub-tab {
    background: transparent;
    border: none;
    color: var(--text-muted);
    padding: 5px 12px;
    font-size: 12px;
    border-radius: calc(var(--r-sm) - 1px);
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    transition: background-color 0.12s, color 0.12s;
  }
  .sub-tab:hover { color: var(--text); }
  .sub-tab.active {
    background: var(--bg-elev);
    color: var(--text);
    box-shadow: var(--shadow-sm);
  }
  .sub-tab-hint {
    font-size: 10px;
  }
  .dirty-dot.inline {
    width: 6px;
    height: 6px;
    margin: 0;
  }
  .editor-wrap {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
  }
  /* Hint linking AI Assist → Markdown for the Refine flow. */
  .assist-hint {
    padding-top: 4px;
    border-top: 1px dashed var(--border);
    margin-top: 4px;
  }
  .link-btn {
    background: none;
    border: none;
    color: var(--accent);
    cursor: pointer;
    font: inherit;
    padding: 0;
    text-decoration: underline;
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
    border: 1px solid var(--color-border-primary);
    background: var(--color-bg-secondary);
    color: var(--color-text-primary);
    padding: 5px 14px;
    font-size: 12px;
    border-radius: 4px;
    cursor: pointer;
  }
  .btn-save {
    background: var(--color-accent);
    color: #111;
    border-color: var(--color-accent);
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
    background: var(--color-bg-tertiary);
    border: 1px solid var(--color-border-primary);
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
    color: var(--color-text-muted);
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  .assist-meta {
    display: flex;
    gap: 10px;
    align-items: center;
  }
  .assist-model {
    color: var(--color-accent);
    font-family: var(--font-mono, monospace);
  }
  .assist-cost {
    color: var(--color-text-muted);
  }
  .assist-prompt {
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-primary);
    color: var(--color-text-primary);
    padding: 6px 10px;
    border-radius: 4px;
    font-size: 12px;
    font-family: var(--font-sans, sans-serif);
    resize: vertical;
    min-height: 38px;
  }
  .assist-prompt:focus {
    outline: 1px solid var(--color-accent);
    outline-offset: -1px;
  }
  .assist-actions {
    display: flex;
    gap: 6px;
  }
  .btn-generate,
  .btn-refine,
  .btn-explain {
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-primary);
    color: var(--color-text-primary);
    padding: 4px 10px;
    font-size: 11px;
    border-radius: 3px;
    cursor: pointer;
  }
  .btn-generate {
    background: var(--color-accent);
    color: #111;
    border-color: var(--color-accent);
    font-weight: 600;
  }
  .btn-generate:disabled,
  .btn-refine:disabled,
  .btn-explain:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
  .explanation {
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-accent);
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
    color: var(--color-text-muted);
    margin-bottom: 4px;
  }
  .explanation-close {
    background: transparent;
    border: none;
    color: var(--color-text-muted);
    cursor: pointer;
    font-size: 14px;
    padding: 0 4px;
  }
  .explanation-body {
    font-size: 12px;
    color: var(--color-text-primary);
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
    color: var(--color-text-muted);
  }
  .field select,
  .field input {
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-primary);
    color: var(--color-text-primary);
    padding: 6px 10px;
    border-radius: 4px;
    font-size: 13px;
  }
  .refs {
    display: flex;
    flex-direction: column;
    gap: 12px;
    padding: 12px;
    background: var(--color-bg-tertiary);
    border: 1px solid var(--color-border-primary);
    border-radius: 4px;
  }
  .refs-heading {
    font-size: 11px;
    color: var(--color-text-muted);
    text-transform: uppercase;
    letter-spacing: 0.5px;
    font-weight: 600;
  }
  .ref-label {
    display: flex;
    align-items: center;
    gap: 6px;
    color: var(--color-text-primary);
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
    color: var(--color-text-muted);
    font-family: var(--font-sans, sans-serif);
  }
  .actions {
    display: flex;
    gap: 8px;
    margin-top: 4px;
  }
  .btn-start {
    background: var(--color-accent);
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
    color: var(--fail);
    font-size: 12px;
    padding: 6px 10px;
    background: var(--fail-soft);
    border-left: 2px solid var(--fail);
  }

  /* New-template modal */
  .new-modal {
    display: flex;
    flex-direction: column;
    gap: 14px;
    color: var(--text);
  }
  .new-modal h3 {
    margin: 0;
    font-size: 16px;
    font-weight: 600;
  }
  .modal-help {
    font-size: 12px;
    margin: 0;
    line-height: 1.5;
  }
  .modal-label {
    display: flex;
    flex-direction: column;
    gap: 6px;
    font-size: 12px;
    color: var(--text-muted);
  }
  .modal-input {
    width: 100%;
    padding: 8px 10px;
    border: 1px solid var(--border);
    border-radius: var(--r-sm);
    background: var(--bg-sunken);
    color: var(--text);
    font-family: var(--font-mono);
    font-size: 13px;
  }
  .modal-input:focus {
    outline: none;
    border-color: var(--accent);
  }
  .modal-error {
    font-size: 12px;
    color: var(--fail);
    padding: 6px 8px;
    background: var(--fail-soft);
    border-left: 2px solid var(--fail);
    border-radius: 2px;
  }
  .modal-actions {
    display: flex;
    gap: 8px;
    justify-content: flex-end;
  }
  .btn-modal-cancel,
  .btn-modal-create {
    border: 1px solid var(--border);
    background: var(--bg-elev);
    color: var(--text);
    padding: 7px 16px;
    font-size: 13px;
    border-radius: var(--r-sm);
    cursor: pointer;
    font-weight: 600;
  }
  .btn-modal-create {
    background: var(--accent);
    color: #fff;
    border-color: var(--accent);
  }
  .btn-modal-create:hover {
    background: color-mix(in srgb, var(--accent) 88%, #000);
  }
  .btn-modal-cancel:hover {
    background: var(--bg-hover);
  }
  .btn-modal-delete {
    border: 1px solid var(--fail);
    background: var(--fail);
    color: #fff;
    padding: 7px 16px;
    font-size: 13px;
    border-radius: var(--r-sm);
    cursor: pointer;
    font-weight: 600;
  }
  .btn-modal-delete:hover:not(:disabled) {
    background: color-mix(in srgb, var(--fail) 88%, #000);
  }
  .btn-modal-delete:disabled,
  .btn-modal-cancel:disabled,
  .btn-modal-create:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .modal-code {
    font-family: var(--font-mono);
    background: var(--bg-sunken);
    padding: 1px 6px;
    border-radius: 3px;
    color: var(--text);
  }
</style>

<script lang="ts">
  /**
   * Trigger tab — extracted from LoopsPanel. Self-contained: pick a template,
   * fill its required_refs (parsed from the frontmatter), and fire start_loop.
   * templateNames feeds the dropdown; onStarted lets the parent switch to Runs.
   */
  import yaml from 'js-yaml';
  import { getApi } from '../utils/api';
  import { notifications } from '../notifications.svelte';

  let { templateNames, onStarted }: {
    templateNames: string[];
    onStarted: () => void;
  } = $props();

  interface RequiredRef {
    name: string;
    type: 'int' | 'string' | 'sha';
    description: string;
    required: boolean;
  }
  interface LoopTemplate { markdown: string }

  let triggerTemplateName = $state<string>('');
  let triggerRequiredRefs = $state<RequiredRef[]>([]);
  let triggerValues = $state<Record<string, string>>({});
  let triggerStarting = $state(false);
  let triggerError = $state<string | null>(null);

  $effect(() => {
    // When the operator picks a template, fetch it and parse required_refs.
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
      // Reset values, preserving any the operator already typed for keys that
      // still exist in the new template.
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

  // Returns the YAML frontmatter slice (between opening and closing ---) from a
  // markdown template, or null if none is present.
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
      onStarted();
    } catch (err) {
      triggerError = err instanceof Error ? err.message : String(err);
    } finally {
      triggerStarting = false;
    }
  }
</script>

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

<style>
  .dim {
    color: var(--color-text-muted);
    font-size: 12px;
  }
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
  .error {
    color: var(--fail);
    font-size: 12px;
    padding: 6px 10px;
    background: var(--fail-soft);
    border-left: 2px solid var(--fail);
  }
</style>

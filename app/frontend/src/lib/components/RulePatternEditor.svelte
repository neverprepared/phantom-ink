<script lang="ts">
  import { getApi } from '../utils/api';
  import {
    builderToPattern,
    emptyBuilder,
    patternToBuilder,
    type BuilderState,
    type RuleTestMatch,
  } from '../rules';
  import { relativeMs } from '../rules';

  // Builder ⇄ raw-JSON pattern editor with live Test against recent events.
  // The parent pulls the current pattern via getPattern(); patternErrors is
  // the shared inline error list (save-time 400s land here too).
  let {
    initial,
    defaultWorkspace = '',
    patternErrors = $bindable([] as string[]),
  }: {
    initial: Record<string, any> | null;
    defaultWorkspace?: string;
    patternErrors?: string[];
  } = $props();

  let mode = $state<'builder' | 'raw'>('builder');
  let builder = $state<BuilderState>(emptyBuilder());
  let rawJson = $state('{}');
  let rawError = $state('');
  let rawNotice = $state('');
  let testing = $state(false);
  let testResult = $state<{ matches: RuleTestMatch[]; scanned: number } | null>(null);

  // Initialize from the incoming pattern exactly once.
  const init = initial && Object.keys(initial).length > 0 ? initial : null;
  const initBuilder = init ? patternToBuilder(init) : null;
  if (init && initBuilder === null) {
    mode = 'raw';
    rawJson = JSON.stringify(init, null, 2);
    rawNotice = "pattern uses features the builder can't represent — editing as raw JSON";
  } else if (initBuilder) {
    builder = initBuilder;
  } else if (defaultWorkspace) {
    builder.workspace = defaultWorkspace;
  }

  export function getPattern(): Record<string, any> | null {
    if (mode === 'builder') return builderToPattern(builder);
    try {
      const parsed = JSON.parse(rawJson);
      rawError = '';
      return parsed;
    } catch (e: any) {
      rawError = `invalid JSON: ${e?.message ?? e}`;
      return null;
    }
  }

  function toRaw() {
    rawJson = JSON.stringify(builderToPattern(builder), null, 2);
    rawError = '';
    rawNotice = '';
    mode = 'raw';
  }

  function toBuilder() {
    let parsed: any;
    try {
      parsed = JSON.parse(rawJson);
    } catch (e: any) {
      rawError = `invalid JSON: ${e?.message ?? e}`;
      return;
    }
    const b = patternToBuilder(parsed);
    if (b === null) {
      rawNotice = "pattern uses features the builder can't represent — staying in raw JSON";
      return;
    }
    builder = b;
    rawError = '';
    rawNotice = '';
    mode = 'builder';
  }

  function addTypeEntry() {
    builder.types = [...builder.types, { value: '', mode: 'exact' }];
  }
  function removeTypeEntry(i: number) {
    builder.types = builder.types.filter((_, idx) => idx !== i);
    if (builder.types.length === 0) builder.types = [{ value: '', mode: 'exact' }];
  }

  function clearFeedback() {
    testResult = null;
    patternErrors = [];
  }

  async function runTest() {
    const pattern = getPattern();
    if (pattern === null) return;
    const api = await getApi();
    if (!api) return;
    testing = true;
    testResult = null;
    try {
      const res = await api.TestRulePattern(pattern, 50);
      if (!res.valid) {
        patternErrors = res.errors ?? [];
        return;
      }
      patternErrors = [];
      testResult = { matches: (res.matches ?? []) as RuleTestMatch[], scanned: res.scanned ?? 0 };
    } catch (e: any) {
      patternErrors = [String(e?.message ?? e)];
    } finally {
      testing = false;
    }
  }

  const isEmptyPattern = $derived(
    mode === 'builder' && Object.keys(builderToPattern(builder)).length === 0
  );
</script>

<div class="pattern-editor">
  <div class="pe-header">
    <span class="form-label">pattern</span>
    <div class="pe-tools">
      <button class="pe-mode" class:active={mode === 'builder'} onclick={toBuilder}>builder</button>
      <button class="pe-mode" class:active={mode === 'raw'} onclick={toRaw}>raw JSON</button>
      <button class="pe-test" onclick={runTest} disabled={testing}>
        {testing ? 'testing…' : 'test'}
      </button>
    </div>
  </div>

  {#if mode === 'builder'}
    <div class="pe-builder">
      <div class="pe-row">
        <span class="pe-label">event type</span>
        <div class="pe-types">
          {#each builder.types as entry, i (i)}
            <div class="pe-type-row">
              <input
                class="form-input"
                bind:value={entry.value}
                oninput={clearFeedback}
                placeholder={entry.mode === 'prefix' ? 'task.' : 'task.failed'}
              />
              <div class="seg-ctrl">
                <button class="seg-btn" class:active={entry.mode === 'exact'}
                  onclick={() => { entry.mode = 'exact'; clearFeedback(); }}>exact</button>
                <button class="seg-btn" class:active={entry.mode === 'prefix'}
                  onclick={() => { entry.mode = 'prefix'; clearFeedback(); }}>prefix</button>
              </div>
              {#if builder.types.length > 1 || entry.value}
                <button class="pe-remove" onclick={() => { removeTypeEntry(i); clearFeedback(); }} title="remove">✕</button>
              {/if}
            </div>
          {/each}
          <button class="pe-add" onclick={addTypeEntry}>+ another type</button>
        </div>
      </div>
      <div class="pe-row">
        <span class="pe-label">workspace</span>
        <input class="form-input" bind:value={builder.workspace} oninput={clearFeedback} placeholder="any" />
      </div>
      <div class="pe-row">
        <span class="pe-label">source</span>
        <input class="form-input" bind:value={builder.source} oninput={clearFeedback} placeholder="any (e.g. brainbox-hub)" />
      </div>
      <div class="pe-row">
        <span class="pe-label">status</span>
        <select class="form-select narrow" bind:value={builder.status} onchange={clearFeedback}>
          <option value="">any</option>
          {#each ['upcoming', 'active', 'done', 'failed', 'blocked', 'needs_action'] as s (s)}
            <option value={s}>{s}</option>
          {/each}
        </select>
      </div>
      {#if isEmptyPattern}
        <p class="pe-hint">empty pattern matches every event</p>
      {/if}
    </div>
  {:else}
    <textarea
      class="pe-raw"
      bind:value={rawJson}
      oninput={() => { rawError = ''; clearFeedback(); }}
      rows="8"
      spellcheck="false"
    ></textarea>
    <p class="pe-hint">
      EventBridge-style: field → list of values or operators
      <code>{'{"prefix"}'}</code> <code>{'{"suffix"}'}</code> <code>{'{"exists"}'}</code>
      <code>{'{"anything-but"}'}</code> <code>{'{"numeric"}'}</code>; nested objects recurse.
    </p>
  {/if}

  {#if rawError}<p class="pe-error">{rawError}</p>{/if}
  {#if rawNotice}<p class="pe-notice">{rawNotice}</p>{/if}
  {#each patternErrors as err (err)}
    <p class="pe-error">{err}</p>
  {/each}

  {#if testResult}
    <div class="pe-test-result" class:none={testResult.matches.length === 0}>
      <div class="pe-test-summary">
        {testResult.matches.length} of {testResult.scanned} recent events match
      </div>
      {#each testResult.matches.slice(0, 10) as m (m.seq)}
        <div class="pe-test-row">#{m.seq} · {m.type || '—'} · {m.status || '—'} · {relativeMs(m.ts)}</div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .pattern-editor { display: flex; flex-direction: column; gap: var(--spacing-sm); }

  .pe-header { display: flex; align-items: center; justify-content: space-between; }
  .pe-tools { display: flex; gap: 6px; align-items: center; }
  .pe-mode {
    background: none; border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm); padding: 2px 8px;
    font-size: 10px; font-family: var(--font-mono);
    color: var(--color-text-muted); cursor: pointer;
  }
  .pe-mode.active { border-color: var(--color-accent); color: var(--color-accent); background: rgba(234,179,8,0.08); }
  .pe-test {
    background: none; border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm); padding: 2px 10px;
    font-size: 10px; font-family: var(--font-mono);
    color: var(--color-text-secondary); cursor: pointer;
  }
  .pe-test:hover:not(:disabled) { border-color: var(--color-accent); color: var(--color-accent); }
  .pe-test:disabled { opacity: 0.5; cursor: default; }

  .pe-builder {
    display: flex; flex-direction: column; gap: var(--spacing-sm);
    padding-left: var(--spacing-md); border-left: 2px solid var(--color-border-primary);
  }
  .pe-row { display: flex; flex-direction: column; gap: 4px; }
  .pe-label { font-family: var(--font-mono); font-size: 10px; color: var(--color-text-muted); }
  .pe-types { display: flex; flex-direction: column; gap: 4px; }
  .pe-type-row { display: flex; gap: 6px; align-items: center; }
  .pe-type-row .form-input { flex: 1; }
  .pe-remove {
    background: none; border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm); padding: 2px 6px;
    font-size: 10px; cursor: pointer; color: var(--color-text-muted);
    font-family: var(--font-mono);
  }
  .pe-remove:hover { border-color: var(--color-error); color: var(--color-error); }
  .pe-add {
    align-self: flex-start; background: none; border: none;
    color: var(--color-text-tertiary); font-size: 10px;
    font-family: var(--font-mono); cursor: pointer; padding: 2px 0;
  }
  .pe-add:hover { color: var(--color-accent); }

  .pe-raw {
    background: var(--color-bg-primary); border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm); padding: 8px;
    font-family: var(--font-mono); font-size: 11px;
    color: var(--color-text-primary); resize: vertical; width: 100%;
    line-height: 1.5;
  }
  .pe-raw:focus { outline: none; border-color: var(--color-accent); }

  .pe-hint { font-family: var(--font-mono); font-size: 10px; color: var(--color-text-tertiary); margin: 0; line-height: 1.6; }
  .pe-hint code { color: var(--color-text-secondary); background: var(--color-bg-tertiary); padding: 0 4px; border-radius: 3px; }
  .pe-error { font-family: var(--font-mono); font-size: 10px; color: var(--color-error); margin: 0; }
  .pe-notice { font-family: var(--font-mono); font-size: 10px; color: var(--color-accent); margin: 0; }

  .pe-test-result {
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm);
    padding: 6px 10px;
    display: flex; flex-direction: column; gap: 2px;
  }
  .pe-test-summary { font-family: var(--font-mono); font-size: 10px; color: var(--color-success); }
  .pe-test-result.none .pe-test-summary { color: var(--color-text-tertiary); }
  .pe-test-row { font-family: var(--font-mono); font-size: 10px; color: var(--color-text-secondary); }

  /* shared input styles (scoped duplicates of the panel form idiom) */
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
  .form-select.narrow { width: auto; min-width: 120px; }
  .form-label { font-family: var(--font-mono); font-size: 10px; color: var(--color-text-muted); }

  .seg-ctrl { display: flex; }
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
</style>

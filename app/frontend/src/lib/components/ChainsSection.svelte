<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { getApi } from '../utils/api';
  import { notifications } from '../notifications.svelte';

  interface UsableAgent {
    id: string;
    label: string;
    binary: string;
    invocation: { prompt_mode: string };
  }

  interface ChainStep {
    agent_id: string;
    prompt_template: string;
    cwd: string;
  }

  interface Chain {
    id: string;
    name: string;
    description: string;
    steps: ChainStep[];
    cwd: string;
    created_at: string;
    updated_at: string;
  }

  interface ChainRunEvent {
    run_id: string;
    chain_id: string;
    phase: 'run:start' | 'step:start' | 'step:output' | 'step:done' | 'run:done';
    step_index: number;
    agent_id: string;
    output: string;
    stderr: string;
    exit_code: number;
    error: string;
    status: string;
    at: string;
  }

  let chains = $state<Chain[]>([]);
  let chainable = $state<UsableAgent[]>([]);
  let expanded = $state(false);
  let loading = $state(false);

  // Editor state
  let editing = $state<Chain | null>(null);
  let editorOpen = $state(false);

  // Runner state
  let runnerOpen = $state(false);
  let runningChain = $state<Chain | null>(null);
  let runInput = $state('');
  let runCwd = $state('');
  let runEvents = $state<ChainRunEvent[]>([]);
  let runActive = $state(false);
  let unsubscribe: (() => void) | null = null;

  async function load() {
    loading = true;
    const a = await getApi();
    if (!a) { loading = false; return; }
    try {
      chains = (await a.ListChains()) ?? [];
      const usable = (await a.UsableAgents()) ?? [];
      chainable = usable.filter((u: any) => u.invocation?.prompt_mode);
    } catch (err: any) {
      notifications.error(`Failed to load chains: ${err?.message ?? err}`);
    } finally {
      loading = false;
    }
  }

  onMount(load);
  onDestroy(() => unsubscribe?.());

  function newChain() {
    editing = {
      id: '', name: '', description: '',
      steps: [{ agent_id: chainable[0]?.id ?? '', prompt_template: '{{input}}', cwd: '' }],
      cwd: '', created_at: '', updated_at: '',
    };
    editorOpen = true;
  }

  function editChain(c: Chain) {
    editing = JSON.parse(JSON.stringify(c));
    editorOpen = true;
  }

  function addStep() {
    if (!editing) return;
    editing.steps = [
      ...editing.steps,
      { agent_id: chainable[0]?.id ?? '', prompt_template: '{{prev.output}}', cwd: '' },
    ];
  }

  function removeStep(i: number) {
    if (!editing) return;
    editing.steps = editing.steps.filter((_, idx) => idx !== i);
  }

  function moveStep(i: number, delta: number) {
    if (!editing) return;
    const j = i + delta;
    if (j < 0 || j >= editing.steps.length) return;
    const next = [...editing.steps];
    [next[i], next[j]] = [next[j], next[i]];
    editing.steps = next;
  }

  async function saveChain() {
    if (!editing) return;
    if (!editing.name.trim()) {
      notifications.error('Chain name is required');
      return;
    }
    if (editing.steps.length === 0) {
      notifications.error('Add at least one step');
      return;
    }
    const a = await getApi();
    if (!a) return;
    try {
      await a.SaveChain(editing);
      notifications.success('Chain saved');
      editorOpen = false;
      editing = null;
      await load();
    } catch (err: any) {
      notifications.error(`Save failed: ${err?.message ?? err}`);
    }
  }

  async function deleteChain(c: Chain) {
    if (!confirm(`Delete chain "${c.name}"?`)) return;
    const a = await getApi();
    if (!a) return;
    try {
      await a.DeleteChain(c.id);
      notifications.success('Chain deleted');
      await load();
    } catch (err: any) {
      notifications.error(`Delete failed: ${err?.message ?? err}`);
    }
  }

  function openRunner(c: Chain) {
    runningChain = c;
    runInput = '';
    runCwd = c.cwd ?? '';
    runEvents = [];
    runActive = false;
    runnerOpen = true;
  }

  function subscribeRunEvents(runID: string) {
    unsubscribe?.();
    const rt = (window as any).runtime;
    if (!rt?.EventsOn) return;
    const handler = (ev: ChainRunEvent) => {
      if (ev.run_id !== runID) return;
      runEvents = [...runEvents, ev];
      if (ev.phase === 'run:done') {
        runActive = false;
        if (ev.status === 'success') notifications.success('Chain finished');
        else if (ev.status === 'failed') notifications.error(`Chain failed: ${ev.error}`);
      }
    };
    rt.EventsOn('chain:run:event', handler);
    unsubscribe = () => rt.EventsOff?.('chain:run:event');
  }

  async function runChain() {
    if (!runningChain) return;
    runEvents = [];
    runActive = true;
    const a = await getApi();
    if (!a) { runActive = false; return; }
    try {
      const runID = await a.RunChain(runningChain.id, runInput, runCwd);
      subscribeRunEvents(runID);
    } catch (err: any) {
      runActive = false;
      notifications.error(`Run failed: ${err?.message ?? err}`);
    }
  }

  function agentLabel(id: string): string {
    return chainable.find(a => a.id === id)?.label ?? id;
  }
</script>

<section class="chains-section">
  <button class="section-toggle" onclick={() => { expanded = !expanded; if (expanded && chains.length === 0 && !loading) load(); }}>
    <svg class="chevron" class:expanded xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg>
    <span class="section-title">chains</span>
    <span class="section-count">{chains.length}</span>
  </button>

  {#if expanded}
    <div class="section-body">
      <p class="hint">
        Pipe one agent's output into the next. Only enabled, detected agents with a wired invocation are pickable as steps.
      </p>

      {#if chainable.length === 0}
        <p class="warn">No usable agents — enable at least one detected agent above to start building chains.</p>
      {/if}

      <div class="chain-actions">
        <button class="btn-primary btn-sm" onclick={newChain} disabled={chainable.length === 0}>
          new chain
        </button>
      </div>

      {#if loading}
        <p class="hint">loading...</p>
      {:else if chains.length === 0}
        <p class="hint">no chains yet</p>
      {:else}
        <div class="chain-list">
          {#each chains as c (c.id)}
            <div class="chain-card">
              <div class="chain-head">
                <div class="chain-identity">
                  <span class="chain-name">{c.name}</span>
                  {#if c.description}
                    <span class="chain-desc">{c.description}</span>
                  {/if}
                </div>
                <div class="chain-tools">
                  <button class="btn-icon" onclick={() => openRunner(c)} title="Run chain" aria-label="Run chain">
                    <svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="6 3 20 12 6 21 6 3"/></svg>
                  </button>
                  <button class="btn-icon" onclick={() => editChain(c)} title="Edit chain" aria-label="Edit chain">
                    <svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="m18.5 2.5 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                  </button>
                  <button class="btn-icon danger" onclick={() => deleteChain(c)} title="Delete chain" aria-label="Delete chain">
                    <svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6 17.5 20a2 2 0 0 1-2 1.9H8.5a2 2 0 0 1-2-1.9L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg>
                  </button>
                </div>
              </div>
              <div class="chain-steps">
                {#each c.steps as s, i}
                  <span class="step-pill">
                    <span class="step-idx">{i + 1}</span>
                    <span class="step-agent">{agentLabel(s.agent_id)}</span>
                  </span>
                  {#if i < c.steps.length - 1}
                    <svg xmlns="http://www.w3.org/2000/svg" class="step-arrow" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="5" x2="19" y1="12" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>
                  {/if}
                {/each}
              </div>
            </div>
          {/each}
        </div>
      {/if}
    </div>
  {/if}
</section>

<!-- Editor modal -->
{#if editorOpen && editing}
  <div class="modal-backdrop" onclick={() => editorOpen = false} role="presentation">
    <div class="modal" onclick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
      <header class="modal-head">
        <h2>{editing.id ? 'edit chain' : 'new chain'}</h2>
        <button class="btn-icon" onclick={() => editorOpen = false} aria-label="Close">×</button>
      </header>
      <div class="modal-body">
        <label class="field">
          <span class="field-label">name</span>
          <input type="text" bind:value={editing.name} placeholder="plan-then-implement" />
        </label>
        <label class="field">
          <span class="field-label">description</span>
          <input type="text" bind:value={editing.description} placeholder="optional" />
        </label>
        <label class="field">
          <span class="field-label">working dir</span>
          <input type="text" bind:value={editing.cwd} placeholder="optional, applies to all steps unless overridden" />
        </label>

        <div class="steps-block">
          <div class="steps-head">
            <span class="field-label">steps</span>
            <button class="btn-sm btn-secondary" onclick={addStep}>+ add step</button>
          </div>
          {#each editing.steps as step, i}
            <div class="step-editor">
              <div class="step-editor-head">
                <span class="step-idx">{i + 1}</span>
                <select bind:value={step.agent_id}>
                  {#each chainable as ag (ag.id)}
                    <option value={ag.id}>{ag.label}</option>
                  {/each}
                </select>
                <button class="btn-icon" onclick={() => moveStep(i, -1)} disabled={i === 0} aria-label="Move up" title="Move up">↑</button>
                <button class="btn-icon" onclick={() => moveStep(i, 1)} disabled={i === editing.steps.length - 1} aria-label="Move down" title="Move down">↓</button>
                <button class="btn-icon danger" onclick={() => removeStep(i)} disabled={editing.steps.length === 1} aria-label="Remove step" title="Remove">×</button>
              </div>
              <textarea
                bind:value={step.prompt_template}
                rows="3"
                placeholder="Prompt. Use {'{{input}}'} for initial input or {'{{prev.output}}'} for previous step output."
              ></textarea>
              <input class="step-cwd" type="text" bind:value={step.cwd} placeholder="step-specific cwd (optional)" />
            </div>
          {/each}
        </div>
      </div>
      <footer class="modal-foot">
        <button class="btn-secondary btn-sm" onclick={() => editorOpen = false}>cancel</button>
        <button class="btn-primary btn-sm" onclick={saveChain}>save</button>
      </footer>
    </div>
  </div>
{/if}

<!-- Run drawer -->
{#if runnerOpen && runningChain}
  <div class="modal-backdrop" onclick={() => { if (!runActive) runnerOpen = false; }} role="presentation">
    <div class="modal modal-wide" onclick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
      <header class="modal-head">
        <h2>run · {runningChain.name}</h2>
        <button class="btn-icon" onclick={() => { if (!runActive) runnerOpen = false; }} aria-label="Close" disabled={runActive}>×</button>
      </header>
      <div class="modal-body">
        <label class="field">
          <span class="field-label">initial input</span>
          <textarea bind:value={runInput} rows="3" placeholder="{'{{input}}'} substitution for the first step"></textarea>
        </label>
        <label class="field">
          <span class="field-label">working dir</span>
          <input type="text" bind:value={runCwd} placeholder="defaults to chain cwd" />
        </label>

        <div class="run-actions">
          <button class="btn-primary btn-sm" onclick={runChain} disabled={runActive}>
            {runActive ? 'running...' : 'start'}
          </button>
        </div>

        {#if runEvents.length > 0}
          <div class="run-log">
            {#each runEvents as ev (ev.at + ev.phase + ev.step_index)}
              {#if ev.phase === 'run:start'}
                <div class="log-line meta">▶ run started</div>
              {:else if ev.phase === 'step:start'}
                <div class="log-line meta">▶ step {ev.step_index + 1} · {agentLabel(ev.agent_id)}</div>
              {:else if ev.phase === 'step:done'}
                <div class="log-line" class:err={ev.status === 'failed'}>
                  <div class="log-head">
                    <span>step {ev.step_index + 1} · {agentLabel(ev.agent_id)}</span>
                    <span class="log-status" class:err={ev.status === 'failed'}>
                      {ev.status} {ev.exit_code !== 0 ? `(exit ${ev.exit_code})` : ''}
                    </span>
                  </div>
                  {#if ev.output}
                    <pre class="log-output">{ev.output}</pre>
                  {/if}
                  {#if ev.stderr}
                    <pre class="log-stderr">{ev.stderr}</pre>
                  {/if}
                  {#if ev.error}
                    <pre class="log-stderr">{ev.error}</pre>
                  {/if}
                </div>
              {:else if ev.phase === 'run:done'}
                <div class="log-line meta" class:err={ev.status === 'failed'}>
                  ● run {ev.status}{ev.error ? `: ${ev.error}` : ''}
                </div>
              {/if}
            {/each}
          </div>
        {/if}
      </div>
    </div>
  </div>
{/if}

<style>
  .chains-section {
    margin-top: 20px;
    border-top: 1px solid var(--color-border-primary);
    padding-top: 14px;
  }

  .section-toggle {
    display: flex; align-items: center; gap: 8px;
    background: none; border: none; padding: 4px 0;
    color: var(--color-text-secondary); cursor: pointer; font-family: inherit;
  }
  .section-toggle:hover { color: var(--color-text-primary); }
  .chevron { color: var(--color-text-tertiary); transition: transform 0.15s; }
  .chevron.expanded { transform: rotate(90deg); }
  .section-title {
    font-size: 11px; font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.06em;
  }
  .section-count {
    font-size: 11px; color: var(--color-text-tertiary);
    background: rgba(255, 255, 255, 0.05); padding: 0 6px;
    border-radius: var(--radius-sm);
  }

  .section-body { padding-top: 10px; }
  .hint { font-size: 12px; color: var(--color-text-tertiary); margin: 6px 0; }
  .warn { font-size: 12px; color: var(--color-warning, #d97706); margin: 6px 0; }

  .chain-actions { display: flex; justify-content: flex-end; margin-bottom: 10px; }

  .chain-list { display: flex; flex-direction: column; gap: 8px; }
  .chain-card {
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-lg); padding: 10px 14px;
  }
  .chain-head { display: flex; align-items: center; justify-content: space-between; }
  .chain-identity { display: flex; flex-direction: column; gap: 2px; }
  .chain-name { font-size: 13px; font-weight: 500; color: var(--color-text-primary); }
  .chain-desc { font-size: 11px; color: var(--color-text-tertiary); }
  .chain-tools { display: flex; gap: 4px; }

  .chain-steps {
    display: flex; flex-wrap: wrap; align-items: center; gap: 6px;
    margin-top: 8px;
  }
  .step-pill {
    display: inline-flex; align-items: center; gap: 6px;
    background: rgba(255, 255, 255, 0.04);
    border: 1px solid var(--color-border-secondary);
    border-radius: 9999px; padding: 2px 8px; font-size: 11px;
  }
  .step-idx {
    background: var(--color-bg-tertiary); color: var(--color-text-secondary);
    width: 16px; height: 16px; border-radius: 50%;
    display: inline-flex; align-items: center; justify-content: center;
    font-size: 10px; font-weight: 600;
  }
  .step-agent { color: var(--color-text-primary); }
  .step-arrow { color: var(--color-text-tertiary); }

  .btn-icon {
    background: none; border: none;
    color: var(--color-text-tertiary); cursor: pointer;
    padding: 3px; border-radius: var(--radius-sm); transition: all 0.15s;
  }
  .btn-icon:hover { color: var(--color-text-primary); background: rgba(255, 255, 255, 0.05); }
  .btn-icon.danger:hover { color: var(--color-error); }
  .btn-icon:disabled { opacity: 0.3; cursor: not-allowed; }

  /* Modal */
  .modal-backdrop {
    position: fixed; inset: 0; background: rgba(0, 0, 0, 0.5);
    display: flex; align-items: center; justify-content: center;
    z-index: 100;
  }
  .modal {
    background: var(--color-bg-primary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-xl);
    width: 540px; max-width: 90vw; max-height: 85vh;
    display: flex; flex-direction: column;
  }
  .modal-wide { width: 720px; }
  .modal-head {
    display: flex; align-items: center; justify-content: space-between;
    padding: 14px 18px; border-bottom: 1px solid var(--color-border-primary);
  }
  .modal-head h2 {
    margin: 0; font-size: 14px; font-weight: 500;
    color: var(--color-text-primary); text-transform: lowercase;
  }
  .modal-body { padding: 14px 18px; overflow-y: auto; flex: 1; }
  .modal-foot {
    display: flex; justify-content: flex-end; gap: 8px;
    padding: 12px 18px; border-top: 1px solid var(--color-border-primary);
  }

  .field { display: flex; flex-direction: column; gap: 4px; margin-bottom: 12px; }
  .field-label {
    font-size: 10px; font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.06em;
    color: var(--color-text-tertiary);
  }
  .field input, .field textarea {
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-secondary);
    border-radius: var(--radius-md);
    color: var(--color-text-primary);
    padding: 6px 10px; font-size: 13px; font-family: inherit;
  }
  .field textarea { font-family: var(--font-mono); font-size: 12px; }

  .steps-block { margin-top: 10px; }
  .steps-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }

  .step-editor {
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-secondary);
    border-radius: var(--radius-md);
    padding: 8px 10px; margin-bottom: 8px;
  }
  .step-editor-head {
    display: flex; align-items: center; gap: 6px; margin-bottom: 6px;
  }
  .step-editor-head select {
    flex: 1;
    background: var(--color-bg-tertiary);
    border: 1px solid var(--color-border-secondary);
    border-radius: var(--radius-sm);
    color: var(--color-text-primary);
    padding: 3px 6px; font-size: 12px; font-family: inherit;
  }
  .step-editor textarea {
    width: 100%; box-sizing: border-box;
    background: var(--color-bg-tertiary);
    border: 1px solid var(--color-border-secondary);
    border-radius: var(--radius-sm);
    color: var(--color-text-primary);
    padding: 6px 8px; font-size: 12px; font-family: var(--font-mono);
    resize: vertical;
  }
  .step-cwd {
    width: 100%; box-sizing: border-box; margin-top: 6px;
    background: var(--color-bg-tertiary);
    border: 1px solid var(--color-border-secondary);
    border-radius: var(--radius-sm);
    color: var(--color-text-primary);
    padding: 4px 8px; font-size: 11px; font-family: var(--font-mono);
  }

  /* Runner */
  .run-actions { display: flex; justify-content: flex-end; margin: 8px 0 12px; }
  .run-log {
    background: var(--color-bg-tertiary);
    border: 1px solid var(--color-border-secondary);
    border-radius: var(--radius-md);
    padding: 8px 10px; max-height: 380px; overflow-y: auto;
    font-family: var(--font-mono); font-size: 11px;
  }
  .log-line { padding: 4px 0; }
  .log-line.meta { color: var(--color-text-tertiary); }
  .log-line.err { color: var(--color-error); }
  .log-head {
    display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 4px;
  }
  .log-status { font-size: 10px; color: var(--color-success); }
  .log-status.err { color: var(--color-error); }
  .log-output, .log-stderr {
    background: rgba(0, 0, 0, 0.2);
    border-radius: var(--radius-sm);
    padding: 6px 8px; margin: 4px 0;
    white-space: pre-wrap; word-break: break-word;
    max-height: 220px; overflow-y: auto;
  }
  .log-stderr { color: var(--color-warning, #d97706); }
</style>

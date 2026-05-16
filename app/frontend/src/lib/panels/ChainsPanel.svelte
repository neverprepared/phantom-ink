<script lang="ts">
  import { onMount, onDestroy, tick } from 'svelte';
  import { getApi } from '../utils/api';
  import { notifications } from '../notifications.svelte';
  import { panelFocus } from '../stores.svelte';

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

  interface ChainFollowup {
    chain_id: string;
    input_from: 'stdout' | 'literal' | '';
    input_literal: string;
    cwd: string;
  }

  interface Chain {
    id: string;
    name: string;
    description: string;
    steps: ChainStep[];
    cwd: string;
    on_success: ChainFollowup[];
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
  let loading = $state(true);

  // Recent task outcomes per chain — shown as colored dots on each card so
  // users can see "is this chain healthy?" at a glance without opening Tasks.
  let recentByChain = $state<Map<string, { status: string }[]>>(new Map());

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
      const [c, usable, tasks] = await Promise.all([
        a.ListChains(),
        a.UsableAgents(),
        a.ListTasks('', 200),
      ]);
      chains = (c ?? []) as Chain[];
      chainable = ((usable ?? []) as any[]).filter((u: any) => u.invocation?.prompt_mode);

      // Bucket last 5 tasks per chain — ListTasks returns newest first, so
      // taking the first 5 per chain gives the freshest outcomes.
      const next = new Map<string, { status: string }[]>();
      for (const t of (tasks ?? []) as any[]) {
        if (!t.chain_id) continue;
        const bucket = next.get(t.chain_id) ?? [];
        if (bucket.length < 5) {
          bucket.push({ status: t.status });
          next.set(t.chain_id, bucket);
        }
      }
      recentByChain = next;
    } catch (err: any) {
      notifications.error(`Failed to load chains: ${err?.message ?? err}`);
    } finally {
      loading = false;
    }
  }

  let highlightedChainID = $state<string>('');

  onMount(async () => {
    await load();
    // If we got here via panelFocus.focusChain(), highlight that chain card.
    const id = panelFocus.consumeChainFocus();
    if (id) {
      highlightedChainID = id;
      await tick();
      document.getElementById(`chain-card-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      // Fade highlight after a moment so it doesn't linger.
      setTimeout(() => { if (highlightedChainID === id) highlightedChainID = ''; }, 2500);
    }
  });
  onDestroy(() => unsubscribe?.());

  // ---------- Schedules ----------
  interface Schedule {
    id: string;
    chain_id: string;
    cron_expr: string;
    input: string;
    cwd: string;
    enabled: boolean;
    created_at: string;
    updated_at: string;
    last_fired_at: string;
    next_fire_at: string;
  }

  let openSchedulesFor = $state<string | null>(null);
  let schedulesByChain = $state<Map<string, Schedule[]>>(new Map());
  let newCron = $state('*/15 * * * *');
  let newSchedInput = $state('');

  async function toggleSchedules(chainID: string) {
    if (openSchedulesFor === chainID) {
      openSchedulesFor = null;
      return;
    }
    openSchedulesFor = chainID;
    const a = await getApi();
    if (!a) return;
    try {
      const list = (await a.ListSchedules(chainID)) ?? [];
      const next = new Map(schedulesByChain);
      next.set(chainID, list);
      schedulesByChain = next;
    } catch (err: any) {
      notifications.error(`Load schedules failed: ${err?.message ?? err}`);
    }
  }

  async function addSchedule(chainID: string) {
    if (!newCron.trim()) return;
    const a = await getApi();
    if (!a) return;
    try {
      await a.SaveSchedule({
        id: '', chain_id: chainID, cron_expr: newCron.trim(),
        input: newSchedInput, cwd: '', enabled: true,
        created_at: '', updated_at: '', last_fired_at: '', next_fire_at: '',
      });
      notifications.success('Schedule added');
      newCron = '*/15 * * * *';
      newSchedInput = '';
      const list = (await a.ListSchedules(chainID)) ?? [];
      const next = new Map(schedulesByChain);
      next.set(chainID, list);
      schedulesByChain = next;
    } catch (err: any) {
      notifications.error(`Save failed: ${err?.message ?? err}`);
    }
  }

  async function toggleScheduleEnabled(chainID: string, s: Schedule) {
    const a = await getApi();
    if (!a) return;
    try {
      await a.SaveSchedule({ ...s, enabled: !s.enabled });
      const list = (await a.ListSchedules(chainID)) ?? [];
      const next = new Map(schedulesByChain);
      next.set(chainID, list);
      schedulesByChain = next;
    } catch (err: any) {
      notifications.error(`Toggle failed: ${err?.message ?? err}`);
    }
  }

  async function removeSchedule(chainID: string, s: Schedule) {
    if (!confirm(`Delete schedule "${s.cron_expr}"?`)) return;
    const a = await getApi();
    if (!a) return;
    try {
      await a.DeleteSchedule(s.id);
      const list = (await a.ListSchedules(chainID)) ?? [];
      const next = new Map(schedulesByChain);
      next.set(chainID, list);
      schedulesByChain = next;
    } catch (err: any) {
      notifications.error(`Delete failed: ${err?.message ?? err}`);
    }
  }

  function newChain() {
    editing = {
      id: '', name: '', description: '',
      steps: [{ agent_id: chainable[0]?.id ?? '', prompt_template: '{{input}}', cwd: '' }],
      cwd: '', on_success: [],
      created_at: '', updated_at: '',
    };
    editorOpen = true;
  }

  function addFollowup() {
    if (!editing) return;
    const firstOther = chains.find(c => c.id !== editing!.id);
    editing.on_success = [
      ...(editing.on_success ?? []),
      { chain_id: firstOther?.id ?? '', input_from: 'stdout', input_literal: '', cwd: '' },
    ];
  }

  function removeFollowup(i: number) {
    if (!editing) return;
    editing.on_success = (editing.on_success ?? []).filter((_, idx) => idx !== i);
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

  async function enqueueTask(c: Chain) {
    const input = prompt(`Initial input for "${c.name}" task:`, '');
    if (input === null) return;
    const a = await getApi();
    if (!a) return;
    try {
      const id = await a.EnqueueTask({
        chain_id: c.id,
        input,
        cwd: c.cwd ?? '',
        priority: 0,
        max_attempts: 1,
        trigger: 'manual',
      });
      notifications.success(`Queued ${id} — see Tasks panel`);
    } catch (err: any) {
      notifications.error(`Queue failed: ${err?.message ?? err}`);
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

<div class="panel">
  <header class="panel-header">
    <h1><span class="panel-accent">chains</span></h1>
    <button class="btn-refresh" onclick={load} title="Refresh" aria-label="Refresh">
      <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/></svg>
    </button>
  </header>

  <section class="chains-section">
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
            {@const recent = recentByChain.get(c.id) ?? []}
            <div class="chain-card" id="chain-card-{c.id}" class:highlight={highlightedChainID === c.id}>
              <div class="chain-head">
                <div class="chain-identity">
                  <span class="chain-name">{c.name}</span>
                  {#if c.description}
                    <span class="chain-desc">{c.description}</span>
                  {/if}
                  {#if recent.length > 0}
                    <div class="recent-runs" title="last {recent.length} runs (newest left)">
                      {#each recent as r, i (i)}
                        <span class="run-dot status-{r.status}"></span>
                      {/each}
                    </div>
                  {/if}
                </div>
                <div class="chain-tools">
                  <button class="btn-icon" onclick={() => openRunner(c)} title="Run chain (foreground)" aria-label="Run chain">
                    <svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="6 3 20 12 6 21 6 3"/></svg>
                  </button>
                  <button class="btn-icon" onclick={() => enqueueTask(c)} title="Queue as task" aria-label="Queue as task">
                    <svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="4" rx="1"/><rect x="3" y="10" width="18" height="4" rx="1"/><rect x="3" y="16" width="18" height="4" rx="1"/></svg>
                  </button>
                  <button class="btn-icon" onclick={() => toggleSchedules(c.id)} title="Schedules" aria-label="Schedules">
                    <svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
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

              {#if c.on_success && c.on_success.length > 0}
                <div class="chain-followups">
                  <span class="followup-arrow">↳ on success:</span>
                  {#each c.on_success as fu (fu.chain_id + fu.input_from)}
                    <span class="step-pill">
                      <span class="step-agent">{chains.find(x => x.id === fu.chain_id)?.name ?? fu.chain_id}</span>
                      <span class="followup-source">{fu.input_from || 'stdout'}</span>
                    </span>
                  {/each}
                </div>
              {/if}

              {#if openSchedulesFor === c.id}
                {@const schedules = schedulesByChain.get(c.id) ?? []}
                <div class="schedules-block">
                  <div class="schedules-head">
                    <span class="field-label">schedules</span>
                    <span class="hint-inline">cron expressions are 5-field (min hr dom mon dow) or @hourly/@daily/@weekly</span>
                  </div>
                  {#each schedules as s (s.id)}
                    <div class="schedule-row">
                      <button class="sched-toggle" onclick={() => toggleScheduleEnabled(c.id, s)} title={s.enabled ? 'Disable' : 'Enable'}>
                        <span class="status-dot" class:on={s.enabled}></span>
                      </button>
                      <code class="sched-expr">{s.cron_expr}</code>
                      {#if s.input}
                        <span class="sched-input" title={s.input}>{s.input.slice(0, 40)}{s.input.length > 40 ? '…' : ''}</span>
                      {/if}
                      <span class="sched-meta">
                        {#if s.next_fire_at}
                          next: {new Date(s.next_fire_at).toLocaleString()}
                        {:else if !s.enabled}
                          disabled
                        {:else}
                          —
                        {/if}
                        {#if s.last_fired_at}
                          · last: {new Date(s.last_fired_at).toLocaleString()}
                        {/if}
                      </span>
                      <button class="btn-icon danger" onclick={() => removeSchedule(c.id, s)} title="Delete schedule" aria-label="Delete schedule">×</button>
                    </div>
                  {/each}
                  <div class="schedule-add">
                    <input class="sched-input-field" type="text" bind:value={newCron} placeholder="*/15 * * * *" />
                    <input class="sched-input-field" type="text" bind:value={newSchedInput} placeholder="optional input" />
                    <button class="btn-sm btn-primary" onclick={() => addSchedule(c.id)}>add</button>
                  </div>
                </div>
              {/if}
            </div>
          {/each}
        </div>
      {/if}
    </div>
  </section>
</div>

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

        <div class="steps-block">
          <div class="steps-head">
            <span class="field-label">on success — follow-up tasks</span>
            <button class="btn-sm btn-secondary" onclick={addFollowup} disabled={chains.filter(c => c.id !== editing.id).length === 0}>+ add follow-up</button>
          </div>
          {#if !editing.on_success || editing.on_success.length === 0}
            <p class="hint">No follow-ups. When this chain succeeds, downstream chains can be queued here.</p>
          {/if}
          {#each editing.on_success ?? [] as fu, i}
            <div class="step-editor">
              <div class="step-editor-head">
                <span class="step-idx">↳</span>
                <select bind:value={fu.chain_id}>
                  {#each chains.filter(c => c.id !== editing.id) as c (c.id)}
                    <option value={c.id}>{c.name}</option>
                  {/each}
                </select>
                <select bind:value={fu.input_from}>
                  <option value="stdout">from stdout</option>
                  <option value="literal">literal input</option>
                </select>
                <button class="btn-icon danger" onclick={() => removeFollowup(i)} aria-label="Remove follow-up" title="Remove">×</button>
              </div>
              {#if fu.input_from === 'literal'}
                <textarea
                  bind:value={fu.input_literal}
                  rows="2"
                  placeholder="literal input passed to the follow-up chain"
                ></textarea>
              {/if}
              <input class="step-cwd" type="text" bind:value={fu.cwd} placeholder="follow-up cwd (optional)" />
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
  .panel { padding: var(--panel-padding); }
  .chains-section { /* legacy class kept so nested rules below match */ }
  .section-body { padding-top: 0; }

  /* Recent-runs dots — quick health glance on each chain card */
  .recent-runs {
    display: inline-flex; gap: 3px; margin-top: 4px;
  }
  .run-dot {
    width: 6px; height: 6px; border-radius: 50%;
    background: var(--color-text-tertiary);
  }
  .run-dot.status-succeeded { background: var(--color-success); }
  .run-dot.status-failed { background: var(--color-error); }
  .run-dot.status-cancelled { background: var(--color-warning, #d97706); }
  .run-dot.status-running {
    background: var(--color-info);
    animation: pulse 1.2s ease-in-out infinite;
  }
  .run-dot.status-pending { background: var(--color-text-tertiary); opacity: 0.5; }
  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
  }

  /* Highlight a card when navigated to from another panel */
  .chain-card.highlight {
    box-shadow: 0 0 0 2px var(--color-accent);
    transition: box-shadow 0.4s ease-out;
  }
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

  /* Follow-ups row under the steps row */
  .chain-followups {
    display: flex; flex-wrap: wrap; align-items: center; gap: 6px;
    margin-top: 6px; font-size: 11px;
  }
  .followup-arrow { color: var(--color-text-tertiary); font-size: 10px; }
  .followup-source {
    color: var(--color-text-tertiary); font-size: 9px;
    text-transform: uppercase; letter-spacing: 0.04em;
  }

  /* Schedules block (inline under chain card) */
  .schedules-block {
    margin-top: 10px; padding-top: 8px;
    border-top: 1px solid var(--color-border-primary);
    display: flex; flex-direction: column; gap: 4px;
  }
  .schedules-head { display: flex; align-items: baseline; gap: 8px; }
  .hint-inline { font-size: 10px; color: var(--color-text-tertiary); }
  .schedule-row {
    display: flex; align-items: center; gap: 8px;
    padding: 3px 4px; border-radius: var(--radius-sm);
    font-size: 11px;
  }
  .schedule-row:hover { background: rgba(255, 255, 255, 0.02); }
  .sched-toggle {
    background: none; border: none; padding: 2px; cursor: pointer;
  }
  .sched-toggle .status-dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--color-text-tertiary);
  }
  .sched-toggle .status-dot.on {
    background: var(--color-success);
    box-shadow: 0 0 4px rgba(16, 185, 129, 0.4);
  }
  .sched-expr {
    font-family: var(--font-mono); font-size: 11px;
    color: var(--color-text-primary);
    background: rgba(255, 255, 255, 0.04);
    padding: 1px 6px; border-radius: var(--radius-sm);
  }
  .sched-input {
    flex: 1; min-width: 0;
    font-size: 10px; color: var(--color-text-secondary);
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .sched-meta {
    font-size: 10px; color: var(--color-text-tertiary);
    margin-left: auto;
  }
  .schedule-add {
    display: flex; gap: 6px; margin-top: 6px;
  }
  .sched-input-field {
    background: var(--color-bg-tertiary);
    border: 1px solid var(--color-border-secondary);
    border-radius: var(--radius-sm);
    color: var(--color-text-primary);
    padding: 3px 8px; font-size: 11px; font-family: var(--font-mono);
  }
  .sched-input-field:first-of-type { flex: 0 0 130px; }
  .sched-input-field:last-of-type { flex: 1; }

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

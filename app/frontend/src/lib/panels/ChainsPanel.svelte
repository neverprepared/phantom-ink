<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { getApi } from '../utils/api';
  import { notifications } from '../notifications.svelte';
  import { panelFocus, profileState, refreshTick } from '../stores.svelte';
  import Modal from '../components/Modal.svelte';
  import Spinner from '../components/Spinner.svelte';

  interface UsableAgent {
    id: string;
    label: string;
    binary: string;
    invocation: { prompt_mode: string };
  }

  interface ChainStep {
    type?: 'agent' | 'playbook';
    agent_id: string;
    playbook_id?: string;
    prompt_template: string;
    cwd: string;
    executor?: string;
  }

  interface PlaybookItem {
    id: string;
    name: string;
    workspace_profile: string;
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
    files: string[];
    created_at: string;
    updated_at: string;
    workspace_profile?: string;
  }

  interface StepOutput {
    output: string;
    stderr: string;
    status: string;
    error: string;
  }

  type NodeType = 'trigger' | 'agent' | 'tool' | 'playbook';
  interface CanvasNode {
    id: string;
    type: NodeType;
    x: number;
    y: number;
    title: string;
    sub: string;
    ref?: string;
    state: 'idle' | 'run' | 'done';
  }
  interface CanvasEdge { from: string; to: string; label: string; }

  const NODE_META: Record<NodeType, { color: string; label: string }> = {
    trigger:  { color: 'var(--accent)', label: 'trigger' },
    agent:    { color: 'var(--run)',    label: 'agent' },
    tool:     { color: 'var(--sched)',  label: 'tool' },
    playbook: { color: 'var(--task)',   label: 'playbook' },
  };
  const NW = 200;
  const NH = 72;

  function edgePath(a: CanvasNode, b: CanvasNode) {
    const x1 = a.x + NW, y1 = a.y + NH / 2, x2 = b.x, y2 = b.y + NH / 2;
    const dx = Math.max(40, Math.abs(x2 - x1) * 0.5);
    return { d: `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}` };
  }

  // ----- shared state -----
  let chains = $state<Chain[]>([]);
  let chainable = $state<UsableAgent[]>([]);
  let loading = $state(true);
  let recentByChain = $state<Map<string, { status: string }[]>>(new Map());
  let activeId = $state<string | null>(null);
  let pendingDelete = $state<Chain | null>(null);
  let query = $state('');
  let editName = $state('');
  let availablePlaybooks = $state<PlaybookItem[]>([]);
  let playbookPickerOpen = $state(false);

  // editor state
  let chainInput = $state('');
  let running = $state(false);
  let runPhase = $state<'idle' | 'running' | 'done'>('idle');
  let runFinalStatus = $state('');
  let stepOutputs = $state<Map<number, StepOutput>>(new Map());
  let stepRunning = $state<Set<number>>(new Set());
  let expandedOutputs = $state<Set<number>>(new Set());

  const filteredChains = $derived(
    (() => {
      const q = query.trim().toLowerCase();
      if (!q) return chains;
      return chains.filter((c) =>
        (c.name + ' ' + (c.description ?? '') + ' ' + c.steps.map((s) => s.agent_id).join(' '))
          .toLowerCase().includes(q),
      );
    })(),
  );

  const activeChain = $derived(activeId ? chains.find((c) => c.id === activeId) ?? null : null);
  const scopeLabel = $derived(profileState.active?.name ?? 'all');

  function agentLabel(id: string): string {
    return chainable.find((a) => a.id === id)?.label ?? id;
  }

  // Reset run state when switching chains
  $effect(() => {
    activeId;
    runPhase = 'idle';
    stepOutputs = new Map();
    stepRunning = new Set();
    expandedOutputs = new Set();
    running = false;
    runFinalStatus = '';
  });

  $effect(() => {
    if (activeChain) editName = activeChain.name;
  });

  async function load(silent = false) {
    if (!silent) loading = true;
    const a = await getApi();
    if (!a) { loading = false; return; }
    try {
      const [c, usable, tasks, pbs] = await Promise.all([
        a.ListChains(),
        a.UsableAgents(),
        a.ListTasks('', profileState.active?.name ?? '', 200),
        a.ListPlaybooks(profileState.active?.name ?? '').catch(() => []),
      ]);
      chains = (c ?? []) as Chain[];
      chainable = ((usable ?? []) as any[]).filter((u: any) => u.invocation?.prompt_mode);
      const next = new Map<string, { status: string }[]>();
      for (const t of (tasks ?? []) as any[]) {
        if (!t.chain_id) continue;
        const bucket = next.get(t.chain_id) ?? [];
        if (bucket.length < 5) { bucket.push({ status: t.status }); next.set(t.chain_id, bucket); }
      }
      recentByChain = next;
      availablePlaybooks = ((pbs ?? []) as any[]).map((p: any) => ({ id: p.id, name: p.name, workspace_profile: p.workspace_profile ?? '' }));
    } catch (err: any) {
      notifications.error(`Failed to load chains: ${err?.message ?? err}`);
    } finally {
      loading = false;
    }
  }

  let unsubChainRun: (() => void) | null = null;

  function subscribeChainEvents() {
    if (typeof window === 'undefined' || !(window as any).runtime?.EventsOn) return;
    unsubChainRun = (window as any).runtime.EventsOn('chain:run:event', (ev: any) => {
      if (!activeChain || ev.chain_id !== activeChain.id) return;
      if (ev.phase === 'run:start') {
        running = true;
        runPhase = 'running';
        stepOutputs = new Map();
        stepRunning = new Set();
        expandedOutputs = new Set();
      } else if (ev.phase === 'step:start') {
        stepRunning = new Set([...stepRunning, ev.step_index]);
        expandedOutputs = new Set([...expandedOutputs, ev.step_index]);
      } else if (ev.phase === 'step:done') {
        const next = new Set(stepRunning);
        next.delete(ev.step_index);
        stepRunning = next;
        stepOutputs = new Map([...stepOutputs, [ev.step_index, {
          output: ev.output ?? '',
          stderr: ev.stderr ?? '',
          status: ev.status ?? '',
          error: ev.error ?? '',
        }]]);
      } else if (ev.phase === 'run:done') {
        running = false;
        runPhase = 'done';
        runFinalStatus = ev.status ?? '';
        stepRunning = new Set();
      }
    });
  }

  onMount(async () => {
    await load();
    const id = panelFocus.consumeChainFocus();
    if (id) activeId = id;

    const trySubscribe = () => {
      if ((window as any).runtime?.EventsOn) { subscribeChainEvents(); }
      else { setTimeout(trySubscribe, 100); }
    };
    trySubscribe();
  });

  onDestroy(() => {
    if (unsubChainRun) { unsubChainRun(); unsubChainRun = null; }
  });

  $effect(() => {
    refreshTick.count;
    void load(true);
  });

  // ----- gallery thumbnail helpers -----
  function nodesFromChain(c: Chain): { nodes: CanvasNode[]; edges: CanvasEdge[] } {
    const nodes: CanvasNode[] = [];
    nodes.push({ id: `t-${c.id}`, type: 'trigger', x: 0, y: 0, title: 'manual', sub: 'trigger', state: 'idle' });
    c.steps.forEach((s, i) => {
      const nid = `s-${c.id}-${i}`;
      if (s.type === 'playbook') {
        const pb = availablePlaybooks.find((p) => p.id === s.playbook_id);
        nodes.push({ id: nid, type: 'playbook', x: (i + 1) * 240, y: 0, title: pb?.name ?? s.playbook_id ?? 'playbook', sub: '', state: 'idle' });
      } else {
        nodes.push({ id: nid, type: 'agent', x: (i + 1) * 240, y: 0, title: agentLabel(s.agent_id), sub: s.prompt_template?.slice(0, 40) ?? '', state: 'idle' });
      }
    });
    const edges: CanvasEdge[] = [];
    for (let i = 0; i < nodes.length - 1; i++) edges.push({ from: nodes[i].id, to: nodes[i + 1].id, label: '' });
    return { nodes, edges };
  }

  // ----- chain CRUD -----
  async function newChain() {
    const a = await getApi();
    if (!a) return;
    if (chainable.length === 0) { notifications.error('No usable agents — enable one to build a chain'); return; }
    const draft: Chain = {
      id: '', name: 'new-chain', description: '',
      steps: [{ type: 'agent', agent_id: chainable[0].id, prompt_template: '{{input}}', cwd: '' }],
      cwd: '', on_success: [], files: [], created_at: '', updated_at: '',
      workspace_profile: profileState.active?.name ?? '',
    };
    try {
      await a.SaveChain(draft as any);
      notifications.success('Chain created');
      await load();
    } catch (err: any) {
      notifications.error(`Create failed: ${err?.message ?? err}`);
    }
  }

  async function confirmDeleteChain() {
    const c = pendingDelete;
    if (!c) return;
    pendingDelete = null;
    const a = await getApi();
    if (!a) return;
    try {
      await a.DeleteChain(c.id);
      notifications.success(`deleted · ${c.name}`);
      if (activeId === c.id) activeId = null;
      await load();
    } catch (err: any) {
      notifications.error(`Delete failed: ${err?.message ?? err}`);
    }
  }

  async function saveChainName() {
    if (!activeChain || editName.trim() === '' || editName.trim() === activeChain.name) return;
    const a = await getApi();
    if (!a) return;
    try {
      await a.SaveChain({ ...activeChain, name: editName.trim() } as any);
      await load(true);
    } catch (err: any) {
      notifications.error(`Rename failed: ${err?.message ?? err}`);
    }
  }

  // ----- step editing -----
  async function saveStep(i: number, updatedStep: ChainStep) {
    if (!activeChain) return;
    const a = await getApi();
    if (!a) return;
    const steps = activeChain.steps.map((s, idx) => idx === i ? updatedStep : s);
    try {
      await a.SaveChain({ ...activeChain, steps } as any);
      await load(true);
    } catch (err: any) {
      notifications.error(`Save failed: ${err?.message ?? err}`);
    }
  }

  async function addAgentStep() {
    if (!activeChain) return;
    if (chainable.length === 0) { notifications.error('No usable agents available'); return; }
    const a = await getApi();
    if (!a) return;
    const newStep: ChainStep = {
      type: 'agent', agent_id: chainable[0].id,
      prompt_template: activeChain.steps.length === 0 ? '{{input}}' : '{{prev.output}}',
      cwd: '',
    };
    try {
      await a.SaveChain({ ...activeChain, steps: [...activeChain.steps, newStep] } as any);
      await load(true);
    } catch (err: any) {
      notifications.error(`Add step failed: ${err?.message ?? err}`);
    }
  }

  async function addPlaybookStepFromPicker(pb: PlaybookItem) {
    if (!activeChain) return;
    playbookPickerOpen = false;
    const a = await getApi();
    if (!a) return;
    const newStep: ChainStep = { type: 'playbook', agent_id: '', playbook_id: pb.id, prompt_template: '', cwd: '' };
    try {
      await a.SaveChain({ ...activeChain, steps: [...activeChain.steps, newStep] } as any);
      await load(true);
    } catch (err: any) {
      notifications.error(`Add playbook step failed: ${err?.message ?? err}`);
    }
  }

  async function deleteStep(i: number) {
    if (!activeChain) return;
    const a = await getApi();
    if (!a) return;
    const steps = activeChain.steps.filter((_, idx) => idx !== i);
    try {
      await a.SaveChain({ ...activeChain, steps } as any);
      await load(true);
    } catch (err: any) {
      notifications.error(`Remove step failed: ${err?.message ?? err}`);
    }
  }

  async function moveStep(i: number, dir: -1 | 1) {
    if (!activeChain) return;
    const j = i + dir;
    if (j < 0 || j >= activeChain.steps.length) return;
    const a = await getApi();
    if (!a) return;
    const steps = [...activeChain.steps];
    [steps[i], steps[j]] = [steps[j], steps[i]];
    try {
      await a.SaveChain({ ...activeChain, steps } as any);
      await load(true);
    } catch (err: any) {
      notifications.error(`Reorder failed: ${err?.message ?? err}`);
    }
  }

  async function runChainReal() {
    if (!activeChain || running) return;
    const a = await getApi();
    if (!a) return;
    runPhase = 'running';
    running = true;
    stepOutputs = new Map();
    stepRunning = new Set();
    expandedOutputs = new Set();
    try {
      await a.RunChain(activeChain.id, chainInput, activeChain.cwd ?? '');
    } catch (err: any) {
      running = false;
      runPhase = 'done';
      runFinalStatus = 'failed';
      notifications.error(`Run failed: ${err?.message ?? err}`);
    }
  }

  function toggleOutput(i: number) {
    const next = new Set(expandedOutputs);
    if (next.has(i)) next.delete(i); else next.add(i);
    expandedOutputs = next;
  }

  function truncateOutput(s: string, max = 2000): string {
    if (s.length <= max) return s;
    return s.slice(0, max) + `\n… (${s.length - max} more chars)`;
  }
</script>

{#if !activeChain}
  <!-- ===== GALLERY ===== -->
  <div class="pi-main-inner" style="padding: var(--panel-padding);">
    <div class="section-row" style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px;">
      <h1 class="page-title" style="display:flex;align-items:center;gap:10px;">
        chains
        <span class="scope-chip mono">{scopeLabel}</span>
        {#if loading}<Spinner />{/if}
      </h1>
      <div style="display:flex;gap:10px;align-items:center;">
        <div class="filter" style="margin:0;width:240px;">
          <input bind:value={query} placeholder="search chains…" />
        </div>
        <button class="btn primary" onclick={newChain}>+ new chain</button>
      </div>
    </div>
    <p style="color: var(--text-faint); font-size: 13px; margin: -4px 0 22px;">
      Wire agents and playbooks into sequential steps. Open a chain to edit its steps and run it.
    </p>

    {#if loading}
      <p style="color: var(--text-faint);">loading…</p>
    {:else if filteredChains.length === 0}
      <div class="card" style="padding: 48px; text-align: center; color: var(--text-faint);">
        <div style="margin-top: 12px; font-size: 14px; color: var(--text-muted);">
          {query ? `no chains match "${query}"` : `no chains on ${scopeLabel} yet`}
        </div>
      </div>
    {:else}
      <div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 18px;">
        {#each filteredChains as c (c.id)}
          {@const preview = nodesFromChain(c)}
          {@const recent = recentByChain.get(c.id) ?? []}
          <div
            class="card"
            onclick={() => (activeId = c.id)}
            role="button"
            tabindex="0"
            onkeydown={(e) => { if (e.key === 'Enter') activeId = c.id; }}
            style="padding:0;overflow:hidden;text-align:left;cursor:pointer;display:flex;flex-direction:column;border-left:3px solid var(--task);"
          >
            <!-- thumbnail -->
            <div style="background: var(--bg-sunken); background-image: radial-gradient(var(--grid-dot) 1px, transparent 1px); background-size: 14px 14px; height: 132px; position: relative;">
              <svg width="100%" height="132" viewBox="0 0 300 132" preserveAspectRatio="xMidYMid meet" style="display:block;">
                {#each preview.edges as e, i (i)}
                  {@const a = preview.nodes.find((n) => n.id === e.from)}
                  {@const b = preview.nodes.find((n) => n.id === e.to)}
                  {#if a && b}
                    {@const bx = Math.min(...preview.nodes.map((n) => n.x))}
                    {@const by = Math.min(...preview.nodes.map((n) => n.y))}
                    {@const bw = Math.max(...preview.nodes.map((n) => n.x + NW)) - bx}
                    {@const bh = Math.max(...preview.nodes.map((n) => n.y + NH)) - by}
                    {@const s = Math.min((300 - 32) / Math.max(bw, 1), (132 - 32) / Math.max(bh, 1))}
                    {@const ox = (300 - bw * s) / 2 - bx * s}
                    {@const oy = (132 - bh * s) / 2 - by * s}
                    {@const p = edgePath(a, b)}
                    <path d={p.d} fill="none" stroke="var(--border-strong)" stroke-width="1.3" transform="translate({ox},{oy}) scale({s})" style="transform-origin: 0 0;" />
                  {/if}
                {/each}
                {#each preview.nodes as n (n.id)}
                  {@const m = NODE_META[n.type]}
                  {@const bx = Math.min(...preview.nodes.map((nn) => nn.x))}
                  {@const by = Math.min(...preview.nodes.map((nn) => nn.y))}
                  {@const bw = Math.max(...preview.nodes.map((nn) => nn.x + NW)) - bx}
                  {@const bh = Math.max(...preview.nodes.map((nn) => nn.y + NH)) - by}
                  {@const s = Math.min((300 - 32) / Math.max(bw, 1), (132 - 32) / Math.max(bh, 1))}
                  {@const ox = (300 - bw * s) / 2 - bx * s}
                  {@const oy = (132 - bh * s) / 2 - by * s}
                  <rect x={ox + n.x * s} y={oy + n.y * s} width={NW * s} height={NH * s} rx="3" fill="var(--bg-elev)" stroke={m.color} stroke-width="1.4" />
                  <rect x={ox + n.x * s} y={oy + n.y * s} width="3" height={NH * s} fill={m.color} />
                {/each}
              </svg>
            </div>
            <div style="padding: 14px 16px; border-top: 1px solid var(--border);">
              <div style="display:flex;align-items:center;gap:9px;">
                <span style="width:8px;height:8px;border-radius:99px;background: var(--accent);"></span>
                <span style="font-size:15.5px;font-weight:700;flex:1;color: var(--text);">{c.name}</span>
                <span style="color: var(--text-faint);">↗</span>
              </div>
              <div class="mono" style="font-size:11px;color: var(--text-faint);margin-top:7px;display:flex;gap:10px;align-items:center;">
                <span>{c.steps.length} steps</span>
                <span style="margin-left:auto;">{recent.length} runs</span>
                {#if c.workspace_profile && c.workspace_profile !== 'global'}
                  <span class="tag" style="color: var(--accent); border-color: color-mix(in srgb, var(--accent) 35%, var(--border)); background: color-mix(in srgb, var(--accent) 8%, var(--bg));">{c.workspace_profile}</span>
                {:else}
                  <span class="tag">global</span>
                {/if}
              </div>
            </div>
            <div style="display:flex;justify-content:flex-end;padding: 0 12px 10px;">
              <button
                class="btn ghost sm"
                onclick={(e) => { e.stopPropagation(); pendingDelete = c; }}
                title="delete"
                aria-label="delete chain"
              >delete</button>
            </div>
          </div>
        {/each}
      </div>
    {/if}
  </div>

{:else}
  <!-- ===== STEP-LIST EDITOR ===== -->
  <div class="pi-main-inner editor-wrap" style="padding: var(--panel-padding);">

    <!-- toolbar -->
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:20px;flex-wrap:wrap;">
      <button class="btn ghost sm" onclick={() => (activeId = null)}>← chains</button>
      <input
        class="chain-name-input"
        bind:value={editName}
        onblur={() => saveChainName()}
        onkeydown={(e) => { if (e.key === 'Enter') { saveChainName(); (e.target as HTMLInputElement).blur(); } }}
        style="font-size:20px;font-weight:700;color:var(--accent);background:transparent;border:none;border-bottom:1px solid transparent;outline:none;flex:1;min-width:160px;padding:0;font-family:inherit;"
        onfocus={(e) => ((e.target as HTMLInputElement).style.borderBottomColor = 'var(--accent)')}
      />
      <span class="scope-chip mono">{scopeLabel}</span>
      <div style="margin-left:auto;display:flex;align-items:center;gap:8px;">
        {#if runPhase === 'done'}
          <span class="mono" style="font-size:12px;color:{runFinalStatus === 'success' ? 'var(--run)' : 'var(--fail)'};">
            {runFinalStatus === 'success' ? '✓ done' : '✗ failed'}
          </span>
        {/if}
        {#if running}<Spinner />{/if}
        <button class="btn primary" onclick={runChainReal} disabled={running || activeChain.steps.length === 0}>
          {running ? 'running…' : '▶ run'}
        </button>
      </div>
    </div>

    <!-- input field -->
    <div class="card" style="margin-bottom:16px;padding:14px 16px;">
      <div style="display:flex;align-items:baseline;gap:8px;margin-bottom:8px;">
        <span style="font-size:12px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:.06em;">Input</span>
        <code class="mono" style="font-size:11px;color:var(--text-faint);">&#123;&#123;input&#125;&#125;</code>
        <span style="font-size:11px;color:var(--text-faint);margin-left:auto;">passed to the first step and available as &#123;&#123;input&#125;&#125; in any step template</span>
      </div>
      <textarea
        class="step-textarea"
        bind:value={chainInput}
        rows={3}
        placeholder="Enter the chain input value…"
        style="width:100%;resize:vertical;"
      ></textarea>
    </div>

    <!-- steps -->
    {#if activeChain.steps.length === 0}
      <div class="card" style="padding:32px;text-align:center;color:var(--text-faint);margin-bottom:16px;">
        No steps yet — add one below.
      </div>
    {:else}
      <div style="display:flex;flex-direction:column;gap:12px;margin-bottom:12px;">
        {#each activeChain.steps as step, i (i)}
          {@const out = stepOutputs.get(i)}
          {@const isRunning = stepRunning.has(i)}
          {@const expanded = expandedOutputs.has(i)}

          <div class="card step-card" style="border-left: 3px solid {step.type === 'playbook' ? 'var(--task)' : 'var(--run)'};">
            <!-- step header -->
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px;">
              <span class="mono" style="font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:var(--text-faint);padding:2px 7px;border:1px solid var(--border);border-radius:99px;">step {i + 1}</span>
              <span class="mono" style="font-size:10px;color:{step.type === 'playbook' ? 'var(--task)' : 'var(--run)'};">{step.type ?? 'agent'}</span>
              {#if isRunning}
                <Spinner />
              {:else if out}
                <span class="mono" style="font-size:10px;color:{out.status === 'success' ? 'var(--run)' : 'var(--fail)'};">
                  {out.status === 'success' ? '✓' : '✗'} {out.status}
                </span>
              {/if}
              <div style="margin-left:auto;display:flex;gap:4px;">
                <button class="iconbtn" onclick={() => moveStep(i, -1)} disabled={i === 0} title="move up" aria-label="move step up">↑</button>
                <button class="iconbtn" onclick={() => moveStep(i, 1)} disabled={i === activeChain.steps.length - 1} title="move down" aria-label="move step down">↓</button>
                <button class="iconbtn" onclick={() => deleteStep(i)} title="remove step" aria-label="remove step" style="color:var(--fail);">×</button>
              </div>
            </div>

            {#if step.type === 'playbook'}
              <!-- playbook step -->
              <div class="field-row">
                <div class="field-label">Playbook</div>
                {#if availablePlaybooks.length === 0}
                  <span style="font-size:13px;color:var(--text-faint);">no playbooks available for {scopeLabel}</span>
                {:else}
                  <select
                    class="step-select"
                    value={step.playbook_id ?? ''}
                    onchange={(e) => saveStep(i, { ...step, playbook_id: (e.target as HTMLSelectElement).value })}
                  >
                    {#each availablePlaybooks as pb (pb.id)}
                      <option value={pb.id}>{pb.name}</option>
                    {/each}
                  </select>
                {/if}
              </div>
            {:else}
              <!-- agent step -->
              <div class="field-row">
                <div class="field-label">Agent</div>
                {#if chainable.length === 0}
                  <span style="font-size:13px;color:var(--text-faint);">no usable agents detected</span>
                {:else}
                  <select
                    class="step-select"
                    value={step.agent_id}
                    onchange={(e) => saveStep(i, { ...step, agent_id: (e.target as HTMLSelectElement).value })}
                  >
                    {#each chainable as agent (agent.id)}
                      <option value={agent.id}>{agent.label}</option>
                    {/each}
                  </select>
                {/if}
              </div>

              <div class="field-row">
                <div class="field-label">
                  Prompt template
                  <span class="mono field-hint">&#123;&#123;input&#125;&#125; · &#123;&#123;prev.output&#125;&#125; · &#123;&#123;files&#125;&#125;</span>
                </div>
                <textarea
                  class="step-textarea"
                  value={step.prompt_template}
                  rows={4}
                  placeholder="&#123;&#123;input&#125;&#125;"
                  onblur={(e) => saveStep(i, { ...step, prompt_template: (e.target as HTMLTextAreaElement).value })}
                ></textarea>
              </div>

              <div class="field-row">
                <div class="field-label">
                  Working directory
                  <span class="field-hint">optional — overrides chain cwd for this step</span>
                </div>
                <input
                  class="step-input"
                  value={step.cwd}
                  placeholder="(inherit chain cwd)"
                  onblur={(e) => saveStep(i, { ...step, cwd: (e.target as HTMLInputElement).value })}
                />
              </div>
            {/if}

            <!-- step output -->
            {#if out || isRunning}
              <div style="margin-top:12px;border-top:1px solid var(--border-subtle);padding-top:10px;">
                <button
                  class="btn ghost sm"
                  onclick={() => toggleOutput(i)}
                  style="font-size:11px;margin-bottom:{expanded ? '8px' : '0'};"
                >
                  {expanded ? '▾ hide output' : '▸ show output'}
                  {#if out && out.output}
                    <span class="mono" style="color:var(--text-faint);margin-left:6px;">{out.output.split('\n').length} lines</span>
                  {/if}
                </button>
                {#if expanded}
                  {#if isRunning}
                    <div style="color:var(--text-faint);font-size:12px;">running…</div>
                  {:else if out}
                    {#if out.error}
                      <pre class="output-block error-block">{out.error}</pre>
                    {/if}
                    {#if out.output}
                      <pre class="output-block">{truncateOutput(out.output)}</pre>
                    {/if}
                    {#if out.stderr}
                      <pre class="output-block stderr-block">{truncateOutput(out.stderr)}</pre>
                    {/if}
                  {/if}
                {/if}
              </div>
            {/if}
          </div>
        {/each}
      </div>
    {/if}

    <!-- add step -->
    <div style="display:flex;gap:8px;margin-bottom:24px;flex-wrap:wrap;position:relative;">
      <button class="btn sm" onclick={addAgentStep}>+ agent step</button>
      {#if availablePlaybooks.length > 0}
        <button class="btn sm ghost" onclick={() => (playbookPickerOpen = !playbookPickerOpen)}>+ playbook step</button>
        {#if playbookPickerOpen}
          <div class="chain-add" style="position:absolute;top:38px;left:0;width:280px;background:var(--bg-elev);border:1px solid var(--border);border-radius:var(--r-lg);box-shadow:var(--shadow-lg);padding:8px;z-index:20;">
            <div style="display:flex;align-items:center;padding:4px 8px 8px;">
              <span class="mono" style="font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--text-muted);flex:1;">pick playbook</span>
              <button class="iconbtn" style="width:22px;height:22px;" onclick={() => (playbookPickerOpen = false)}>×</button>
            </div>
            <div style="max-height:200px;overflow-y:auto;padding:2px 4px 4px;">
              {#each availablePlaybooks as pb (pb.id)}
                <button
                  class="btn sm ghost"
                  style="width:100%;text-align:left;justify-content:flex-start;padding:5px 8px;gap:6px;"
                  onclick={() => addPlaybookStepFromPicker(pb)}
                >
                  <span style="width:8px;height:8px;border-radius:99px;background:var(--task);flex-shrink:0;"></span>
                  <span style="font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{pb.name}</span>
                </button>
              {/each}
            </div>
          </div>
        {/if}
      {/if}
    </div>

    <!-- chain cwd -->
    <div class="card" style="padding:14px 16px;">
      <div style="display:flex;align-items:baseline;gap:8px;margin-bottom:8px;">
        <span style="font-size:12px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:.06em;">Chain cwd</span>
        <span style="font-size:11px;color:var(--text-faint);">default working directory for all steps</span>
      </div>
      <input
        class="step-input"
        value={activeChain.cwd}
        placeholder="(profile home)"
        onblur={async (e) => {
          const a = await getApi();
          if (!a || !activeChain) return;
          try { await a.SaveChain({ ...activeChain, cwd: (e.target as HTMLInputElement).value } as any); await load(true); }
          catch (err: any) { notifications.error(`Save cwd failed: ${err?.message ?? err}`); }
        }}
      />
    </div>

  </div>
{/if}

{#if pendingDelete}
  <Modal onClose={() => (pendingDelete = null)}>
    <div style="display:flex;flex-direction:column;gap:16px;">
      <h3 style="font-size:15px;font-weight:600;color:var(--text);margin:0;">Delete chain?</h3>
      <p style="font-size:13px;color:var(--text-muted);margin:0;">
        <strong style="color:var(--text);">{pendingDelete.name}</strong> will be permanently removed.
      </p>
      <div style="display:flex;justify-content:flex-end;gap:8px;">
        <button class="btn ghost" onclick={() => (pendingDelete = null)}>cancel</button>
        <button class="btn danger" onclick={confirmDeleteChain}>delete</button>
      </div>
    </div>
  </Modal>
{/if}

<style>
  .pi-main-inner { max-width: 1280px; margin: 0 auto; }
  .editor-wrap { max-width: 820px; }

  .scope-chip {
    font-size: 11px;
    color: var(--text-faint);
    border: 1px solid var(--border);
    background: var(--bg-elev);
    padding: 2px 9px;
    border-radius: 99px;
    text-transform: lowercase;
    letter-spacing: .04em;
  }

  .step-card { padding: 14px 16px; }

  .field-row { margin-bottom: 12px; }
  .field-row:last-child { margin-bottom: 0; }

  .field-label {
    display: block;
    font-size: 11.5px;
    font-weight: 600;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: .06em;
    margin-bottom: 5px;
  }

  .field-hint {
    font-size: 10.5px;
    color: var(--text-faint);
    text-transform: none;
    letter-spacing: 0;
    font-weight: 400;
    margin-left: 6px;
  }

  .step-select {
    width: 100%;
    padding: 7px 10px;
    background: var(--bg-sunken);
    border: 1px solid var(--border);
    border-radius: var(--r-md);
    color: var(--text);
    font-size: 13px;
    font-family: inherit;
  }

  .step-input {
    width: 100%;
    padding: 7px 10px;
    background: var(--bg-sunken);
    border: 1px solid var(--border);
    border-radius: var(--r-md);
    color: var(--text);
    font-size: 13px;
    font-family: inherit;
    box-sizing: border-box;
  }

  .step-textarea {
    width: 100%;
    padding: 8px 10px;
    background: var(--bg-sunken);
    border: 1px solid var(--border);
    border-radius: var(--r-md);
    color: var(--text);
    font-size: 13px;
    font-family: var(--font-mono, monospace);
    box-sizing: border-box;
    resize: vertical;
    line-height: 1.5;
  }

  .step-textarea:focus, .step-select:focus, .step-input:focus {
    outline: none;
    border-color: var(--accent);
  }

  .output-block {
    background: var(--bg-sunken);
    border: 1px solid var(--border-subtle);
    border-radius: var(--r-md);
    padding: 10px 12px;
    font-size: 11.5px;
    font-family: var(--font-mono, monospace);
    white-space: pre-wrap;
    word-break: break-all;
    color: var(--text-muted);
    max-height: 300px;
    overflow-y: auto;
    margin: 0 0 6px;
  }

  .error-block { border-color: color-mix(in srgb, var(--fail) 40%, var(--border)); color: var(--fail); }
  .stderr-block { color: var(--text-faint); border-style: dashed; }
</style>

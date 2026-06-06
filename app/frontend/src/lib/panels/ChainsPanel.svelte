<script lang="ts">
  import { onMount, onDestroy, tick } from 'svelte';
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
    stepIdx?: number; // index into chain.steps when type === 'agent'
  }
  interface CanvasEdge {
    from: string;
    to: string;
    label: string;
    on?: boolean;
  }

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
    return {
      d: `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`,
      mx: (x1 + x2) / 2,
      my: (y1 + y2) / 2,
    };
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

  // search/filter
  const filteredChains = $derived(
    (() => {
      const q = query.trim().toLowerCase();
      if (!q) return chains;
      return chains.filter((c) =>
        (c.name + ' ' + (c.description ?? '') + ' ' + c.steps.map((s) => s.agent_id).join(' '))
          .toLowerCase()
          .includes(q),
      );
    })(),
  );

  const activeChain = $derived(activeId ? chains.find((c) => c.id === activeId) ?? null : null);

  // scope label
  const scopeLabel = $derived(profileState.active?.name ?? 'all');

  function agentLabel(id: string): string {
    return chainable.find((a) => a.id === id)?.label ?? id;
  }

  async function load(silent = false) {
    if (!silent) loading = true;
    const a = await getApi();
    if (!a) { loading = false; return; }
    try {
      const [c, usable, tasks, pbs] = await Promise.all([
        a.ListChains(),
        a.UsableAgents(),
        a.ListTasks('', 200),
        a.ListPlaybooks(profileState.active?.name ?? '').catch(() => []),
      ]);
      chains = (c ?? []) as Chain[];
      chainable = ((usable ?? []) as any[]).filter((u: any) => u.invocation?.prompt_mode);
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
      availablePlaybooks = ((pbs ?? []) as any[]).map((p: any) => ({ id: p.id, name: p.name, workspace_profile: p.workspace_profile ?? '' }));
    } catch (err: any) {
      notifications.error(`Failed to load chains: ${err?.message ?? err}`);
    } finally {
      loading = false;
    }
  }

  onMount(async () => {
    await load();
    const id = panelFocus.consumeChainFocus();
    if (id) {
      activeId = id;
    }
  });

  $effect(() => {
    refreshTick.count;
    void load(true);
  });

  // ----- gallery helpers: derive canvas-style nodes/edges from a chain -----
  function nodesFromChain(c: Chain): { nodes: CanvasNode[]; edges: CanvasEdge[] } {
    const positions = loadPositions(c.id);
    const nodes: CanvasNode[] = [];
    // trigger virtual node
    nodes.push({
      id: `t-${c.id}`,
      type: 'trigger',
      x: positions[`t-${c.id}`]?.x ?? 0,
      y: positions[`t-${c.id}`]?.y ?? 0,
      title: 'manual',
      sub: 'trigger',
      state: 'idle',
    });
    c.steps.forEach((s, i) => {
      const nid = `s-${c.id}-${i}`;
      if (s.type === 'playbook') {
        const pb = availablePlaybooks.find((p) => p.id === s.playbook_id);
        nodes.push({
          id: nid,
          type: 'playbook',
          x: positions[nid]?.x ?? (i + 1) * 240,
          y: positions[nid]?.y ?? 0,
          title: pb?.name ?? s.playbook_id ?? 'playbook',
          sub: s.playbook_id?.slice(0, 8) ?? '',
          ref: s.playbook_id,
          state: 'idle',
          stepIdx: i,
        });
      } else {
        nodes.push({
          id: nid,
          type: 'agent',
          x: positions[nid]?.x ?? (i + 1) * 240,
          y: positions[nid]?.y ?? 0,
          title: agentLabel(s.agent_id),
          sub: s.prompt_template?.slice(0, 40) ?? '',
          state: 'idle',
          stepIdx: i,
        });
      }
    });
    const edges: CanvasEdge[] = [];
    for (let i = 0; i < nodes.length - 1; i++) {
      edges.push({ from: nodes[i].id, to: nodes[i + 1].id, label: '' });
    }
    return { nodes, edges };
  }

  function loadPositions(chainId: string): Record<string, { x: number; y: number }> {
    try {
      const raw = localStorage.getItem(`chain-pos-${chainId}`);
      if (!raw) return {};
      return JSON.parse(raw);
    } catch {
      return {};
    }
  }
  function savePositions(chainId: string, nodes: CanvasNode[]) {
    const out: Record<string, { x: number; y: number }> = {};
    for (const n of nodes) out[n.id] = { x: n.x, y: n.y };
    try { localStorage.setItem(`chain-pos-${chainId}`, JSON.stringify(out)); } catch {}
  }

  async function newChain() {
    const a = await getApi();
    if (!a) return;
    if (chainable.length === 0) {
      notifications.error('No usable agents — enable one to build a chain');
      return;
    }
    const draft: Chain = {
      id: '', name: 'new-chain', description: '',
      steps: [{ type: 'agent', agent_id: chainable[0].id, prompt_template: '{{input}}', cwd: '' }],
      cwd: '', on_success: [], files: [], created_at: '', updated_at: '',
      workspace_profile: profileState.active?.name ?? '',
    };
    try {
      await a.SaveChain(draft);
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
      await a.SaveChain({ ...activeChain, name: editName.trim() });
      await load();
    } catch (err: any) {
      notifications.error(`Rename failed: ${err?.message ?? err}`);
    }
  }

  // ============== CANVAS VIEW ==============
  let canvasNodes = $state<CanvasNode[]>([]);
  let canvasEdges = $state<CanvasEdge[]>([]);
  let panX = $state(30);
  let panY = $state(10);
  let zoom = $state(1);
  let sel = $state<string | null>(null);
  let running = $state(false);
  let addOpen = $state(false);

  // drag-to-connect temp
  let connecting = $state<{ from: string; x1: number; y1: number; x2: number; y2: number } | null>(null);

  let canvasEl: HTMLDivElement | null = $state(null);

  // when activeChain changes, hydrate canvas state
  let lastHydratedId: string | null = null;
  $effect(() => {
    const c = activeChain;
    if (!c) {
      lastHydratedId = null;
      return;
    }
    if (lastHydratedId === c.id) return;
    lastHydratedId = c.id;
    const { nodes, edges } = nodesFromChain(c);
    canvasNodes = nodes;
    canvasEdges = edges;
    panX = 30; panY = 10; zoom = 1; sel = null;
    running = false;
  });

  $effect(() => {
    if (activeChain) editName = activeChain.name;
  });

  function onCanvasMouseDown(e: MouseEvent) {
    const t = e.target as HTMLElement;
    if (t.closest('.chain-node, .chain-toolbar, .chain-add, .chain-mini, .chain-handle')) return;
    sel = null;
    const sx = e.clientX, sy = e.clientY;
    const ox = panX, oy = panY;
    const move = (ev: MouseEvent) => { panX = ox + ev.clientX - sx; panY = oy + ev.clientY - sy; };
    const up = () => {
      document.removeEventListener('mousemove', move);
      document.removeEventListener('mouseup', up);
    };
    document.addEventListener('mousemove', move);
    document.addEventListener('mouseup', up);
  }

  function onNodeMouseDown(e: MouseEvent, id: string) {
    e.stopPropagation();
    sel = id;
    const n = canvasNodes.find((m) => m.id === id);
    if (!n) return;
    const sx = e.clientX, sy = e.clientY;
    const ox = n.x, oy = n.y;
    const move = (ev: MouseEvent) => {
      const nx = ox + (ev.clientX - sx) / zoom;
      const ny = oy + (ev.clientY - sy) / zoom;
      canvasNodes = canvasNodes.map((m) => (m.id === id ? { ...m, x: nx, y: ny } : m));
    };
    const up = () => {
      document.removeEventListener('mousemove', move);
      document.removeEventListener('mouseup', up);
      if (activeChain) savePositions(activeChain.id, canvasNodes);
    };
    document.addEventListener('mousemove', move);
    document.addEventListener('mouseup', up);
  }

  function worldPoint(clientX: number, clientY: number): { x: number; y: number } {
    if (!canvasEl) return { x: 0, y: 0 };
    const r = canvasEl.getBoundingClientRect();
    return { x: (clientX - r.left - panX) / zoom, y: (clientY - r.top - panY) / zoom };
  }

  function onOutputHandleDown(e: MouseEvent, nodeId: string) {
    e.stopPropagation();
    e.preventDefault();
    const n = canvasNodes.find((m) => m.id === nodeId);
    if (!n) return;
    const start = { x: n.x + NW, y: n.y + NH / 2 };
    const cur = worldPoint(e.clientX, e.clientY);
    connecting = { from: nodeId, x1: start.x, y1: start.y, x2: cur.x, y2: cur.y };
    const move = (ev: MouseEvent) => {
      const p = worldPoint(ev.clientX, ev.clientY);
      if (connecting) connecting = { ...connecting, x2: p.x, y2: p.y };
    };
    const up = (ev: MouseEvent) => {
      document.removeEventListener('mousemove', move);
      document.removeEventListener('mouseup', up);
      // hit-test input handles
      const tgt = (ev.target as HTMLElement)?.closest('.chain-handle-in') as HTMLElement | null;
      if (tgt && tgt.dataset.nodeId && tgt.dataset.nodeId !== nodeId) {
        const toId = tgt.dataset.nodeId;
        if (!canvasEdges.find((ed) => ed.from === nodeId && ed.to === toId)) {
          canvasEdges = [...canvasEdges, { from: nodeId, to: toId, label: '' }];
        }
      }
      connecting = null;
    };
    document.addEventListener('mousemove', move);
    document.addEventListener('mouseup', up);
  }

  function addNode(type: NodeType, ref?: string, title?: string, sub?: string) {
    const id = 'n' + Date.now();
    const cx = (-panX + 380) / zoom;
    const cy = (-panY + 230) / zoom;
    canvasNodes = [
      ...canvasNodes,
      { id, type, x: cx, y: cy, title: title || NODE_META[type].label, sub: sub || '', ref, state: 'idle' },
    ];
    addOpen = false;
    if (activeChain) savePositions(activeChain.id, canvasNodes);
    notifications.info('node added');
  }

  async function delNode(id: string) {
    canvasNodes = canvasNodes.filter((n) => n.id !== id);
    canvasEdges = canvasEdges.filter((e) => e.from !== id && e.to !== id);
    sel = null;
    if (activeChain) {
      savePositions(activeChain.id, canvasNodes);
      const match = id.match(/^s-[^-]+-(\d+)$/);
      if (match) {
        const stepIdx = parseInt(match[1], 10);
        const updatedSteps = activeChain.steps.filter((_, i) => i !== stepIdx);
        const a = await getApi();
        if (a) {
          try {
            await a.SaveChain({ ...activeChain, steps: updatedSteps });
            await load();
          } catch (err: any) {
            notifications.error(`Remove node failed: ${err?.message ?? err}`);
          }
        }
      }
    }
  }

  async function addPlaybookNode(pb: PlaybookItem) {
    if (!activeChain) return;
    playbookPickerOpen = false;
    const a = await getApi();
    if (!a) return;
    const updatedSteps: ChainStep[] = [
      ...activeChain.steps,
      { type: 'playbook', agent_id: '', playbook_id: pb.id, prompt_template: '', cwd: '' },
    ];
    try {
      await a.SaveChain({ ...activeChain, steps: updatedSteps });
      await load();
    } catch (err: any) {
      notifications.error(`Add playbook node failed: ${err?.message ?? err}`);
    }
  }

  function runChainAnim() {
    if (running) return;
    running = true;
    const order = [...canvasNodes].sort((a, b) => {
      const ta = a.type === 'trigger' ? -1 : 0;
      const tb = b.type === 'trigger' ? -1 : 0;
      return ta - tb || a.x - b.x;
    });
    canvasNodes = canvasNodes.map((n) => ({ ...n, state: 'idle' }));
    canvasEdges = canvasEdges.map((e) => ({ ...e, on: false }));
    order.forEach((n, i) => {
      setTimeout(() => {
        canvasNodes = canvasNodes.map((m) => (m.id === n.id ? { ...m, state: 'run' } : m));
        canvasEdges = canvasEdges.map((e) => (e.from === n.id ? { ...e, on: true } : e));
        setTimeout(() => {
          canvasNodes = canvasNodes.map((m) => (m.id === n.id ? { ...m, state: 'done' } : m));
        }, 520);
      }, i * 620);
    });
    setTimeout(() => {
      running = false;
      notifications.success('chain run complete');
    }, order.length * 620 + 600);
  }

  async function runChainReal() {
    if (!activeChain) return;
    const a = await getApi();
    if (!a) return;
    runChainAnim();
    try {
      await a.RunChain(activeChain.id, '', activeChain.cwd ?? '');
    } catch (err: any) {
      notifications.error(`Run failed: ${err?.message ?? err}`);
    }
  }

  // ----- minimap bounds -----
  const mmBounds = $derived(
    (() => {
      if (canvasNodes.length === 0) return { bx: 0, by: 0, bw: 1, bh: 1, ms: 1 };
      const bx = Math.min(...canvasNodes.map((n) => n.x), 0);
      const by = Math.min(...canvasNodes.map((n) => n.y), 0);
      const bw = Math.max(...canvasNodes.map((n) => n.x + NW)) - bx + 40;
      const bh = Math.max(...canvasNodes.map((n) => n.y + NH)) - by + 40;
      const ms = Math.min(150 / bw, 90 / bh);
      return { bx, by, bw, bh, ms };
    })(),
  );

  function nodeStateColor(s: CanvasNode['state']): string {
    if (s === 'run') return 'var(--accent)';
    if (s === 'done') return 'var(--run)';
    return 'var(--text-faint)';
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
      Orchestration graphs — each wires playbooks, agents and tools into a flow. Open one to edit on the canvas.
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
            <!-- thumb -->
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
                <span>{preview.nodes.length} nodes</span>
                <span>{preview.edges.length} edges</span>
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
  <!-- ===== CANVAS ===== -->
  <div
    bind:this={canvasEl}
    class="scroll"
    onmousedown={onCanvasMouseDown}
    role="presentation"
    style="position: relative; height: calc(100vh - 86px); width: 100%; overflow: hidden; cursor: grab; background-image: radial-gradient(var(--grid-dot) 1.4px, transparent 1.4px); background-size: {22 * zoom}px {22 * zoom}px; background-position: {panX}px {panY}px;"
  >
    <!-- toolbar -->
    <div class="chain-toolbar" style="position:absolute;top:18px;left:22px;right:22px;z-index:10;display:flex;align-items:center;gap:12px;pointer-events:none;">
      <div style="pointer-events:auto;display:flex;align-items:center;gap:12px;">
        <button class="btn ghost sm" onclick={() => (activeId = null)} title="all chains">←</button>
        <div>
          <input
            class="chain-name-input"
            bind:value={editName}
            onblur={(e) => { (e.target as HTMLInputElement).style.borderBottomColor = 'transparent'; saveChainName(); }}
            onkeydown={(e) => { if (e.key === 'Enter') { saveChainName(); (e.target as HTMLInputElement).blur(); } }}
            onfocus={(e) => { (e.target as HTMLInputElement).style.borderBottomColor = 'var(--accent)'; }}
            style="font-size:26px;font-weight:700;color:var(--accent);background:transparent;border:none;border-bottom:1px solid transparent;outline:none;width:260px;padding:0;font-family:inherit;cursor:text;"
          />
          <div class="mono" style="font-size:11.5px;color: var(--text-faint);margin-top:2px;">
            {scopeLabel} · {canvasNodes.length} nodes · {canvasEdges.length} edges
          </div>
        </div>
      </div>
      <div style="margin-left:auto;display:flex;gap:8px;pointer-events:auto;position:relative;">
        <button class="btn sm" onclick={() => (addOpen = !addOpen)}>+ node</button>
        <button class="btn sm {running ? '' : 'primary'}" onclick={runChainReal} disabled={running}>
          {running ? 'running…' : '▶ run chain'}
        </button>
        {#if addOpen}
          <div class="chain-add" style="position:absolute;top:40px;right:0;width:280px;background: var(--bg-elev);border:1px solid var(--border);border-radius: var(--r-lg);box-shadow: var(--shadow-lg);padding:8px;z-index:20;">
            <div style="display:flex;align-items:center;padding:6px 8px 8px;">
              <span class="mono" style="font-size:11px;letter-spacing:.1em;text-transform:uppercase;color: var(--text-muted);flex:1;">» add node</span>
              <button class="iconbtn" style="width:22px;height:22px;" onclick={() => (addOpen = false)}>×</button>
            </div>
            <div class="mono" style="font-size:9.5px;color: var(--text-faint);padding:2px 9px;letter-spacing:.08em;">PRIMITIVES</div>
            <div style="display:flex;gap:6px;padding:4px 6px 8px;">
              {#each ['trigger', 'agent', 'tool'] as t (t)}
                <button class="btn sm ghost" onclick={() => addNode(t as NodeType)} style="flex:1;flex-direction:column;gap:4px;padding:9px 4px;">
                  <span style="width:10px;height:10px;border-radius:99px;background: {NODE_META[t as NodeType].color};"></span>
                  <span class="mono" style="font-size:10px;">{t}</span>
                </button>
              {/each}
            </div>
            {#if availablePlaybooks.length > 0}
              <div class="mono" style="font-size:9.5px;color: var(--text-faint);padding:6px 9px 2px;letter-spacing:.08em;border-top:1px solid var(--border-subtle);margin-top:4px;">PLAYBOOKS</div>
              <div style="max-height:160px;overflow-y:auto;padding:2px 4px 4px;">
                {#each availablePlaybooks as pb (pb.id)}
                  <button
                    class="btn sm ghost"
                    style="width:100%;text-align:left;justify-content:flex-start;padding:5px 8px;gap:6px;"
                    onclick={() => addPlaybookNode(pb)}
                  >
                    <span style="width:8px;height:8px;border-radius:99px;background:var(--task);flex-shrink:0;"></span>
                    <span style="font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{pb.name}</span>
                  </button>
                {/each}
              </div>
            {/if}
          </div>
        {/if}
      </div>
    </div>

    <!-- world -->
    <div style="position:absolute;left:0;top:0;transform: translate({panX}px, {panY}px) scale({zoom}); transform-origin: 0 0;">
      <svg style="position:absolute;overflow:visible;pointer-events:none;left:0;top:0;" width="10" height="10">
        {#each canvasEdges as e, i (i)}
          {@const a = canvasNodes.find((n) => n.id === e.from)}
          {@const b = canvasNodes.find((n) => n.id === e.to)}
          {#if a && b}
            {@const p = edgePath(a, b)}
            <path
              d={p.d}
              fill="none"
              stroke={e.on ? 'var(--accent)' : 'var(--border-strong)'}
              stroke-width={e.on ? 2.4 : 1.6}
              stroke-dasharray={e.on ? '6 5' : '0'}
              class={e.on ? 'edge-anim' : ''}
            />
          {/if}
        {/each}
        {#if connecting}
          {@const x1 = connecting.x1}
          {@const y1 = connecting.y1}
          {@const x2 = connecting.x2}
          {@const y2 = connecting.y2}
          {@const dx = Math.max(40, Math.abs(x2 - x1) * 0.5)}
          <path d={`M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`} fill="none" stroke="var(--accent)" stroke-width="2" stroke-dasharray="5 4" />
        {/if}
      </svg>

      {#each canvasEdges as e, i (i)}
        {@const a = canvasNodes.find((n) => n.id === e.from)}
        {@const b = canvasNodes.find((n) => n.id === e.to)}
        {#if a && b && e.label}
          {@const p = edgePath(a, b)}
          <div class="mono" style="position:absolute;left:{p.mx}px;top:{p.my}px;transform: translate(-50%,-50%);font-size:9.5px;color: var(--text-muted);background: var(--bg);border:1px solid var(--border);padding:1px 6px;border-radius:99px;white-space:nowrap;pointer-events:none;">
            {e.label}
          </div>
        {/if}
      {/each}

      {#each canvasNodes as n (n.id)}
        {@const m = NODE_META[n.type]}
        {@const ring = sel === n.id}
        <div
          class="chain-node"
          onmousedown={(e) => onNodeMouseDown(e, n.id)}
          role="presentation"
          style="position:absolute;left:{n.x}px;top:{n.y}px;width:{NW}px;min-height:{NH}px;cursor:grab;background: var(--bg-elev);border: 1px solid {ring ? m.color : 'var(--border)'};border-left: 3px solid {m.color};border-radius: var(--r-md); box-shadow: {ring ? 'var(--shadow-md)' : 'var(--shadow-sm)'}; padding: 11px 13px; user-select: none; {n.state === 'run' ? `outline: 2px solid color-mix(in srgb, ${m.color} 50%, transparent); outline-offset: 2px;` : ''}"
        >
          {#if n.type !== 'trigger'}
            <span
              class="chain-handle chain-handle-in"
              data-node-id={n.id}
              style="position:absolute;left:-6px;top:{NH / 2 - 5}px;width:10px;height:10px;border-radius:99px;background: var(--bg-elev);border: 2px solid {m.color};"
            ></span>
          {/if}
          <span
            class="chain-handle chain-handle-out"
            data-node-id={n.id}
            onmousedown={(e) => onOutputHandleDown(e, n.id)}
            role="presentation"
            style="position:absolute;right:-6px;top:{NH / 2 - 5}px;width:10px;height:10px;border-radius:99px;background: var(--bg-elev);border: 2px solid {m.color};cursor:crosshair;"
          ></span>
          <div style="display:flex;align-items:center;gap:7px;">
            <span class="mono" style="font-size:9px;letter-spacing:.1em;text-transform:uppercase;color: {m.color};flex:1;">{m.label}</span>
            <span style="width:7px;height:7px;border-radius:99px;background: {nodeStateColor(n.state)};"></span>
            {#if ring}
              <button
                onclick={(e) => { e.stopPropagation(); void delNode(n.id); }}
                class="iconbtn"
                style="width:18px;height:18px;"
                aria-label="delete node"
              >×</button>
            {/if}
          </div>
          <div style="font-size:13.5px;font-weight:700;margin-top:5px;color: var(--text);">{n.title}</div>
          {#if n.sub}
            <div class="mono" style="font-size:10.5px;color: var(--text-faint);margin-top:2px;">{n.sub}</div>
          {/if}
        </div>
      {/each}
    </div>

    <!-- zoom controls -->
    <div class="chain-mini" style="position:absolute;bottom:18px;left:22px;z-index:10;display:flex;gap:6px;">
      <button class="btn sm" onclick={() => (zoom = Math.min(1.6, +(zoom + 0.15).toFixed(2)))}>+</button>
      <button class="btn sm" onclick={() => (zoom = Math.max(0.5, +(zoom - 0.15).toFixed(2)))}>−</button>
      <button class="btn sm" onclick={() => { zoom = 1; panX = 30; panY = 10; }}>fit</button>
      <span class="mono" style="align-self:center;font-size:11px;color: var(--text-faint);margin-left:4px;">{Math.round(zoom * 100)}%</span>
    </div>

    <!-- minimap -->
    <div class="chain-mini card" style="position:absolute;bottom:18px;right:22px;z-index:10;width:160px;height:100px;overflow:hidden;padding:0;">
      <svg width="160" height="100">
        {#each canvasNodes as n (n.id)}
          {@const m = NODE_META[n.type]}
          <rect
            x={5 + (n.x - mmBounds.bx) * mmBounds.ms}
            y={5 + (n.y - mmBounds.by) * mmBounds.ms}
            width={NW * mmBounds.ms}
            height={NH * mmBounds.ms}
            rx="2"
            fill={m.color}
            opacity="0.85"
          />
        {/each}
      </svg>
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
</style>

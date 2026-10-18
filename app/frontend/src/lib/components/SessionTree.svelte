<script lang="ts">
  // Three shapes of ONE session's observations, from the Agent Work Wheel
  // probe's own rationale: a list to act on, a tree to audit, a wheel to see
  // scale. Each answers a different question, which is why all three are kept
  // rather than one being picked as "best".
  //
  //   list   — chronological rows indented by depth. Scan and act.
  //   tree   — depth on x, siblings stacked in TIME order on y, so chronology
  //            survives the hierarchy. This is the audit shape.
  //   radial — angle by chronological position, radius by depth. Fan-out and
  //            overall scale read at a glance; detail does not.
  //
  // Hand-rolled SVG, consistent with MetricsChart and PieChart. No dependency.
  //
  // Colour encodes TYPE, not "status". The probe coloured by active/done/failed
  // because its nodes were agent tasks; a Langfuse observation is always
  // finished, so the informative dimension is what KIND of work it was, with an
  // error ring overriding when one failed.

  interface Observation {
    id: string;
    parent_id?: string;
    depth: number;
    name: string;
    type: string;
    start_time: string;
    end_time?: string;
    latency_ms?: number;
    model?: string;
    cost_usd?: number;
    level?: string;
    status_message?: string;
    input?: string;
    output?: string;
  }

  type View = 'list' | 'tree' | 'radial';

  interface Props {
    observations?: Observation[] | null;
    view?: View;
    selectedId?: string | null;
    onSelect?: (id: string | null) => void;
  }

  let {
    observations = [],
    view = 'list',
    selectedId = null,
    onSelect,
  }: Props = $props();

  // Never trust the array: a nil Go slice marshals to null, and reading
  // .length on it threw during render once already.
  const obs = $derived(observations ?? []);

  let hoverId = $state<string | null>(null);

  function isError(o: Observation): boolean {
    return (o.level ?? '').toUpperCase() === 'ERROR';
  }

  // One hue per observation type. Taken from the app's token set so the shapes
  // sit in the product rather than beside it.
  function hue(o: Observation): string {
    if (isError(o)) return 'var(--color-status-error-text)';
    switch (o.type) {
      case 'GENERATION':
        return 'var(--color-role-blue-text, var(--color-info))';
      case 'TOOL':
        return 'var(--color-status-success-text)';
      default:
        return 'var(--color-text-tertiary)';
    }
  }

  function ms(n?: number): string {
    if (!n) return '';
    return n < 1000 ? `${Math.round(n)}ms` : `${(n / 1000).toFixed(1)}s`;
  }

  function usd(n?: number): string {
    if (!n) return '';
    return n < 0.01 ? '<$0.01' : `$${n.toFixed(2)}`;
  }

  function clock(iso: string): string {
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? '' : d.toLocaleTimeString([], { hour12: false });
  }

  function summary(o: Observation): string {
    const bits = [o.type.toLowerCase()];
    if (o.model) bits.push(o.model);
    if (o.latency_ms) bits.push(ms(o.latency_ms));
    if (o.cost_usd) bits.push(usd(o.cost_usd));
    return bits.join(' · ');
  }

  // --- tree layout ----------------------------------------------------------
  // y is the CHRONOLOGICAL index, not a subtree-packing position. That is the
  // whole point of this shape: the hierarchy is on x and time is preserved on
  // y, so an audit reads top to bottom in the order things happened.

  const ROW = 20;
  const DEPTH_X = 26;
  const LEFT = 14;

  const treeNodes = $derived(
    obs.map((o, i) => ({
      o,
      x: LEFT + Math.min(o.depth, 10) * DEPTH_X,
      y: i * ROW + ROW / 2,
    })),
  );

  // Connectors: an elbow from the parent's column down to the child's row.
  const treeLinks = $derived.by(() => {
    const at = new Map(treeNodes.map((n) => [n.o.id, n]));
    const out: { x1: number; y1: number; x2: number; y2: number; c: string }[] = [];
    for (const n of treeNodes) {
      const p = n.o.parent_id ? at.get(n.o.parent_id) : undefined;
      if (!p) continue;
      out.push({ x1: p.x, y1: p.y, x2: n.x, y2: n.y, c: hue(n.o) });
    }
    return out;
  });

  const treeHeight = $derived(Math.max(ROW, obs.length * ROW));
  // The viewBox must leave room for the LABELS, not just the depth columns:
  // text is drawn at x+10 and anything past the viewBox width is clipped, so
  // a label column is reserved and the container scrolls horizontally.
  const LABEL_W = 520;
  const treeWidth = $derived(LEFT * 2 + 11 * DEPTH_X + LABEL_W);

  // --- radial layout --------------------------------------------------------
  // Angle by chronological position, radius by depth. Deliberately label-free:
  // a few hundred nodes at sub-degree spacing cannot carry text, so identity
  // comes from hover and the pinned detail instead of from a crowded ring.

  const R_SIZE = 460;
  const R_C = R_SIZE / 2;
  const R_STEP = 62;

  const radialNodes = $derived.by(() => {
    const n = Math.max(obs.length, 1);
    const maxDepth = Math.max(1, ...obs.map((o) => o.depth));
    // Leave a margin so the outermost ring is not clipped by the viewBox.
    const usable = R_C - 26;
    return obs.map((o, i) => {
      // -90deg so the first observation starts at the top and time runs
      // clockwise, which is how a clock is read.
      const a = (i / n) * Math.PI * 2 - Math.PI / 2;
      const r = Math.min(usable, 30 + (o.depth / maxDepth) * Math.min(usable - 30, maxDepth * R_STEP));
      return { o, x: R_C + Math.cos(a) * r, y: R_C + Math.sin(a) * r };
    });
  });

  const radialLinks = $derived.by(() => {
    const at = new Map(radialNodes.map((n) => [n.o.id, n]));
    const out: { x1: number; y1: number; x2: number; y2: number; c: string }[] = [];
    for (const n of radialNodes) {
      const p = n.o.parent_id ? at.get(n.o.parent_id) : undefined;
      if (!p) continue;
      out.push({ x1: p.x, y1: p.y, x2: n.x, y2: n.y, c: hue(n.o) });
    }
    return out;
  });

  // Node radius shrinks as the session grows, so a 500-observation wheel is
  // dense rather than a solid disc.
  const dotR = $derived(obs.length > 300 ? 2.5 : obs.length > 120 ? 3.5 : 5);

  function pick(id: string) {
    onSelect?.(selectedId === id ? null : id);
  }

  const active = $derived(
    obs.find((o) => o.id === (hoverId ?? selectedId)) ?? null,
  );
</script>

{#if obs.length === 0}
  <p class="muted empty">No observations recorded for this session.</p>
{:else if view === 'list'}
  <ol class="rows">
    {#each obs as o (o.id)}
      <li
        class="row"
        class:bad={isError(o)}
        class:on={o.id === selectedId}
        style="padding-left: {Math.min(o.depth, 8) * 15}px"
      >
        <button class="rowbtn" onclick={() => pick(o.id)} title={o.depth > 8 ? `depth ${o.depth}` : undefined}>
          <span class="t">{clock(o.start_time)}</span>
          <span class="swatch" style="background: {hue(o)}"></span>
          <span class="nm">{o.name || '(unnamed)'}</span>
          <span class="meta">{summary(o)}</span>
        </button>
      </li>
    {/each}
  </ol>
{:else if view === 'tree'}
  <div class="plot">
    <svg
      class="tree"
      viewBox="0 0 {treeWidth} {treeHeight}"
      width={treeWidth}
      height={treeHeight}
      role="img"
      aria-label="Session tree: depth left to right, time top to bottom"
    >
      {#each treeLinks as l, i (i)}
        <path
          class="link"
          d="M{l.x1},{l.y1} V{l.y2} H{l.x2}"
          stroke={l.c}
          fill="none"
        />
      {/each}
      {#each treeNodes as n (n.o.id)}
        <g
          class="node"
          class:on={n.o.id === selectedId}
          onmouseenter={() => (hoverId = n.o.id)}
          onmouseleave={() => (hoverId = null)}
          onclick={() => pick(n.o.id)}
          onkeydown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              pick(n.o.id);
            }
          }}
          role="button"
          tabindex="0"
          aria-label="{n.o.name} — {summary(n.o)}"
        >
          <circle cx={n.x} cy={n.y} r={n.o.id === selectedId ? 6 : 4} fill={hue(n.o)} />
          <text x={n.x + 10} y={n.y + 3.5}>{n.o.name || '(unnamed)'}</text>
        </g>
      {/each}
    </svg>
  </div>
{:else}
  <div class="plot radialwrap">
    <svg
      class="radial"
      viewBox="0 0 {R_SIZE} {R_SIZE}"
      role="img"
      aria-label="Session wheel: time clockwise from the top, depth outward from the centre"
    >
      {#each radialLinks as l, i (i)}
        <line class="link" x1={l.x1} y1={l.y1} x2={l.x2} y2={l.y2} stroke={l.c} />
      {/each}
      {#each radialNodes as n (n.o.id)}
        <circle
          class="rdot"
          class:on={n.o.id === selectedId}
          cx={n.x}
          cy={n.y}
          r={n.o.id === selectedId ? dotR + 2.5 : dotR}
          fill={hue(n.o)}
          onmouseenter={() => (hoverId = n.o.id)}
          onmouseleave={() => (hoverId = null)}
          onclick={() => pick(n.o.id)}
          onkeydown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              pick(n.o.id);
            }
          }}
          role="button"
          tabindex="0"
          aria-label="{n.o.name} — {summary(n.o)}"
        />
      {/each}
    </svg>
  </div>
{/if}

{#if obs.length > 0}
  <div class="footer">
    <!-- Identity is never colour-alone: the legend is labelled, and every node
         carries an aria-label with its name and summary. -->
    <div class="legend">
      <span class="key"><span class="swatch" style="background: var(--color-role-blue-text, var(--color-info))"></span>generation</span>
      <span class="key"><span class="swatch" style="background: var(--color-status-success-text)"></span>tool</span>
      <span class="key"><span class="swatch" style="background: var(--color-text-tertiary)"></span>span</span>
      <span class="key"><span class="swatch" style="background: var(--color-status-error-text)"></span>error</span>
    </div>
    <!-- The peek. A pinned selection wins over hover, so clicking a node and
         then moving the mouse does not lose what you pinned. -->
    {#if active}
      <p class="peek">
        <strong>{active.name || '(unnamed)'}</strong>
        <span class="meta">{summary(active)}</span>
        {#if isError(active) && active.status_message}
          <span class="err">{active.status_message}</span>
        {/if}
      </p>
    {:else if view !== 'list'}
      <p class="peek muted">Hover a node; click to pin it.</p>
    {/if}
  </div>
{/if}

<style>
  .empty,
  .muted {
    color: var(--color-text-tertiary);
    font-size: 12px;
  }
  .plot {
    overflow: auto;
    max-height: 520px;
    border: 1px solid var(--color-border-secondary);
    border-radius: var(--radius-sm);
  }
  .radialwrap {
    display: flex;
    justify-content: center;
    overflow: hidden;
  }
  /* Explicit width + horizontal scroll rather than min-width:100%, which
     would stretch the viewBox and squash the layout on a wide panel. */
  svg.tree {
    display: block;
  }
  svg.tree text {
    font-size: 10px;
    fill: var(--color-text-secondary);
    pointer-events: none;
  }
  svg.radial {
    display: block;
    width: 100%;
    max-width: 460px;
    height: auto;
  }
  /* Links are recessive: the grouping should be legible without competing with
     the nodes it groups. Opacity is lifted in dark mode, where the same alpha
     disappears against the darker ground. */
  .link {
    stroke-width: 1;
    stroke-opacity: 0.45;
  }
  @media (prefers-color-scheme: dark) {
    .link {
      stroke-opacity: 0.6;
    }
  }
  .node circle,
  .rdot {
    cursor: pointer;
  }
  .node:focus-visible circle,
  .rdot:focus-visible {
    outline: 2px solid var(--color-text-primary);
    outline-offset: 2px;
  }
  .node.on circle,
  .rdot.on {
    stroke: var(--color-text-primary);
    stroke-width: 1.5;
  }

  ol.rows {
    list-style: none;
    margin: 0;
    padding: 0;
    max-height: 520px;
    overflow: auto;
  }
  .row {
    border-left: 2px solid var(--color-border-secondary);
  }
  .row.bad {
    border-left-color: var(--color-status-error-text);
  }
  .row.on {
    background: var(--color-muted-bg);
  }
  .rowbtn {
    display: flex;
    align-items: baseline;
    gap: var(--spacing-xs);
    width: 100%;
    background: none;
    border: 0;
    padding: 2px var(--spacing-sm);
    text-align: left;
    cursor: pointer;
    color: inherit;
    font: inherit;
    font-size: 12px;
  }
  .rowbtn:hover {
    background: var(--color-muted-bg);
  }
  .t {
    font-family: var(--font-mono, monospace);
    font-size: 11px;
    color: var(--color-text-tertiary);
    flex: 0 0 auto;
  }
  .nm {
    font-weight: 500;
  }
  .meta {
    color: var(--color-text-tertiary);
    font-size: 11px;
  }
  .swatch {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    display: inline-block;
    flex: 0 0 auto;
  }

  .footer {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: var(--spacing-md);
    flex-wrap: wrap;
    margin-top: var(--spacing-xs);
  }
  .legend {
    display: flex;
    gap: var(--spacing-md);
    flex-wrap: wrap;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--color-text-tertiary);
  }
  .key {
    display: inline-flex;
    align-items: center;
    gap: 4px;
  }
  .peek {
    margin: 0;
    font-size: 11px;
    display: flex;
    gap: var(--spacing-xs);
    flex-wrap: wrap;
    align-items: baseline;
    min-width: 0;
  }
  .peek .err {
    color: var(--color-status-error-text);
  }
</style>

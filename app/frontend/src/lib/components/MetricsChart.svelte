<script lang="ts">
  import { scaleTime, scaleLinear } from 'd3-scale';
  import { line, curveMonotoneX } from 'd3-shape';
  import { extent, max } from 'd3-array';

  interface Sample {
    ts: number;        // unix seconds
    value: number;
  }

  interface Props {
    data: Sample[];
    label: string;
    current: string;
    color?: string;
    formatY?: (v: number) => string;
    width?: number;
    height?: number;
    compact?: boolean;  // strip axes/labels, just line+area
    strokeWidth?: number;
    hoverIdx?: number | null;          // externally synced hover position
    onHover?: (idx: number) => void;   // called on mousemove with nearest index
    onHoverEnd?: () => void;           // called on mouseleave
  }

  let {
    data,
    label,
    current,
    color = 'var(--color-accent)',
    formatY = (v: number) => v.toFixed(1),
    width = 300,
    height = 80,
    compact = false,
    strokeWidth = 1.5,
    hoverIdx = undefined,
    onHover = undefined,
    onHoverEnd = undefined,
  }: Props = $props();

  const PAD = $derived(compact
    ? { top: 2, right: 2, bottom: 2, left: 2 }
    : { top: 8, right: 8, bottom: 24, left: 40 });

  let innerW = $derived(width - PAD.left - PAD.right);
  let innerH = $derived(height - PAD.top - PAD.bottom);

  let xScale = $derived.by(() => {
    if (data.length < 2) return null;
    const [t0, t1] = extent(data, d => d.ts * 1000) as [number, number];
    return scaleTime().domain([t0, t1]).range([0, innerW]);
  });

  let yScale = $derived.by(() => {
    if (data.length < 2) return null;
    const maxVal = max(data, d => d.value) ?? 0;
    const ceiling = maxVal > 0 ? maxVal * 1.1 : 1; // avoid zero-range scale
    return scaleLinear().domain([0, ceiling]).range([innerH, 0]).nice();
  });

  let pathD = $derived.by(() => {
    if (!xScale || !yScale || data.length < 2) return '';
    const gen = line<Sample>()
      .x(d => xScale!(d.ts * 1000))
      .y(d => yScale!(d.value))
      .curve(curveMonotoneX);
    return gen(data) ?? '';
  });

  let areaD = $derived.by(() => {
    if (!xScale || !yScale || data.length < 2) return '';
    const top = pathD;
    const last = data[data.length - 1];
    const first = data[0];
    return `${top}L${xScale(last.ts * 1000)},${innerH}L${xScale(first.ts * 1000)},${innerH}Z`;
  });

  // Y axis ticks — 3 evenly spaced
  let yTicks = $derived.by(() => {
    if (!yScale) return [];
    return yScale.ticks(3).map(v => ({ v, y: yScale!(v) }));
  });

  // X axis ticks — time labels
  let xTicks = $derived.by(() => {
    if (!xScale) return [];
    return xScale.ticks(4).map(t => ({
      t,
      x: xScale!(t),
      label: new Date(t).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    }));
  });

  // Hover crosshair — internal index (from mouse), overridden by external hoverIdx prop
  let _hoverIdx = $state<number | null>(null);
  let activeIdx = $derived(hoverIdx !== undefined ? hoverIdx : _hoverIdx);
  let hoverX  = $derived(activeIdx !== null && xScale && data[activeIdx] ? xScale(data[activeIdx].ts * 1000) : 0);
  let hoverY  = $derived(activeIdx !== null && yScale && data[activeIdx] ? yScale(data[activeIdx].value) : 0);
  let hoverVal = $derived(activeIdx !== null && data[activeIdx] ? formatY(data[activeIdx].value) : '');

  function onMouseMove(e: MouseEvent) {
    if (!xScale || !yScale || data.length < 2) return;
    const svg = (e.currentTarget as SVGElement);
    const rect = svg.getBoundingClientRect();
    // Convert rendered-pixel mouse position into SVG viewBox units before
    // subtracting the PAD (which is expressed in SVG units, not pixels).
    // Without this scaling, charts whose CSS width differs from the viewBox
    // width compute the wrong index and emit misaligned hover positions.
    const scaleX = rect.width > 0 ? width / rect.width : 1;
    const mx = (e.clientX - rect.left) * scaleX - PAD.left;
    const ratio = mx / innerW;
    const clamped = Math.max(0, Math.min(data.length - 1, Math.round(ratio * (data.length - 1))));
    _hoverIdx = clamped;
    onHover?.(clamped);
  }

  function onMouseLeave() {
    _hoverIdx = null;
    onHoverEnd?.();
  }
</script>

<div class="chart-card" class:compact>
  {#if !compact}
    <div class="chart-header">
      <span class="chart-label">{label}</span>
      <span class="chart-current">{current}</span>
    </div>
  {/if}

  {#if data.length < 2}
    {#if !compact}
      <div class="chart-empty" style:height="{height}px">collecting…</div>
    {/if}
  {:else}
    <svg
      {width}
      {height}
      viewBox="0 0 {width} {height}"
      onmousemove={compact ? undefined : onMouseMove}
      onmouseleave={compact ? undefined : onMouseLeave}
      role="img"
      aria-label="{label} trend chart"
    >
      <defs>
        <linearGradient id="grad-{label.replace(/\s/g,'')}" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color={color} stop-opacity="0.18" />
          <stop offset="100%" stop-color={color} stop-opacity="0.01" />
        </linearGradient>
        <clipPath id="clip-{label.replace(/\s/g,'')}">
          <rect x="0" y="0" width={innerW} height={innerH} />
        </clipPath>
      </defs>

      <g transform="translate({PAD.left},{PAD.top})">
        {#if !compact}
        <!-- Y grid lines + labels -->
        {#each yTicks as tick}
          <line
            x1="0" y1={tick.y}
            x2={innerW} y2={tick.y}
            stroke="var(--color-border-primary)"
            stroke-width="1"
          />
          <text
            x="-6" y={tick.y}
            text-anchor="end"
            dominant-baseline="middle"
            font-size="9"
            fill="var(--color-text-tertiary)"
          >{formatY(tick.v)}</text>
        {/each}

        <!-- X axis labels -->
        {#each xTicks as tick}
          <text
            x={tick.x}
            y={innerH + 14}
            text-anchor="middle"
            font-size="9"
            fill="var(--color-text-tertiary)"
          >{tick.label}</text>
        {/each}
        {/if}

        <!-- Area fill + line (clipped) -->
        <g clip-path="url(#clip-{label.replace(/\s/g,'')})">
          <path d={areaD} fill="url(#grad-{label.replace(/\s/g,'')})" />
          <path
            d={pathD}
            fill="none"
            stroke={color}
            stroke-width={strokeWidth}
            stroke-linejoin="round"
            stroke-linecap="round"
          />
        </g>

        <!-- Hover crosshair -->
        {#if !compact && activeIdx !== null}
          <line
            x1={hoverX} y1="0"
            x2={hoverX} y2={innerH}
            stroke={color}
            stroke-width="1"
            stroke-dasharray="3 2"
            opacity="0.6"
          />
          <circle cx={hoverX} cy={hoverY} r="3" fill={color} />
          <!-- Tooltip bubble -->
          {@const tipX = hoverX > innerW * 0.75 ? hoverX - 52 : hoverX + 8}
          <rect x={tipX} y={hoverY - 11} width="48" height="16" rx="3"
            fill="var(--color-bg-primary)" stroke={color} stroke-width="0.5" opacity="0.9" />
          <text x={tipX + 4} y={hoverY + 1} font-size="9" fill="var(--color-text-primary)">
            {hoverVal}
          </text>
        {/if}
      </g>
    </svg>
  {/if}
</div>

<style>
  .chart-card {
    flex: 1;
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-lg);
    padding: 12px 14px 8px;
    min-width: 0;
  }

  .chart-card.compact {
    background: none;
    border: none;
    border-radius: 0;
    padding: 0;
  }

  .chart-header {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 6px;
  }

  .chart-label {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--color-text-tertiary);
  }

  .chart-current {
    font-size: 18px;
    font-weight: 600;
    color: var(--color-text-primary);
  }

  .chart-empty {
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 11px;
    color: var(--color-text-tertiary);
  }

  svg {
    display: block;
    width: 100%;
    height: auto;
    cursor: crosshair;
    overflow: visible;
  }

  .chart-card.compact svg {
    height: 20px;
  }
</style>

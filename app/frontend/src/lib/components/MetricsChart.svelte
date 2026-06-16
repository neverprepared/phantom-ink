<script lang="ts">
  import { linearScale, extent, arrayMax, niceCeiling, ticks, monotonePath } from './chartMath';

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
    const [t0, t1] = extent(data, d => d.ts * 1000);
    return linearScale(t0, t1, 0, innerW);
  });

  let yCeiling = $derived.by(() => {
    const maxVal = arrayMax(data, d => d.value);
    return niceCeiling(maxVal > 0 ? maxVal * 1.1 : 1);
  });

  let yScale = $derived.by(() => {
    if (data.length < 2) return null;
    return linearScale(0, yCeiling, innerH, 0);
  });

  let points = $derived.by(() => {
    if (!xScale || !yScale) return [];
    return data.map(d => ({ x: xScale(d.ts * 1000), y: yScale(d.value) }));
  });

  let pathD = $derived(monotonePath(points));

  let areaD = $derived.by(() => {
    if (points.length < 2) return '';
    const last = points[points.length - 1];
    const first = points[0];
    return `${pathD}L${last.x},${innerH}L${first.x},${innerH}Z`;
  });

  // Y axis ticks — 3 evenly spaced over [0, yCeiling]
  let yTicks = $derived.by(() => {
    if (!yScale) return [];
    return ticks(0, yCeiling, 3).map(v => ({ v, y: yScale!(v) }));
  });

  // X axis ticks — time labels at 4 evenly spaced points
  let xTicks = $derived.by(() => {
    if (!xScale || data.length < 2) return [];
    const [t0, t1] = extent(data, d => d.ts * 1000);
    return ticks(t0, t1, 4).map(t => ({
      t,
      x: xScale!(t),
      label: new Date(t).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    }));
  });

  // Hover crosshair — internal index (from mouse), overridden by external hoverIdx prop.
  // The vertical line tracks the cursor's exact x position so it always sits under
  // the pointer regardless of sample density. Only the data-point dot + tooltip snap
  // to the nearest sample's time, so hover feedback stays informative without lag.
  let _hoverIdx = $state<number | null>(null);
  // Cursor x in viewBox units. Drives the vertical line directly so it never lags.
  let _hoverCursorX = $state<number | null>(null);
  let activeIdx = $derived(hoverIdx !== undefined ? hoverIdx : _hoverIdx);
  let cursorX  = $derived.by(() => {
    if (_hoverCursorX !== null) return _hoverCursorX;
    // Externally-driven hover (sibling chart): fall back to the sample's x.
    if (activeIdx !== null && xScale && data[activeIdx]) return xScale(data[activeIdx].ts * 1000);
    return 0;
  });
  let hoverX  = $derived(activeIdx !== null && xScale && data[activeIdx] ? xScale(data[activeIdx].ts * 1000) : 0);
  let hoverY  = $derived(activeIdx !== null && yScale && data[activeIdx] ? yScale(data[activeIdx].value) : 0);
  let hoverVal = $derived(activeIdx !== null && data[activeIdx] ? formatY(data[activeIdx].value) : '');

  /** Binary-search the closest sample to the given target time (epoch ms). */
  function nearestIndexByTime(targetMs: number): number {
    if (data.length === 0) return 0;
    if (data.length === 1) return 0;
    let lo = 0, hi = data.length - 1;
    while (lo < hi) {
      const m = (lo + hi) >> 1;
      const mt = data[m].ts * 1000;
      if (mt < targetMs) lo = m + 1; else hi = m;
    }
    // lo now lands at the first sample ≥ target. Compare with lo-1 to pick the closer one.
    if (lo > 0) {
      const aDist = Math.abs(data[lo - 1].ts * 1000 - targetMs);
      const bDist = Math.abs(data[lo].ts * 1000 - targetMs);
      if (aDist < bDist) return lo - 1;
    }
    return lo;
  }

  function onMouseMove(e: MouseEvent) {
    if (!xScale || !yScale || data.length < 2) return;
    const svg = (e.currentTarget as SVGElement);
    const rect = svg.getBoundingClientRect();
    // Convert rendered-pixel mouse position into SVG viewBox units before
    // subtracting the PAD (which is expressed in SVG units, not pixels).
    const scaleX = rect.width > 0 ? width / rect.width : 1;
    const mxRaw = (e.clientX - rect.left) * scaleX - PAD.left;
    const mx = Math.max(0, Math.min(innerW, mxRaw));
    _hoverCursorX = mx;
    // Pick the nearest sample by time (robust against non-uniform spacing).
    const [t0, t1] = extent(data, d => d.ts * 1000);
    const targetTime = t0 + (mx / innerW) * (t1 - t0);
    const idx = nearestIndexByTime(targetTime);
    _hoverIdx = idx;
    onHover?.(idx);
  }

  function onMouseLeave() {
    _hoverIdx = null;
    _hoverCursorX = null;
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

        <!-- Hover crosshair: vertical line follows the cursor exactly; the
             data-point dot + tooltip snap to the nearest sample. -->
        {#if !compact && activeIdx !== null}
          <line
            x1={cursorX} y1="0"
            x2={cursorX} y2={innerH}
            stroke={color}
            stroke-width="1"
            stroke-dasharray="3 2"
            opacity="0.6"
          />
          <circle cx={hoverX} cy={hoverY} r="3" fill={color} />
          <!-- Tooltip bubble anchored to the snapped sample, not the cursor,
               so the value text doesn't shimmer between samples. -->
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

<script lang="ts">
  import { pieLayout, arcPath } from './chartMath';

  interface Slice {
    name: string;
    value: number;
    label: string;
  }

  let {
    slices = [],
    size = 180,
    innerRadius = 0.55,
    colors,
  }: {
    slices: Slice[];
    size?: number;
    innerRadius?: number;
    colors?: string[];
  } = $props();

  const defaultColors = [
    'var(--color-accent)',
    'var(--color-info)',
    'var(--color-success)',
    '#d8b4fe',
    '#f472b6',
    '#fb923c',
    '#a78bfa',
    '#34d399',
    'var(--color-text-tertiary)',
  ];

  let palette = $derived(colors ?? defaultColors);

  let outerR = $derived(size / 2 - 2);
  let innerR = $derived((size / 2) * innerRadius);

  let pie = $derived(pieLayout(slices, d => d.value, 0.02));

  let hoveredIdx = $state<number | null>(null);

  function colorFor(idx: number): string {
    return palette[idx % palette.length];
  }
</script>

<div class="pie-container" style="width:{size}px; height:{size}px">
  <svg width={size} height={size} viewBox="0 0 {size} {size}">
    <g transform="translate({size / 2},{size / 2})">
      {#each pie as d, i (d.data.name)}
        <path
          d={arcPath(d.startAngle, d.endAngle, innerR, outerR)}
          fill={colorFor(i)}
          opacity={hoveredIdx === null || hoveredIdx === i ? 1 : 0.4}
          onmouseenter={() => hoveredIdx = i}
          onmouseleave={() => hoveredIdx = null}
          style="transition: opacity 0.15s; cursor: default;"
        />
      {/each}
    </g>
  </svg>
  {#if hoveredIdx !== null && pie[hoveredIdx]}
    <div class="pie-tooltip">
      <span class="tooltip-name">{pie[hoveredIdx].data.name}</span>
      <span class="tooltip-val">{pie[hoveredIdx].data.label}</span>
    </div>
  {/if}
</div>

<style>
  .pie-container {
    position: relative;
    flex-shrink: 0;
  }

  .pie-tooltip {
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    text-align: center;
    pointer-events: none;
  }

  .tooltip-name {
    display: block;
    font-size: 11px;
    font-weight: 600;
    color: var(--color-text-primary);
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }

  .tooltip-val {
    display: block;
    font-size: 14px;
    font-weight: 600;
    color: var(--color-text-primary);
    font-family: var(--font-mono);
  }
</style>

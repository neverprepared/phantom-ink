<script lang="ts">
  import { scaleLinear } from 'd3-scale';
  import { area, curveMonotoneX } from 'd3-shape';
  import { max } from 'd3-array';

  interface Sample {
    ts: number;
    value: number;
  }

  interface Props {
    data: Sample[];
    label: string;
    color?: string;
    bands?: number;
    height?: number;
  }

  let {
    data,
    label,
    color = 'var(--color-accent)',
    bands = 3,
    height = 20,
  }: Props = $props();

  const VB_W = 300;

  let maxVal = $derived(Math.max(max(data, d => d.value) ?? 1, 0.001));
  let bandSize = $derived(maxVal / bands);

  let xScale = $derived.by(() => {
    if (data.length < 2) return null;
    return scaleLinear().domain([0, data.length - 1]).range([0, VB_W]);
  });

  let bandPaths = $derived.by(() => {
    if (!xScale || data.length < 2) return [];

    const yScale = scaleLinear().domain([0, bandSize]).range([height, 0]).clamp(true);

    return Array.from({ length: bands }, (_, i) => {
      const offset = i * bandSize;

      const gen = area<Sample>()
        .x((_d, idx) => xScale!(idx))
        .y0(height)
        .y1((d) => {
          const shifted = d.value - offset;
          const clamped = Math.max(0, Math.min(bandSize, shifted));
          return yScale(clamped);
        })
        .curve(curveMonotoneX);

      return {
        d: gen(data) ?? '',
        opacity: 0.3 + (i * 0.35),
      };
    });
  });
</script>

<svg
  viewBox="0 0 {VB_W} {height}"
  preserveAspectRatio="none"
  role="img"
  aria-label="{label} horizon chart"
>
  <defs>
    <clipPath id="hclip-{label.replace(/\s/g,'')}">
      <rect x="0" y="0" width={VB_W} height={height} />
    </clipPath>
  </defs>
  <g clip-path="url(#hclip-{label.replace(/\s/g,'')})">
    {#each bandPaths as band, i (i)}
      <path d={band.d} fill={color} opacity={band.opacity} />
    {/each}
  </g>
</svg>

<style>
  svg {
    display: block;
    width: 100%;
    height: 20px;
    border-radius: 3px;
  }
</style>

<script lang="ts">
  import { linearScale, arrayMax, monotonePath, type Pt } from './chartMath';

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

  let maxVal = $derived(Math.max(arrayMax(data, d => d.value), 0.001));
  let bandSize = $derived(maxVal / bands);

  let xScale = $derived.by(() => {
    if (data.length < 2) return null;
    return linearScale(0, data.length - 1, 0, VB_W);
  });

  let bandPaths = $derived.by(() => {
    if (!xScale || data.length < 2) return [];

    const yToPx = linearScale(0, bandSize, height, 0);
    const clampedY = (v: number) => yToPx(Math.max(0, Math.min(bandSize, v)));

    return Array.from({ length: bands }, (_, i) => {
      const offset = i * bandSize;
      // Each band's path follows the value above its lower bound, clamped to
      // the band height. Stack same-color paths with rising opacity to fake
      // the d3 horizon look without d3's area generator.
      const pts: Pt[] = data.map((d, idx) => ({
        x: xScale!(idx),
        y: clampedY(d.value - offset),
      }));
      const top = monotonePath(pts);
      const last = pts[pts.length - 1];
      const first = pts[0];
      const d = `${top}L${last.x},${height}L${first.x},${height}Z`;
      return {
        d,
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

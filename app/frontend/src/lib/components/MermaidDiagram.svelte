<script lang="ts">
  /**
   * Renders a mermaid diagram string into inline SVG.
   *
   * Used by LoopsPanel and LoopRunsPanel to display the persisted
   * mermaid graph for a loop template / instance.
   */
  import { onMount } from 'svelte';
  import mermaid from 'mermaid';

  interface Props {
    source: string;
    /** Render the SVG at this fraction of its natural size. 1.0 = native;
     *  0.5 ≈ half scale (denser overview). Layout space is reclaimed —
     *  the wrap shrinks with the SVG so it doesn't leave empty padding. */
    scale?: number;
  }

  let { source, scale = 1.0 }: Props = $props();

  let container: HTMLDivElement;
  let initialized = false;
  let renderCounter = 0;
  let errorMessage = $state<string | null>(null);

  function ensureInit() {
    if (initialized) return;
    mermaid.initialize({ startOnLoad: false, theme: 'dark', securityLevel: 'loose' });
    initialized = true;
  }

  async function render() {
    if (!container) return;
    const src = (source ?? '').trim();
    if (!src) {
      container.innerHTML = '';
      errorMessage = null;
      return;
    }
    ensureInit();
    renderCounter += 1;
    const id = `mermaid-diagram-${Date.now()}-${renderCounter}`;
    try {
      const { svg } = await mermaid.render(id, src);
      container.innerHTML = svg;
      errorMessage = null;
    } catch (err) {
      container.innerHTML = '';
      errorMessage = err instanceof Error ? err.message : String(err);
    }
  }

  onMount(() => {
    void render();
  });

  $effect(() => {
    // Re-render whenever source changes
    source;
    void render();
  });
</script>

<div class="mermaid-wrap">
  {#if !source || !source.trim()}
    <div class="placeholder">No diagram available</div>
  {:else if errorMessage}
    <div class="error">Diagram render failed: {errorMessage}</div>
  {/if}
  <div
    class="diagram"
    bind:this={container}
    style:--scale={scale}
  ></div>
</div>

<style>
  .mermaid-wrap {
    display: flex;
    flex-direction: column;
    gap: 6px;
    width: 100%;
  }
  .diagram {
    width: 100%;
    overflow: auto;
    /* Center the SVG horizontally — at small scales the diagram is
       narrower than the container, so this stops it hugging the left. */
    display: flex;
    justify-content: center;
    align-items: flex-start;
  }
  .diagram :global(svg) {
    /* `--scale` is multiplied into max-width so the SVG actually shrinks
       its layout box (not just its visual paint). max-width 50% at
       scale=1 still falls back to a sane size; scale=0.5 gives ~half. */
    max-width: calc(100% * var(--scale, 1));
    height: auto;
  }
  .placeholder {
    color: var(--color-text-muted, #888);
    font-size: 12px;
    font-style: italic;
    padding: 8px 0;
  }
  .error {
    color: #ff9a9a;
    font-size: 12px;
    padding: 6px 10px;
    background: rgba(255, 0, 0, 0.05);
    border-left: 2px solid #ff9a9a;
    word-break: break-word;
    font-family: var(--font-mono, monospace);
  }
</style>

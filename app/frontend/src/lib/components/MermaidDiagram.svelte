<script lang="ts">
  /**
   * Renders a mermaid diagram string into inline SVG with built-in
   * zoom controls.
   *
   * Default fit: SVG width = 100% of the wrap so the whole diagram is
   * visible without scrolling on first paint. ``+`` zooms in,
   * ``-`` zooms out, ``Fit`` returns to 100%.
   *
   * Used by LoopsPanel and LoopRunsPanel.
   */
  import { onMount } from 'svelte';
  import mermaid from 'mermaid';

  interface Props {
    source: string;
    /** Initial zoom level. 1.0 = fit-to-container. The operator can
     *  still zoom in/out from there via the controls. */
    initialZoom?: number;
  }

  let { source, initialZoom = 1.0 }: Props = $props();

  const ZOOM_MIN = 0.25;
  const ZOOM_MAX = 4.0;
  const ZOOM_STEP = 1.25;

  let zoom = $state(initialZoom);
  let container: HTMLDivElement;
  let initialized = false;
  let renderCounter = 0;
  let errorMessage = $state<string | null>(null);

  function ensureInit() {
    if (initialized) return;
    // 'default' is mermaid's stock palette — colored node fills, the
    // operator gets the standard graph-engineering visual language
    // (pink decisions, blue boxes, green terminals) rather than a
    // washed-out black/white render. Works across phantom-ink themes
    // because the SVG carries its own fills and never inherits from
    // the page.
    mermaid.initialize({ startOnLoad: false, theme: 'default', securityLevel: 'loose' });
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

  function zoomIn() {
    zoom = Math.min(ZOOM_MAX, Math.round(zoom * ZOOM_STEP * 100) / 100);
  }
  function zoomOut() {
    zoom = Math.max(ZOOM_MIN, Math.round((zoom / ZOOM_STEP) * 100) / 100);
  }
  function resetZoom() {
    zoom = 1.0;
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
  <div class="zoom-controls" aria-label="Diagram zoom">
    <button
      class="zoom-btn"
      type="button"
      onclick={zoomOut}
      disabled={zoom <= ZOOM_MIN + 0.001}
      title="Zoom out"
      aria-label="Zoom out"
    >−</button>
    <button
      class="zoom-reset"
      type="button"
      onclick={resetZoom}
      title="Fit to container"
      aria-label="Fit diagram to container"
    >{Math.round(zoom * 100)}%</button>
    <button
      class="zoom-btn"
      type="button"
      onclick={zoomIn}
      disabled={zoom >= ZOOM_MAX - 0.001}
      title="Zoom in"
      aria-label="Zoom in"
    >+</button>
  </div>
  {#if !source || !source.trim()}
    <div class="placeholder">No diagram available</div>
  {:else if errorMessage}
    <div class="error">Diagram render failed: {errorMessage}</div>
  {/if}
  <div
    class="diagram"
    bind:this={container}
    style:--zoom={zoom}
  ></div>
</div>

<style>
  .mermaid-wrap {
    display: flex;
    flex-direction: column;
    gap: 6px;
    width: 100%;
    height: 100%;
    min-height: 0;
    position: relative;
  }
  .zoom-controls {
    /* Float on top of the diagram so the controls don't push the
       diagram off-screen. */
    position: absolute;
    top: 4px;
    right: 4px;
    z-index: 2;
    display: inline-flex;
    align-items: center;
    gap: 2px;
    padding: 2px;
    background: color-mix(in srgb, var(--bg-elev) 88%, transparent);
    border: 1px solid var(--border);
    border-radius: var(--r-sm);
    box-shadow: var(--shadow-sm);
    font-family: var(--font-mono);
  }
  .zoom-btn,
  .zoom-reset {
    background: transparent;
    border: none;
    color: var(--text-muted);
    cursor: pointer;
    padding: 3px 8px;
    font-size: 13px;
    border-radius: 3px;
    transition: background-color 0.12s, color 0.12s;
  }
  .zoom-btn { font-weight: 700; line-height: 1; min-width: 22px; }
  .zoom-reset { font-size: 11px; min-width: 42px; }
  .zoom-btn:hover:not(:disabled),
  .zoom-reset:hover {
    background: var(--bg-hover);
    color: var(--text);
  }
  .zoom-btn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
  .diagram {
    width: 100%;
    height: 100%;
    min-height: 0;
    overflow: auto;
    display: flex;
    justify-content: center;
    align-items: flex-start;
  }
  .diagram :global(svg) {
    /* zoom=1 fits the container (max-width 100%). zoom>1 grows the SVG
       past the container so the .diagram div scrolls; zoom<1 shrinks it
       to a fraction of the container. Width is the lever (height auto
       follows via the SVG's intrinsic aspect ratio). */
    width: calc(100% * var(--zoom, 1));
    max-width: none;
    height: auto;
    flex-shrink: 0;
  }
  .placeholder {
    color: var(--text-muted);
    font-size: 12px;
    font-style: italic;
    padding: 8px 0;
  }
  .error {
    color: var(--fail);
    font-size: 12px;
    padding: 6px 10px;
    background: var(--fail-soft);
    border-left: 2px solid var(--fail);
    word-break: break-word;
    font-family: var(--font-mono);
  }
</style>

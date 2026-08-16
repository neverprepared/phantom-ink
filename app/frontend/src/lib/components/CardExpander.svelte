<script lang="ts">
  import type { Snippet } from 'svelte';

  // Uniform collapsible section for resource cards: chevron + lowercase label
  // + optional mono count, body indented under the toggle. Replaces the
  // per-panel bespoke toggle styles (gw-toggle, pse-toggle, logs-toggle).
  let {
    label,
    count = '',
    hint = '',
    description = '',
    open = $bindable(false),
    onOpen,
    children,
  }: {
    label: string;
    count?: string;
    hint?: string;         // brief one-liner shown next to the label in the header
    description?: string;  // longer explanation shown at the top of the body when open
    open?: boolean;
    onOpen?: () => void;
    children?: Snippet;
  } = $props();

  function toggle() {
    open = !open;
    if (open) onOpen?.();
  }
</script>

<div class="expander">
  <button class="expander-toggle" onclick={toggle} aria-expanded={open}>
    <svg class="chevron" class:open width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg>
    <span class="expander-label">{label}</span>
    {#if count}<span class="expander-count">{count}</span>{/if}
    {#if hint && !open}<span class="expander-hint">{hint}</span>{/if}
  </button>
  {#if open}
    <div class="expander-body">
      {#if description}<p class="expander-desc">{description}</p>{/if}
      {@render children?.()}
    </div>
  {/if}
</div>

<style>
  .expander { display: flex; flex-direction: column; }
  .expander-toggle {
    display: flex;
    align-items: center;
    gap: 6px;
    width: 100%;
    background: none;
    border: none;
    padding: 4px 0;
    font-size: 11px;
    color: var(--color-text-tertiary, var(--text-muted));
    cursor: pointer;
    text-align: left;
    transition: color 0.15s;
  }
  .expander-toggle:hover { color: var(--color-text-secondary, var(--text)); }
  .chevron { flex-shrink: 0; transition: transform 0.15s; }
  .chevron.open { transform: rotate(90deg); }
  .expander-count {
    color: var(--color-text-muted, var(--text-faint));
    font-family: var(--font-mono, monospace);
    font-size: 10.5px;
    flex-shrink: 0;
  }
  /* Brief one-liner in the header (collapsed state); takes the remaining width
     and ellipsis-truncates so a long hint never wraps or pushes the layout. */
  .expander-hint {
    color: var(--color-text-muted, var(--text-faint));
    font-size: 10.5px;
    font-style: italic;
    flex: 1 1 auto;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .expander-body { padding: 2px 0 4px 16px; }
  /* Longer explanation shown once the card is expanded. */
  .expander-desc {
    margin: 0 0 6px;
    font-size: 11px;
    line-height: 1.45;
    color: var(--color-text-secondary, var(--text-muted));
  }
</style>

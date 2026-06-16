<script lang="ts">
  // Lightweight floating context menu. Caller controls visibility + position
  // (typically from a contextmenu event on a parent row). The menu closes on
  // any outside click, escape, or after one of its items fires.

  import { onMount } from 'svelte';

  export interface MenuItem {
    label: string;
    onClick: () => void;
    danger?: boolean;
    disabled?: boolean;
  }

  let {
    open = false,
    x = 0,
    y = 0,
    items = [],
    onClose,
  }: {
    open: boolean;
    x: number;
    y: number;
    items: MenuItem[];
    onClose: () => void;
  } = $props();

  let menuEl: HTMLDivElement | undefined = $state();

  // Clamp x/y so the menu never spills off-screen (rough estimate of size).
  let clampedX = $derived(Math.min(x, (typeof window === 'undefined' ? 9999 : window.innerWidth) - 180));
  let clampedY = $derived(Math.min(y, (typeof window === 'undefined' ? 9999 : window.innerHeight) - items.length * 32 - 20));

  onMount(() => {
    function handleDown(e: MouseEvent) {
      if (!open || !menuEl) return;
      if (!menuEl.contains(e.target as Node)) onClose();
    }
    function handleKey(e: KeyboardEvent) {
      if (open && e.key === 'Escape') onClose();
    }
    window.addEventListener('mousedown', handleDown, true);
    window.addEventListener('keydown', handleKey);
    return () => {
      window.removeEventListener('mousedown', handleDown, true);
      window.removeEventListener('keydown', handleKey);
    };
  });
</script>

{#if open}
  <div
    bind:this={menuEl}
    class="ctx-menu"
    role="menu"
    style="left: {clampedX}px; top: {clampedY}px;">
    {#each items as item (item.label)}
      <button
        class="ctx-item"
        class:danger={item.danger}
        disabled={item.disabled}
        onclick={() => { item.onClick(); onClose(); }}>
        {item.label}
      </button>
    {/each}
  </div>
{/if}

<style>
  .ctx-menu {
    position: fixed;
    z-index: 9999;
    min-width: 170px;
    background: var(--bg-elev, var(--color-bg-secondary));
    border: 1px solid var(--border, var(--color-border-primary));
    border-radius: var(--r-md, var(--radius-md));
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.18);
    padding: 4px;
    display: flex;
    flex-direction: column;
    gap: 1px;
  }
  .ctx-item {
    text-align: left;
    background: transparent;
    border: none;
    color: var(--text, var(--color-text-primary));
    padding: 6px 10px;
    border-radius: var(--r-sm, var(--radius-sm));
    font-size: 12.5px;
    font-family: inherit;
    cursor: pointer;
    transition: background 0.1s;
  }
  .ctx-item:hover:not(:disabled) {
    background: var(--bg-hover, var(--color-surface-hover));
  }
  .ctx-item:disabled {
    color: var(--text-faint, var(--color-text-tertiary));
    cursor: not-allowed;
  }
  .ctx-item.danger { color: var(--color-error, #b71c1c); }
</style>

<script lang="ts">
  import { onMount } from 'svelte';

  let { onClose, children }: { onClose: () => void; children: () => any } = $props();

  let dialogEl: HTMLDivElement;

  onMount(() => {
    const prev = document.activeElement as HTMLElement;
    const first = dialogEl?.querySelector<HTMLElement>('input, button, select, textarea');
    first?.focus();
    return () => prev?.focus();
  });

  function handleBackdrop(e: MouseEvent) {
    if (e.target === e.currentTarget) onClose();
  }

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') onClose();
  }
</script>

<svelte:window onkeydown={handleKeydown} />

<!-- svelte-ignore a11y_click_events_have_key_events -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="backdrop" onclick={handleBackdrop} role="dialog" aria-modal="true" tabindex="-1">
  <div class="modal" bind:this={dialogEl}>
    {@render children()}
  </div>
</div>

<style>
  .backdrop {
    position: fixed;
    inset: 0;
    background: var(--color-overlay);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 500;
    padding: 24px;
  }

  .modal {
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-secondary);
    border-radius: var(--radius-xl);
    padding: 24px;
    width: 100%;
    max-width: 480px;
    max-height: 80vh;
    overflow-y: auto;
    box-shadow: var(--shadow-modal);
    animation: appear 0.15s ease;
  }

  @keyframes appear {
    from { opacity: 0; transform: scale(0.97); }
    to   { opacity: 1; transform: scale(1); }
  }
</style>

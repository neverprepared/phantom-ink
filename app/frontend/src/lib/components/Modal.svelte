<script lang="ts">
  import { onMount } from 'svelte';

  let {
    onClose,
    children,
    maxWidth = '480px',
    labelledby,
  }: {
    onClose: () => void;
    children: () => any;
    maxWidth?: string;
    /** id of the element naming this dialog, for aria-labelledby. */
    labelledby?: string;
  } = $props();

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

  // Everything focusable inside the dialog, in document order. Queried per
  // keypress rather than cached because dialog content is dynamic.
  function focusable(): HTMLElement[] {
    if (!dialogEl) return [];
    return Array.from(
      dialogEl.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ),
    ).filter((el) => el.offsetParent !== null || el === document.activeElement);
  }

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') {
      onClose();
      return;
    }
    if (e.key !== 'Tab') return;

    // Keep Tab inside the dialog. Without this, aria-modal="true" is a lie:
    // Tab walks out past the last field into the panel behind the scrim, where
    // a keyboard user can focus and activate a destructive row action they
    // cannot even see.
    const items = focusable();
    if (items.length === 0) {
      e.preventDefault();
      dialogEl?.focus();
      return;
    }
    const first = items[0];
    const last = items[items.length - 1];
    const active = document.activeElement as HTMLElement | null;

    if (!active || !dialogEl.contains(active)) {
      e.preventDefault();
      (e.shiftKey ? last : first).focus();
      return;
    }
    if (e.shiftKey && active === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && active === last) {
      e.preventDefault();
      first.focus();
    }
  }
</script>

<svelte:window onkeydown={handleKeydown} />

<!-- svelte-ignore a11y_click_events_have_key_events -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="backdrop" onclick={handleBackdrop}>
  <div
    class="modal"
    bind:this={dialogEl}
    role="dialog"
    aria-modal="true"
    aria-labelledby={labelledby}
    tabindex="-1"
    style="max-width:{maxWidth}"
  >
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
    max-width: var(--modal-max-width, 480px); /* overridden by maxWidth prop via inline style */
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

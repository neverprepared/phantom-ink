<script lang="ts">
  /**
   * Renders the pending confirmAsk() request. Mounted once at the app root;
   * see lib/confirm.svelte.ts for why window.confirm() cannot be used here.
   *
   * Uses the shared .btn-danger / .btn-ghost variants from styles/buttons.css
   * rather than redefining button styling locally.
   */
  import Modal from './Modal.svelte';
  import { confirmDialog } from '../confirm.svelte';

  const req = $derived(confirmDialog.pending);
</script>

{#if req}
  <Modal onClose={() => confirmDialog.answer(false)} maxWidth="420px" labelledby="confirm-title">
    <h3 id="confirm-title" class="title">{req.title}</h3>
    <p class="message">{req.message}</p>
    <div class="actions">
      <button class="btn-ghost" onclick={() => confirmDialog.answer(false)}>Cancel</button>
      <button
        class={req.danger ? 'btn-danger' : 'btn-primary'}
        onclick={() => confirmDialog.answer(true)}
      >{req.confirmLabel}</button>
    </div>
  </Modal>
{/if}

<style>
  .title {
    margin: 0 0 8px;
    font-size: 14px;
    font-weight: 600;
    color: var(--color-text-primary);
  }

  .message {
    margin: 0 0 20px;
    font-size: 13px;
    line-height: 1.5;
    color: var(--color-text-secondary);
    /* Prompts carry embedded newlines; keep them. */
    white-space: pre-wrap;
  }

  .actions {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
  }
</style>

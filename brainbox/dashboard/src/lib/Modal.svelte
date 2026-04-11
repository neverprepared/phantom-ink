<script>
  let { onClose, children } = $props();

  function handleKeydown(e) {
    if (e.key === 'Escape') onClose();
  }

  function handleOverlayClick(e) {
    if (e.target === e.currentTarget) onClose();
  }
</script>

<svelte:window onkeydown={handleKeydown} />

<!-- svelte-ignore a11y_click_events_have_key_events -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="modal-overlay" onclick={handleOverlayClick}>
  <div class="modal">
    {@render children()}
  </div>
</div>

<style>
  .modal-overlay {
    position: fixed;
    top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(0, 0, 0, 0.6);
    backdrop-filter: blur(4px);
    z-index: 100;
    display: flex;
    justify-content: center;
    align-items: center;
  }
  .modal {
    background: #111827;
    border: 1px solid #1e293b;
    border-radius: 12px;
    padding: 28px;
    min-width: 420px;
    max-width: 90vw;
    box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5);
  }

  /* Global modal styles for use by children */
  :global(.modal h2) {
    font-size: 18px;
    font-weight: 600;
    margin-bottom: 6px;
    color: #e2e8f0;
  }
  :global(.modal-subtitle) {
    font-size: 13px;
    color: #64748b;
    margin-bottom: 24px;
  }
  :global(.modal-field) {
    margin-bottom: 16px;
  }
  :global(.modal-field label) {
    display: block;
    font-size: 12px;
    color: #94a3b8;
    margin-bottom: 6px;
    text-transform: uppercase;
    letter-spacing: 0.03em;
  }
  :global(.modal-field input[type="text"]) {
    width: 100%;
    padding: 10px 12px;
    background: #0a0e1a;
    border: 1px solid #1e293b;
    border-radius: 6px;
    color: #e2e8f0;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, monospace;
    font-size: 14px;
    transition: border-color 0.15s;
  }
  :global(.modal-field input[type="text"]:focus) {
    outline: none;
    border-color: #3b82f6;
  }
  :global(.modal-field input[type="text"]::placeholder) {
    color: #374151;
  }
  :global(.modal-field input[type="text"].error) {
    border-color: #ef4444;
  }
  :global(.modal-field select) {
    width: 100%;
    padding: 10px 12px;
    background: #0a0e1a;
    border: 1px solid #1e293b;
    border-radius: 6px;
    color: #e2e8f0;
    font-family: inherit;
    font-size: 14px;
    cursor: pointer;
    appearance: auto;
  }
  :global(.modal-field select:focus) {
    outline: none;
    border-color: #3b82f6;
  }
  :global(.modal-hint) {
    font-size: 11px;
    color: #64748b;
    margin-top: 4px;
  }
  :global(.modal-hint.error) {
    color: #f87171;
  }
  :global(.modal-field-checkbox) {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 16px;
  }
  :global(.modal-field-checkbox input[type="checkbox"]) {
    width: 16px;
    height: 16px;
    accent-color: #3b82f6;
    color-scheme: dark;
  }
  :global(.modal-field-checkbox label) {
    font-size: 14px;
    color: #e2e8f0;
    cursor: pointer;
  }
  :global(.modal-actions) {
    display: flex;
    justify-content: flex-end;
    gap: 12px;
    margin-top: 24px;
  }
  :global(.modal-cancel) {
    background: transparent;
    border: 1px solid #1e293b;
    color: #94a3b8;
    padding: 8px 16px;
    border-radius: 6px;
    cursor: pointer;
    font-family: inherit;
    font-size: 14px;
    transition: all 0.15s;
  }
  :global(.modal-cancel:hover) {
    background: #1e293b;
    color: #e2e8f0;
  }
  :global(.modal-submit) {
    background: #3b82f6;
    border: 1px solid #3b82f6;
    color: #fff;
    padding: 8px 16px;
    border-radius: 6px;
    cursor: pointer;
    font-family: inherit;
    font-size: 14px;
    font-weight: 500;
    transition: all 0.15s;
  }
  :global(.modal-submit:hover) {
    background: #2563eb;
  }
</style>

<script lang="ts">
  let { title = 'Nothing here yet', message = '' }: { title?: string; message?: string } = $props();
</script>

<div class="empty">
  <div class="empty-icon">
    <svg xmlns="http://www.w3.org/2000/svg" width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/></svg>
  </div>
  <p class="empty-title">{title}</p>
  {#if message}
    <p class="empty-msg">{message}</p>
  {/if}
</div>

<style>
  .empty {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 60px 24px;
    text-align: center;
    color: var(--color-text-tertiary);
  }

  .empty-icon {
    opacity: 0.3;
    margin-bottom: 16px;
  }

  .empty-title {
    font-size: 15px;
    font-weight: 500;
    color: var(--color-text-secondary);
    margin-bottom: 8px;
  }

  .empty-msg {
    font-size: 13px;
    max-width: 320px;
    line-height: 1.6;
  }
</style>

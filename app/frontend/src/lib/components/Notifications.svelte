<script lang="ts">
  import { notifications } from '../notifications.svelte';
</script>

{#if notifications.value.length > 0}
  <div class="notifications" aria-live="polite" role="status">
    {#each notifications.value as n (n.id)}
      <div class="notification {n.type}" role="alert">
        <span class="msg">{n.message}</span>
        <button class="dismiss" onclick={() => notifications.dismiss(n.id)} aria-label="Dismiss">×</button>
      </div>
    {/each}
  </div>
{/if}

<style>
  .notifications {
    position: fixed;
    bottom: 40px;
    right: 16px;
    z-index: 1000;
    display: flex;
    flex-direction: column;
    gap: 8px;
    max-width: 380px;
  }

  .notification {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    padding: 10px 12px;
    border-radius: var(--radius-lg);
    font-size: 13px;
    animation: slideIn 0.2s ease;
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.4);
  }

  @keyframes slideIn {
    from { opacity: 0; transform: translateY(8px); }
    to   { opacity: 1; transform: translateY(0); }
  }

  .notification.success {
    background: rgba(16, 185, 129, 0.15);
    border: 1px solid rgba(16, 185, 129, 0.3);
    color: #6ee7b7;
  }

  .notification.error {
    background: rgba(239, 68, 68, 0.15);
    border: 1px solid rgba(239, 68, 68, 0.3);
    color: #fca5a5;
  }

  .notification.info {
    background: rgba(59, 130, 246, 0.15);
    border: 1px solid rgba(59, 130, 246, 0.3);
    color: #93c5fd;
  }

  .notification.warning {
    background: rgba(245, 158, 11, 0.15);
    border: 1px solid rgba(245, 158, 11, 0.3);
    color: #fcd34d;
  }

  .msg {
    flex: 1;
    word-break: break-word;
  }

  .dismiss {
    background: none;
    border: none;
    color: inherit;
    opacity: 0.6;
    padding: 0;
    font-size: 16px;
    line-height: 1;
    cursor: pointer;
    flex-shrink: 0;
  }

  .dismiss:hover { opacity: 1; }
</style>

<script>
  import { notifications } from './notifications.svelte.js';
</script>

<div class="notifications-container" aria-live="polite" aria-atomic="false">
  {#each notifications.value as notification (notification.id)}
    <div
      class="notification"
      data-type={notification.type}
      role="alert"
      aria-label={`${notification.type} notification: ${notification.message}`}
    >
      <span class="icon" aria-hidden="true">
        {#if notification.type === 'success'}✓{/if}
        {#if notification.type === 'error'}✗{/if}
        {#if notification.type === 'warning'}⚠{/if}
        {#if notification.type === 'info'}ℹ{/if}
      </span>
      <span class="message">{notification.message}</span>
      <button class="close" onclick={() => notifications.dismiss(notification.id)} aria-label="Close notification">×</button>
    </div>
  {/each}
</div>

<style>
  .notifications-container {
    position: fixed;
    top: var(--spacing-xl);
    right: var(--spacing-xl);
    z-index: 9999;
    display: flex;
    flex-direction: column;
    gap: var(--spacing-md);
    max-width: 400px;
  }

  .notification {
    display: flex;
    align-items: center;
    gap: var(--spacing-md);
    padding: var(--spacing-md) var(--spacing-lg);
    border-radius: var(--radius-lg);
    background: var(--color-bg-tertiary);
    border: 1px solid var(--color-border-secondary);
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
    transition: all 0.2s ease;
    animation: slideIn 0.3s ease;
  }

  .notification[data-type="success"] {
    border-left: 4px solid var(--color-success);
  }

  .notification[data-type="error"] {
    border-left: 4px solid var(--color-error);
  }

  .notification[data-type="warning"] {
    border-left: 4px solid var(--color-warning);
  }

  .notification[data-type="info"] {
    border-left: 4px solid var(--color-info);
  }

  .icon {
    font-size: 18px;
    font-weight: bold;
    flex-shrink: 0;
  }

  .notification[data-type="success"] .icon {
    color: var(--color-success);
  }

  .notification[data-type="error"] .icon {
    color: var(--color-error);
  }

  .notification[data-type="warning"] .icon {
    color: var(--color-warning);
  }

  .notification[data-type="info"] .icon {
    color: var(--color-info);
  }

  .message {
    flex: 1;
    color: var(--color-text-primary);
    font-size: 14px;
    line-height: 1.5;
  }

  .close {
    background: none;
    border: none;
    color: var(--color-text-secondary);
    font-size: 24px;
    line-height: 1;
    cursor: pointer;
    padding: 0;
    width: 24px;
    height: 24px;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: var(--radius-md);
    transition: all 0.2s ease;
    flex-shrink: 0;
  }

  .close:hover {
    background: rgba(148, 163, 184, 0.1);
    color: var(--color-text-primary);
  }

  @keyframes slideIn {
    from {
      opacity: 0;
      transform: translateX(20px);
    }
    to {
      opacity: 1;
      transform: translateX(0);
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .notification {
      animation: none;
    }
  }

  @media (max-width: 768px) {
    .notifications-container {
      right: 12px;
      left: 12px;
      max-width: none;
    }
  }
</style>

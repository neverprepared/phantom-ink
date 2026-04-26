<script lang="ts">
  let { text, variant = 'default' }: { text: string; variant?: string } = $props();
</script>

<span class="badge {variant}">{text}</span>

<style>
  .badge {
    display: inline-flex;
    align-items: center;
    padding: 1px 6px;
    border-radius: 9999px;
    font-size: 9px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    line-height: 1.4;
    white-space: nowrap;
    background: rgba(100, 116, 139, 0.15);
    color: var(--color-text-tertiary);
    border: 1px solid rgba(100, 116, 139, 0.2);
    flex-shrink: 0;
  }

  /* Status variants */
  .badge.running, .badge.active {
    background: rgba(16, 185, 129, 0.15);
    color: var(--color-status-success-text);
    border-color: rgba(16, 185, 129, 0.3);
  }

  .badge.stopped, .badge.failed, .badge.cancelled {
    background: rgba(239, 68, 68, 0.1);
    color: var(--color-status-error-text);
    border-color: rgba(239, 68, 68, 0.2);
  }

  .badge.pending {
    background: rgba(245, 158, 11, 0.1);
    color: var(--color-status-warning-text);
    border-color: rgba(245, 158, 11, 0.2);
  }

  .badge.completed {
    background: rgba(59, 130, 246, 0.1);
    color: #60a5fa;
    border-color: rgba(59, 130, 246, 0.2);
  }

  /* Role variants */
  .badge.developer {
    background: rgba(59, 130, 246, 0.1);
    color: #3b82f6;
    border-color: rgba(59, 130, 246, 0.2);
  }

  .badge.supervisor {
    background: rgba(245, 158, 11, 0.1);
    color: #f59e0b;
    border-color: rgba(245, 158, 11, 0.2);
  }

  .badge.worker {
    background: rgba(16, 185, 129, 0.1);
    color: #10b981;
    border-color: rgba(16, 185, 129, 0.2);
  }

  .badge.reviewer {
    background: rgba(236, 72, 153, 0.1);
    color: #ec4899;
    border-color: rgba(236, 72, 153, 0.2);
  }

  .badge.researcher {
    background: rgba(168, 85, 247, 0.1);
    color: #a855f7;
    border-color: rgba(168, 85, 247, 0.2);
  }

  .badge.assistant {
    background: rgba(99, 102, 241, 0.1);
    color: #6366f1;
    border-color: rgba(99, 102, 241, 0.2);
  }

  .badge.qa {
    background: rgba(245, 158, 11, 0.1);
    color: #f59e0b;
    border-color: rgba(245, 158, 11, 0.2);
  }

  .badge.linter {
    background: rgba(14, 165, 233, 0.1);
    color: #0ea5e9;
    border-color: rgba(14, 165, 233, 0.2);
  }
</style>

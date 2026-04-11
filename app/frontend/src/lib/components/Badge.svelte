<script lang="ts">
  let { text, variant = 'default' }: { text: string; variant?: string } = $props();
</script>

<span class="badge {variant}">{text}</span>

<style>
  .badge {
    display: inline-flex;
    align-items: center;
    padding: 2px 7px;
    border-radius: 9999px;
    font-size: 11px;
    font-weight: 500;
    line-height: 1.4;
    white-space: nowrap;
    background: rgba(100, 116, 139, 0.15);
    color: var(--color-text-tertiary);
    border: 1px solid rgba(100, 116, 139, 0.2);
  }

  /* Status variants */
  .badge.running, .badge.active {
    background: rgba(16, 185, 129, 0.15);
    color: #6ee7b7;
    border-color: rgba(16, 185, 129, 0.3);
  }

  .badge.stopped, .badge.failed, .badge.cancelled {
    background: rgba(239, 68, 68, 0.1);
    color: #fca5a5;
    border-color: rgba(239, 68, 68, 0.2);
  }

  .badge.pending {
    background: rgba(245, 158, 11, 0.1);
    color: #fcd34d;
    border-color: rgba(245, 158, 11, 0.2);
  }

  .badge.completed {
    background: rgba(59, 130, 246, 0.1);
    color: #93c5fd;
    border-color: rgba(59, 130, 246, 0.2);
  }

  /* Role variants */
  .badge.developer {
    background: rgba(59, 130, 246, 0.1);
    color: #93c5fd;
    border-color: rgba(59, 130, 246, 0.2);
  }

  .badge.supervisor {
    background: rgba(245, 158, 11, 0.1);
    color: #fcd34d;
    border-color: rgba(245, 158, 11, 0.2);
  }

  .badge.worker {
    background: rgba(16, 185, 129, 0.1);
    color: #6ee7b7;
    border-color: rgba(16, 185, 129, 0.2);
  }

  .badge.reviewer {
    background: rgba(236, 72, 153, 0.1);
    color: #f9a8d4;
    border-color: rgba(236, 72, 153, 0.2);
  }

  .badge.researcher {
    background: rgba(168, 85, 247, 0.1);
    color: #d8b4fe;
    border-color: rgba(168, 85, 247, 0.2);
  }
</style>

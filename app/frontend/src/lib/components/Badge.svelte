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
    background: var(--color-muted-bg);
    color: var(--color-text-tertiary);
    border: 1px solid var(--color-muted-border);
    flex-shrink: 0;
  }

  /* Status variants */
  .badge.running, .badge.active {
    background: var(--color-success-bg);
    color: var(--color-status-success-text);
    border-color: var(--color-success-border);
  }

  .badge.stopped, .badge.failed, .badge.cancelled {
    background: var(--color-error-bg);
    color: var(--color-status-error-text);
    border-color: var(--color-error-border);
  }

  .badge.pending {
    background: var(--color-warning-bg);
    color: var(--color-status-warning-text);
    border-color: var(--color-warning-border);
  }

  .badge.completed {
    background: var(--color-info-bg);
    color: var(--color-role-blue-text);
    border-color: var(--color-info-border);
  }

  /* Role variants */
  .badge.developer {
    background: var(--color-info-bg);
    color: var(--color-role-developer);
    border-color: var(--color-info-border);
  }

  .badge.supervisor {
    background: var(--color-warning-bg);
    color: var(--color-role-supervisor);
    border-color: var(--color-warning-border);
  }

  .badge.worker {
    background: var(--color-success-bg);
    color: var(--color-role-worker);
    border-color: var(--color-success-border);
  }

  .badge.reviewer {
    background: rgba(236, 72, 153, 0.1);
    color: var(--color-role-reviewer);
    border-color: rgba(236, 72, 153, 0.2);
  }

  .badge.researcher {
    background: rgba(168, 85, 247, 0.1);
    color: var(--color-role-researcher);
    border-color: rgba(168, 85, 247, 0.2);
  }

  .badge.assistant {
    background: var(--color-info-bg);
    color: var(--color-role-blue-text);
    border-color: var(--color-info-border);
  }

  .badge.qa {
    background: var(--color-warning-bg);
    color: var(--color-role-supervisor);
    border-color: var(--color-warning-border);
  }

  .badge.linter {
    background: var(--color-info-bg);
    color: var(--color-info);
    border-color: var(--color-info-border);
  }
</style>

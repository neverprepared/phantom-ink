<script>
  let { label, value, variant = 'default' } = $props();
</script>

<div class="stat-card" class:stat-active={variant === 'active'} class:stat-stopped={variant === 'stopped'} class:stat-ports={variant === 'ports'} class:stat-healthy={variant === 'healthy'} class:stat-unhealthy={variant === 'unhealthy'} class:stat-errors={variant === 'errors'} class:stat-sessions={variant === 'sessions'}>
  <div class="stat-label">{label}</div>
  <div class="stat-value">{value}</div>
</div>

<style>
  .stat-card {
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-xl);
    padding: var(--spacing-lg) var(--spacing-xl);
  }
  .stat-label {
    font-size: 11px;
    color: var(--color-text-tertiary);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-bottom: var(--spacing-sm);
  }
  .stat-value {
    font-size: 28px;
    font-weight: 600;
    color: var(--color-text-primary);
  }

  /* Variant styles */
  .stat-active .stat-value {
    color: var(--color-success);
  }
  .stat-stopped .stat-value {
    color: var(--color-text-tertiary);
  }
  .stat-ports .stat-value {
    font-size: 20px;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, monospace;
  }
  .stat-healthy .stat-value {
    color: var(--color-success);
    font-size: 20px;
  }
  .stat-unhealthy .stat-value {
    color: var(--color-error);
    font-size: 20px;
  }
  .stat-errors .stat-value {
    color: var(--color-error);
  }
  .stat-sessions .stat-value {
    color: var(--color-success);
  }
</style>

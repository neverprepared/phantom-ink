<script>
  import StatCard from './StatCard.svelte';

  let { total, active, stopped, portRange } = $props();
</script>

<div class="stats">
  <StatCard label="Total Sessions" value={total} />
  <StatCard label="Active" value={active} variant="active" />
  <StatCard label="Stopped" value={stopped} variant="stopped" />
  <StatCard label="Port Range" value={portRange} variant="ports" />
</div>

<style>
  .stats {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: var(--spacing-lg);
    margin-bottom: var(--spacing-2xl);
  }
  @media (max-width: 600px) {
    .stats { grid-template-columns: repeat(2, 1fr); }
  }
</style>

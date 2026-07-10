<script lang="ts">
  /**
   * Aggregate metrics strip for the Sessions panel — agents / total CPU / total
   * memory sparklines over the combined (container + local) history. Reads the
   * global combinedHistory store directly, so it takes no props; SessionsPanel
   * writes the history, this only renders it.
   */
  import { combinedHistory as combinedHistoryStore } from '../metricsHistory.svelte';
  import MetricsChart from './MetricsChart.svelte';
  import { formatBytes } from '../utils/format';

  // Shared hover index so hovering one chart highlights the same tick on all three.
  let aggregateHoverIdx = $state<number | null>(null);

  let agentData    = $derived(combinedHistoryStore.value.map((s) => ({ ts: s.ts, value: s.agent_count })));
  let cpuData      = $derived(combinedHistoryStore.value.map((s) => ({ ts: s.ts, value: s.total_cpu })));
  let memData      = $derived(combinedHistoryStore.value.map((s) => ({ ts: s.ts, value: s.total_mem })));
  let latestAgents = $derived(combinedHistoryStore.value.length ? String(combinedHistoryStore.value[combinedHistoryStore.value.length - 1].agent_count) : '–');
  let latestCPU    = $derived(combinedHistoryStore.value.length ? `${combinedHistoryStore.value[combinedHistoryStore.value.length - 1].total_cpu.toFixed(1)}%` : '–');
  let latestMem    = $derived(combinedHistoryStore.value.length ? formatBytes(combinedHistoryStore.value[combinedHistoryStore.value.length - 1].total_mem) : '–');
</script>

{#if combinedHistoryStore.value.length >= 2}
  <div class="charts-row">
    <MetricsChart
      data={agentData}
      label="agents"
      current={latestAgents}
      color="var(--color-accent)"
      formatY={(v) => String(Math.round(v))}
      hoverIdx={aggregateHoverIdx}
      onHover={(idx) => aggregateHoverIdx = idx}
      onHoverEnd={() => aggregateHoverIdx = null}
    />
    <MetricsChart
      data={cpuData}
      label="total cpu"
      current={latestCPU}
      color="var(--color-info)"
      formatY={(v) => `${v.toFixed(1)}%`}
      hoverIdx={aggregateHoverIdx}
      onHover={(idx) => aggregateHoverIdx = idx}
      onHoverEnd={() => aggregateHoverIdx = null}
    />
    <MetricsChart
      data={memData}
      label="total memory"
      current={latestMem}
      color="var(--color-success)"
      formatY={formatBytes}
      hoverIdx={aggregateHoverIdx}
      onHover={(idx) => aggregateHoverIdx = idx}
      onHoverEnd={() => aggregateHoverIdx = null}
    />
  </div>
{/if}

<style>
  .charts-row {
    display: flex;
    gap: 12px;
    margin-bottom: 20px;
  }
</style>

<script>
  import { onMount, onDestroy } from 'svelte';
  import { fetchSessionSummary } from './api.js';

  let { sessions = [] } = $props();

  let toolCounts = $state({});
  let timer = null;

  async function refresh() {
    try {
      const summaries = await Promise.all(
        sessions.map(s => fetchSessionSummary(s).catch(() => ({ tool_counts: {} })))
      );
      // Merge tool counts across all sessions
      const merged = {};
      for (const s of summaries) {
        for (const [tool, count] of Object.entries(s.tool_counts || {})) {
          merged[tool] = (merged[tool] || 0) + count;
        }
      }
      toolCounts = merged;
    } catch { /* noop */ }
  }

  onMount(() => {
    refresh();
    timer = setInterval(refresh, 10000);
  });

  onDestroy(() => {
    if (timer) clearInterval(timer);
  });

  $effect(() => {
    sessions;
    refresh();
  });

  let sortedTools = $derived(
    Object.entries(toolCounts)
      .sort(([, a], [, b]) => b - a)
      .slice(0, 15)
  );
  let maxCount = $derived(sortedTools.length > 0 ? sortedTools[0][1] : 1);
</script>

<div class="breakdown-section">
  <h3>Tool Usage</h3>
  {#if sortedTools.length === 0}
    <p class="empty">No tool data</p>
  {:else}
    <div class="bar-chart">
      {#each sortedTools as [name, count] (name)}
        <div class="bar-row">
          <span class="bar-label" title={name}>{name}</span>
          <div class="bar-track">
            <div class="bar-fill" style="width: {(count / maxCount) * 100}%"></div>
          </div>
          <span class="bar-count">{count}</span>
        </div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .breakdown-section {
    background: #111827;
    border: 1px solid #1e293b;
    border-radius: 8px;
    padding: 20px;
  }
  h3 {
    font-size: 14px;
    font-weight: 600;
    color: #94a3b8;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-bottom: 16px;
  }
  .empty { color: #64748b; font-size: 14px; }
  .bar-chart {
    display: flex;
    flex-direction: column;
    gap: 8px;
    max-height: 400px;
    overflow-y: auto;
  }
  .bar-row {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .bar-label {
    min-width: 100px;
    max-width: 140px;
    font-size: 12px;
    color: #94a3b8;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, monospace;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .bar-track {
    flex: 1;
    height: 16px;
    background: rgba(30, 41, 59, 0.5);
    border-radius: 3px;
    overflow: hidden;
  }
  .bar-fill {
    height: 100%;
    background: linear-gradient(90deg, #f59e0b, #f97316);
    border-radius: 3px;
    transition: width 0.3s ease;
  }
  .bar-count {
    min-width: 32px;
    text-align: right;
    font-size: 12px;
    color: #64748b;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, monospace;
  }
</style>

<script lang="ts">
  import { dashboardDataStore } from '../stores.svelte';
  import Icon from '../components/Icon.svelte';

  let data = $derived(dashboardDataStore.value);
  let dockerStats  = $derived(data?.dockerStats ?? []);
  let localProcs   = $derived(data?.localProcs ?? []);
  let systemInfo   = $derived(data?.systemInfo ?? { cpu_cores: 0, mem_total_gib: 0 });

  let containerCPU = $derived(dockerStats.reduce((s: number, d: any) => s + parseFloat(d.cpu_perc || '0'), 0));
  let containerMem = $derived(dockerStats.reduce((s: number, d: any) => {
    const m = d.mem_usage || '';
    const match = m.match(/([\d.]+)\s*(MiB|GiB)/);
    if (!match) return s;
    const val = parseFloat(match[1]);
    return s + (match[2] === 'GiB' ? val * 1024 : val);
  }, 0));
  let localCPU = $derived(localProcs.reduce((s: number, p: any) => s + parseFloat(p.cpu_perc || '0'), 0));
  let localMem = $derived(localProcs.reduce((s: number, p: any) => s + parseFloat(p.mem_mb || '0'), 0));
  let totalCPU = $derived(containerCPU + localCPU);
  let totalMem = $derived(containerMem + localMem);
  let sysCPUMax = $derived(systemInfo.cpu_cores * 100);
  let sysCPUPct = $derived(sysCPUMax > 0 ? (totalCPU / sysCPUMax) * 100 : 0);
  let sysMemMiB  = $derived(systemInfo.mem_total_gib * 1024);
  let sysMemPct  = $derived(sysMemMiB > 0 ? (totalMem / sysMemMiB) * 100 : 0);

  function fmtMem(mib: number): string {
    return mib >= 1024 ? `${(mib / 1024).toFixed(1)} GiB` : `${mib.toFixed(0)} MiB`;
  }
</script>

<div class="widget">
  <div class="widget-header widget-drag-handle">
    <Icon name="server" size={15} style="color: var(--text-muted); flex-shrink: 0;" />
    <span class="widget-title">» RESOURCE USAGE</span>
    <span class="res-summary">
      {totalCPU.toFixed(1)}% cpu · {fmtMem(totalMem)} mem
    </span>
  </div>
  <div class="widget-body">
    {#if dockerStats.length === 0 && localProcs.length === 0}
      <div class="empty">no active sessions</div>
    {:else}
      {#if systemInfo.cpu_cores > 0}
        <div class="bars">
          <div class="bar-row">
            <span class="bar-label">CPU</span>
            <div class="bar-track"><div class="bar-fill cpu" style="width: {Math.min(sysCPUPct, 100)}%"></div></div>
            <span class="bar-pct">{sysCPUPct.toFixed(1)}%</span>
          </div>
          {#if sysMemMiB > 0}
          <div class="bar-row">
            <span class="bar-label">MEM</span>
            <div class="bar-track"><div class="bar-fill mem" style="width: {Math.min(sysMemPct, 100)}%"></div></div>
            <span class="bar-pct">{sysMemPct.toFixed(1)}%</span>
          </div>
          {/if}
        </div>
      {/if}
      {#if dockerStats.length > 0}
        <div class="table">
          <div class="thead"><span>container</span><span>cpu</span><span>memory</span><span>net i/o</span></div>
          {#each dockerStats as stat (stat.id)}
            <div class="trow">
              <span class="t-name">{stat.name}</span>
              <span class="t-val">{stat.cpu_perc}</span>
              <span class="t-val">{stat.mem_usage}</span>
              <span class="t-val">{stat.net_io}</span>
            </div>
          {/each}
        </div>
      {/if}
    {/if}
  </div>
</div>

<style>
  .widget { display: flex; flex-direction: column; height: 100%; }

  .widget-header {
    display: flex;
    align-items: center;
    gap: var(--spacing-sm);
    padding: var(--spacing-sm) var(--spacing-md);
    border-bottom: 1px solid var(--color-border-primary);
    cursor: grab;
    flex-shrink: 0;
  }

  .widget-title {
    font-family: var(--font-mono);
    font-size: 11px;
    letter-spacing: 0.08em;
    color: var(--color-text-secondary);
    flex: 1;
  }

  .res-summary {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-muted);
  }

  .widget-body {
    flex: 1;
    overflow-y: auto;
    padding: var(--spacing-md);
    display: flex;
    flex-direction: column;
    gap: var(--spacing-md);
  }

  .empty {
    font-size: 12px;
    color: var(--color-text-tertiary);
  }

  .bars { display: flex; flex-direction: column; gap: var(--spacing-xs); }

  .bar-row {
    display: grid;
    grid-template-columns: 36px 1fr 44px;
    align-items: center;
    gap: var(--spacing-sm);
  }

  .bar-label {
    font-family: var(--font-mono);
    font-size: 10px;
    letter-spacing: 0.06em;
    color: var(--color-text-tertiary);
  }

  .bar-track {
    height: 6px;
    background: var(--color-bg-tertiary);
    border-radius: 3px;
    overflow: hidden;
  }

  .bar-fill {
    height: 100%;
    border-radius: 3px;
    transition: width 0.5s ease;
    min-width: 2px;
  }
  .bar-fill.cpu { background: var(--color-accent); }
  .bar-fill.mem { background: var(--color-info); }

  .bar-pct {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-muted);
    text-align: right;
  }

  .table {
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm);
    overflow: hidden;
  }

  .thead, .trow {
    display: grid;
    grid-template-columns: 1fr 72px 120px 100px;
    gap: var(--spacing-sm);
    padding: 4px var(--spacing-md);
    font-family: var(--font-mono);
    font-size: 10px;
  }

  .thead {
    background: var(--color-bg-tertiary);
    color: var(--color-text-tertiary);
    letter-spacing: 0.06em;
  }

  .trow {
    border-top: 1px solid var(--color-border-primary);
    color: var(--color-text-secondary);
  }

  .t-name {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    color: var(--color-text-primary);
  }

  .t-val { color: var(--color-text-secondary); }
</style>

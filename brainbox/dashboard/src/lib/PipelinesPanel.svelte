<script>
  import { onMount, onDestroy } from 'svelte';
  import { fetchPipelineDefinitions, fetchPipelineRuns, fetchPipelineRun, cancelPipelineRun, connectSSE } from './api.js';
  import { notifications } from './notifications.svelte.js';

  let definitions = $state([]);
  let runs = $state([]);
  let loading = $state(true);
  let error = $state(null);
  let eventSource = null;
  let pollInterval = null;

  // Tabs: 'active', 'catalog', or a pipeline_name for history
  let activeTab = $state('active');
  let searchQuery = $state('');
  let expandedRuns = $state(new Set());

  // Pagination
  const PAGE_SIZE = 20;
  let currentPage = $state(0);

  async function refresh() {
    try {
      const [defRes, runRes] = await Promise.all([
        fetchPipelineDefinitions(),
        fetchPipelineRuns(),
      ]);
      definitions = defRes.pipelines || [];
      runs = (runRes.runs || []).sort((a, b) => b.created_at - a.created_at);
      error = null;
    } catch (err) {
      console.error('Failed to fetch pipelines:', err);
      error = err.message;
    } finally {
      loading = false;
    }
  }

  async function handleCancel(runId) {
    try {
      await cancelPipelineRun(runId);
      await refresh();
    } catch (err) {
      console.error('Failed to cancel run:', err);
      notifications.error(`Failed to cancel run: ${err.message}`);
    }
  }

  async function toggleExpand(runId) {
    const next = new Set(expandedRuns);
    if (next.has(runId)) {
      next.delete(runId);
    } else {
      try {
        const detail = await fetchPipelineRun(runId);
        const idx = runs.findIndex(r => r.id === runId);
        if (idx >= 0) {
          runs[idx] = { ...runs[idx], ...detail, _detailed: true };
          runs = [...runs];
        }
      } catch { /* use summary */ }
      next.add(runId);
    }
    expandedRuns = next;
  }

  function formatDuration(ms) {
    if (!ms) return '-';
    if (ms < 1000) return `${ms}ms`;
    if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
    return `${Math.floor(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`;
  }

  function formatTime(epochMs) {
    if (!epochMs) return '-';
    return new Date(epochMs).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }

  function formatDate(epochMs) {
    if (!epochMs) return '';
    return new Date(epochMs).toLocaleDateString([], { month: 'short', day: 'numeric' });
  }

  function timeAgo(epochMs) {
    if (!epochMs) return '';
    const secs = Math.floor((Date.now() - epochMs) / 1000);
    if (secs < 60) return `${secs}s ago`;
    if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
    if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
    return `${Math.floor(secs / 86400)}d ago`;
  }

  function statusColor(status) {
    switch (status) {
      case 'completed': return '#10b981';
      case 'running': case 'pending': return '#3b82f6';
      case 'failed': return '#ef4444';
      case 'cancelled': case 'skipped': return '#64748b';
      default: return '#94a3b8';
    }
  }

  function providerLabel(p) { return p || 'private'; }
  function providerColor(p) {
    switch (p) {
      case 'private': return '#10b981';
      case 'cloud': return '#8b5cf6';
      case 'mixed': return '#f59e0b';
      default: return '#94a3b8';
    }
  }

  function tierColor(t) {
    switch (t) {
      case 'repo': return '#3b82f6';
      case 'workspace': return '#f59e0b';
      case 'generic': return '#64748b';
      default: return '#64748b';
    }
  }

  // Derived: active (running/pending) runs — always sorted newest first
  let activeRuns = $derived(runs.filter(r => r.status === 'running' || r.status === 'pending'));

  // Derived: completed/failed runs (history)
  let historyRuns = $derived(runs.filter(r => r.status !== 'running' && r.status !== 'pending'));

  // Filtered definitions for catalog search
  let filteredDefs = $derived(
    searchQuery
      ? definitions.filter(d =>
          d.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
          (d.description || '').toLowerCase().includes(searchQuery.toLowerCase()) ||
          (d.step_types || []).some(t => t.toLowerCase().includes(searchQuery.toLowerCase()))
        )
      : definitions
  );

  // History tab: runs for the selected pipeline, paginated
  let historyTabRuns = $derived(
    activeTab !== 'active' && activeTab !== 'catalog'
      ? historyRuns.filter(r => r.pipeline_name === activeTab)
      : historyRuns
  );
  let pagedHistoryRuns = $derived(historyTabRuns.slice(currentPage * PAGE_SIZE, (currentPage + 1) * PAGE_SIZE));
  let totalHistoryPages = $derived(Math.ceil(historyTabRuns.length / PAGE_SIZE));

  // Pipeline names that have history
  let pipelineNamesWithHistory = $derived(
    [...new Set(historyRuns.map(r => r.pipeline_name))].sort()
  );

  let activeRunCount = $derived(activeRuns.length);

  onMount(async () => {
    await refresh();
    pollInterval = setInterval(refresh, activeRunCount > 0 ? 3000 : 10000);
    eventSource = connectSSE((data) => {
      try {
        const parsed = JSON.parse(data);
        if (parsed.hub || parsed.pipeline) refresh();
      } catch { /* plain text */ }
    });
  });

  onDestroy(() => {
    if (eventSource) eventSource.close();
    if (pollInterval) clearInterval(pollInterval);
  });

  $effect(() => {
    if (pollInterval) clearInterval(pollInterval);
    pollInterval = setInterval(refresh, activeRunCount > 0 ? 3000 : 10000);
  });

  // Reset page when switching tabs
  $effect(() => { void activeTab; currentPage = 0; });
</script>

<header>
  <h1><span class="accent">pipelines</span></h1>
  <div class="header-stats">
    <span class="stat-pill">{definitions.length} available</span>
    {#if activeRunCount > 0}
      <span class="stat-pill active"><span class="spinner-sm"></span> {activeRunCount} running</span>
    {/if}
    <span class="stat-pill">{runs.length} total runs</span>
  </div>
</header>

{#if error}
  <div class="error-bar">{error}</div>
{/if}

<!-- Tab bar -->
<div class="tab-bar">
  <button class="tab" class:active={activeTab === 'active'} class:has-active={activeRunCount > 0} onclick={() => activeTab = 'active'}>
    {#if activeRunCount > 0}<span class="spinner-sm"></span>{/if}
    Active
    {#if activeRunCount > 0}<span class="tab-count live">{activeRunCount}</span>{/if}
  </button>
  <button class="tab" class:active={activeTab === 'catalog'} onclick={() => activeTab = 'catalog'}>
    Catalog
  </button>
  <span class="tab-divider"></span>
  {#each pipelineNamesWithHistory as name}
    <button class="tab" class:active={activeTab === name} onclick={() => activeTab = name}>
      {name}
      <span class="tab-count">{historyRuns.filter(r => r.pipeline_name === name).length}</span>
    </button>
  {/each}
</div>

{#if loading}
  <div class="empty">Loading...</div>

<!-- ==================== Active Tab ==================== -->
{:else if activeTab === 'active'}
  {#if activeRuns.length === 0}
    <div class="empty-state">
      <div class="empty-icon">
        <svg xmlns="http://www.w3.org/2000/svg" width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect width="8" height="8" x="3" y="3" rx="2"/><path d="M7 11v4a2 2 0 0 0 2 2h4"/><rect width="8" height="8" x="13" y="13" rx="2"/></svg>
      </div>
      <p>No active pipelines</p>
      <span class="empty-hint">Submit a pipeline run via the API to see it here</span>
    </div>
  {:else}
    <div class="active-list">
      {#each activeRuns as run (run.id)}
        <div class="active-card">
          <!-- Top bar: status + title + cancel -->
          <div class="active-top">
            <div class="active-status">
              <span class="spinner-md"></span>
              <span class="active-status-text">{run.status}</span>
            </div>
            <div class="active-title-area">
              <span class="active-pipeline-name">{run.pipeline_name}</span>
              {#if run.title}
                <span class="active-doc-title">{run.title}</span>
              {/if}
            </div>
            <span class="provider-badge" style="color:{providerColor(run.provider)};border-color:{providerColor(run.provider)}30;background:{providerColor(run.provider)}10">
              {providerLabel(run.provider)}
            </span>
            <button class="cancel-btn-sm" onclick={() => handleCancel(run.id)}>Cancel</button>
          </div>

          <!-- Step progress: linear timeline -->
          <div class="active-steps">
            {#each Object.entries(run.steps || {}) as [stepName, step], i}
              <div class="active-step" class:current={step.status === 'running'}>
                <span class="active-step-indicator" style="background:{statusColor(step.status)}">
                  {#if step.status === 'running'}
                    <span class="spinner-xs"></span>
                  {:else if step.status === 'completed'}
                    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg>
                  {:else if step.status === 'failed'}
                    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="3"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                  {:else}
                    <span class="step-num">{i + 1}</span>
                  {/if}
                </span>
                {#if i < Object.keys(run.steps || {}).length - 1}
                  <span class="active-step-connector" style="background:{step.status === 'completed' ? statusColor('completed') : '#1e293b'}"></span>
                {/if}
                <div class="active-step-info">
                  <span class="active-step-name">{stepName}</span>
                  <span class="active-step-dur">{formatDuration(step.duration_ms)}</span>
                </div>
              </div>
            {/each}
          </div>

          <!-- Footer: timing -->
          <div class="active-footer">
            <span class="active-meta">Started {timeAgo(run.started_at || run.created_at)}</span>
            <span class="active-meta">{formatTime(run.created_at)}</span>
            <span class="active-meta mono">{run.id.slice(0, 8)}</span>
          </div>
        </div>
      {/each}
    </div>
  {/if}

  <!-- Recent completed below active -->
  {#if historyRuns.length > 0}
    <div class="recent-section">
      <h3>Recent</h3>
      <div class="runs-list">
        {#each historyRuns.slice(0, 10) as run (run.id)}
          {@render runRow(run)}
        {/each}
      </div>
    </div>
  {/if}

<!-- ==================== Catalog Tab ==================== -->
{:else if activeTab === 'catalog'}
  <div class="search-bar">
    <input type="text" placeholder="Search pipelines..." bind:value={searchQuery} class="search-input" />
    {#if searchQuery}
      <button class="search-clear" onclick={() => searchQuery = ''}>x</button>
    {/if}
  </div>
  <div class="def-grid">
    {#each filteredDefs as def (def.name)}
      <div class="def-card">
        <div class="def-top">
          <span class="def-name">{def.name}</span>
          <div class="def-badges">
            <span class="tier-badge" style="color:{tierColor(def.source_tier)};border-color:{tierColor(def.source_tier)}30;background:{tierColor(def.source_tier)}10">
              {def.source_tier || 'generic'}
            </span>
            <span class="provider-badge" style="color:{providerColor(def.provider)};border-color:{providerColor(def.provider)}30;background:{providerColor(def.provider)}10">
              {providerLabel(def.provider)}
            </span>
          </div>
        </div>
        {#if def.description}
          <p class="def-desc">{def.description.trim()}</p>
        {/if}
        <div class="def-meta">
          <span class="meta-item">{def.steps} steps</span>
          <span class="meta-sep">|</span>
          {#each def.step_types || [] as st}
            <span class="step-type-tag">{st}</span>
          {/each}
          <span class="meta-sep">|</span>
          <span class="meta-item">v{def.version || '1'}</span>
        </div>
      </div>
    {/each}
    {#if filteredDefs.length === 0}
      <div class="empty-inline">{searchQuery ? 'No matches' : 'No pipeline definitions found'}</div>
    {/if}
  </div>

<!-- ==================== Pipeline History Tab ==================== -->
{:else}
  <div class="runs-header">
    <span class="runs-title">{activeTab}</span>
    {#each definitions.filter(d => d.name === activeTab) as def}
      <span class="provider-badge" style="color:{providerColor(def.provider)};border-color:{providerColor(def.provider)}30;background:{providerColor(def.provider)}10">
        {providerLabel(def.provider)}
      </span>
      <span class="runs-desc">{def.description?.trim()}</span>
    {/each}
  </div>
  <div class="runs-list">
    {#each pagedHistoryRuns as run (run.id)}
      {@render runRow(run)}
    {/each}
    {#if historyTabRuns.length === 0}
      <div class="empty-inline">No runs for this pipeline</div>
    {/if}
  </div>
  <!-- Pagination -->
  {#if totalHistoryPages > 1}
    <div class="pagination">
      <button class="page-btn" disabled={currentPage === 0} onclick={() => currentPage--}>Prev</button>
      <span class="page-info">Page {currentPage + 1} of {totalHistoryPages}</span>
      <button class="page-btn" disabled={currentPage >= totalHistoryPages - 1} onclick={() => currentPage++}>Next</button>
    </div>
  {/if}
{/if}

<!-- ==================== Shared run row snippet ==================== -->
{#snippet runRow(run)}
  <div class="run-card" class:expanded={expandedRuns.has(run.id)}>
    <button class="run-row" onclick={() => toggleExpand(run.id)}>
      <span class="run-status-dot" style="background:{statusColor(run.status)}"></span>
      <span class="run-status-text" style="color:{statusColor(run.status)}">{run.status}</span>
      <span class="run-title">{run.title || run.pipeline_name}</span>
      <span class="run-provider" style="color:{providerColor(run.provider)}">{providerLabel(run.provider)}</span>
      <span class="run-time">{formatDate(run.created_at)} {formatTime(run.created_at)}</span>
      {#if run.finished_at && run.started_at}
        <span class="run-dur">{formatDuration(run.finished_at - run.started_at)}</span>
      {:else}
        <span class="run-dur">-</span>
      {/if}
      <div class="step-progress">
        {#each Object.entries(run.steps || {}) as [, step]}
          <span class="step-dot" style="background:{statusColor(step.status)}"></span>
        {/each}
      </div>
      <span class="expand-chevron">{expandedRuns.has(run.id) ? '▾' : '▸'}</span>
    </button>
    {#if expandedRuns.has(run.id)}
      <div class="run-detail">
        <div class="detail-row"><span class="detail-label">Run ID</span><span class="detail-value mono">{run.id}</span></div>
        {#if run.title}<div class="detail-row"><span class="detail-label">Title</span><span class="detail-value">{run.title}</span></div>{/if}
        {#if run.description}<div class="detail-row"><span class="detail-label">Description</span><span class="detail-value">{run.description}</span></div>{/if}
        {#if run.error}<div class="run-error">{run.error}</div>{/if}
        <div class="steps-timeline">
          {#each Object.entries(run.steps || {}) as [stepName, step]}
            <div class="step-item">
              <span class="step-indicator" style="background:{statusColor(step.status)}">
                {#if step.status === 'completed'}✓{:else if step.status === 'failed'}✗{:else if step.status === 'skipped'}–{:else}·{/if}
              </span>
              <div class="step-info"><span class="step-name">{stepName}</span><span class="step-dur">{formatDuration(step.duration_ms)}</span></div>
              {#if step.error}<span class="step-error-text">{step.error}</span>{/if}
            </div>
          {/each}
        </div>
      </div>
    {/if}
  </div>
{/snippet}

<style>
  /* Header */
  header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; flex-wrap: wrap; gap: 10px; }
  h1 { font-size: 22px; font-weight: 600; color: #e2e8f0; margin: 0; }
  .accent { color: #f59e0b; }
  .header-stats { display: flex; gap: 8px; align-items: center; }
  .stat-pill {
    font-size: 12px; color: #94a3b8; background: rgba(148,163,184,0.08);
    border: 1px solid rgba(148,163,184,0.15); padding: 3px 10px; border-radius: 12px;
    display: flex; align-items: center; gap: 5px;
  }
  .stat-pill.active { color: #3b82f6; border-color: rgba(59,130,246,0.3); background: rgba(59,130,246,0.08); }
  .error-bar { background: rgba(239,68,68,0.08); border: 1px solid rgba(239,68,68,0.25); color: #fca5a5; padding: 8px 14px; border-radius: 6px; font-size: 13px; margin-bottom: 12px; }

  /* Tab bar */
  .tab-bar { display: flex; gap: 2px; border-bottom: 1px solid #1e293b; margin-bottom: 20px; overflow-x: auto; scrollbar-width: none; }
  .tab-bar::-webkit-scrollbar { display: none; }
  .tab {
    padding: 8px 16px; font-size: 13px; font-weight: 500; font-family: inherit;
    color: #64748b; background: none; border: none; border-bottom: 2px solid transparent;
    cursor: pointer; white-space: nowrap; transition: all 0.15s; display: flex; align-items: center; gap: 6px;
  }
  .tab:hover { color: #94a3b8; }
  .tab.active { color: #e2e8f0; border-bottom-color: #f59e0b; }
  .tab.has-active { color: #3b82f6; }
  .tab-count { font-size: 11px; background: rgba(148,163,184,0.12); color: #64748b; padding: 1px 6px; border-radius: 8px; }
  .tab-count.live { background: rgba(59,130,246,0.15); color: #3b82f6; }
  .tab-divider { width: 1px; background: #1e293b; margin: 4px 8px; align-self: stretch; }

  /* ---- Active tab: cards ---- */
  .active-list { display: flex; flex-direction: column; gap: 12px; }
  .active-card { background: #111827; border: 1px solid #1e293b; border-radius: 10px; padding: 16px; border-left: 3px solid #3b82f6; }
  .active-top { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; }
  .active-status { display: flex; align-items: center; gap: 6px; }
  .active-status-text { font-size: 12px; font-weight: 600; color: #3b82f6; text-transform: uppercase; letter-spacing: 0.03em; }
  .active-title-area { flex: 1; display: flex; flex-direction: column; gap: 2px; min-width: 0; }
  .active-pipeline-name { font-size: 14px; font-weight: 600; color: #e2e8f0; }
  .active-doc-title { font-size: 12px; color: #94a3b8; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  /* Linear step timeline */
  .active-steps { display: flex; align-items: flex-start; gap: 0; margin-bottom: 12px; padding: 0 4px; }
  .active-step { display: flex; align-items: center; flex: 1; position: relative; }
  .active-step.current .active-step-name { color: #3b82f6; font-weight: 600; }
  .active-step-indicator {
    width: 24px; height: 24px; border-radius: 50%; display: flex; align-items: center;
    justify-content: center; flex-shrink: 0; z-index: 1;
  }
  .step-num { font-size: 11px; color: white; font-weight: 600; }
  .active-step-connector { height: 2px; flex: 1; min-width: 12px; margin: 0 -1px; }
  .active-step-info { position: absolute; top: 28px; left: 0; display: flex; flex-direction: column; }
  .active-step-name { font-size: 11px; color: #94a3b8; white-space: nowrap; }
  .active-step-dur { font-size: 10px; color: #64748b; }

  .active-footer { display: flex; gap: 16px; padding-top: 28px; border-top: 1px solid #1e293b; }
  .active-meta { font-size: 11px; color: #64748b; }
  .active-meta.mono { font-family: monospace; }

  .cancel-btn-sm {
    padding: 3px 10px; font-size: 11px; font-family: inherit; font-weight: 500;
    background: rgba(239,68,68,0.08); border: 1px solid rgba(239,68,68,0.25);
    color: #ef4444; border-radius: 4px; cursor: pointer; transition: all 0.15s; flex-shrink: 0;
  }
  .cancel-btn-sm:hover { background: rgba(239,68,68,0.15); border-color: #ef4444; }

  /* Recent section under active */
  .recent-section { margin-top: 32px; }
  .recent-section h3 { font-size: 13px; font-weight: 600; color: #64748b; text-transform: uppercase; letter-spacing: 0.05em; margin: 0 0 10px; }

  /* ---- Catalog tab ---- */
  .search-bar { position: relative; margin-bottom: 16px; }
  .search-input {
    width: 100%; padding: 9px 14px; padding-right: 32px; font-size: 13px; font-family: inherit;
    background: #111827; border: 1px solid #1e293b; border-radius: 6px; color: #e2e8f0; outline: none; transition: border-color 0.15s;
  }
  .search-input::placeholder { color: #475569; }
  .search-input:focus { border-color: #334155; }
  .search-clear { position: absolute; right: 8px; top: 50%; transform: translateY(-50%); background: none; border: none; color: #64748b; cursor: pointer; font-size: 14px; padding: 4px 6px; }

  .def-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 12px; }
  .def-card { background: #111827; border: 1px solid #1e293b; border-radius: 8px; padding: 14px 16px; transition: border-color 0.15s; }
  .def-card:hover { border-color: #334155; }
  .def-top { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; gap: 8px; }
  .def-name { font-size: 14px; font-weight: 600; color: #e2e8f0; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .def-badges { display: flex; gap: 4px; flex-shrink: 0; }
  .provider-badge, .tier-badge { font-size: 11px; font-weight: 500; padding: 2px 8px; border-radius: 10px; border: 1px solid; text-transform: uppercase; letter-spacing: 0.03em; white-space: nowrap; }
  .def-desc { font-size: 12px; color: #94a3b8; margin: 0 0 10px 0; line-height: 1.4; }
  .def-meta { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
  .meta-item { font-size: 11px; color: #64748b; }
  .meta-sep { color: #334155; font-size: 10px; }
  .step-type-tag { font-size: 10px; color: #94a3b8; background: rgba(148,163,184,0.08); padding: 1px 5px; border-radius: 3px; font-family: monospace; }

  /* ---- History/run list (shared) ---- */
  .runs-header { display: flex; align-items: center; gap: 10px; margin-bottom: 16px; flex-wrap: wrap; }
  .runs-title { font-size: 16px; font-weight: 600; color: #e2e8f0; }
  .runs-desc { font-size: 12px; color: #64748b; flex: 1; }

  .runs-list { display: flex; flex-direction: column; gap: 4px; }
  .run-card { background: #111827; border: 1px solid #1e293b; border-radius: 6px; overflow: hidden; transition: border-color 0.15s; }
  .run-card.expanded { border-color: #334155; }
  .run-row {
    display: flex; align-items: center; gap: 10px; padding: 10px 14px;
    width: 100%; background: none; border: none; color: inherit; cursor: pointer;
    font-family: inherit; font-size: 13px; text-align: left; transition: background 0.1s;
  }
  .run-row:hover { background: rgba(255,255,255,0.015); }
  .run-status-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
  .run-status-text { font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; min-width: 70px; }
  .run-title { flex: 1; color: #e2e8f0; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .run-provider { font-size: 11px; font-weight: 500; text-transform: uppercase; }
  .run-time { color: #64748b; font-size: 12px; white-space: nowrap; }
  .run-dur { color: #94a3b8; font-size: 12px; min-width: 55px; text-align: right; }
  .expand-chevron { color: #475569; font-size: 11px; flex-shrink: 0; }
  .step-progress { display: flex; gap: 3px; align-items: center; }
  .step-dot { width: 6px; height: 6px; border-radius: 50%; }

  .run-detail { padding: 12px 14px; border-top: 1px solid #1e293b; }
  .detail-row { display: flex; gap: 12px; margin-bottom: 4px; font-size: 12px; }
  .detail-label { color: #64748b; min-width: 80px; }
  .detail-value { color: #e2e8f0; }
  .detail-value.mono { font-family: monospace; font-size: 11px; color: #94a3b8; }
  .run-error { font-size: 12px; color: #fca5a5; background: rgba(239,68,68,0.08); border: 1px solid rgba(239,68,68,0.2); padding: 6px 10px; border-radius: 4px; margin: 8px 0; }

  .steps-timeline { margin: 12px 0 8px; display: flex; flex-direction: column; gap: 1px; }
  .step-item { display: flex; align-items: center; gap: 10px; padding: 4px 8px; border-radius: 4px; }
  .step-item:hover { background: rgba(255,255,255,0.02); }
  .step-indicator { width: 20px; height: 20px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 11px; color: white; flex-shrink: 0; font-weight: 600; }
  .step-info { display: flex; align-items: center; gap: 8px; flex: 1; }
  .step-name { font-size: 13px; color: #e2e8f0; font-weight: 500; }
  .step-dur { font-size: 12px; color: #64748b; }
  .step-error-text { font-size: 11px; color: #ef4444; }

  /* Pagination */
  .pagination { display: flex; align-items: center; justify-content: center; gap: 12px; margin-top: 16px; }
  .page-btn {
    padding: 5px 14px; font-size: 12px; font-family: inherit;
    background: rgba(148,163,184,0.08); border: 1px solid #1e293b;
    color: #94a3b8; border-radius: 4px; cursor: pointer; transition: all 0.15s;
  }
  .page-btn:hover:not(:disabled) { background: rgba(148,163,184,0.15); color: #e2e8f0; }
  .page-btn:disabled { opacity: 0.4; cursor: default; }
  .page-info { font-size: 12px; color: #64748b; }

  /* Empty states */
  .empty, .empty-inline { color: #64748b; text-align: center; padding: 32px 20px; font-size: 14px; }
  .empty-inline { padding: 20px; }
  .empty-state { display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 48px 20px; }
  .empty-icon { color: #334155; }
  .empty-state p { color: #94a3b8; font-size: 15px; margin: 0; }
  .empty-hint { color: #475569; font-size: 12px; }

  /* Spinners */
  .spinner-sm { display: inline-block; width: 12px; height: 12px; border: 2px solid rgba(59,130,246,0.25); border-top-color: #3b82f6; border-radius: 50%; animation: spin 0.8s linear infinite; }
  .spinner-md { display: inline-block; width: 16px; height: 16px; border: 2px solid rgba(59,130,246,0.25); border-top-color: #3b82f6; border-radius: 50%; animation: spin 0.8s linear infinite; }
  .spinner-xs { display: inline-block; width: 10px; height: 10px; border: 1.5px solid rgba(255,255,255,0.25); border-top-color: white; border-radius: 50%; animation: spin 0.8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>

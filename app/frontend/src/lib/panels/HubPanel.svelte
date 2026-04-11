<script lang="ts">
  import { getApi } from '../utils/api';
  import { onMount } from 'svelte';
  import { brainboxEvents } from '../events.svelte';
  import { notifications } from '../notifications.svelte';
  import { profileState } from '../stores.svelte';
  import EmptyState from '../components/EmptyState.svelte';
  import Badge from '../components/Badge.svelte';
  import Modal from '../components/Modal.svelte';


  // --- Data ---
  let sessions = $state<any[]>([]);
  let tasks = $state<any[]>([]);
  let agents = $state<any[]>([]);
  let tokens = $state<any[]>([]);
  let dockerStats = $state<any[]>([]);
  let localProcesses = $state<any[]>([]);
  let systemInfo = $state<{ cpu_cores: number; mem_total_gib: number }>({ cpu_cores: 0, mem_total_gib: 0 });
  let loading = $state(true);

  // --- Task submission ---
  let showSubmitModal = $state(false);
  let taskDesc = $state('');
  let taskAgent = $state('');
  let taskRepo = $state('');
  let isSubmitting = $state(false);

  // --- Profile filtering ---
  let activeProfile = $derived(profileState.active);

  // Filter sessions by profile
  let filteredSessions = $derived.by(() => {
    if (!activeProfile) return sessions;
    return sessions.filter(s => (s.workspace_profile ?? '').toLowerCase() === activeProfile.name.toLowerCase());
  });

  // Filter local processes by profile
  let filteredLocal = $derived.by(() => {
    if (!activeProfile) return localProcesses;
    return localProcesses.filter(p => (p.workspace_profile ?? '').toLowerCase() === activeProfile.name.toLowerCase());
  });

  // Filter docker stats to only containers matching filtered sessions
  let filteredDockerStats = $derived.by(() => {
    if (!activeProfile) return dockerStats;
    const sessionNames = new Set(filteredSessions.map(s => s.name));
    return dockerStats.filter(d => sessionNames.has(d.name));
  });

  // --- Derived metrics (all from filtered data) ---
  let activeSessions = $derived(filteredSessions.filter(s => s.active));
  let stoppedSessions = $derived(filteredSessions.filter(s => !s.active));
  let totalSessionCount = $derived(filteredSessions.length + filteredLocal.length);
  let activeSessionCount = $derived(activeSessions.length + filteredLocal.length);
  let runningTasks = $derived(tasks.filter(t => (t.status ?? t.Status) === 'running'));

  let containerCPU = $derived(filteredDockerStats.reduce((sum, s) => sum + parseFloat(s.cpu_perc || '0'), 0));
  let containerMem = $derived(filteredDockerStats.reduce((sum, s) => {
    const m = s.mem_usage || '';
    const match = m.match(/([\d.]+)\s*(MiB|GiB)/);
    if (!match) return sum;
    const val = parseFloat(match[1]);
    return sum + (match[2] === 'GiB' ? val * 1024 : val);
  }, 0));
  let localCPU = $derived(filteredLocal.reduce((sum, p) => sum + parseFloat(p.cpu_perc || '0'), 0));
  let localMem = $derived(filteredLocal.reduce((sum, p) => sum + parseFloat(p.mem_mb || '0'), 0));
  let totalCPU = $derived(containerCPU + localCPU);
  let totalMem = $derived(containerMem + localMem);

  // System percentages — CPU% is per-core so divide by core count for system %
  let sysCPUMax = $derived(systemInfo.cpu_cores * 100);
  let sysCPUPct = $derived(sysCPUMax > 0 ? (totalCPU / sysCPUMax) * 100 : 0);
  let sysMemTotalMiB = $derived(systemInfo.mem_total_gib * 1024);
  let sysMemPct = $derived(sysMemTotalMiB > 0 ? (totalMem / sysMemTotalMiB) * 100 : 0);

  async function refresh() {
    const a = await getApi();
    if (!a) return;
    try {
      const [sess, hubState, stats, procs] = await Promise.all([
        a.GetSessions(),
        a.GetHubState(),
        a.GetDockerStats(),
        a.FindClaudeProcesses(),
      ]);
      sessions = sess ?? [];
      tasks = hubState?.tasks ?? [];
      agents = hubState?.agents ?? [];
      tokens = hubState?.tokens ?? [];
      dockerStats = stats ?? [];
      localProcesses = procs ?? [];
    } catch (err) {
      console.error('Dashboard refresh failed:', err);
    } finally {
      loading = false;
    }
  }

  onMount(async () => {
    refresh();
    // Load system info once
    const a = await getApi();
    if (a) {
      try { systemInfo = await a.GetSystemInfo(); } catch {}
    }
  });

  // Auto-refresh on SSE events
  $effect(() => {
    const ev = brainboxEvents.last;
    if (ev) refresh();
  });

  // Poll docker stats every 10s
  onMount(() => {
    const interval = setInterval(async () => {
      const a = await getApi();
      if (a) {
        try { dockerStats = (await a.GetDockerStats()) ?? []; } catch {}
      }
    }, 10000);
    return () => clearInterval(interval);
  });

  async function handleCancel(id: string) {
    const a = await getApi();
    if (!a) return;
    try {
      await a.CancelTask(id);
      notifications.success('Task cancelled');
      refresh();
    } catch (err: any) {
      notifications.error(`Failed to cancel: ${err}`);
    }
  }

  async function handleSubmit() {
    if (!taskDesc.trim() || !taskAgent.trim()) return;
    isSubmitting = true;
    const a = await getApi();
    if (!a) { isSubmitting = false; return; }
    try {
      await a.SubmitTask({ description: taskDesc, agent_name: taskAgent, repo_url: taskRepo });
      notifications.success('Task submitted');
      showSubmitModal = false;
      taskDesc = ''; taskAgent = ''; taskRepo = '';
      refresh();
    } catch (err: any) {
      notifications.error(`Failed to submit: ${err}`);
    } finally {
      isSubmitting = false;
    }
  }
</script>

<div class="panel">
  <header>
    <h1><span class="accent">dashboard</span></h1>
    <button class="new-btn" onclick={() => showSubmitModal = true}>+ submit task</button>
  </header>

  {#if loading}
    <div class="loading">loading dashboard...</div>
  {:else}
    <!-- Overview cards -->
    <div class="cards-grid">
      <div class="stat-card">
        <div class="stat-icon sessions">
          <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/></svg>
        </div>
        <div class="stat-body">
          <span class="stat-label">Sessions</span>
          <span class="stat-value">{activeSessionCount}<span class="stat-sub"> / {totalSessionCount}</span></span>
          <span class="stat-detail">{activeSessions.length} containers, {filteredLocal.length} local{stoppedSessions.length > 0 ? `, ${stoppedSessions.length} stopped` : ''}</span>
        </div>
      </div>

      <div class="stat-card">
        <div class="stat-icon tasks">
          <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>
        </div>
        <div class="stat-body">
          <span class="stat-label">Tasks</span>
          <span class="stat-value">{runningTasks.length}<span class="stat-sub"> / {tasks.length}</span></span>
          <span class="stat-detail">{runningTasks.length} running, {agents.length} agents</span>
        </div>
      </div>

      <div class="stat-card">
        <div class="stat-icon cpu">
          <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="16" height="16" x="4" y="4" rx="2"/><rect width="6" height="6" x="9" y="9" rx="1"/><path d="M15 2v2"/><path d="M15 20v2"/><path d="M2 15h2"/><path d="M2 9h2"/><path d="M20 15h2"/><path d="M20 9h2"/><path d="M9 2v2"/><path d="M9 20v2"/></svg>
        </div>
        <div class="stat-body">
          <span class="stat-label">CPU Usage</span>
          <span class="stat-value">{sysCPUPct.toFixed(1)}<span class="stat-sub">%</span></span>
          {#if systemInfo.cpu_cores > 0}
            <div class="sys-bar-wrap">
              <div class="sys-bar cpu" style="width: {Math.min(sysCPUPct, 100)}%"></div>
            </div>
            <span class="stat-detail">{totalCPU.toFixed(1)}% of {systemInfo.cpu_cores} cores</span>
          {:else}
            <span class="stat-detail">{totalCPU.toFixed(1)}% total</span>
          {/if}
        </div>
      </div>

      <div class="stat-card">
        <div class="stat-icon memory">
          <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 19v-3"/><path d="M10 19v-3"/><path d="M14 19v-6"/><path d="M18 19v-9"/><rect width="20" height="14" x="2" y="3" rx="2"/></svg>
        </div>
        <div class="stat-body">
          <span class="stat-label">Memory</span>
          <span class="stat-value">{totalMem >= 1024 ? (totalMem / 1024).toFixed(1) : totalMem.toFixed(0)}<span class="stat-sub">{totalMem >= 1024 ? ' GiB' : ' MiB'}</span></span>
          {#if systemInfo.mem_total_gib > 0}
            <div class="sys-bar-wrap">
              <div class="sys-bar mem" style="width: {Math.min(sysMemPct, 100)}%"></div>
            </div>
            <span class="stat-detail">{sysMemPct.toFixed(1)}% of {systemInfo.mem_total_gib.toFixed(0)} GiB</span>
          {:else}
            <span class="stat-detail">total across sessions</span>
          {/if}
        </div>
      </div>
    </div>

    <!-- Resource breakdown by type -->
    <div class="breakdown-grid">
      <div class="breakdown-card">
        <span class="breakdown-label">Containers</span>
        <div class="breakdown-stats">
          <span class="breakdown-val">{containerCPU.toFixed(1)}%</span>
          <span class="breakdown-sep">cpu</span>
          <span class="breakdown-val">{containerMem >= 1024 ? (containerMem / 1024).toFixed(1) + ' GiB' : containerMem.toFixed(0) + ' MiB'}</span>
          <span class="breakdown-sep">mem</span>
          <span class="breakdown-count">{filteredDockerStats.length}</span>
        </div>
      </div>
      {#if filteredLocal.length > 0}
        <div class="breakdown-card">
          <span class="breakdown-label">Local Agents</span>
          <div class="breakdown-stats">
            <span class="breakdown-val">{localCPU.toFixed(1)}%</span>
            <span class="breakdown-sep">cpu</span>
            <span class="breakdown-val">{localMem >= 1024 ? (localMem / 1024).toFixed(1) + ' GiB' : localMem.toFixed(0) + ' MiB'}</span>
            <span class="breakdown-sep">mem</span>
            <span class="breakdown-count">{filteredLocal.length}</span>
          </div>
        </div>
      {/if}
    </div>

    <!-- Resource breakdown -->
    {#if filteredDockerStats.length > 0}
      <div class="section">
        <h2>resource usage</h2>
        <div class="resource-table">
          <div class="resource-header">
            <span class="res-col name">container</span>
            <span class="res-col">cpu</span>
            <span class="res-col">memory</span>
            <span class="res-col">net i/o</span>
            <span class="res-col">pids</span>
          </div>
          {#each filteredDockerStats as stat (stat.id)}
            <div class="resource-row">
              <span class="res-col name">{stat.name}</span>
              <span class="res-col">
                <span class="res-bar-wrap">
                  <span class="res-bar cpu" style="width: {Math.min(parseFloat(stat.cpu_perc || '0'), 100)}%"></span>
                </span>
                <span class="res-val">{stat.cpu_perc}</span>
              </span>
              <span class="res-col">
                <span class="res-val">{stat.mem_usage}</span>
              </span>
              <span class="res-col"><span class="res-val">{stat.net_io}</span></span>
              <span class="res-col"><span class="res-val">{stat.pids}</span></span>
            </div>
          {/each}
        </div>
      </div>
    {/if}

    <!-- Active tasks -->
    <div class="section">
      <h2>tasks</h2>
      {#if tasks.length === 0}
        <EmptyState title="No tasks" message="Submit a task to start an agent." />
      {:else}
        <div class="list">
          {#each tasks as task (task.id)}
            {@const status = task.status}
            <div class="row">
              <div class="row-main">
                <span class="row-desc">{task.description}</span>
                <div class="row-meta">
                  <Badge text={status} variant={status} />
                  <span class="meta-text">{task.agent_name}</span>
                  {#if task.repo_url}
                    <span class="meta-text muted">{task.repo_url.split('/').slice(-1)[0]}</span>
                  {/if}
                </div>
              </div>
              {#if status === 'running' || status === 'pending'}
                <button class="btn-task-cancel" onclick={() => handleCancel(task.id)}>cancel</button>
              {/if}
            </div>
          {/each}
        </div>
      {/if}
    </div>

    <!-- Agents -->
    <div class="section">
      <h2>agents</h2>
      {#if agents.length === 0}
        <EmptyState title="No agents registered" />
      {:else}
        <div class="agent-grid">
          {#each agents as agent (agent.name)}
            <div class="agent-card">
              <span class="agent-name">{agent.name}</span>
              <Badge text={agent.role ?? 'unknown'} variant={agent.role} />
              {#if agent.persistent}
                <span class="agent-tag">persistent</span>
              {/if}
            </div>
          {/each}
        </div>
      {/if}
    </div>
  {/if}
</div>

{#if showSubmitModal}
  <Modal onClose={() => showSubmitModal = false}>
    {#snippet children()}
      <h2>submit task</h2>
      <p class="modal-sub">dispatch a task to a registered agent</p>

      <div class="field">
        <label for="tdesc">description</label>
        <textarea id="tdesc" bind:value={taskDesc} rows="3" placeholder="What should the agent do?"></textarea>
      </div>

      <div class="field">
        <label for="tagent">agent</label>
        <input id="tagent" type="text" bind:value={taskAgent} list="agent-names" placeholder="worker" />
        <datalist id="agent-names">
          {#each agents as agent}
            <option value={agent.name}></option>
          {/each}
        </datalist>
      </div>

      <div class="field">
        <label for="trepo">repo url (optional)</label>
        <input id="trepo" type="url" bind:value={taskRepo} placeholder="https://github.com/org/repo" />
      </div>

      <div class="modal-actions">
        <button class="btn-cancel-modal" onclick={() => showSubmitModal = false} disabled={isSubmitting}>cancel</button>
        <button class="btn-submit" onclick={handleSubmit} disabled={isSubmitting || !taskDesc.trim() || !taskAgent.trim()}>
          {isSubmitting ? 'submitting...' : 'submit'}
        </button>
      </div>
    {/snippet}
  </Modal>
{/if}

<style>
  .panel { padding-bottom: 24px; }
  header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; }
  h1 { font-size: 22px; font-weight: 600; }
  .accent { color: var(--color-accent); }

  .new-btn {
    background: rgba(59, 130, 246, 0.1);
    border: 1px solid rgba(59, 130, 246, 0.3);
    color: var(--color-info);
    padding: 7px 14px;
    border-radius: var(--radius-lg);
    font-size: 13px;
    font-weight: 500;
    transition: all 0.15s;
  }
  .new-btn:hover { background: rgba(59, 130, 246, 0.2); border-color: var(--color-info); }

  .loading { color: var(--color-text-tertiary); font-size: 13px; padding: 24px 0; }

  /* === Overview cards === */
  .cards-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 16px;
    margin-bottom: 28px;
  }

  .stat-card {
    display: flex;
    align-items: flex-start;
    gap: 14px;
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-xl);
    padding: 18px;
    box-shadow: var(--shadow-card);
    transition: box-shadow 0.2s;
  }
  .stat-card:hover { box-shadow: var(--shadow-card-hover); }

  .stat-icon {
    width: 42px;
    height: 42px;
    border-radius: var(--radius-lg);
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
  }
  .stat-icon.sessions { background: rgba(59, 130, 246, 0.12); color: var(--color-info); }
  .stat-icon.tasks { background: rgba(34, 197, 94, 0.12); color: var(--color-success); }
  .stat-icon.cpu { background: rgba(245, 158, 11, 0.12); color: var(--color-accent); }
  .stat-icon.memory { background: rgba(168, 85, 247, 0.12); color: #a855f7; }

  .stat-body { display: flex; flex-direction: column; min-width: 0; }

  .stat-label {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-tertiary);
    margin-bottom: 2px;
  }

  .stat-value {
    font-size: 24px;
    font-weight: 700;
    color: var(--color-text-primary);
    line-height: 1.2;
  }

  .stat-sub {
    font-size: 14px;
    font-weight: 400;
    color: var(--color-text-tertiary);
  }

  .stat-detail {
    font-size: 11px;
    color: var(--color-text-tertiary);
    margin-top: 2px;
  }

  .sys-bar-wrap {
    width: 100%;
    height: 6px;
    background: var(--color-bg-tertiary);
    border-radius: 3px;
    overflow: hidden;
    margin-top: 6px;
  }

  .sys-bar {
    height: 100%;
    border-radius: 3px;
    transition: width 0.5s ease;
    min-width: 2px;
  }
  .sys-bar.cpu { background: var(--color-accent); }
  .sys-bar.mem { background: #a855f7; }

  /* === Resource breakdown === */
  .breakdown-grid {
    display: flex;
    gap: 12px;
    margin-bottom: 28px;
  }

  .breakdown-card {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: space-between;
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-lg);
    padding: 10px 16px;
  }

  .breakdown-label {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-tertiary);
  }

  .breakdown-stats {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .breakdown-val {
    font-family: var(--font-mono);
    font-size: 12px;
    font-weight: 600;
    color: var(--color-text-primary);
  }

  .breakdown-sep {
    font-size: 10px;
    color: var(--color-text-muted);
  }

  .breakdown-count {
    font-size: 10px;
    background: var(--color-bg-tertiary);
    color: var(--color-text-tertiary);
    padding: 1px 6px;
    border-radius: 9999px;
  }

  /* === Sections === */
  .section { margin-bottom: 28px; }

  h2 {
    font-size: 13px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--color-text-tertiary);
    margin-bottom: 14px;
  }

  /* === Resource table === */
  .resource-table {
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-xl);
    overflow: hidden;
    box-shadow: var(--shadow-card);
  }

  .resource-header, .resource-row {
    display: grid;
    grid-template-columns: 2fr 1.2fr 1.5fr 1.2fr 0.5fr;
    padding: 10px 16px;
    gap: 8px;
    align-items: center;
  }

  .resource-header {
    background: var(--color-bg-tertiary);
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--color-text-tertiary);
  }

  .resource-row {
    border-top: 1px solid var(--color-border-primary);
    font-size: 12px;
  }

  .res-col { min-width: 0; }
  .res-col.name {
    font-weight: 500;
    color: var(--color-text-primary);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .res-val {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--color-text-secondary);
  }

  .res-bar-wrap {
    display: inline-block;
    width: 48px;
    height: 6px;
    background: var(--color-bg-tertiary);
    border-radius: 3px;
    overflow: hidden;
    vertical-align: middle;
    margin-right: 6px;
  }

  .res-bar {
    height: 100%;
    border-radius: 3px;
    transition: width 0.5s;
  }
  .res-bar.cpu { background: var(--color-accent); }

  /* === Task list === */
  .list { display: flex; flex-direction: column; gap: 8px; }

  .row {
    display: flex;
    align-items: center;
    gap: 12px;
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-lg);
    padding: 12px 16px;
    box-shadow: var(--shadow-card);
  }

  .row-main { flex: 1; min-width: 0; }
  .row-desc {
    display: block;
    font-size: 13px;
    color: var(--color-text-primary);
    margin-bottom: 6px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .row-meta { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .meta-text { font-size: 11px; color: var(--color-text-secondary); }
  .meta-text.muted { color: var(--color-text-tertiary); }

  .btn-task-cancel {
    background: rgba(239, 68, 68, 0.1);
    border: 1px solid rgba(239, 68, 68, 0.2);
    color: #f87171;
    padding: 4px 10px;
    border-radius: var(--radius-md);
    font-size: 11px;
    flex-shrink: 0;
    transition: all 0.15s;
  }
  .btn-task-cancel:hover { background: rgba(239, 68, 68, 0.2); border-color: var(--color-error); }

  /* === Agent grid === */
  .agent-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    gap: 10px;
  }

  .agent-card {
    display: flex;
    flex-direction: column;
    gap: 6px;
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-lg);
    padding: 14px;
    box-shadow: var(--shadow-card);
  }

  .agent-name {
    font-size: 13px;
    font-weight: 600;
    color: var(--color-text-primary);
  }

  .agent-tag {
    font-size: 9px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-tertiary);
  }

  /* === Modal === */
  h2 { font-size: 18px; font-weight: 600; margin-bottom: 4px; }
  .modal-sub { font-size: 12px; color: var(--color-text-muted); margin-bottom: 20px; }
  .field { margin-bottom: 14px; }

  .modal-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 20px; }
  .btn-cancel-modal {
    background: none;
    border: 1px solid var(--color-border-secondary);
    color: var(--color-text-secondary);
    padding: 7px 16px;
    border-radius: var(--radius-md);
    font-size: 13px;
  }
  .btn-submit {
    background: rgba(59, 130, 246, 0.1);
    border: 1px solid rgba(59, 130, 246, 0.3);
    color: var(--color-info);
    padding: 7px 16px;
    border-radius: var(--radius-md);
    font-size: 13px;
    font-weight: 500;
    transition: all 0.15s;
  }
  .btn-submit:hover { background: rgba(59, 130, 246, 0.2); border-color: var(--color-info); }
</style>

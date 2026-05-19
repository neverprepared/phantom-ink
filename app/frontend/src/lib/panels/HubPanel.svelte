<script lang="ts">
  import { getApi } from '../utils/api';
  import { onMount } from 'svelte';
  import { brainboxEvents } from '../events.svelte';
  import { notifications } from '../notifications.svelte';
  import { profileState, panelFocus } from '../stores.svelte';
  import EmptyState from '../components/EmptyState.svelte';
  import Badge from '../components/Badge.svelte';


  // --- Data ---
  let sessions = $state<any[]>([]);
  let tasks = $state<any[]>([]);
  let agents = $state<any[]>([]);
  let tokens = $state<any[]>([]);

  // --- Submit task form ---
  const AGENT_OPTIONS = [
    { value: 'supervisor', label: 'supervisor', desc: 'orchestrates agents' },
    { value: 'worker',     label: 'worker',     desc: 'implements & opens PR' },
    { value: 'reviewer',   label: 'reviewer',   desc: 'reviews open PRs' },
    { value: 'linter',     label: 'linter',     desc: 'static analysis' },
    { value: 'qa',         label: 'qa',         desc: 'writes tests' },
    { value: 'python',     label: 'python',     desc: 'python expert' },
    { value: 'golang',     label: 'golang',     desc: 'go expert' },
    { value: 'typescript', label: 'typescript', desc: 'ts/node expert' },
    { value: 'assistant',  label: 'assistant',  desc: 'general purpose' },
  ];
  let submitAgent = $state('supervisor');
  let submitDesc = $state('');
  let submitRepo = $state('');
  let isSubmitting = $state(false);

  async function handleSubmitTask() {
    if (!submitDesc.trim()) return;
    isSubmitting = true;
    try {
      const a = await getApi();
      if (!a) return;
      await a.SubmitTask({
        description: submitDesc.trim(),
        agent_name: submitAgent,
        repo_url: submitRepo.trim() || undefined,
        workspace_profile: profileState.active?.name ?? '',
      });
      submitDesc = '';
      submitRepo = '';
      notifications.success('Task submitted');
      refresh();
    } catch (err: any) {
      notifications.error(`Submit failed: ${err?.message ?? err}`);
    } finally {
      isSubmitting = false;
    }
  }
  let dockerStats = $state<any[]>([]);
  let localProcesses = $state<any[]>([]);
  let systemInfo = $state<{ cpu_cores: number; mem_total_gib: number }>({ cpu_cores: 0, mem_total_gib: 0 });
  let loading = $state(true);

  // Autonomous-ops slice — local task queue, schedules, recent task feed.
  // These come from the in-app SQLite, not brainbox.
  let taskStats = $state<{ pending: number; running: number; succeeded: number; failed: number; cancelled: number; window_hours: number } | null>(null);
  let upcomingFires = $state<{ schedule_id: string; chain_id: string; chain_name: string; cron_expr: string; next_fire_at: string }[]>([]);
  let recentLocalTasks = $state<any[]>([]);
  let chainNames = $state<Map<string, string>>(new Map());


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
  // session name → workspace_profile lookup (covers tasks that pre-date the workspace_profile field)
  let sessionProfileMap = $derived(new Map(sessions.map((s: any) => [s.name, (s.workspace_profile ?? '').toLowerCase()])));

  // Filter tasks: use task's own workspace_profile, fall back to its session's profile.
  let filteredTasks = $derived.by(() => {
    if (!activeProfile) return tasks;
    const target = activeProfile.name.toLowerCase();
    return tasks.filter((t: any) => {
      const profile = (t.workspace_profile || sessionProfileMap.get(t.session_name) || '').toLowerCase();
      return profile === target;
    });
  });
  let runningTasks = $derived(filteredTasks.filter((t: any) => (t.status ?? t.Status) === 'running'));

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
    if (!a) { loading = false; return; }
    try {
      const [sess, hubState, stats, procs, ts, fires, localTasks, chains] = await Promise.all([
        a.GetSessions(),
        a.GetHubState(),
        a.GetDockerStats(),
        a.FindClaudeProcesses(),
        a.GetTaskStats(24),
        a.ListUpcomingFires(5),
        a.ListTasks('', 5),
        a.ListChains(),
      ]);
      sessions = sess ?? [];
      tasks = hubState?.tasks ?? [];
      agents = hubState?.agents ?? [];
      tokens = hubState?.tokens ?? [];
      dockerStats = stats ?? [];
      localProcesses = procs ?? [];
      taskStats = ts ?? null;
      upcomingFires = (fires ?? []) as any[];
      recentLocalTasks = (localTasks ?? []) as any[];
      chainNames = new Map(((chains ?? []) as any[]).map(c => [c.id, c.name]));
    } catch (err: any) {
      notifications.error(`Dashboard refresh failed: ${err?.message ?? err}`);
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
      await a.CancelHubTask(id);
      notifications.success('Task cancelled');
      refresh();
    } catch (err: any) {
      notifications.error(`Failed to cancel: ${err}`);
    }
  }

</script>

<div class="panel" aria-busy={loading}>
  <header>
    <h1><span class="accent">dashboard</span></h1>
    <button class="btn-refresh" onclick={refresh} title="Refresh" aria-label="Refresh dashboard">
      <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/></svg>
    </button>
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
          <span class="stat-value">{runningTasks.length}<span class="stat-sub"> / {filteredTasks.length}</span></span>
          <span class="stat-detail">{runningTasks.length} running</span>
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

    <!-- Autonomous ops: local task queue summary + upcoming schedules + recent feed -->
    <div class="section autonomous-section">
      <h2>autonomous ops</h2>
      <div class="auto-grid">
        <div class="auto-card">
          <span class="auto-label">queue · last {taskStats?.window_hours ?? 24}h</span>
          {#if taskStats}
            <div class="auto-stats">
              <span class="auto-stat"><span class="auto-num running">{taskStats.running}</span> running</span>
              <span class="auto-stat"><span class="auto-num pending">{taskStats.pending}</span> pending</span>
              <span class="auto-stat"><span class="auto-num ok">{taskStats.succeeded}</span> ok</span>
              <span class="auto-stat"><span class="auto-num err">{taskStats.failed}</span> failed</span>
              {#if taskStats.cancelled > 0}
                <span class="auto-stat"><span class="auto-num warn">{taskStats.cancelled}</span> cancelled</span>
              {/if}
            </div>
          {:else}
            <span class="auto-empty">no data</span>
          {/if}
        </div>

        <div class="auto-card">
          <span class="auto-label">upcoming schedules</span>
          {#if upcomingFires.length === 0}
            <span class="auto-empty">no enabled schedules</span>
          {:else}
            <div class="auto-list">
              {#each upcomingFires as f (f.schedule_id)}
                <button class="auto-row" onclick={() => panelFocus.focusChain(f.chain_id)}>
                  <span class="auto-time">{new Date(f.next_fire_at).toLocaleString()}</span>
                  <span class="auto-name">{f.chain_name || f.chain_id}</span>
                  <code class="auto-cron">{f.cron_expr}</code>
                </button>
              {/each}
            </div>
          {/if}
        </div>

        <div class="auto-card auto-card-wide">
          <span class="auto-label">recent tasks</span>
          {#if recentLocalTasks.length === 0}
            <span class="auto-empty">no tasks yet</span>
          {:else}
            <div class="auto-list">
              {#each recentLocalTasks as t (t.id)}
                <button class="auto-row" onclick={() => panelFocus.focusChain(t.chain_id)}>
                  <span class="run-dot status-{t.status}"></span>
                  <span class="auto-status">{t.status}</span>
                  <span class="auto-name">{chainNames.get(t.chain_id) ?? t.chain_id}</span>
                  <span class="auto-meta">{t.trigger}{t.started_at ? ` · ${new Date(t.started_at).toLocaleTimeString()}` : ''}</span>
                </button>
              {/each}
            </div>
          {/if}
        </div>
      </div>
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

      <!-- Submit form -->
      <div class="submit-form">
        <div class="submit-row">
          <select bind:value={submitAgent} class="agent-select" aria-label="Agent">
            {#each AGENT_OPTIONS as opt (opt.value)}
              <option value={opt.value}>{opt.label} — {opt.desc}</option>
            {/each}
          </select>
          <input
            class="repo-input"
            bind:value={submitRepo}
            placeholder="repo url (optional)"
            aria-label="Repo URL"
          />
          <button
            class="btn-submit-task"
            onclick={handleSubmitTask}
            disabled={isSubmitting || !submitDesc.trim()}
          >{isSubmitting ? 'submitting…' : 'submit'}</button>
        </div>
        <textarea
          class="desc-input"
          bind:value={submitDesc}
          rows="2"
          placeholder="Describe what the agent should do…"
          aria-label="Task description"
          onkeydown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) handleSubmitTask(); }}
        ></textarea>
      </div>

      {#if filteredTasks.length === 0}
        <EmptyState title="No tasks" message="Submit a task above to start an agent." />
      {:else}
        <div class="list">
          {#each filteredTasks as task (task.id)}
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

  {/if}
</div>


<style>
  .panel { padding: var(--panel-padding); }
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

  /* Autonomous-ops overview row */
  .autonomous-section { margin-top: 24px; }
  .auto-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 12px;
  }
  .auto-card {
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-lg);
    padding: 12px 14px;
    display: flex; flex-direction: column; gap: 8px;
  }
  .auto-card-wide { grid-column: span 2; }
  @media (max-width: 700px) { .auto-card-wide { grid-column: span 1; } }

  .auto-label {
    font-size: 10px; font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.06em;
    color: var(--color-text-tertiary);
  }
  .auto-stats { display: flex; flex-wrap: wrap; gap: 12px; }
  .auto-stat {
    display: inline-flex; align-items: baseline; gap: 4px;
    font-size: 11px; color: var(--color-text-tertiary);
  }
  .auto-num {
    font-size: 18px; font-weight: 500;
    font-variant-numeric: tabular-nums;
    color: var(--color-text-primary);
  }
  .auto-num.ok { color: var(--color-success); }
  .auto-num.err { color: var(--color-error); }
  .auto-num.warn { color: var(--color-warning, #d97706); }
  .auto-num.running { color: var(--color-info); }
  .auto-num.pending { color: var(--color-text-secondary); }

  .auto-empty { font-size: 11px; color: var(--color-text-tertiary); font-style: italic; }

  .auto-list { display: flex; flex-direction: column; gap: 2px; }
  .auto-row {
    display: flex; align-items: center; gap: 8px;
    background: none; border: none; padding: 4px 6px;
    color: inherit; cursor: pointer; font-family: inherit;
    border-radius: var(--radius-sm); text-align: left;
    font-size: 11px;
  }
  .auto-row:hover { background: rgba(255, 255, 255, 0.03); }
  .auto-time {
    font-family: var(--font-mono); font-size: 10px;
    color: var(--color-text-tertiary);
    min-width: 130px;
  }
  .auto-status {
    text-transform: uppercase; letter-spacing: 0.04em;
    font-size: 9px; font-weight: 600;
    color: var(--color-text-secondary);
    min-width: 56px;
  }
  .auto-name {
    flex: 1; color: var(--color-text-primary);
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .auto-cron {
    font-family: var(--font-mono); font-size: 10px;
    color: var(--color-text-tertiary);
    background: rgba(255, 255, 255, 0.04);
    padding: 1px 5px; border-radius: var(--radius-sm);
  }
  .auto-meta { font-size: 10px; color: var(--color-text-tertiary); }

  .run-dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--color-text-tertiary); flex-shrink: 0;
  }
  .run-dot.status-succeeded { background: var(--color-success); }
  .run-dot.status-failed { background: var(--color-error); }
  .run-dot.status-running { background: var(--color-info); }
  .run-dot.status-cancelled { background: var(--color-warning, #d97706); }
  .run-dot.status-pending { background: var(--color-text-tertiary); opacity: 0.5; }

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

  /* === Submit form === */
  .submit-form {
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-xl);
    padding: 14px 16px;
    margin-bottom: 16px;
    display: flex;
    flex-direction: column;
    gap: 10px;
    box-shadow: var(--shadow-card);
  }

  .submit-row {
    display: flex;
    gap: 8px;
    align-items: center;
  }

  .agent-select {
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-md);
    color: var(--color-text-primary);
    font-family: var(--font-mono);
    font-size: 12px;
    padding: 6px 10px;
    flex-shrink: 0;
    cursor: pointer;
  }
  .agent-select:focus { outline: 1px solid var(--color-info); border-color: var(--color-info); }

  .repo-input {
    flex: 1;
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-md);
    color: var(--color-text-primary);
    font-size: 12px;
    padding: 6px 10px;
    min-width: 0;
  }
  .repo-input::placeholder { color: var(--color-text-tertiary); }
  .repo-input:focus { outline: 1px solid var(--color-info); border-color: var(--color-info); }

  .desc-input {
    width: 100%;
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-md);
    color: var(--color-text-primary);
    font-size: 13px;
    font-family: inherit;
    padding: 8px 10px;
    resize: vertical;
    box-sizing: border-box;
  }
  .desc-input::placeholder { color: var(--color-text-tertiary); }
  .desc-input:focus { outline: 1px solid var(--color-info); border-color: var(--color-info); }

  .btn-submit-task {
    background: rgba(59, 130, 246, 0.1);
    border: 1px solid rgba(59, 130, 246, 0.3);
    color: var(--color-info);
    padding: 6px 16px;
    border-radius: var(--radius-md);
    font-size: 12px;
    font-weight: 500;
    flex-shrink: 0;
    transition: all 0.15s;
  }
  .btn-submit-task:hover:not(:disabled) { background: rgba(59, 130, 246, 0.2); border-color: var(--color-info); }
  .btn-submit-task:disabled { opacity: 0.4; cursor: not-allowed; }

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

</style>
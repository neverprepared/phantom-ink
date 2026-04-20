<script lang="ts">
  import { getApi, openInBrowser } from '../utils/api';
  import { onMount, onDestroy } from 'svelte';
  import { brainboxEvents } from '../events.svelte';
  import { notifications } from '../notifications.svelte';
  import { combinedHistory as combinedHistoryStore, localHistory as localHistoryStore, diskHistory as diskHistoryStore } from '../metricsHistory.svelte';
  import { profileState, featureFlags } from '../stores.svelte';
  import EmptyState from '../components/EmptyState.svelte';
  import Badge from '../components/Badge.svelte';
  import Modal from '../components/Modal.svelte';
  import ProfilePicker from '../components/ProfilePicker.svelte';
  import MetricsChart from '../components/MetricsChart.svelte';


  let allSessions = $state<any[]>([]);
  let tasks = $state<any[]>([]);
  let agents = $state<any[]>([]);
  let localProcesses = $state<any[]>([]);
  let diskUsageMap = $state<Record<string, string>>({}); // container name → writable size
  let diskBreakdown = $state<any | null>(null);
  let sessionHistory = $state<Record<string, any[]>>({});
  let history = $state<any[]>([]);
  let aggregateHoverIdx = $state<number | null>(null);
  let loading = $state(true);
  let showNewModal = $state(false);
  let terminalSession = $state<any | null>(null);
  let terminalUrl = $state('');
  let busySessions = $state<Set<string>>(new Set());
  let expandedMounts = $state<Set<string>>(new Set());
  let metricsTimer: ReturnType<typeof setInterval> | null = null;

  function parseMounts(volume: string): { host: string; container: string; mode: string }[] {
    if (!volume) return [];
    return volume.split(',').map(s => s.trim()).filter(Boolean).map(pair => {
      const parts = pair.split(':');
      if (parts.length === 1) return { host: parts[0], container: parts[0], mode: 'ro' };
      return { host: parts[0], container: parts[1], mode: parts[2] ?? 'ro' };
    });
  }

  function truncatePath(p: string, max = 28): string {
    if (p.length <= max) return p;
    const parts = p.split('/');
    // Keep last 2 segments visible
    const tail = parts.slice(-2).join('/');
    return tail.length + 4 >= max ? '…/' + parts[parts.length - 1] : '…/' + tail;
  }

  function toggleMounts(name: string) {
    const next = new Set(expandedMounts);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    expandedMounts = next;
  }



  // Defer iframe src to let the panel render first, avoids blank frame on first open
  $effect(() => {
    if (terminalSession?.url) {
      terminalUrl = '';
      const t = setTimeout(() => { terminalUrl = terminalSession?.url ?? ''; }, 100);
      return () => clearTimeout(t);
    } else {
      terminalUrl = '';
    }
  });

  // Available agent roles (loaded dynamically)
  let availableRoles = $state<string[]>(['assistant']);

  async function loadRoles() {
    const a = await getApi();
    if (!a) return;
    try {
      const agentDefs = await a.ListAgents();
      availableRoles = (agentDefs ?? []).map((ag: any) => ag.name);
    } catch {
      availableRoles = ['assistant'];
    }
  }

  // New session form
  let newName = $state('');
  let newRole = $state('assistant');
  let newLLM = $state('claude');
  let newModel = $state('');

  const CODEX_MODELS = [
    'gpt-5.4',
    'gpt-5.2-codex',
    'gpt-5.1-codex-max',
    'gpt-5.4-mini',
    'gpt-5.3-codex',
    'gpt-5.2',
    'gpt-5.1-codex-mini',
  ];

  const CLAUDE_MODELS = [
    'claude-opus-4-6',
    'claude-sonnet-4-6',
    'claude-haiku-4-5-20251001',
  ];

  let ollamaModels = $state<string[]>([]);
  let newBackend = $state('docker');
  let newVMTemplate = $state('');
  let newGuestOS = $state('linux');
  let newProfile = $state('');
  let isCreating = $state(false);

  // Volume mounts (browse-based)
  let mountPaths = $state<string[]>([]);

  // Task (optional — runs after session starts)
  let newTask = $state('');

  let activeProfile = $derived(profileState.active);
  let profiles = $derived(profileState.profiles);

  const DOCKER_EVENTS = ['create', 'start', 'stop', 'die', 'destroy'];

  // Filter by active TitleBar profile
  let filtered = $derived.by(() => {
    if (!activeProfile) return allSessions;
    return allSessions.filter(s => {
      const sp = (s.workspace_profile ?? '').toLowerCase();
      return sp === activeProfile.name.toLowerCase();
    });
  });

  let showStopped = $state(false);
  let activeSessions = $derived(filtered.filter(s => s.active));
  let stoppedSessions = $derived(filtered.filter(s => !s.active));
  let visibleSessions = $derived(showStopped ? filtered : activeSessions);
  let filteredLocal = $derived.by(() => {
    if (!activeProfile) return localProcesses;
    return localProcesses.filter(p => p.workspace_profile?.toLowerCase() === activeProfile.name.toLowerCase());
  });

  // Map session name → running task, and agent name → agent def
  let taskBySession = $derived(
    new Map(tasks.filter(t => t.session_name).map((t: any) => [t.session_name, t]))
  );
  let agentByName = $derived(new Map(agents.map((a: any) => [a.name, a])));

  async function refresh() {
    const a = await getApi();
    if (!a) { loading = false; return; }
    try {
      const [sess, hubState, procs, diskStats, diskBk] = await Promise.all([
        a.GetSessions(),
        a.GetHubState(),
        a.FindClaudeProcesses(),
        a.GetContainerDiskUsage().catch(() => []),
        a.GetDiskBreakdown().catch(() => null),
      ]);
      allSessions = sess ?? [];
      tasks = hubState?.tasks ?? [];
      agents = hubState?.agents ?? [];
      localProcesses = procs ?? [];
      diskBreakdown = diskBk;
      const dm: Record<string, string> = {};
      for (const d of (diskStats ?? [])) {
        dm[d.name] = d.writable_size;
      }
      diskUsageMap = dm;
    } catch (err: any) {
      notifications.error(`Failed to load sessions: ${err?.message ?? err}`);
    } finally {
      loading = false;
    }
  }

  function formatBytes(bytes: number): string {
    if (!bytes) return '–';
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  }

  function fmtMB(mb: number): string {
    if (mb >= 1024) return `${(mb / 1024).toFixed(1)} GB`;
    return `${mb.toFixed(0)} MB`;
  }

  async function refreshMetrics() {
    const a = await getApi();
    if (!a) return;

    // Metrics fetch — must not be blocked by local process scan
    try {
      const [sh, hist, diskStats] = await Promise.all([
        a.GetSessionsMetricsHistory(),
        a.GetMetricsHistory(),
        a.GetContainerDiskUsage().catch(() => []),
      ]);
      sessionHistory = sh ?? {};
      history = hist ?? [];

      // Per-container disk history
      const diskNow = Date.now();
      const diskKeys = new Set<string>();
      for (const d of (diskStats ?? [])) {
        diskKeys.add(d.name);
        diskHistoryStore.update(d.name, { ts: diskNow, bytes: d.writable_size_bytes ?? 0 });
      }
      diskHistoryStore.pruneKeys(diskKeys);
    } catch { /* non-critical */ }

    // Local process history + combined aggregate — isolated so failures don't affect metrics
    try {
      const procs = await a.FindClaudeProcesses();
      const now = Date.now();

      // Per-process history (for local agent cards)
      const activeKeys = new Set<string>();
      for (const p of (procs ?? [])) {
        const key = p.tty || p.pid;
        activeKeys.add(key);
        const cpu = parseFloat(p.cpu_perc) || 0;
        const mem = parseFloat(p.mem_mb) || 0;
        localHistoryStore.update(key, { ts: now, cpu, mem });
      }
      localHistoryStore.pruneKeys(activeKeys);

      // Combined aggregate: Docker containers + local processes
      const localAgents = (procs ?? []).length;
      const localCPU    = (procs ?? []).reduce((s, p) => s + (parseFloat(p.cpu_perc) || 0), 0);
      const localMemB   = (procs ?? []).reduce((s, p) => s + (parseFloat(p.mem_mb) || 0) * 1024 * 1024, 0);
      const latestDocker = history.length ? history[history.length - 1] : null;
      combinedHistoryStore.push({
        ts: now / 1000,
        agent_count: (latestDocker?.agent_count ?? 0) + localAgents,
        total_cpu:   (latestDocker?.total_cpu ?? 0) + localCPU,
        total_mem:   (latestDocker?.total_mem ?? 0) + localMemB,
      });
    } catch { /* non-critical */ }
  }

  let agentData  = $derived(combinedHistoryStore.value.map(s => ({ ts: s.ts, value: s.agent_count })));
  let cpuData    = $derived(combinedHistoryStore.value.map(s => ({ ts: s.ts, value: s.total_cpu })));
  let memData    = $derived(combinedHistoryStore.value.map(s => ({ ts: s.ts, value: s.total_mem / 1024 / 1024 })));
  let latestAgents = $derived(combinedHistoryStore.value.length ? String(combinedHistoryStore.value[combinedHistoryStore.value.length - 1].agent_count) : '–');
  let latestCPU    = $derived(combinedHistoryStore.value.length ? `${combinedHistoryStore.value[combinedHistoryStore.value.length - 1].total_cpu.toFixed(1)}%` : '–');
  let latestMem    = $derived(combinedHistoryStore.value.length ? formatBytes(combinedHistoryStore.value[combinedHistoryStore.value.length - 1].total_mem) : '–');

  async function handleFocusTab(tty: string) {
    const a = await getApi();
    if (!a) return;
    try {
      await a.FocusTerminalTab(tty);
    } catch (err: any) {
      notifications.error(`Could not find terminal tab: ${err}`);
    }
  }

  async function loadOllamaModels() {
    const a = await getApi();
    if (!a) return;
    try {
      const models = (await a.ListOllamaModels()) ?? [];
      ollamaModels = models.map((m: any) => m.name ?? m);
    } catch {}
  }

  onMount(() => {
    refresh();
    refreshMetrics();
    loadRoles();
    loadOllamaModels();
    metricsTimer = setInterval(refreshMetrics, 10_000);
  });

  onDestroy(() => {
    if (metricsTimer) clearInterval(metricsTimer);
  });

  $effect(() => {
    const ev = brainboxEvents.last;
    if (!ev) return;
    const raw = ev.raw;
    if (DOCKER_EVENTS.includes(raw) || (ev.data && (ev.data as any).hub)) {
      refresh();
    }
    const action = (ev.data as any)?.action ?? '';
    if (action === 'agent.created' || action === 'agent.updated' || action === 'agent.deleted') {
      loadRoles();
    }
  });

  function setBusy(name: string) {
    busySessions = new Set([...busySessions, name]);
  }
  function clearBusy(name: string) {
    const next = new Set(busySessions);
    next.delete(name);
    busySessions = next;
  }

  async function handleStart(name: string) {
    setBusy(name);
    const a = await getApi();
    if (!a) { clearBusy(name); return; }
    try {
      const resp = await a.StartSession(name);
      if (resp.success ?? resp.Success) notifications.success(`Started: ${name}`);
      else notifications.error(resp.error ?? resp.Error ?? 'Failed to start');
      refresh();
    } catch (err: any) {
      notifications.error(`Failed to start: ${err}`);
    } finally {
      clearBusy(name);
    }
  }

  async function handleStop(name: string) {
    setBusy(name);
    if (terminalSession?.name === name) terminalSession = null;
    const a = await getApi();
    if (!a) { clearBusy(name); return; }
    try {
      await a.StopSession(name);
      notifications.success(`Stopped: ${name}`);
      refresh();
    } catch (err: any) {
      notifications.error(`Failed to stop: ${err}`);
    } finally {
      clearBusy(name);
    }
  }

  async function handleDelete(name: string) {
    setBusy(name);
    if (terminalSession?.name === name) terminalSession = null;
    const a = await getApi();
    if (!a) { clearBusy(name); return; }
    try {
      await a.DeleteSession(name);
      notifications.success(`Deleted: ${name}`);
      refresh();
    } catch (err: any) {
      notifications.error(`Failed to delete: ${err}`);
    } finally {
      clearBusy(name);
    }
  }

  async function handleCancelTask(taskId: string) {
    const a = await getApi();
    if (!a) return;
    try {
      await a.CancelTask(taskId);
      notifications.success('Task cancelled');
      refresh();
    } catch (err: any) {
      notifications.error(`Failed to cancel: ${err}`);
    }
  }

  function openCreateModal() {
    newProfile = activeProfile?.name ?? profiles[0]?.name ?? '';
    mountPaths = [];
    newTask = '';
    showNewModal = true;
  }

  async function browseAndAddMount() {
    const a = await getApi();
    if (!a) return;
    try {
      const path = await a.BrowseFolder();
      if (path && !mountPaths.includes(path)) {
        mountPaths = [...mountPaths, path];
      }
    } catch {}
  }

  function removeMount(path: string) {
    mountPaths = mountPaths.filter(p => p !== path);
  }

  // Set a sensible default model when switching LLM provider.
  // Uses $state so the previous-value comparison is tracked reactively.
  let prevLLM = $state('');
  $effect(() => {
    if (newLLM !== prevLLM) {
      prevLLM = newLLM;
      if (newLLM === 'codex') newModel = 'gpt-5.4';
      else if (newLLM === 'ollama') newModel = ollamaModels[0] ?? '';
      else newModel = '';
    }
  });

  async function handleCreate() {
    if (!newName.trim() || !newProfile) return;
    isCreating = true;
    const a = await getApi();
    if (!a) { isCreating = false; return; }
    try {
      const profile = profiles.find(p => p.name === newProfile);
      const wsHome = profile?.workspace_home ?? '';
      const volumes = mountPaths.map(p => {
        const name = p.split('/').pop() || p;
        return `${p}:/home/developer/${name}`;
      });
      const req: Record<string, any> = {
        name: newName.trim().replace(/\s+/g, '-').toLowerCase(),
        role: newRole,
        volumes: volumes.length > 0 ? volumes : undefined,
        llm_provider: newLLM,
        llm_model: newLLM === 'ollama' || newLLM === 'codex' ? newModel : '',
        backend: newBackend,
        workspace_profile: newProfile,
        workspace_home: wsHome,
        task: newTask.trim() || undefined,
      };
      if (newBackend === 'utm') {
        req.vm_template = newVMTemplate;
        req.guest_os = newGuestOS;
      }
      const resp = await a.CreateSession(req);
      if (resp.success ?? resp.Success) {
        notifications.success(`Created session: ${newName}`);
        showNewModal = false;
        newName = ''; newVMTemplate = ''; mountPaths = []; newTask = '';
        refresh();
      } else {
        notifications.error(resp.error ?? resp.Error ?? 'Failed to create session');
      }
    } catch (err: any) {
      notifications.error(`Failed to create: ${err}`);
    } finally {
      isCreating = false;
    }
  }
</script>

<div class="panel" aria-busy={loading}>
  <header>
    <h1><span class="accent">sessions</span></h1>
    <div class="header-actions">
      <button class="new-btn" onclick={openCreateModal}>+ new session</button>
      <button class="refresh-btn" onclick={refresh} title="Refresh" aria-label="Refresh">
        <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/></svg>
      </button>
    </div>
  </header>

  {#if filtered.length > 0}
    <div class="stats-row">
      <span class="stat"><span class="stat-num">{activeSessions.length}</span> active</span>
      {#if stoppedSessions.length > 0}
        <button class="stat-toggle" class:active={showStopped} onclick={() => showStopped = !showStopped}>
          <span class="stat-num">{stoppedSessions.length}</span> stopped
          <svg class="toggle-chevron" class:open={showStopped} xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg>
        </button>
      {/if}
    </div>
  {/if}

  {#if diskBreakdown}
    <div class="disk-breakdown">
      <div class="disk-total">
        <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14c0 1.66 4.03 3 9 3s9-1.34 9-3V5"/><path d="M3 12c0 1.66 4.03 3 9 3s9-1.34 9-3"/></svg>
        <span class="disk-total-label">{diskBreakdown.total_label}</span>
        <span class="disk-total-sub">total disk</span>
      </div>
      <div class="disk-bar">
        {#each diskBreakdown.categories as cat (cat.name)}
          {@const pct = diskBreakdown.total_bytes > 0 ? (cat.bytes / diskBreakdown.total_bytes) * 100 : 0}
          {#if pct > 0}
            <div
              class="disk-segment disk-{cat.name}"
              style="width: {Math.max(pct, 2)}%"
              title="{cat.name}: {cat.label}"
            ></div>
          {/if}
        {/each}
      </div>
      <div class="disk-legend">
        {#each diskBreakdown.categories as cat (cat.name)}
          <span class="disk-legend-item">
            <span class="disk-dot disk-{cat.name}"></span>
            <span class="disk-cat-name">{cat.name}</span>
            <span class="disk-cat-size">{cat.label}</span>
          </span>
        {/each}
      </div>
    </div>
  {/if}

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
        formatY={fmtMB}
        hoverIdx={aggregateHoverIdx}
        onHover={(idx) => aggregateHoverIdx = idx}
        onHoverEnd={() => aggregateHoverIdx = null}
      />
    </div>
  {/if}

  {#if loading}
    <div class="loading">loading sessions...</div>
  {:else if allSessions.length === 0}
    <EmptyState title="No sessions" message="Create a new session to get started." />
  {:else if visibleSessions.length === 0 && filteredLocal.length === 0}
    <EmptyState title="No active sessions" message={stoppedSessions.length > 0 ? 'Toggle "stopped" above to view stopped sessions.' : 'Create a new session to get started.'} />
  {:else}
    <div class="session-list">
      {#each visibleSessions as session (session.name)}
        {@const active = session.active}
        {@const role = session.role ?? 'developer'}
        {@const backend = session.backend ?? 'docker'}
        {@const busy = busySessions.has(session.name)}
        {@const mounts = parseMounts(session.volume ?? '')}
        {@const mountsExpanded = expandedMounts.has(session.name)}
        {@const task = taskBySession.get(session.name)}
        {@const agent = task ? agentByName.get(task.agent_name) : null}
        {@const isPersistent = agent?.persistent ?? false}
        {@const isWorktree = session.name.startsWith('wt-')}
        {@const isPlaybook = session.name.startsWith('pb-')}
        {@const isManual = !task && !isWorktree && !isPlaybook}

        <div class="session-card" class:active class:inactive={!active}>
          <div class="card-header">
            <span class="status-dot" class:active></span>
            <span class="session-name">{session.session_name ?? session.name}</span>
            <Badge text={role} variant={role} />
            <span class="backend-badge" class:vm={backend === 'utm'}>
              {backend === 'utm' ? 'vm' : 'container'}
            </span>
            {#if isManual}
              <span class="manual-badge">manual</span>
            {/if}
            {#if isWorktree}
              <span class="worktree-badge">worktree</span>
            {/if}
            {#if isPlaybook}
              <span class="playbook-badge">playbook</span>
            {/if}
            {#if task}
              <span class="task-badge">task</span>
              {#if isPersistent}
                <span class="persistent-badge">persistent</span>
              {/if}
            {/if}
            {#if !activeProfile && session.workspace_profile}
              <span class="profile-badge">{session.workspace_profile}</span>
            {/if}
          </div>

          <div class="card-meta">
            {#if session.llm_provider}
              <span class="meta-item">{session.llm_provider}{session.llm_model ? ` / ${session.llm_model}` : ''}</span>
            {/if}
            {#if diskUsageMap[session.name]}
              <span class="meta-disk" title="Container writable layer disk usage">
                <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14c0 1.66 4.03 3 9 3s9-1.34 9-3V5"/><path d="M3 12c0 1.66 4.03 3 9 3s9-1.34 9-3"/></svg>
                {diskUsageMap[session.name]}
              </span>
            {/if}
            {#if active && session.url}
              <a class="meta-url" href={session.url} target="_blank">{session.url.replace('http://', '')}</a>
            {/if}
          </div>

          {#if active}
            {@const sname = session.session_name ?? session.name}
            {@const hist = sessionHistory[sname] ?? []}
            {@const diskHist = diskHistoryStore.value[session.name] ?? []}
            {@const memData = hist.map((s: any) => ({ ts: s.ts, value: s.mem_usage / 1024 / 1024 }))}
            {@const cpuData = hist.map((s: any) => ({ ts: s.ts, value: s.cpu_percent }))}
            {@const diskData = diskHist.map((s: any) => ({ ts: s.ts / 1000, value: s.bytes / 1024 / 1024 }))}
            {@const latestMem = hist.length ? (() => { const v = hist[hist.length-1].mem_usage; return v < 1024*1024 ? `${(v/1024).toFixed(0)} KB` : `${(v/1024/1024).toFixed(1)} MB`; })() : '–'}
            {@const latestCPU = hist.length ? `${hist[hist.length-1].cpu_percent.toFixed(1)}%` : '–'}
            {@const latestDisk = diskHist.length ? (() => { const v = diskHist[diskHist.length-1].bytes; return v < 1024*1024 ? `${(v/1024).toFixed(0)} KB` : `${(v/1024/1024).toFixed(1)} MB`; })() : (diskUsageMap[session.name] || '–')}
            {#if hist.length >= 2}
              <div class="card-charts">
                <div class="card-chart">
                  <div class="card-chart-label">
                    <span>memory</span>
                    <span class="card-chart-current">{latestMem}</span>
                  </div>
                  <MetricsChart
                    data={memData}
                    label="{sname}-mem"
                    current={latestMem}
                    color="var(--color-success)"
                    formatY={(v) => `${v.toFixed(0)}MB`}
                    width={80}
                    height={20}
                    compact={true}
                    strokeWidth={0.5}
                  />
                </div>
                <div class="card-chart">
                  <div class="card-chart-label">
                    <span>cpu</span>
                    <span class="card-chart-current">{latestCPU}</span>
                  </div>
                  <MetricsChart
                    data={cpuData}
                    label="{sname}-cpu"
                    current={latestCPU}
                    color="var(--color-info)"
                    formatY={(v) => `${v.toFixed(1)}%`}
                    width={80}
                    height={20}
                    compact={true}
                    strokeWidth={0.5}
                  />
                </div>
                {#if diskHist.length >= 2}
                  <div class="card-chart">
                    <div class="card-chart-label">
                      <span>disk</span>
                      <span class="card-chart-current">{latestDisk}</span>
                    </div>
                    <MetricsChart
                      data={diskData}
                      label="{sname}-disk"
                      current={latestDisk}
                      color="var(--color-accent)"
                      formatY={(v) => `${v.toFixed(0)}MB`}
                      width={80}
                      height={20}
                      compact={true}
                      strokeWidth={0.5}
                    />
                  </div>
                {/if}
              </div>
            {/if}
          {/if}

          {#if mounts.length > 0}
            <div class="card-mounts">
              <button class="mounts-toggle" onclick={() => toggleMounts(session.name)} aria-expanded={mountsExpanded}>
                <svg class="chevron" class:expanded={mountsExpanded} xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg>
                <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z"/></svg>
                <span class="mounts-label">{mounts.length} mount{mounts.length !== 1 ? 's' : ''}</span>
              </button>
              {#if mountsExpanded}
                <div class="mounts-list">
                  {#each mounts as mount}
                    <div class="mount-row">
                      <svg class="mount-icon" xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z"/></svg>
                      <span class="mount-host" title={mount.host}>{truncatePath(mount.host)}</span>
                      <span class="mount-arrow">→</span>
                      <span class="mount-container">{mount.container}</span>
                      <span class="mount-mode" class:mount-mode-ro={mount.mode === 'ro'}>{mount.mode}</span>
                    </div>
                  {/each}
                </div>
              {/if}
            </div>
          {/if}

          <div class="card-actions">
            {#if busy}
              <span class="busy-indicator">
                <svg class="spinner" xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>
                working...
              </span>
            {:else if active}
              {#if session.url}
                <button class="btn-terminal" onclick={() => terminalSession = session}>terminal</button>
              {/if}
              {#if task && (task.status === 'running' || task.status === 'pending')}
                <button class="btn-cancel" onclick={() => handleCancelTask(task.id)}>cancel</button>
              {:else}
                <button class="btn-stop" onclick={() => handleStop(session.name)}>stop</button>
              {/if}
            {:else}
              <button class="btn-start" onclick={() => handleStart(session.name)}>start</button>
              <button class="btn-delete" onclick={() => handleDelete(session.name)}>delete</button>
            {/if}
          </div>
        </div>
      {/each}
    </div>
  {/if}

  <!-- Local agents -->
  {#if filteredLocal.length > 0}
    <div class="local-section">
      <h2 class="local-heading">local agents</h2>
      <div class="local-list">
        {#each filteredLocal as proc (proc.pid)}
          {@const lkey = proc.tty || proc.pid}
          {@const lhist = localHistoryStore.value[lkey] ?? []}
          {@const lcpuData = lhist.map(s => ({ ts: s.ts, value: s.cpu }))}
          {@const lmemData = lhist.map(s => ({ ts: s.ts, value: s.mem }))}
          {@const latestLCPU = lhist.length ? `${lhist[lhist.length-1].cpu.toFixed(1)}%` : proc.cpu_perc}
          {@const latestLMem = lhist.length ? `${lhist[lhist.length-1].mem.toFixed(0)} MB` : proc.mem_mb}
          <div class="local-card">
            <div class="local-row">
              <span class="local-dot"></span>
              <span class="local-name">{proc.name}</span>
              <span class="local-type-badge">local</span>
              {#if !activeProfile && proc.workspace_profile}
                <span class="local-profile-badge">{proc.workspace_profile}</span>
              {/if}
              <span class="local-meta">PID {proc.pid}</span>
              <button class="btn-focus" onclick={() => handleFocusTab(proc.tty)} title="Focus terminal tab ({proc.tty})">
                <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
                focus
              </button>
            </div>
            {#if lhist.length >= 2}
              <div class="local-charts">
                <div class="card-chart">
                  <div class="card-chart-label">
                    <span>cpu</span>
                    <span class="card-chart-current">{latestLCPU}</span>
                  </div>
                  <MetricsChart
                    data={lcpuData}
                    label="{lkey}-cpu"
                    current={latestLCPU}
                    color="var(--color-info)"
                    formatY={(v) => `${v.toFixed(1)}%`}
                    width={80}
                    height={20}
                    compact={true}
                    strokeWidth={0.5}
                  />
                </div>
                <div class="card-chart">
                  <div class="card-chart-label">
                    <span>memory</span>
                    <span class="card-chart-current">{latestLMem}</span>
                  </div>
                  <MetricsChart
                    data={lmemData}
                    label="{lkey}-mem"
                    current={latestLMem}
                    color="var(--color-success)"
                    formatY={(v) => `${v.toFixed(0)} MB`}
                    width={80}
                    height={20}
                    compact={true}
                    strokeWidth={0.5}
                  />
                </div>
              </div>
            {:else}
              <div class="local-stats-text">{proc.cpu_perc} cpu &middot; {proc.mem_mb} mem</div>
            {/if}
          </div>
        {/each}
      </div>
    </div>
  {/if}
</div>

{#if terminalSession}
  <div class="terminal-panel">
    <div class="terminal-header">
      <span class="terminal-title">
        <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>
        {terminalSession.session_name ?? terminalSession.name}
      </span>
      <div class="terminal-actions">
        <button class="terminal-pop" onclick={() => openInBrowser(terminalSession.url)} title="Open in browser" aria-label="Open terminal in browser">
          <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
        </button>
        <button class="terminal-close" onclick={() => terminalSession = null} title="Close terminal" aria-label="Close terminal">
          <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
      </div>
    </div>
    {#key terminalSession.name}
      {#if terminalUrl}
        <iframe
          class="terminal-frame"
          src={terminalUrl}
          title="Terminal — {terminalSession.session_name ?? terminalSession.name}"
        ></iframe>
      {:else}
        <div class="terminal-loading">connecting...</div>
      {/if}
    {/key}
  </div>
{/if}

{#if showNewModal}
  <Modal onClose={() => showNewModal = false}>
    {#snippet children()}
      <h2>new session</h2>
      <p class="modal-sub">create an isolated environment for agentic work</p>

      <div class="field">
        <label for="sname">name</label>
        <input id="sname" type="text" bind:value={newName} placeholder="my-session" />
      </div>

      <!-- Backend -->
      <div class="field">
        <label for="sbackend">backend</label>
        <div class="toggle-group" id="sbackend">
          <button class="toggle-opt" class:active={newBackend === 'docker'} onclick={() => newBackend = 'docker'}>
            <span class="toggle-icon">&#x1f4e6;</span> container
          </button>
          <button class="toggle-opt" class:active={newBackend === 'utm'} onclick={() => newBackend = 'utm'}>
            <span class="toggle-icon">&#x1f5a5;</span> vm
          </button>
        </div>
      </div>

      {#if newBackend === 'utm'}
        <div class="field">
          <label for="svmtpl">vm template</label>
          <input id="svmtpl" type="text" bind:value={newVMTemplate} placeholder="ubuntu-24.04-brainbox" />
          <p class="hint">name of the UTM template VM to clone</p>
        </div>
        <div class="field">
          <label for="sguestos">guest os</label>
          <select id="sguestos" bind:value={newGuestOS}>
            <option value="linux">linux</option>
            <option value="macos">macos</option>
            <option value="windows">windows</option>
          </select>
        </div>
      {/if}

      <!-- Profile -->
      <ProfilePicker bind:selected={newProfile} />

      <div class="field">
        <label for="srole">role</label>
        <select id="srole" bind:value={newRole}>
          {#each availableRoles as role (role)}
            <option value={role}>{role}</option>
          {/each}
        </select>
      </div>

      <div class="field">
        <label for="sllm">llm provider</label>
        <select id="sllm" bind:value={newLLM}>
          <option value="claude">claude (anthropic)</option>
          <option value="codex">codex (openai)</option>
          {#if featureFlags.isEnabled('ollama')}
            <option value="ollama">ollama (local)</option>
          {/if}
        </select>
      </div>

      {#if newLLM === 'ollama'}
        <div class="field">
          <label for="smodel">model</label>
          <select id="smodel" bind:value={newModel}>
            {#each ollamaModels as m}
              <option value={m}>{m}</option>
            {/each}
            {#if newModel && !ollamaModels.includes(newModel)}
              <option value={newModel}>{newModel}</option>
            {/if}
          </select>
        </div>
      {/if}

      {#if newLLM === 'codex'}
        <div class="field">
          <label for="scodexmodel">model</label>
          <select id="scodexmodel" bind:value={newModel}>
            {#each CODEX_MODELS as m}
              <option value={m}>{m}</option>
            {/each}
          </select>
        </div>
      {/if}

      {#if newLLM === 'claude'}
        <div class="field">
          <label for="sclaudemodel">model</label>
          <select id="sclaudemodel" bind:value={newModel}>
            <option value="">— default —</option>
            {#each CLAUDE_MODELS as m}
              <option value={m}>{m}</option>
            {/each}
          </select>
        </div>
      {/if}

      {#if newBackend === 'docker'}
        <div class="field">
          <label for="smounts">mounts</label>
          <div class="mount-list" id="smounts">
            {#each mountPaths as path (path)}
              <div class="mount-row">
                <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z"/></svg>
                <span class="mount-path" title={path}>{truncatePath(path, 40)}</span>
                <button class="mount-remove" onclick={() => removeMount(path)} title="Remove mount" aria-label="Remove {path}">
                  <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                </button>
              </div>
            {/each}
            <button class="mount-add" onclick={browseAndAddMount}>
              <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 5v14"/><path d="M5 12h14"/></svg>
              add folder
            </button>
          </div>
        </div>
      {/if}

      <!-- Optional task -->
      <div class="field">
        <label for="stask">task (optional)</label>
        <textarea id="stask" bind:value={newTask} rows="3" placeholder="Describe what the agent should do after starting..."></textarea>
        <p class="hint">if provided, the agent will start working on this immediately</p>
      </div>

      <div class="modal-actions">
        <button class="btn-cancel" onclick={() => showNewModal = false} disabled={isCreating}>cancel</button>
        <button class="btn-submit" onclick={handleCreate} disabled={isCreating || !newName.trim() || !newProfile}>
          {isCreating ? 'creating...' : 'create'}
        </button>
      </div>
    {/snippet}
  </Modal>
{/if}

<style>
  .panel { padding: var(--panel-padding); }

  header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 20px;
  }

  h1 { font-size: 22px; font-weight: 600; }
  .accent { color: var(--color-accent); }

  .header-actions { display: flex; gap: 8px; align-items: center; }

  .charts-row {
    display: flex;
    gap: 12px;
    margin-bottom: 20px;
  }

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

  .refresh-btn {
    background: none;
    border: 1px solid var(--color-border-secondary);
    color: var(--color-text-tertiary);
    padding: 6px;
    border-radius: var(--radius-md);
    display: flex;
    transition: all 0.15s;
  }
  .refresh-btn:hover { color: var(--color-text-primary); border-color: var(--color-text-tertiary); }

  .stats-row {
    display: flex;
    align-items: center;
    gap: 16px;
    margin-bottom: 20px;
    font-size: 13px;
  }
  .stat { color: var(--color-text-tertiary); }
  .stat-num { font-weight: 600; color: var(--color-text-secondary); }
  .stat.active .stat-num { color: var(--color-success); }

  .stat-toggle {
    display: flex;
    align-items: center;
    gap: 4px;
    background: transparent;
    border: 1px solid var(--color-border-secondary);
    border-radius: var(--radius-md);
    color: var(--color-text-tertiary);
    font-size: 12px;
    padding: 3px 10px;
    transition: all 0.15s;
  }
  .stat-toggle:hover { color: var(--color-text-secondary); border-color: var(--color-text-tertiary); }
  .stat-toggle.active {
    background: var(--color-surface-hover);
    color: var(--color-text-secondary);
    border-color: var(--color-text-tertiary);
  }
  .stat-toggle .stat-num { font-weight: 600; color: inherit; }

  .toggle-chevron {
    transition: transform 0.15s;
  }
  .toggle-chevron.open { transform: rotate(180deg); }

  .loading { color: var(--color-text-tertiary); font-size: 13px; padding: 24px 0; }

  .session-list { display: flex; flex-direction: column; gap: 12px; }

  .session-card {
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-primary);
    border-left: 3px solid var(--color-border-primary);
    border-radius: var(--radius-xl);
    padding: 14px 18px;
    transition: opacity 0.2s;
  }
  .session-card.active { border-left-color: var(--color-success); }
  .session-card.inactive { opacity: 0.55; }

  .card-header {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 8px;
  }

  .status-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--color-text-muted);
    flex-shrink: 0;
  }
  .status-dot.active {
    background: var(--color-success);
    box-shadow: 0 0 6px rgba(16, 185, 129, 0.4);
  }

  .session-name {
    font-weight: 500;
    font-size: 14px;
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .backend-badge {
    font-size: 9px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    padding: 1px 6px;
    border-radius: 9999px;
    background: rgba(59, 130, 246, 0.1);
    color: var(--color-info);
    border: 1px solid rgba(59, 130, 246, 0.2);
    flex-shrink: 0;
  }
  .backend-badge.vm {
    background: rgba(168, 85, 247, 0.1);
    color: #d8b4fe;
    border-color: rgba(168, 85, 247, 0.2);
  }

  .profile-badge {
    font-size: 9px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    padding: 1px 6px;
    border-radius: 9999px;
    background: rgba(245, 158, 11, 0.1);
    color: var(--color-accent);
    border: 1px solid rgba(245, 158, 11, 0.2);
    flex-shrink: 0;
  }

  .manual-badge {
    font-size: 9px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    padding: 1px 6px;
    border-radius: 9999px;
    background: rgba(148, 163, 184, 0.1);
    color: var(--color-text-secondary);
    border: 1px solid rgba(148, 163, 184, 0.2);
    flex-shrink: 0;
  }

  .playbook-badge {
    font-size: 9px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    padding: 1px 6px;
    border-radius: 9999px;
    background: rgba(245, 158, 11, 0.1);
    color: var(--color-accent);
    border: 1px solid rgba(245, 158, 11, 0.2);
    flex-shrink: 0;
  }

  .worktree-badge {
    font-size: 9px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    padding: 1px 6px;
    border-radius: 9999px;
    background: rgba(20, 184, 166, 0.1);
    color: #2dd4bf;
    border: 1px solid rgba(20, 184, 166, 0.2);
    flex-shrink: 0;
  }

  .task-badge {
    font-size: 9px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    padding: 1px 6px;
    border-radius: 9999px;
    background: rgba(34, 197, 94, 0.1);
    color: var(--color-success);
    border: 1px solid rgba(34, 197, 94, 0.2);
    flex-shrink: 0;
  }

  .persistent-badge {
    font-size: 9px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    padding: 1px 6px;
    border-radius: 9999px;
    background: rgba(168, 85, 247, 0.1);
    color: #d8b4fe;
    border: 1px solid rgba(168, 85, 247, 0.2);
    flex-shrink: 0;
  }

  .card-meta {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 6px;
    font-size: 12px;
  }

  .meta-item {
    color: var(--color-text-tertiary);
  }

  .meta-disk {
    display: flex;
    align-items: center;
    gap: 3px;
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--color-text-tertiary);
  }

  .meta-url {
    color: var(--color-accent);
    text-decoration: none;
    font-family: var(--font-mono);
    font-size: 11px;
  }
  .meta-url:hover { text-decoration: underline; }

  /* Charts row inside card */
  .card-charts {
    margin: 6px 0 4px;
    display: flex;
    gap: 16px;
  }

  .card-chart {
    display: flex;
    align-items: center;
    gap: 10px;
    flex: 1;
    min-width: 0;
  }

  .card-chart-label {
    display: flex;
    flex-direction: column;
    gap: 1px;
    flex-shrink: 0;
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--color-text-tertiary);
  }

  .card-chart-current {
    font-size: 13px;
    font-weight: 600;
    color: var(--color-text-primary);
    text-transform: none;
    letter-spacing: 0;
  }

  /* Mounts section */
  .card-mounts {
    margin-top: 6px;
    margin-bottom: 2px;
  }

  .mounts-toggle {
    display: flex;
    align-items: center;
    gap: 5px;
    background: none;
    border: none;
    color: var(--color-text-tertiary);
    font-size: 11px;
    padding: 2px 0;
    cursor: pointer;
    transition: color 0.15s;
    text-transform: none;
    letter-spacing: normal;
    font-weight: normal;
  }
  .mounts-toggle:hover { color: var(--color-text-secondary); }

  .chevron { color: var(--color-text-tertiary); transition: transform 0.15s; flex-shrink: 0; }
  .chevron.expanded { transform: rotate(90deg); }

  .mounts-label { font-family: var(--font-mono); }

  .mounts-list {
    display: flex;
    flex-direction: column;
    gap: 3px;
    margin-top: 6px;
    padding-left: 16px;
  }

  .mount-row {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 11px;
    color: var(--color-text-tertiary);
  }

  .mount-icon { color: var(--color-text-tertiary); flex-shrink: 0; }

  .mount-host {
    font-family: var(--font-mono);
    color: var(--color-text-secondary);
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    cursor: default;
  }

  .mount-arrow { color: var(--color-text-tertiary); flex-shrink: 0; }

  .mount-container {
    font-family: var(--font-mono);
    color: var(--color-text-tertiary);
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .mount-mode {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-tertiary);
    background: var(--color-bg-tertiary, rgba(255,255,255,0.06));
    border: 1px solid var(--color-border-primary);
    border-radius: 3px;
    padding: 0 4px;
    flex-shrink: 0;
  }

  .mount-mode-ro {
    color: var(--color-warning, #f59e0b);
    border-color: var(--color-warning, #f59e0b);
  }

  .card-actions {
    margin-top: 10px;
    padding-top: 10px;
    border-top: 1px solid var(--color-border-primary);
    display: flex;
    gap: 8px;
  }

  .btn-stop, .btn-delete {
    background: rgba(239, 68, 68, 0.1);
    border: 1px solid rgba(239, 68, 68, 0.2);
    color: #f87171;
    padding: 4px 12px;
    border-radius: var(--radius-md);
    font-size: 12px;
    transition: all 0.15s;
  }
  .btn-stop { color: var(--color-error); border-color: rgba(239, 68, 68, 0.3); }
  .btn-cancel {
    background: rgba(239, 68, 68, 0.1);
    border: 1px solid rgba(239, 68, 68, 0.3);
    color: var(--color-error);
    padding: 4px 10px;
    border-radius: var(--radius-md);
    font-size: 11px;
    flex-shrink: 0;
    transition: all 0.15s;
  }
  .btn-cancel:hover { background: rgba(239, 68, 68, 0.2); border-color: var(--color-error); }
  .btn-stop:hover, .btn-delete:hover {
    background: rgba(239, 68, 68, 0.2);
    border-color: var(--color-error);
  }

  .btn-terminal {
    background: rgba(245, 158, 11, 0.1);
    border: 1px solid rgba(245, 158, 11, 0.3);
    color: var(--color-accent);
    padding: 4px 12px;
    border-radius: var(--radius-md);
    font-size: 12px;
    font-weight: 500;
    transition: all 0.15s;
  }
  .btn-terminal:hover { background: rgba(245, 158, 11, 0.2); border-color: var(--color-accent); }

  .btn-start {
    background: rgba(16, 185, 129, 0.1);
    border: 1px solid rgba(16, 185, 129, 0.3);
    color: var(--color-success);
    padding: 4px 12px;
    border-radius: var(--radius-md);
    font-size: 12px;
    transition: all 0.15s;
  }
  .btn-start:hover { background: rgba(16, 185, 129, 0.2); border-color: var(--color-success); }

  /* Modal */
  h2 { font-size: 18px; font-weight: 600; margin-bottom: 4px; }
  .modal-sub { font-size: 12px; color: var(--color-text-muted); margin-bottom: 20px; }
  .field { margin-bottom: 14px; }

  .hint {
    font-size: 11px;
    color: var(--color-text-tertiary);
    margin-top: 4px;
  }

  /* Backend toggle */
  .toggle-group {
    display: flex;
    gap: 8px;
  }

  .toggle-opt {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    background: var(--color-bg-tertiary);
    border: 1px solid var(--color-border-secondary);
    border-radius: var(--radius-md);
    color: var(--color-text-tertiary);
    padding: 8px 12px;
    font-size: 13px;
    transition: all 0.15s;
  }
  .toggle-opt:hover {
    color: var(--color-text-secondary);
    border-color: var(--color-text-tertiary);
  }
  .toggle-opt.active {
    background: rgba(59, 130, 246, 0.1);
    border-color: rgba(59, 130, 246, 0.4);
    color: var(--color-info);
    font-weight: 500;
  }

  .toggle-icon { font-size: 16px; }

  /* Mount list */
  .mount-list {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .mount-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 8px;
    background: var(--color-bg-primary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm);
    color: var(--color-text-tertiary);
  }

  .mount-path {
    flex: 1;
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--color-text-secondary);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .mount-remove {
    background: transparent;
    border: none;
    color: var(--color-text-tertiary);
    padding: 2px;
    display: flex;
    border-radius: var(--radius-sm);
    transition: all 0.15s;
  }
  .mount-remove:hover { color: var(--color-error); }

  .mount-add {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    padding: 6px;
    background: transparent;
    border: 1px dashed var(--color-border-secondary);
    border-radius: var(--radius-sm);
    color: var(--color-text-tertiary);
    font-size: 12px;
    transition: all 0.15s;
  }
  .mount-add:hover {
    border-color: var(--color-text-tertiary);
    color: var(--color-text-secondary);
    background: rgba(255, 255, 255, 0.02);
  }

  /* Profile picker */
  .modal-actions {
    display: flex;
    gap: 8px;
    justify-content: flex-end;
    margin-top: 20px;
  }

  .btn-cancel {
    background: none;
    border: 1px solid var(--color-border-secondary);
    color: var(--color-text-secondary);
    padding: 7px 16px;
    border-radius: var(--radius-md);
    font-size: 13px;
    transition: all 0.15s;
  }
  .btn-cancel:hover { background: rgba(255,255,255,0.05); }

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

  /* === Local agents === */
  .local-section {
    margin-top: 28px;
  }

  .local-heading {
    font-size: 13px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--color-text-tertiary);
    margin-bottom: 12px;
  }

  .local-list {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .local-card {
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-primary);
    border-left: 3px solid var(--color-success);
    border-radius: var(--radius-xl);
    padding: 10px 14px;
  }

  .local-row {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .local-charts {
    display: flex;
    gap: 16px;
    margin-top: 10px;
  }

  .local-stats-text {
    font-size: 11px;
    color: var(--color-text-muted);
    margin-top: 6px;
  }

  .local-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--color-success);
    box-shadow: 0 0 6px rgba(34, 197, 94, 0.4);
    flex-shrink: 0;
  }

  .local-name {
    font-size: 13px;
    font-weight: 500;
    color: var(--color-text-primary);
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .local-type-badge {
    font-size: 9px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    padding: 1px 6px;
    border-radius: 9999px;
    background: rgba(148, 163, 184, 0.1);
    color: var(--color-text-secondary);
    border: 1px solid rgba(148, 163, 184, 0.2);
    flex-shrink: 0;
  }

  .local-profile-badge {
    font-size: 9px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    padding: 1px 6px;
    border-radius: 9999px;
    background: rgba(245, 158, 11, 0.1);
    color: var(--color-accent);
    border: 1px solid rgba(245, 158, 11, 0.2);
    flex-shrink: 0;
  }

  .local-meta {
    font-size: 11px;
    color: var(--color-text-tertiary);
    font-family: var(--font-mono);
    flex-shrink: 0;
  }

  .local-tty {
    font-size: 11px;
    color: var(--color-text-tertiary);
    font-family: var(--font-mono);
    flex-shrink: 0;
  }

  .btn-focus {
    display: flex;
    align-items: center;
    gap: 4px;
    background: rgba(245, 158, 11, 0.1);
    border: 1px solid rgba(245, 158, 11, 0.25);
    color: var(--color-accent);
    padding: 4px 10px;
    border-radius: var(--radius-md);
    font-size: 11px;
    font-weight: 500;
    flex-shrink: 0;
    transition: all 0.15s;
  }
  .btn-focus:hover { background: rgba(245, 158, 11, 0.2); border-color: var(--color-accent); }

  /* Terminal panel */
  .terminal-panel {
    position: fixed;
    bottom: 28px; /* above StatusBar */
    left: var(--sidebar-width);
    right: 0;
    height: 50vh;
    display: flex;
    flex-direction: column;
    background: #000;
    border-top: 1px solid var(--color-border-secondary);
    z-index: 100;
  }

  :global(.sidebar-collapsed) .terminal-panel {
    left: var(--sidebar-collapsed-width);
  }

  .terminal-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 6px 12px;
    background: var(--color-bg-secondary);
    border-bottom: 1px solid var(--color-border-primary);
    flex-shrink: 0;
  }

  .terminal-title {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 12px;
    font-weight: 500;
    color: var(--color-text-secondary);
  }

  .terminal-actions {
    display: flex;
    gap: 4px;
  }

  .terminal-pop,
  .terminal-close {
    background: transparent;
    border: none;
    color: var(--color-text-tertiary);
    padding: 4px;
    border-radius: var(--radius-sm);
    display: flex;
    transition: all 0.15s;
  }
  .terminal-pop:hover,
  .terminal-close:hover {
    color: var(--color-text-primary);
    background: rgba(255, 255, 255, 0.08);
  }

  .terminal-frame {
    flex: 1;
    border: none;
    width: 100%;
    background: #000;
  }

  .terminal-loading {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--color-text-tertiary);
    font-size: 13px;
    background: #000;
  }
  /* Disk breakdown */
  .disk-breakdown {
    display: flex;
    align-items: center;
    gap: 14px;
    margin-bottom: 20px;
    padding: 10px 14px;
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-lg);
  }

  .disk-total {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-shrink: 0;
    color: var(--color-text-tertiary);
  }

  .disk-total-label {
    font-size: 16px;
    font-weight: 600;
    color: var(--color-text-primary);
    font-family: var(--font-mono);
  }

  .disk-total-sub {
    font-size: 11px;
    color: var(--color-text-tertiary);
  }

  .disk-bar {
    flex: 1;
    display: flex;
    height: 8px;
    border-radius: 4px;
    overflow: hidden;
    background: var(--color-bg-tertiary, rgba(255,255,255,0.06));
    gap: 1px;
  }

  .disk-segment { min-width: 3px; border-radius: 2px; }
  .disk-segment.disk-containers { background: var(--color-info); }
  .disk-segment.disk-images { background: var(--color-accent); }
  .disk-segment.disk-sessions { background: var(--color-success); }
  .disk-segment.disk-config { background: #d8b4fe; }

  .disk-legend {
    display: flex;
    gap: 10px;
    flex-shrink: 0;
  }

  .disk-legend-item {
    display: flex;
    align-items: center;
    gap: 4px;
    font-size: 11px;
    color: var(--color-text-tertiary);
  }

  .disk-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    flex-shrink: 0;
  }

  .disk-dot.disk-containers { background: var(--color-info); }
  .disk-dot.disk-images { background: var(--color-accent); }
  .disk-dot.disk-sessions { background: var(--color-success); }
  .disk-dot.disk-config { background: #d8b4fe; }

  .disk-cat-name { font-weight: 500; }
  .disk-cat-size { font-family: var(--font-mono); font-size: 10px; }
</style>

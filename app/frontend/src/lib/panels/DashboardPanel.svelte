<script lang="ts">
  import { getApi } from '../utils/api';
  import { onMount } from 'svelte';
  import { brainboxEvents } from '../events.svelte';
  import { authorityState } from '../authority.svelte';
  import { currentPanel, profileState } from '../stores.svelte';
  import { notifications } from '../notifications.svelte';

  interface ActionItem {
    kind: string;
    title: string;
    desc: string;
    severity: 'urgent' | 'warning' | 'info';
    ref?: string;
  }

  let sessions    = $state<any[]>([]);
  let hubTasks    = $state<any[]>([]);
  let fires       = $state<any[]>([]);
  let taskStats   = $state<any>(null);
  let loading     = $state(true);
  let refreshing  = $state(false);

  let activeProfile = $derived(profileState.active);

  let filteredSessions = $derived.by(() => {
    if (!activeProfile) return sessions;
    return sessions.filter((s: any) =>
      (s.workspace_profile ?? '').toLowerCase() === activeProfile!.name.toLowerCase()
    );
  });

  let filteredTasks = $derived.by(() => {
    if (!activeProfile) return hubTasks;
    return hubTasks.filter((t: any) =>
      (t.workspace_profile ?? '').toLowerCase() === activeProfile!.name.toLowerCase()
    );
  });

  let activeSessions   = $derived(filteredSessions.filter((s: any) => s.active));
  let runningHubTasks  = $derived(filteredTasks.filter((t: any) => t.status === 'running'));
  let failedHubTasks   = $derived(filteredTasks.filter((t: any) => t.status === 'failed'));

  let actionItems = $derived.by((): ActionItem[] => {
    const items: ActionItem[] = [];
    const auth = authorityState.status;
    const now = Date.now();

    if (auth) {
      if (auth.authorities.length > 0 && !auth.any_online) {
        items.push({
          kind: 'auth',
          title: 'credential authority offline',
          desc: 'all registered runners are stale — credential sealing will fail',
          severity: 'urgent',
        });
      } else if (auth.recent_failures.length > 0) {
        items.push({
          kind: 'auth_failure',
          title: `${auth.recent_failures.length} credential seal failure(s)`,
          desc: auth.recent_failures[0]?.error?.slice(0, 100) ?? '',
          severity: 'warning',
        });
      }
    }

    for (const t of runningHubTasks) {
      const created = typeof t.created_at === 'number'
        ? t.created_at
        : parseFloat(t.created_at ?? '0') * 1000;
      const ageMin = Math.floor((now - created) / 60_000);
      if (ageMin > 30) {
        items.push({
          kind: 'task_stuck',
          title: `${t.session_name || t.id.slice(0, 8)} still running`,
          desc: `running for ${ageMin}m — may be stuck`,
          severity: 'warning',
          ref: t.id,
        });
      }
    }

    for (const t of failedHubTasks.slice(0, 3)) {
      const err = typeof t.error === 'string' ? t.error : JSON.stringify(t.error ?? '');
      items.push({
        kind: 'task_failed',
        title: `task failed`,
        desc: err.slice(0, 100) || (t.description ?? '').slice(0, 100) || t.id.slice(0, 12),
        severity: 'warning',
        ref: t.id,
      });
    }

    return items;
  });

  async function load(silent = false) {
    const a = await getApi();
    if (!a) return;
    if (!silent) loading = true; else refreshing = true;
    try {
      const [s, tasks, f, ts] = await Promise.all([
        (a.GetSessions() as Promise<any>).catch(() => []),
        (a.ListHubTasks('') as Promise<any>).catch(() => []),
        (a.ListUpcomingFires(5) as Promise<any>).catch(() => []),
        (a.GetTaskStats(24) as Promise<any>).catch(() => null),
      ]);
      sessions  = s ?? [];
      hubTasks  = tasks ?? [];
      fires     = f ?? [];
      taskStats = ts;
    } finally {
      loading    = false;
      refreshing = false;
    }
  }

  onMount(() => { void load(); });

  let _lastEvent = $derived(brainboxEvents.last);
  $effect(() => {
    if (_lastEvent) void load(true);
  });

  let now = $state(new Date());
  const _tick = setInterval(() => { now = new Date(); }, 60_000);
  $effect(() => () => clearInterval(_tick));

  function formatDate(d: Date): string {
    return d.toLocaleDateString('en-US', {
      weekday: 'long', month: 'long', day: 'numeric', year: 'numeric',
    });
  }

  function formatNextFire(iso: string): string {
    const d = new Date(iso);
    const today = new Date();
    const tomorrow = new Date(today);
    tomorrow.setDate(today.getDate() + 1);
    const time = d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false });
    if (d.toDateString() === today.toDateString()) return time;
    if (d.toDateString() === tomorrow.toDateString()) return `tomorrow ${time}`;
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) + ` ${time}`;
  }

  function failedCount(): number {
    return failedHubTasks.length + (taskStats?.failed ?? 0);
  }

  function navigate(panel: string) {
    currentPanel.value = panel;
  }

  // --- Dispatch task form ---
  const AGENTS = ['supervisor','worker','reviewer','linter','qa','python','golang','typescript','assistant'];
  let dispatchAgent = $state('supervisor');
  let dispatchDesc  = $state('');
  let dispatchRepo  = $state('');
  let dispatching   = $state(false);

  async function handleDispatch() {
    if (!dispatchDesc.trim()) return;
    dispatching = true;
    try {
      const a = await getApi();
      if (!a) return;
      await a.SubmitTask({
        description: dispatchDesc.trim(),
        agent_name: dispatchAgent,
        repo_url: dispatchRepo.trim() || undefined,
        workspace_profile: profileState.active?.name ?? '',
      });
      dispatchDesc = '';
      dispatchRepo = '';
      notifications.success('Task dispatched');
      void load(true);
    } catch (err: any) {
      notifications.error(`Dispatch failed: ${err?.message ?? err}`);
    } finally {
      dispatching = false;
    }
  }
</script>

<div class="dashboard">
  <div class="header">
    <div class="brand">
      <span class="os-badge">OS</span>
      <span class="brand-name">PHANTOM-INK</span>
      {#if refreshing}<span class="refreshing">·</span>{/if}
    </div>
    <div class="datestamp">[ {formatDate(now)} ]</div>
  </div>

  <!-- Stat cards -->
  <div class="stat-row">
    <button class="stat-card" onclick={() => navigate('sessions')}>
      <span class="stat-label">» ACTIVE SESSIONS</span>
      <span class="stat-value" class:green={activeSessions.length > 0} class:muted={activeSessions.length === 0}>
        {activeSessions.length}
      </span>
    </button>

    <button class="stat-card" onclick={() => navigate('timeline')}>
      <span class="stat-label">» RUNNING TASKS</span>
      <span class="stat-value blue">{runningHubTasks.length + (taskStats?.running ?? 0)}</span>
    </button>

    <button class="stat-card" onclick={() => navigate('timeline')}>
      <span class="stat-label">» FAILED (24h)</span>
      <span class="stat-value" class:red={failedCount() > 0} class:muted={failedCount() === 0}>
        {failedCount()}
      </span>
    </button>

    <button class="stat-card" onclick={() => navigate('chains')}>
      <span class="stat-label">» SCHEDULED</span>
      <span class="stat-value">{fires.length}</span>
    </button>

    <button class="stat-card" class:urgent={actionItems.length > 0}>
      <span class="stat-label">» ACTION ITEMS</span>
      <span class="stat-value" class:orange={actionItems.length > 0} class:muted={actionItems.length === 0}>
        {actionItems.length}
      </span>
    </button>
  </div>

  <!-- Dispatch task -->
  <section class="section">
    <div class="section-header">
      <span>» DISPATCH AGENT</span>
    </div>
    <div class="dispatch-form">
      <div class="dispatch-row">
        <select bind:value={dispatchAgent} class="dispatch-select">
          {#each AGENTS as a (a)}
            <option value={a}>{a}</option>
          {/each}
        </select>
        <input
          class="dispatch-repo"
          bind:value={dispatchRepo}
          placeholder="repo url (optional)"
        />
        <button
          class="dispatch-btn"
          onclick={handleDispatch}
          disabled={dispatching || !dispatchDesc.trim()}
        >{dispatching ? '…' : '[ run ]'}</button>
      </div>
      <textarea
        class="dispatch-desc"
        bind:value={dispatchDesc}
        rows="2"
        placeholder="describe the task…"
        onkeydown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) handleDispatch(); }}
      ></textarea>
    </div>
  </section>

  <!-- Scheduled chains -->
  {#if fires.length > 0}
  <section class="section">
    <div class="section-header">
      <span>» SCHEDULED CHAINS</span>
      <span class="badge">{fires.length} upcoming</span>
    </div>
    <div class="fire-list">
      {#each fires as fire (fire.schedule_id)}
      <button class="fire-row" onclick={() => navigate('chains')}>
        <span class="fire-time">{formatNextFire(fire.next_fire_at)}</span>
        <span class="fire-name">{fire.chain_name || fire.chain_id.slice(0, 12)}</span>
        <span class="fire-cron mono">{fire.cron_expr}</span>
      </button>
      {/each}
    </div>
  </section>
  {/if}

  <!-- Action items -->
  {#if actionItems.length > 0}
  <section class="section">
    <div class="section-header">
      <span>» ACTION ITEMS</span>
      <span class="badge warn">{actionItems.length} pending</span>
    </div>
    <div class="action-list">
      {#each actionItems as item}
      <div class="action-row sev-{item.severity}">
        <span class="check">[ ]</span>
        <div class="action-body">
          <span class="action-title">{item.title}</span>
          {#if item.desc}<span class="action-desc">{item.desc}</span>{/if}
        </div>
        {#if item.ref}
          <span class="action-ref mono">{item.ref.slice(0, 8)}</span>
        {/if}
      </div>
      {/each}
    </div>
  </section>
  {/if}

  {#if loading}
    <div class="loading">loading system state…</div>
  {:else if !loading && actionItems.length === 0 && fires.length === 0}
    <div class="nominal">
      <span class="dot-green"></span>
      all systems nominal
    </div>
  {/if}
</div>

<style>
  .dashboard {
    padding: var(--panel-padding);
    display: flex;
    flex-direction: column;
    gap: var(--spacing-3xl);
    min-height: 100%;
  }

  /* --- Header --- */
  .header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding-bottom: var(--spacing-md);
    border-bottom: 1px solid var(--color-border-primary);
  }

  .brand {
    display: flex;
    align-items: center;
    gap: var(--spacing-sm);
  }

  .os-badge {
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 600;
    color: var(--color-accent);
    border: 1px solid var(--color-accent);
    border-radius: var(--radius-sm);
    padding: 1px 6px;
    letter-spacing: 0.05em;
  }

  .brand-name {
    font-family: var(--font-mono);
    font-size: 13px;
    font-weight: 600;
    letter-spacing: 0.12em;
    color: var(--color-text-primary);
  }

  .refreshing {
    color: var(--color-accent);
    animation: blink 1s step-end infinite;
  }
  @keyframes blink { 50% { opacity: 0; } }

  .datestamp {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--color-text-tertiary);
    letter-spacing: 0.04em;
  }

  /* --- Stat cards --- */
  .stat-row {
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: var(--spacing-md);
  }

  .stat-card {
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-secondary);
    border-radius: var(--radius-md);
    padding: var(--spacing-lg) var(--spacing-xl);
    cursor: pointer;
    text-align: left;
    display: flex;
    flex-direction: column;
    gap: var(--spacing-sm);
    box-shadow: var(--shadow-card);
    transition: box-shadow 120ms ease, border-color 120ms ease;
  }

  .stat-card:hover {
    box-shadow: var(--shadow-card-hover);
    border-color: var(--color-border-secondary);
  }

  .stat-card.urgent {
    border-color: rgba(234, 179, 8, 0.3);
  }

  .stat-label {
    font-family: var(--font-mono);
    font-size: 10px;
    letter-spacing: 0.08em;
    color: var(--color-text-tertiary);
    white-space: nowrap;
  }

  .stat-value {
    font-family: var(--font-mono);
    font-size: 32px;
    font-weight: 700;
    line-height: 1;
    color: var(--color-text-primary);
  }
  .stat-value.green  { color: var(--color-success); }
  .stat-value.blue   { color: var(--color-info); }
  .stat-value.red    { color: var(--color-error); }
  .stat-value.orange { color: var(--color-warning); }
  .stat-value.muted  { color: var(--color-text-muted); }

  /* --- Sections --- */
  .section {
    display: flex;
    flex-direction: column;
    gap: var(--spacing-md);
  }

  .section-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    font-family: var(--font-mono);
    font-size: 11px;
    letter-spacing: 0.08em;
    color: var(--color-text-secondary);
  }

  .badge {
    font-size: 10px;
    padding: 2px 8px;
    border-radius: var(--radius-sm);
    background: var(--color-surface-subtle);
    border: 1px solid var(--color-border-primary);
    color: var(--color-text-tertiary);
  }
  .badge.warn {
    background: rgba(234, 179, 8, 0.08);
    border-color: rgba(234, 179, 8, 0.2);
    color: var(--color-warning);
  }

  /* --- Dispatch form --- */
  .dispatch-form {
    display: flex;
    flex-direction: column;
    gap: var(--spacing-sm);
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-secondary);
    border-radius: var(--radius-md);
    padding: var(--spacing-md);
  }

  .dispatch-row {
    display: flex;
    gap: var(--spacing-sm);
    align-items: center;
  }

  .dispatch-select,
  .dispatch-repo {
    font-family: var(--font-mono);
    font-size: 12px;
    background: var(--color-bg-primary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm);
    color: var(--color-text-primary);
    padding: 5px 9px;
  }
  .dispatch-select { flex-shrink: 0; cursor: pointer; }
  .dispatch-repo { flex: 1; min-width: 0; }
  .dispatch-repo::placeholder { color: var(--color-text-muted); }
  .dispatch-select:focus,
  .dispatch-repo:focus { outline: 1px solid var(--color-accent); border-color: var(--color-accent); }

  .dispatch-desc {
    width: 100%;
    font-family: var(--font-mono);
    font-size: 12px;
    background: var(--color-bg-primary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm);
    color: var(--color-text-primary);
    padding: 6px 9px;
    resize: vertical;
    box-sizing: border-box;
  }
  .dispatch-desc::placeholder { color: var(--color-text-muted); }
  .dispatch-desc:focus { outline: 1px solid var(--color-accent); border-color: var(--color-accent); }

  .dispatch-btn {
    font-family: var(--font-mono);
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.04em;
    color: var(--color-accent);
    background: transparent;
    border: 1px solid var(--color-accent);
    border-radius: var(--radius-sm);
    padding: 5px 14px;
    flex-shrink: 0;
    transition: background 120ms ease;
  }
  .dispatch-btn:hover:not(:disabled) { background: rgba(234, 179, 8, 0.08); }
  .dispatch-btn:disabled { opacity: 0.35; cursor: not-allowed; }

  /* --- Scheduled chains --- */
  .fire-list {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .fire-row {
    display: grid;
    grid-template-columns: 120px 1fr auto;
    align-items: center;
    gap: var(--spacing-lg);
    padding: var(--spacing-sm) var(--spacing-md);
    border-radius: var(--radius-sm);
    background: transparent;
    border: none;
    cursor: pointer;
    text-align: left;
    color: inherit;
    transition: background 100ms ease;
  }
  .fire-row:hover { background: var(--color-surface-hover); }

  .fire-time {
    font-family: var(--font-mono);
    font-size: 12px;
    color: var(--color-accent);
    font-weight: 500;
  }

  .fire-name {
    font-size: 13px;
    color: var(--color-text-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .fire-cron {
    font-size: 11px;
    color: var(--color-text-muted);
  }

  /* --- Action items --- */
  .action-list {
    display: flex;
    flex-direction: column;
    gap: var(--spacing-sm);
  }

  .action-row {
    display: flex;
    align-items: flex-start;
    gap: var(--spacing-md);
    padding: var(--spacing-sm) var(--spacing-md);
    border-radius: var(--radius-sm);
    border-left: 2px solid transparent;
  }
  .action-row.sev-urgent  { border-left-color: var(--color-error); background: rgba(239, 68, 68, 0.04); }
  .action-row.sev-warning { border-left-color: var(--color-warning); background: rgba(234, 179, 8, 0.04); }
  .action-row.sev-info    { border-left-color: var(--color-info); background: rgba(14, 165, 233, 0.04); }

  .check {
    font-family: var(--font-mono);
    font-size: 12px;
    color: var(--color-text-muted);
    padding-top: 1px;
    flex-shrink: 0;
  }

  .action-body {
    display: flex;
    flex-direction: column;
    gap: 2px;
    flex: 1;
    min-width: 0;
  }

  .action-title {
    font-size: 13px;
    color: var(--color-text-primary);
  }

  .action-desc {
    font-size: 11px;
    color: var(--color-text-tertiary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .action-ref {
    font-size: 10px;
    color: var(--color-text-muted);
    flex-shrink: 0;
  }

  /* --- Loading / nominal --- */
  .loading, .nominal {
    font-family: var(--font-mono);
    font-size: 12px;
    color: var(--color-text-muted);
    display: flex;
    align-items: center;
    gap: var(--spacing-sm);
    padding: var(--spacing-2xl) 0;
  }

  .dot-green {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--color-success);
    box-shadow: var(--shadow-status-active);
    flex-shrink: 0;
  }

  .mono { font-family: var(--font-mono); }

  /* Responsive: collapse to 2-col on narrow windows */
  @media (max-width: 700px) {
    .stat-row {
      grid-template-columns: repeat(2, 1fr);
    }
  }
</style>

<script lang="ts">
  import 'gridstack/dist/gridstack.min.css';
  import 'gridstack/dist/gridstack-extra.min.css';
  import { GridStack } from 'gridstack';
  import { mount, unmount, onMount } from 'svelte';
  import { getApi } from '../utils/api';
  import { brainboxEvents } from '../events.svelte';
  import { profileState, dashboardState, dashboardDataStore, featureFlags, attentionStore, currentPanel } from '../stores.svelte';
  import { runnerQueueHistory } from '../metricsHistory.svelte';
  import { DEFAULT_LAYOUT } from '../widgets/defaultLayout';
  import type { WidgetInstance, WidgetKind, ActionItem, OpenSearchOverview } from '../widgets/types';

  import StatCounterWidget       from '../widgets/StatCounterWidget.svelte';
  import DispatchFormWidget      from '../widgets/DispatchFormWidget.svelte';
  import ActionItemsWidget       from '../widgets/ActionItemsWidget.svelte';
  import ResourceMonitorWidget   from '../widgets/ResourceMonitorWidget.svelte';
  import CustomCounterWidget     from '../widgets/CustomCounterWidget.svelte';
  import ScriptMetricWidget      from '../widgets/ScriptMetricWidget.svelte';
  import HttpMetricWidget        from '../widgets/HttpMetricWidget.svelte';
  import StreamWidget            from '../widgets/StreamWidget.svelte';
  import CalendarWidget          from '../widgets/CalendarWidget.svelte';
  import TasksWidget             from '../widgets/TasksWidget.svelte';
  import NotesWidget             from '../widgets/NotesWidget.svelte';
  import SessionsMiniWidget      from '../widgets/SessionsMiniWidget.svelte';
  import OpenSearchMetricWidget  from '../widgets/OpenSearchMetricWidget.svelte';
  import WidgetDrawer            from '../components/WidgetDrawer.svelte';


  // --- Data state ---
  let sessions    = $state<any[]>([]);
  let hubTasks    = $state<any[]>([]);
  let fires       = $state<any[]>([]);
  let taskStats   = $state<any>(null);
  let dockerStats = $state<any[]>([]);
  let localProcs  = $state<any[]>([]);
  let runners     = $state<any[]>([]);
  let systemInfo  = $state<{ cpu_cores: number; mem_total_gib: number }>({ cpu_cores: 0, mem_total_gib: 0 });
  let loading     = $state(true);
  let refreshing  = $state(false);

  // Runners are global (not profile-scoped). Threshold matches the server's
  // dispatcher eligibility window so the count matches what brainbox routes.
  const RUNNER_ONLINE_WINDOW_MS = 90_000;
  let nowMs = $state(Date.now());
  const _nowTick = setInterval(() => { nowMs = Date.now(); }, 10_000);
  $effect(() => () => clearInterval(_nowTick));

  let offlineRunners = $derived(
    runners.filter((r: any) => (nowMs - (r.last_seen ?? 0)) >= RUNNER_ONLINE_WINDOW_MS).length
  );

  // Peak queue depth observed across all runners in the last hour. Source is
  // runnerQueueHistory which is populated by RunnersPanel's 5s poll; we also
  // top it up here whenever DashboardPanel reloads so the card stays warm
  // when the user hasn't visited Runners.
  let peakQueue1h = $derived.by(() => {
    void nowMs; // recompute on the 10s tick so 1h window stays current
    void runners; // and whenever the runner list changes
    return runnerQueueHistory.peakAll(60 * 60_000);
  });

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

  let filteredDockerStats = $derived.by(() => {
    if (!activeProfile) return dockerStats;
    const sessionNames = new Set(filteredSessions.map((s: any) => s.name));
    return dockerStats.filter((d: any) => sessionNames.has(d.name));
  });

  let filteredLocal = $derived.by(() => {
    if (!activeProfile) return localProcs;
    return localProcs.filter((p: any) =>
      (p.workspace_profile ?? '').toLowerCase() === activeProfile!.name.toLowerCase()
    );
  });

  let activeSessions  = $derived(filteredSessions.filter((s: any) => s.active));
  let runningHubTasks = $derived(filteredTasks.filter((t: any) => t.status === 'running'));
  let failedHubTasks  = $derived(filteredTasks.filter((t: any) => t.status === 'failed'));

  // Parse a "12.3%" cpu_perc / "45.6%" mem_perc string from docker stats.
  function parsePct(s: any): number {
    if (typeof s !== 'string') return 0;
    const n = parseFloat(s.replace('%', '').trim());
    return Number.isFinite(n) ? n : 0;
  }

  let actionItems = $derived.by((): ActionItem[] => {
    const items: ActionItem[] = [];
    const now = Date.now();

    // 1) Bus attention envelopes — the highest-signal item per row. Wired
    // through AttentionOpenTarget so clicking jumps to the owning session /
    // loop / job, not just the Stream panel.
    for (const att of attentionStore.items.slice(0, 5)) {
      const sev: ActionItem['severity'] =
        att.status === 'failed' || att.status === 'blocked' ? 'urgent' : 'warning';
      items.push({
        kind: 'bus_attention',
        title: att.title || (att.status ? att.status.replace('_', ' ') : 'attention'),
        desc: att.reason || att.subtitle || '',
        severity: sev,
        ref: att.id,
        openAttentionId: att.id,
      });
    }

    // 2) Long-running hub tasks ("stuck").
    for (const t of runningHubTasks) {
      const ageMin = Math.floor((now - (t.created_at ?? 0)) / 60_000);
      if (ageMin > 30) {
        items.push({
          kind: 'task_stuck',
          title: `${t.session_name || t.id.slice(0, 8)} still running`,
          desc: `running for ${ageMin}m — may be stuck`,
          severity: 'warning', ref: t.id,
        });
      }
    }

    // 3) Recent failed hub tasks (cap at 3 — anything more belongs in Stream).
    for (const t of failedHubTasks.slice(0, 3)) {
      items.push({
        kind: 'task_failed', title: 'task failed',
        desc: (t.error ?? '').slice(0, 100) || (t.description ?? '').slice(0, 100) || t.id.slice(0, 12),
        severity: 'warning', ref: t.id,
      });
    }

    // 4) Resource threshold alerts. Raw bar charts in widgets are passive;
    //    threshold-derived items convert them into focus signals.
    for (const d of filteredDockerStats) {
      const cpu = parsePct(d.cpu_perc);
      const mem = parsePct(d.mem_perc);
      if (cpu > 80) {
        items.push({
          kind: 'resource_cpu',
          title: `${d.name} CPU ${cpu.toFixed(0)}%`,
          desc: 'sustained above 80% — investigate',
          severity: 'warning',
          ref: d.name,
          navTarget: 'sessions',
        });
      }
      if (mem > 80) {
        items.push({
          kind: 'resource_mem',
          title: `${d.name} memory ${mem.toFixed(0)}%`,
          desc: mem > 90 ? 'near container limit' : 'high memory pressure',
          severity: mem > 90 ? 'urgent' : 'warning',
          ref: d.name,
          navTarget: 'sessions',
        });
      }
    }

    // 5) Offline runner alert — at most one item, drills into Runners panel.
    if (offlineRunners > 0 && runners.length > 0) {
      items.push({
        kind: 'runner_offline',
        title: `${offlineRunners} runner${offlineRunners === 1 ? '' : 's'} offline`,
        desc: `${runners.length - offlineRunners}/${runners.length} reachable`,
        severity: 'warning',
        navTarget: 'runners',
      });
    }

    return items;
  });

  // --- OpenSearch overview (shared poll for opensearch-metric widgets) ---
  let opensearchOverview = $state<OpenSearchOverview | null>(null);

  // Sync computed data into shared store so widget components can read reactively
  $effect(() => {
    dashboardDataStore.value = {
      sessions, hubTasks, fires, taskStats,
      dockerStats: filteredDockerStats,
      localProcs: filteredLocal,
      systemInfo, actionItems,
      activeSessions: activeSessions.length,
      runningTasks: runningHubTasks.length + (taskStats?.running ?? 0),
      failedTasks: failedHubTasks.length + (taskStats?.failed ?? 0),
      attentionItems: attentionStore.count,
      offlineRunners,
      peakQueue1h,
      loading, refreshing,
      opensearch: opensearchOverview,
    };
  });

  // Poll OpenSearch overview only when (a) opensearch is enabled AND
  // (b) at least one opensearch-metric widget is on the dashboard.
  let opensearchPoll: ReturnType<typeof setInterval> | null = null;
  const OPENSEARCH_POLL_MS = 15_000;
  let opensearchNeeded = $derived(
    featureFlags.isEnabled('opensearch')
      && dashboardState.widgets.some(w => w.kind === 'opensearch-metric')
  );
  let opensearchWorkspace = $derived(profileState.active?.name ?? '');

  async function refreshOpenSearch(): Promise<void> {
    const a = await getApi();
    if (!a) return;
    try {
      opensearchOverview = (await a.GetObservabilityOverview(opensearchWorkspace)) as OpenSearchOverview;
    } catch {
      // leave previous value in place; widgets render '—' on null
    }
  }

  $effect(() => {
    // Re-run whenever needed/workspace change.
    const need = opensearchNeeded;
    const ws = opensearchWorkspace;
    void ws;  // dependency
    if (opensearchPoll) { clearInterval(opensearchPoll); opensearchPoll = null; }
    if (!need) {
      opensearchOverview = null;
      return;
    }
    void refreshOpenSearch();
    opensearchPoll = setInterval(refreshOpenSearch, OPENSEARCH_POLL_MS);
    return () => {
      if (opensearchPoll) { clearInterval(opensearchPoll); opensearchPoll = null; }
    };
  });

  // --- Grid state ---
  let gridEl: HTMLElement;
  let grid: GridStack | null = null;
  const mountedWidgets = new Map<string, any>();
  // Set to true while we are programmatically (re)mounting widgets so the
  // grid 'change' handler doesn't mark the layout dirty for transient reflow.
  let suppressSave = false;
  // The layout is committed EXPLICITLY via the Save button, never on a timer.
  // Debounced auto-saves raced with profile switches and cloned one profile's
  // layout onto another; an explicit commit makes the write a deliberate,
  // single-profile action. `dirty` tracks uncommitted edits.
  let dirty = $state(false);
  let saving = $state(false);
  let drawerOpen = $state(false);
  let editTarget = $state<WidgetInstance | null>(null);

  let arrangeMode = $state(false);

  // Mark the layout as having uncommitted changes (ignored during the
  // programmatic remount window). Replaces every former debounced save.
  function markDirty(): void {
    if (suppressSave) return;
    dirty = true;
  }

  // Commit the current layout for the active profile. saveLayout captures its
  // target profile synchronously, so this is always a single-profile write.
  async function commitLayout(): Promise<void> {
    if (saving) return;
    saving = true;
    try {
      await saveLayout();
      dirty = false;
    } finally {
      saving = false;
    }
  }

  function visibleWidgets(): WidgetInstance[] {
    return dashboardState.widgets;
  }

  const WIDGET_MAP: Record<WidgetKind, any> = {
    'stat-counter':     StatCounterWidget,
    'dispatch-form':    DispatchFormWidget,
    'action-items':     ActionItemsWidget,
    'resource-monitor': ResourceMonitorWidget,
    'custom-counter':   CustomCounterWidget,
    'script-metric':    ScriptMetricWidget,
    'http-metric':      HttpMetricWidget,
    'stream':           StreamWidget,
    'calendar':         CalendarWidget,
    'tasks':            TasksWidget,
    'notes':            NotesWidget,
    'sessions-mini':    SessionsMiniWidget,
    'opensearch-metric': OpenSearchMetricWidget,
  };

  function patchWidgetConfig(id: string, patch: Record<string, unknown>): void {
    const updated = dashboardState.widgets.map(existing =>
      existing.id === id ? { ...existing, config: { ...existing.config, ...patch } } : existing
    );
    dashboardState.updateWidgets(updated);
    // In-memory only: this is internal bookkeeping (a widget resolving its own
    // collect-job id at mount), not a user edit. It re-resolves on the next
    // mount via owner_widget_id, so it need not be persisted here — and must
    // not silently commit the layout. It rides along the next explicit save.
  }

  function mountWidget(w: WidgetInstance): void {
    if (!grid) return;
    const itemEl = grid.addWidget({
      id: w.id, x: w.x, y: w.y, w: w.w, h: w.h,
      minW: w.minW, minH: w.minH,
    }) as HTMLElement;
    const contentEl = itemEl.querySelector('.grid-stack-item-content') as HTMLElement;
    const props: Record<string, unknown> = { config: w.config };
    if (w.kind === 'script-metric' || w.kind === 'http-metric') {
      props.widgetId = w.id;
      props.onConfigUpdate = (patch: Record<string, unknown>) => patchWidgetConfig(w.id, patch);
    }
    const instance = mount(WIDGET_MAP[w.kind], { target: contentEl, props });
    mountedWidgets.set(w.id, instance);

    const editBtn = document.createElement('button');
    editBtn.className = 'widget-edit-btn';
    editBtn.textContent = '✎';
    editBtn.setAttribute('aria-label', 'Edit widget');
    editBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      const current = dashboardState.widgets.find(x => x.id === w.id);
      if (current) { editTarget = current; drawerOpen = true; }
    });
    itemEl.appendChild(editBtn);

    const btn = document.createElement('button');
    btn.className = 'widget-remove-btn';
    btn.textContent = '✕';
    btn.setAttribute('aria-label', 'Remove widget');
    btn.addEventListener('click', (e) => { e.stopPropagation(); handleRemoveWidget(w.id); });
    itemEl.appendChild(btn);

    // Uniform card header — title + drag handle. Replaces the previous
    // grip overlay and supersedes any internal widget-header (suppressed
    // via CSS below) so every card looks consistent.
    const dragSvg = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true">
      <circle cx="9" cy="6" r="1.4" fill="currentColor" stroke="none"/>
      <circle cx="9" cy="12" r="1.4" fill="currentColor" stroke="none"/>
      <circle cx="9" cy="18" r="1.4" fill="currentColor" stroke="none"/>
      <circle cx="15" cy="6" r="1.4" fill="currentColor" stroke="none"/>
      <circle cx="15" cy="12" r="1.4" fill="currentColor" stroke="none"/>
      <circle cx="15" cy="18" r="1.4" fill="currentColor" stroke="none"/>
    </svg>`;
    const title = ((w.config as any).label?.trim()) || KIND_TITLES[w.kind];
    const cardHeader = document.createElement('div');
    cardHeader.className = 'widget-card-header widget-drag-handle';
    cardHeader.innerHTML = `<span class="widget-card-grip" aria-hidden="true">${dragSvg}</span><span class="widget-card-title">${title}</span>`;
    contentEl.prepend(cardHeader);
  }

  // Display titles when the widget config has no `label`. Mirrors
  // KIND_LABELS in WidgetDrawer.
  const KIND_TITLES: Record<WidgetKind, string> = {
    'stat-counter':      'Stat Counter',
    'custom-counter':    'Custom Counter',
    'script-metric':     'Script Metric',
    'http-metric':       'HTTP Metric',
    'calendar':          'Task Calendar',
    'tasks':             'Tasks',
    'sessions-mini':     'Live Sessions',
    'notes':             'Scratchpad',
    'dispatch-form':     'Dispatch Form',
    'action-items':      'Action Items',
    'resource-monitor':  'Resource Monitor',
    'stream':            'Stream',
    'opensearch-metric': 'OpenSearch Metric',
  };

  // Locked-in profile name used by saveLayout. Set when the panel mounts
  // and updated explicitly during profile switch — never reads from the
  // live profileState, so a debounced save can't write to the wrong key
  // when the user switches profiles mid-debounce.
  let saveProfile = '';

  async function saveLayout(profileNameOverride?: string): Promise<void> {
    if (!grid) return;
    // Capture the target profile SYNCHRONOUSLY, before any await. The widget
    // set below is read from the live DOM synchronously too, so target and
    // widgets are a consistent pair. Reading saveProfile after an await would
    // let a profile switch mid-save write THIS profile's widgets under the
    // NEXT profile's key — that is how a dashboard layout gets cloned onto
    // another profile (cross-profile data leak).
    const target = profileNameOverride ?? saveProfile;
    const current = dashboardState.widgets;
    // Read positions from the DOM directly — more reliable than
    // grid.save() which historically drops ids in some scenarios.
    const updated: WidgetInstance[] = [];
    for (const orig of current) {
      const el = gridEl?.querySelector(`[gs-id="${orig.id}"]`) as HTMLElement | null;
      if (!el) {
        // Element not in the grid right now — keep stored values.
        updated.push(orig);
        continue;
      }
      const xRaw = el.getAttribute('gs-x');
      const yRaw = el.getAttribute('gs-y');
      const wRaw = el.getAttribute('gs-w');
      const hRaw = el.getAttribute('gs-h');
      updated.push({
        ...orig,
        x: xRaw !== null ? Number(xRaw) : orig.x,
        y: yRaw !== null ? Number(yRaw) : orig.y,
        w: wRaw !== null ? Number(wRaw) : orig.w,
        h: hRaw !== null ? Number(hRaw) : orig.h,
      });
    }
    // Hard safety net: never persist an empty layout when state still
    // believes there are widgets.
    if (updated.length === 0 && current.length > 0) return;
    dashboardState.updateWidgets(updated);
    const a = await getApi();
    if (a) {
      try {
        await a.SaveDashboardLayout(
          target,
          JSON.stringify({
            version: 1,
            widgets: updated,
          }),
        );
      } catch {}
    }
  }

  function handleUpdateWidget(w: WidgetInstance): void {
    const updated = dashboardState.widgets.map(existing => existing.id === w.id ? { ...existing, config: w.config } : existing);
    dashboardState.updateWidgets(updated);

    // Re-mount the widget with its new config
    const inst = mountedWidgets.get(w.id);
    const el = gridEl?.querySelector(`[gs-id="${w.id}"]`) as HTMLElement | null;
    if (inst && el) {
      unmount(inst);
      const contentEl = el.querySelector('.grid-stack-item-content') as HTMLElement;
      // Preserve the injected header — it lives outside the widget body.
      const cardHeader = contentEl.querySelector('.widget-card-header');
      // Clear everything except the header before re-mounting.
      Array.from(contentEl.childNodes).forEach(n => { if (n !== cardHeader) contentEl.removeChild(n); });
      const reProps: Record<string, unknown> = { config: w.config };
      if (w.kind === 'script-metric' || w.kind === 'http-metric') {
        reProps.widgetId = w.id;
        reProps.onConfigUpdate = (patch: Record<string, unknown>) => patchWidgetConfig(w.id, patch);
      }
      const newInst = mount(WIDGET_MAP[w.kind], { target: contentEl, props: reProps });
      mountedWidgets.set(w.id, newInst);
      // Update the title in case the user changed config.label.
      const titleEl = cardHeader?.querySelector('.widget-card-title') as HTMLElement | null;
      if (titleEl) titleEl.textContent = ((w.config as any).label?.trim()) || KIND_TITLES[w.kind];
    }

    markDirty();
  }

  function handleAddWidget(w: WidgetInstance): void {
    if (!grid) return;
    // Drawer creates new widgets with y=999 expecting GridStack to compact.
    // With float=true that no longer happens, so place the widget at the
    // first column on the row directly below all existing widgets.
    const maxBottom = dashboardState.widgets.reduce(
      (acc, ex) => Math.max(acc, (ex.y ?? 0) + (ex.h ?? 1)),
      0,
    );
    const placed: WidgetInstance = { ...w, x: 0, y: maxBottom };
    dashboardState.updateWidgets([...dashboardState.widgets, placed]);
    mountWidget(placed);
    // Drawer stays open so the user can add multiple widgets in one session.
    // Backdrop click / close button still dismisses it.
    markDirty();
  }

  async function pruneDuplicateMetricJobs(widgets: WidgetInstance[]): Promise<void> {
    const a = await getApi();
    if (!a) return;
    const profile = profileState.active?.name ?? '';
    // Build a map of widgetId → kept jobId for every metric widget on
    // this dashboard. Also collect legacy (name, command) fingerprints
    // so we can still match older jobs that have no owner_widget_id.
    const byWidget = new Map<string, string>();         // widgetId → keepJobId
    const byFingerprint = new Map<string, string>();    // key → keepJobId
    for (const w of widgets) {
      if (w.kind !== 'script-metric' && w.kind !== 'http-metric') continue;
      const cfg = w.config as any;
      const jobId = cfg?.jobId as string | undefined;
      if (!jobId) continue;
      byWidget.set(w.id, jobId);
      let command = cfg.command as string | undefined;
      if (w.kind === 'http-metric') {
        command = `op run -- curl -sf${cfg.header ? ` -H "${cfg.header}"` : ''} "${cfg.url}"` +
          (cfg.path ? ` | jq -r '.${cfg.path}'` : '');
      }
      if (command) byFingerprint.set(`${cfg.label}::${command}`, jobId);
    }
    if (byWidget.size === 0 && byFingerprint.size === 0) return;
    let jobs: any[] = [];
    try { jobs = (await (a as any).ListCollectJobs(profile)) ?? []; } catch { return; }
    for (const job of jobs) {
      // Prefer the direct id link.
      if (job.owner_widget_id) {
        const keepId = byWidget.get(job.owner_widget_id);
        if (keepId && job.id !== keepId) {
          try { await (a as any).DeleteCollectJob(job.id); } catch {}
        }
        continue;
      }
      // Legacy fingerprint match — same widget's keep id wins.
      const keepId = byFingerprint.get(`${job.name}::${job.command}`);
      if (keepId && job.id !== keepId) {
        try { await (a as any).DeleteCollectJob(job.id); } catch {}
      }
    }
  }

  async function handleRemoveWidget(id: string): Promise<void> {
    if (!grid) return;
    // If this widget owns a collect job, delete it so the scheduler stops
    // running it and orphaned entries don't accumulate.
    const removed = dashboardState.widgets.find(w => w.id === id);
    const jobId = (removed?.config as any)?.jobId as string | undefined;
    const inst = mountedWidgets.get(id);
    if (inst) { unmount(inst); mountedWidgets.delete(id); }
    const el = gridEl.querySelector(`[gs-id="${id}"]`) as HTMLElement | null;
    if (el) grid.removeWidget(el, true);
    dashboardState.updateWidgets(dashboardState.widgets.filter(w => w.id !== id));
    markDirty();
    if (jobId) {
      const a = await getApi();
      try { await (a as any)?.DeleteCollectJob?.(jobId); } catch {}
    }
  }

  async function reloadLayout(profileName: string): Promise<void> {
    if (!grid) return;
    // Suppress dirty-marking BEFORE we tear the grid down — removeAll() fires
    // 'change' events that would otherwise mark a transiently-empty grid dirty.
    suppressSave = true;
    // Loading a profile's stored layout discards any uncommitted edits from the
    // previous view — the Save button is the only commit point.
    dirty = false;
    try {
      for (const [, inst] of mountedWidgets) unmount(inst);
      mountedWidgets.clear();
      grid.removeAll();

      const a = await getApi();
      let layout: typeof dashboardState.layout = { version: 1, widgets: [...DEFAULT_LAYOUT.widgets] };
      if (a) {
        const stored = await a.GetDashboardLayout(profileName).catch(() => '');
        if (stored) {
          try {
            const parsed = JSON.parse(stored);
            if (parsed?.version === 1 && Array.isArray(parsed.widgets) && parsed.widgets.length > 0) {
              layout = parsed;
            }
          } catch {}
        }
      }
      dashboardState.layout = layout;

      for (const w of visibleWidgets()) mountWidget(w);
    } finally {
      // Wait a frame so any deferred 'change' events fire while
      // suppression is still active.
      await new Promise<void>((r) => requestAnimationFrame(() => r()));
      suppressSave = false;
    }
  }

  function handleReset(): void {
    if (!grid) return;
    for (const [, inst] of mountedWidgets) unmount(inst);
    mountedWidgets.clear();
    grid.removeAll();
    const layout = { version: 1 as const, widgets: [...DEFAULT_LAYOUT.widgets] };
    dashboardState.layout = layout;
    for (const w of layout.widgets) mountWidget(w);
    drawerOpen = false;
    markDirty();
  }

  // --- Data loading ---
  async function load(silent = false): Promise<void> {
    const a = await getApi();
    if (!a) return;
    if (!silent) loading = true; else refreshing = true;
    try {
      const [s, tasks, f, ts, ds, procs, rs] = await Promise.all([
        (a.GetSessions(profileState.active?.name ?? '') as Promise<any>).catch(() => []),
        (a.ListHubTasks('', profileState.active?.name ?? '') as Promise<any>).catch(() => []),
        (a.ListUpcomingFires(5) as Promise<any>).catch(() => []),
        (a.GetTaskStats(24) as Promise<any>).catch(() => null),
        (a.GetDockerStats() as Promise<any>).catch(() => []),
        (a.FindClaudeProcesses() as Promise<any>).catch(() => []),
        (a.ListRunners() as Promise<any>).catch(() => []),
      ]);
      sessions    = s ?? [];
      hubTasks    = tasks ?? [];
      fires       = f ?? [];
      taskStats   = ts;
      dockerStats = ds ?? [];
      localProcs  = procs ?? [];
      runners     = rs ?? [];
      // Feed runner queue history from here too — keeps the peak-1h card
      // current even when the user hasn't opened Runners.
      const ts2 = Date.now();
      const active = new Set<string>();
      for (const r of runners) {
        active.add(r.name);
        runnerQueueHistory.update(r.name, {
          ts: ts2,
          depth: r.queue_depth || 0,
          inflight: r.in_flight || 0,
        });
      }
      runnerQueueHistory.pruneKeys(active);
    } finally {
      loading    = false;
      refreshing = false;
    }
  }

  // Reload layout and data when profile changes (after initial mount).
  // No auto-save on switch: layout commits are explicit (the Save button), so
  // switching profiles simply loads the target's stored layout and discards
  // any uncommitted edits (reloadLayout resets `dirty`). This removes the
  // save/switch race that could clone one profile's layout onto another.
  let _trackedProfile = '';
  $effect(() => {
    const name = profileState.active?.name ?? '';
    if (name !== _trackedProfile && grid) {
      _trackedProfile = name;
      saveProfile = name;
      void reloadLayout(name);
      void load();
    }
  });

  let _lastEvent = $derived(brainboxEvents.last);
  $effect(() => { if (_lastEvent) void load(true); });

  let now = $state(new Date());
  const _tick = setInterval(() => { now = new Date(); }, 60_000);
  $effect(() => () => clearInterval(_tick));

  function formatDate(d: Date): string {
    return d.toLocaleDateString('en-US', {
      weekday: 'long', month: 'long', day: 'numeric', year: 'numeric',
    });
  }

  onMount(async () => {
    const a = await getApi();
    let layout = { version: 1 as const, widgets: [...DEFAULT_LAYOUT.widgets] };

    if (a) {
      const stored = await a.GetDashboardLayout(profileState.active?.name ?? '').catch(() => '');
      if (stored) {
        try {
          const parsed = JSON.parse(stored);
          if (parsed?.version === 1 && Array.isArray(parsed.widgets) && parsed.widgets.length > 0) {
            layout = parsed;
          }
        } catch {}
      }
      try { systemInfo = await a.GetSystemInfo(); } catch {}
    }

    dashboardState.layout = layout;
    _trackedProfile = profileState.active?.name ?? '';
    saveProfile = _trackedProfile;

    grid = GridStack.init({
      column: 12,
      cellHeight: 60,
      cellHeightUnit: 'px',
      margin: 8,
      animate: true,
      // float=true keeps widgets where the user placed them. Without
      // it, GridStack auto-compacts toward the top on every mount,
      // which drifts positions across navigations.
      float: true,
      draggable: { handle: '.widget-drag-handle' },
      resizable: { handles: 'se' },
    }, gridEl);

    suppressSave = true;
    for (const w of layout.widgets) mountWidget(w);
    requestAnimationFrame(() => { suppressSave = false; });

    // Prune duplicate collect jobs that share (profile, name, command)
    // with a widget-bound job. These accumulated when earlier widget
    // mounts created a new job each time before the jobId was persisted.
    void pruneDuplicateMetricJobs(layout.widgets);

    // 'change' covers drag + resize + add + remove. Listen to 'resizestop'
    // and 'dragstop' as well — in some GridStack versions a resize that
    // ends at the same grid-cell boundary it started near does not emit a
    // 'change', causing the new (smaller) size to be lost.
    // Drag / resize / add / remove mark the layout dirty; the user commits
    // explicitly. resizestop/dragstop are covered too — some GridStack
    // versions don't emit 'change' for a resize that ends near its start.
    grid.on('change', markDirty);
    grid.on('resizestop', markDirty);
    grid.on('dragstop', markDirty);

    void load();

    const dockerInterval = setInterval(async () => {
      const api = await getApi();
      if (api) { try { dockerStats = (await api.GetDockerStats()) ?? []; } catch {} }
    }, 10_000);

    return () => {
      for (const [, inst] of mountedWidgets) unmount(inst);
      mountedWidgets.clear();
      grid?.destroy(false);
      clearInterval(dockerInterval);
    };
  });
</script>

<div class="dashboard">
  <div class="header">
    <div class="brand">
      <span class="brand-name">PHANTOM-INK</span>
      <span class="os-badge">OS</span>
      {#if refreshing}<span class="refreshing">·</span>{/if}
    </div>
    <div class="datestamp">[ {formatDate(now)} ]</div>
  </div>

  <div class="dashboard-actions">
    {#if dirty}
      <span class="unsaved" title="This dashboard has uncommitted changes">● unsaved</span>
      <button
        class="ds-btn sm primary"
        disabled={saving}
        onclick={commitLayout}
      >{saving ? 'saving…' : 'save'}</button>
    {/if}
    {#if arrangeMode}
      <button class="ds-btn sm" onclick={() => drawerOpen = true}>+ widget</button>
    {/if}
    <button
      class="ds-btn sm {arrangeMode ? 'primary' : ''}"
      onclick={() => { arrangeMode = !arrangeMode; if (!arrangeMode) drawerOpen = false; }}
    >{arrangeMode ? 'done' : 'arrange'}</button>
  </div>

  <div class="grid-wrap" class:arrange={arrangeMode}>
    <div class="grid-stack" bind:this={gridEl}></div>
  </div>
</div>

<WidgetDrawer
  open={drawerOpen}
  onClose={() => { drawerOpen = false; editTarget = null; }}
  onAdd={handleAddWidget}
  onUpdate={handleUpdateWidget}
  onRemove={handleRemoveWidget}
  onReset={handleReset}
  widgets={dashboardState.widgets}
  editTarget={editTarget}
  onEditConsumed={() => editTarget = null}
/>

<style>
  .dashboard {
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow: hidden;
  }

  .header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: var(--panel-padding);
    padding-bottom: var(--spacing-md);
    border-bottom: 1px solid var(--border, var(--color-border-primary));
    flex-shrink: 0;
  }

  .brand {
    display: flex;
    align-items: center;
    gap: 14px;
  }

  .os-badge {
    font-family: var(--font-mono);
    font-size: 13px;
    font-weight: 700;
    color: var(--accent, var(--color-accent));
    border: 1.5px solid var(--accent, var(--color-accent));
    border-radius: 6px;
    padding: 3px 7px;
    letter-spacing: 0.05em;
  }

  .brand-name {
    font-family: var(--font-mono);
    font-size: 22px;
    font-weight: 600;
    letter-spacing: 0.04em;
    color: var(--text, var(--color-text-primary));
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

  .dashboard-actions {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px var(--panel-padding);
    border-bottom: 1px solid var(--border, var(--color-border-primary));
    flex-shrink: 0;
    justify-content: flex-end;
  }

  .unsaved {
    font-family: var(--font-mono);
    font-size: 10px;
    letter-spacing: 0.04em;
    color: var(--color-warning, var(--color-accent));
    margin-right: 2px;
    white-space: nowrap;
  }

  .grid-wrap {
    flex: 1;
    overflow-y: auto;
    padding: 8px;
  }

  /* In arrange mode, always show widget edit + remove buttons */
  .grid-wrap.arrange :global(.widget-remove-btn) { opacity: 1; }
  .grid-wrap.arrange :global(.widget-edit-btn) { opacity: 1; }

  /* Uniform card header injected into every widget. Styled to match the
     runner-panel table header (uppercase, tertiary text, subtle bg). */
  :global(.widget-card-header) {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 4px 8px;
    background: var(--bg-sunken, var(--color-surface-subtle));
    border-bottom: 1px solid var(--border, var(--color-border-primary));
    color: var(--text-faint, var(--color-text-tertiary));
    font-size: 9px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.03em;
    cursor: grab;
    user-select: none;
    flex-shrink: 0;
  }
  :global(.widget-card-header:active) { cursor: grabbing; }
  :global(.widget-card-grip) {
    display: inline-flex;
    align-items: center;
    color: var(--text-faint, var(--color-text-tertiary));
    opacity: 0.6;
    flex-shrink: 0;
  }
  :global(.widget-card-grip svg) { width: 11px; height: 11px; }
  :global(.widget-card-title) {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  /* Suppress widgets' internal headers and inline labels since the card
     header carries the title now. Each widget keeps its own layout below. */
  :global(.grid-stack-item .widget-header) { display: none !important; }
  :global(.grid-stack-item .stat-label) { display: none !important; }
  :global(.grid-stack-item .drag-strip) { display: none !important; }

  /* Widgets must not auto-scroll. If content doesn't fit, the user
     should resize the widget. Override per-widget overflow rules. */
  :global(.grid-stack-item .widget-body),
  :global(.grid-stack-item .session-list),
  :global(.grid-stack-item .task-list),
  :global(.grid-stack-item .item-list),
  :global(.grid-stack-item .scroll),
  :global(.grid-stack-item [class*="-list"]),
  :global(.grid-stack-item [class*="-body"]) {
    overflow: hidden !important;
    overflow-y: hidden !important;
  }

  /* gridstack overrides */
  :global(.grid-stack-item-content) {
    background: var(--bg-elev, var(--color-bg-secondary));
    border: 1px solid var(--border, var(--color-border-secondary));
    border-radius: var(--r-lg, var(--radius-lg));
    overflow: hidden;
    box-shadow: var(--shadow-sm, var(--shadow-card));
    display: flex;
    flex-direction: column;
  }
  /* Widget root element (the first non-header child) fills remaining height. */
  :global(.grid-stack-item-content > *:not(.widget-card-header):not(.widget-edit-btn):not(.widget-remove-btn)) {
    flex: 1;
    min-height: 0;
    overflow: hidden;
  }

  :global(.grid-stack-item > .ui-resizable-se) {
    bottom: 4px;
    right: 4px;
    width: 12px;
    height: 12px;
    opacity: 0.25;
  }
  :global(.grid-stack-item:hover > .ui-resizable-se) { opacity: 0.6; }

  :global(.widget-remove-btn) {
    position: absolute;
    top: 6px;
    right: 6px;
    width: 20px;
    height: 20px;
    border-radius: 50%;
    background: var(--color-bg-primary);
    border: 1px solid var(--color-border-primary);
    color: var(--color-text-muted);
    font-size: 10px;
    line-height: 1;
    cursor: pointer;
    z-index: 10;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 0;
    opacity: 0;
    transition: opacity 120ms ease, background 120ms ease, color 120ms ease;
  }
  :global(.grid-stack-item:hover .widget-remove-btn) { opacity: 1; }
  :global(.widget-remove-btn:hover) {
    background: var(--color-error);
    border-color: var(--color-error);
    color: #fff;
  }

  :global(.widget-edit-btn) {
    position: absolute;
    top: 6px;
    right: 30px;
    width: 20px;
    height: 20px;
    border-radius: 50%;
    background: var(--color-bg-primary);
    border: 1px solid var(--color-border-primary);
    color: var(--color-text-muted);
    font-size: 11px;
    line-height: 1;
    cursor: pointer;
    z-index: 10;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 0;
    opacity: 0;
    transition: opacity 120ms ease, background 120ms ease, color 120ms ease;
  }
  :global(.grid-stack-item:hover .widget-edit-btn) { opacity: 1; }
  :global(.widget-edit-btn:hover) {
    background: var(--accent, var(--color-accent));
    border-color: var(--accent, var(--color-accent));
    color: #fff;
  }

  :global(.grid-stack) { background: transparent; }
</style>

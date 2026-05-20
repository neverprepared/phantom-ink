<script lang="ts">
  import 'gridstack/dist/gridstack.min.css';
  import 'gridstack/dist/gridstack-extra.min.css';
  import { GridStack } from 'gridstack';
  import { mount, unmount, onMount } from 'svelte';
  import { getApi } from '../utils/api';
  import { brainboxEvents } from '../events.svelte';
  import { authorityState } from '../authority.svelte';
  import { profileState, dashboardState, dashboardDataStore } from '../stores.svelte';
  import { DEFAULT_LAYOUT } from '../widgets/defaultLayout';
  import type { WidgetInstance, WidgetKind, ActionItem } from '../widgets/types';

  import StatCounterWidget    from '../widgets/StatCounterWidget.svelte';
  import DispatchFormWidget   from '../widgets/DispatchFormWidget.svelte';
  import ChainsListWidget     from '../widgets/ChainsListWidget.svelte';
  import ActionItemsWidget    from '../widgets/ActionItemsWidget.svelte';
  import ResourceMonitorWidget from '../widgets/ResourceMonitorWidget.svelte';
  import CustomCounterWidget  from '../widgets/CustomCounterWidget.svelte';
  import ScriptMetricWidget   from '../widgets/ScriptMetricWidget.svelte';
  import HttpMetricWidget     from '../widgets/HttpMetricWidget.svelte';
  import WidgetDrawer         from '../components/WidgetDrawer.svelte';

  // --- Data state ---
  let sessions    = $state<any[]>([]);
  let hubTasks    = $state<any[]>([]);
  let fires       = $state<any[]>([]);
  let taskStats   = $state<any>(null);
  let dockerStats = $state<any[]>([]);
  let localProcs  = $state<any[]>([]);
  let systemInfo  = $state<{ cpu_cores: number; mem_total_gib: number }>({ cpu_cores: 0, mem_total_gib: 0 });
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

  let actionItems = $derived.by((): ActionItem[] => {
    const items: ActionItem[] = [];
    const auth = authorityState.status;
    const now = Date.now();

    if (auth) {
      if (auth.authorities.length > 0 && !auth.any_online) {
        items.push({
          kind: 'auth', title: 'credential authority offline',
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
        ? t.created_at : parseFloat(t.created_at ?? '0') * 1000;
      const ageMin = Math.floor((now - created) / 60_000);
      if (ageMin > 30) {
        items.push({
          kind: 'task_stuck',
          title: `${t.session_name || t.id.slice(0, 8)} still running`,
          desc: `running for ${ageMin}m — may be stuck`,
          severity: 'warning', ref: t.id,
        });
      }
    }

    for (const t of failedHubTasks.slice(0, 3)) {
      const err = typeof t.error === 'string' ? t.error : JSON.stringify(t.error ?? '');
      items.push({
        kind: 'task_failed', title: 'task failed',
        desc: err.slice(0, 100) || (t.description ?? '').slice(0, 100) || t.id.slice(0, 12),
        severity: 'warning', ref: t.id,
      });
    }

    return items;
  });

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
      loading, refreshing,
    };
  });

  // --- Grid state ---
  let gridEl: HTMLElement;
  let grid: GridStack | null = null;
  const mountedWidgets = new Map<string, any>();
  let saveTimer: ReturnType<typeof setTimeout> | null = null;
  let drawerOpen = $state(false);

  const WIDGET_MAP: Record<WidgetKind, any> = {
    'stat-counter':     StatCounterWidget,
    'dispatch-form':    DispatchFormWidget,
    'chains-list':      ChainsListWidget,
    'action-items':     ActionItemsWidget,
    'resource-monitor': ResourceMonitorWidget,
    'custom-counter':   CustomCounterWidget,
    'script-metric':    ScriptMetricWidget,
    'http-metric':      HttpMetricWidget,
  };

  function mountWidget(w: WidgetInstance): void {
    if (!grid) return;
    const itemEl = grid.addWidget({
      id: w.id, x: w.x, y: w.y, w: w.w, h: w.h,
      minW: w.minW, minH: w.minH,
    }) as HTMLElement;
    const contentEl = itemEl.querySelector('.grid-stack-item-content') as HTMLElement;
    const instance = mount(WIDGET_MAP[w.kind], { target: contentEl, props: { config: w.config } });
    mountedWidgets.set(w.id, instance);

    const btn = document.createElement('button');
    btn.className = 'widget-remove-btn';
    btn.textContent = '✕';
    btn.setAttribute('aria-label', 'Remove widget');
    btn.addEventListener('click', (e) => { e.stopPropagation(); handleRemoveWidget(w.id); });
    itemEl.appendChild(btn);
  }

  async function saveLayout(): Promise<void> {
    if (!grid) return;
    const saved = grid.save(false) as any[];
    const current = dashboardState.widgets;
    const updated: WidgetInstance[] = saved
      .map((item: any) => {
        const orig = current.find(w => w.id === item.id);
        if (!orig) return null;
        return { ...orig, x: item.x ?? orig.x, y: item.y ?? orig.y, w: item.w ?? orig.w, h: item.h ?? orig.h };
      })
      .filter(Boolean) as WidgetInstance[];
    dashboardState.updateWidgets(updated);
    const a = await getApi();
    if (a) {
      try {
        await a.SaveDashboardLayout(
          profileState.active?.name ?? '',
          JSON.stringify({ version: 1, widgets: updated }),
        );
      } catch {}
    }
  }

  function handleAddWidget(w: WidgetInstance): void {
    if (!grid) return;
    dashboardState.updateWidgets([...dashboardState.widgets, w]);
    mountWidget(w);
    drawerOpen = false;
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(saveLayout, 800);
  }

  function handleRemoveWidget(id: string): void {
    if (!grid) return;
    const inst = mountedWidgets.get(id);
    if (inst) { unmount(inst); mountedWidgets.delete(id); }
    const el = gridEl.querySelector(`[gs-id="${id}"]`) as HTMLElement | null;
    if (el) grid.removeWidget(el, true);
    dashboardState.updateWidgets(dashboardState.widgets.filter(w => w.id !== id));
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(saveLayout, 800);
  }

  async function reloadLayout(profileName: string): Promise<void> {
    if (!grid) return;
    for (const [, inst] of mountedWidgets) unmount(inst);
    mountedWidgets.clear();
    grid.removeAll();

    const a = await getApi();
    let layout = { version: 1 as const, widgets: [...DEFAULT_LAYOUT.widgets] };
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
    for (const w of layout.widgets) mountWidget(w);
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
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(saveLayout, 800);
  }

  // --- Data loading ---
  async function load(silent = false): Promise<void> {
    const a = await getApi();
    if (!a) return;
    if (!silent) loading = true; else refreshing = true;
    try {
      const [s, tasks, f, ts, ds, procs] = await Promise.all([
        (a.GetSessions() as Promise<any>).catch(() => []),
        (a.ListHubTasks('', profileState.active?.name ?? '') as Promise<any>).catch(() => []),
        (a.ListUpcomingFires(5) as Promise<any>).catch(() => []),
        (a.GetTaskStats(24) as Promise<any>).catch(() => null),
        (a.GetDockerStats() as Promise<any>).catch(() => []),
        (a.FindClaudeProcesses() as Promise<any>).catch(() => []),
      ]);
      sessions    = s ?? [];
      hubTasks    = tasks ?? [];
      fires       = f ?? [];
      taskStats   = ts;
      dockerStats = ds ?? [];
      localProcs  = procs ?? [];
    } finally {
      loading    = false;
      refreshing = false;
    }
  }

  // Reload layout and data when profile changes (after initial mount)
  let _trackedProfile = '';
  $effect(() => {
    const name = profileState.active?.name ?? '';
    if (name !== _trackedProfile && grid) {
      _trackedProfile = name;
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

    grid = GridStack.init({
      column: 12,
      cellHeight: 60,
      cellHeightUnit: 'px',
      margin: 8,
      animate: true,
      draggable: { handle: '.widget-drag-handle' },
      resizable: { handles: 'se' },
    }, gridEl);

    for (const w of layout.widgets) mountWidget(w);

    grid.on('change', () => {
      if (saveTimer) clearTimeout(saveTimer);
      saveTimer = setTimeout(saveLayout, 800);
    });

    void load();

    const dockerInterval = setInterval(async () => {
      const api = await getApi();
      if (api) { try { dockerStats = (await api.GetDockerStats()) ?? []; } catch {} }
    }, 10_000);

    return () => {
      if (saveTimer) clearTimeout(saveTimer);
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
      <span class="os-badge">OS</span>
      <span class="brand-name">PHANTOM-INK</span>
      {#if refreshing}<span class="refreshing">·</span>{/if}
    </div>
    <div class="datestamp">[ {formatDate(now)} ]</div>
  </div>

  <div class="grid-wrap">
    <div class="grid-stack" bind:this={gridEl}></div>
  </div>
</div>

<button class="fab" onclick={() => drawerOpen = !drawerOpen} aria-label="Open widget drawer">+</button>

<WidgetDrawer
  open={drawerOpen}
  onClose={() => drawerOpen = false}
  onAdd={handleAddWidget}
  onRemove={handleRemoveWidget}
  onReset={handleReset}
  widgets={dashboardState.widgets}
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
    border-bottom: 1px solid var(--color-border-primary);
    flex-shrink: 0;
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

  .grid-wrap {
    flex: 1;
    overflow-y: auto;
    padding: 8px;
  }

  /* FAB */
  .fab {
    position: fixed;
    bottom: 24px;
    right: 24px;
    width: 44px;
    height: 44px;
    border-radius: 50%;
    background: var(--color-accent);
    color: #000;
    border: none;
    font-size: 22px;
    line-height: 1;
    cursor: pointer;
    z-index: 190;
    display: flex;
    align-items: center;
    justify-content: center;
    font-family: inherit;
    box-shadow: 0 4px 16px rgba(0,0,0,0.2);
    transition: opacity 120ms ease, transform 120ms ease;
  }
  .fab:hover { opacity: 0.85; transform: scale(1.05); }

  /* gridstack overrides */
  :global(.grid-stack-item-content) {
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-secondary);
    border-radius: var(--radius-md);
    overflow: hidden;
    box-shadow: var(--shadow-card);
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

  :global(.grid-stack) { background: transparent; }
</style>

<script lang="ts">
  import 'gridstack/dist/gridstack.min.css';
  import 'gridstack/dist/gridstack-extra.min.css';
  import { GridStack } from 'gridstack';
  import { mount, unmount, onMount } from 'svelte';
  import { getApi } from '../utils/api';
  import { brainboxEvents } from '../events.svelte';
  import { profileState, dashboardState, dashboardDataStore } from '../stores.svelte';
  import { DEFAULT_LAYOUT } from '../widgets/defaultLayout';
  import type { WidgetInstance, WidgetKind, ActionItem } from '../widgets/types';

  import StatCounterWidget     from '../widgets/StatCounterWidget.svelte';
  import DispatchFormWidget    from '../widgets/DispatchFormWidget.svelte';
  import ChainsListWidget      from '../widgets/ChainsListWidget.svelte';
  import ActionItemsWidget     from '../widgets/ActionItemsWidget.svelte';
  import ResourceMonitorWidget from '../widgets/ResourceMonitorWidget.svelte';
  import CustomCounterWidget   from '../widgets/CustomCounterWidget.svelte';
  import ScriptMetricWidget    from '../widgets/ScriptMetricWidget.svelte';
  import HttpMetricWidget      from '../widgets/HttpMetricWidget.svelte';
  import StreamWidget          from '../widgets/StreamWidget.svelte';
  import CalendarWidget        from '../widgets/CalendarWidget.svelte';
  import TasksWidget           from '../widgets/TasksWidget.svelte';
  import NotesWidget           from '../widgets/NotesWidget.svelte';
  import SessionsMiniWidget    from '../widgets/SessionsMiniWidget.svelte';
  import WidgetDrawer          from '../components/WidgetDrawer.svelte';

  import type { DashboardCollection } from '../widgets/types';

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
    const now = Date.now();

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

  // --- Collections ---
  let activeCollectionId = $state<string | null>(null);
  let arrangeMode = $state(false);

  let collections = $derived<DashboardCollection[]>(
    dashboardState.layout?.collections ?? []
  );

  let activeCollection = $derived(
    collections.find(c => c.id === activeCollectionId) ?? collections[0] ?? null
  );

  function addCollection(name: string): void {
    const id = 'col-' + Date.now();
    const updated = [...collections, { id, name }];
    const layout = dashboardState.layout ?? { version: 1 as const, widgets: [] };
    dashboardState.layout = { ...layout, collections: updated };
    activeCollectionId = id;
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(saveLayout, 800);
    // Reload grid to show empty collection
    void reloadLayout(profileState.active?.name ?? '');
  }

  function switchCollection(id: string): void {
    activeCollectionId = id;
    void reloadLayout(profileState.active?.name ?? '');
  }

  function collectionWidgetCount(id: string): number {
    return dashboardState.widgets.filter(w => w.collectionId === id).length;
  }

  function promptNewCollection(): void {
    const name = window.prompt('Collection name?');
    if (name?.trim()) addCollection(name.trim());
  }

  function visibleWidgets(): WidgetInstance[] {
    const all = dashboardState.widgets;
    if (!activeCollectionId && collections.length === 0) return all;
    const cid = activeCollectionId ?? collections[0]?.id ?? null;
    if (!cid) return all;
    // If no widgets have a collectionId, show all (backwards compatibility)
    const hasCollectionWidgets = all.some(w => w.collectionId);
    if (!hasCollectionWidgets) return all;
    return all.filter(w => !w.collectionId || w.collectionId === cid);
  }

  const WIDGET_MAP: Record<WidgetKind, any> = {
    'stat-counter':     StatCounterWidget,
    'dispatch-form':    DispatchFormWidget,
    'chains-list':      ChainsListWidget,
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
  };

  function patchWidgetConfig(id: string, patch: Record<string, unknown>): void {
    const updated = dashboardState.widgets.map(existing =>
      existing.id === id ? { ...existing, config: { ...existing.config, ...patch } } : existing
    );
    dashboardState.updateWidgets(updated);
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(saveLayout, 800);
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
      props.onConfigUpdate = (patch: Record<string, unknown>) => patchWidgetConfig(w.id, patch);
    }
    const instance = mount(WIDGET_MAP[w.kind], { target: contentEl, props });
    mountedWidgets.set(w.id, instance);

    const btn = document.createElement('button');
    btn.className = 'widget-remove-btn';
    btn.textContent = '✕';
    btn.setAttribute('aria-label', 'Remove widget');
    btn.addEventListener('click', (e) => { e.stopPropagation(); handleRemoveWidget(w.id); });
    itemEl.appendChild(btn);

    // Inject grip into the widget header if one exists, otherwise overlay top-left.
    // Uses the verbatim `drag` glyph from icons.jsx (2 cols × 3 rows, cx=9/15, cy=6/12/18).
    const dragSvg = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
      <circle cx="9" cy="6" r="1.4" fill="currentColor" stroke="none"/>
      <circle cx="9" cy="12" r="1.4" fill="currentColor" stroke="none"/>
      <circle cx="9" cy="18" r="1.4" fill="currentColor" stroke="none"/>
      <circle cx="15" cy="6" r="1.4" fill="currentColor" stroke="none"/>
      <circle cx="15" cy="12" r="1.4" fill="currentColor" stroke="none"/>
      <circle cx="15" cy="18" r="1.4" fill="currentColor" stroke="none"/>
    </svg>`;

    const header = contentEl.querySelector('.widget-header');
    if (header) {
      // Header widget: prepend grip span as first child of the header row
      const grip = document.createElement('span');
      grip.className = 'widget-grip widget-drag-handle';
      grip.setAttribute('aria-hidden', 'true');
      grip.innerHTML = dragSvg;
      header.prepend(grip);
    } else {
      // Drag-strip widget: overlay grip at top-left of the card
      const grip = document.createElement('div');
      grip.className = 'widget-grip widget-grip-overlay widget-drag-handle';
      grip.setAttribute('aria-hidden', 'true');
      grip.innerHTML = dragSvg;
      itemEl.appendChild(grip);
    }
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
        const layout = dashboardState.layout;
        await a.SaveDashboardLayout(
          profileState.active?.name ?? '',
          JSON.stringify({
            version: 1,
            widgets: updated,
            collections: layout?.collections ?? [],
            activeCollectionId: activeCollectionId ?? undefined,
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
      const newInst = mount(WIDGET_MAP[w.kind], { target: contentEl, props: { config: w.config } });
      mountedWidgets.set(w.id, newInst);
    }

    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(saveLayout, 800);
  }

  function handleAddWidget(w: WidgetInstance): void {
    if (!grid) return;
    // Stamp the active collection onto new widgets
    const cid = activeCollectionId ?? collections[0]?.id;
    const stamped: WidgetInstance = cid ? { ...w, collectionId: cid } : w;
    dashboardState.updateWidgets([...dashboardState.widgets, stamped]);
    mountWidget(stamped);
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

    // Restore active collection from saved layout
    if (layout.activeCollectionId) {
      activeCollectionId = layout.activeCollectionId;
    } else if (layout.collections?.length) {
      activeCollectionId = layout.collections[0].id;
    }

    for (const w of visibleWidgets()) mountWidget(w);
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

    // Restore active collection
    if (layout.activeCollectionId) {
      activeCollectionId = layout.activeCollectionId;
    } else if (layout.collections?.length) {
      activeCollectionId = layout.collections[0].id;
    }

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

  <div class="collections-bar">
    <span class="collections-label">collections</span>
    <div class="collection-pills">
      {#each collections as c}
        <button
          class="ds-btn sm {activeCollectionId === c.id ? 'soft' : 'ghost'}"
          onclick={() => switchCollection(c.id)}
        >{c.name}<span class="pill-count">{collectionWidgetCount(c.id)}</span></button>
      {/each}
      <button class="ds-btn ghost sm icon-only" onclick={promptNewCollection} title="new collection">+</button>
    </div>
    <div class="collections-actions">
      {#if arrangeMode}
        <button class="ds-btn sm" onclick={() => drawerOpen = true}>+ widget</button>
      {/if}
      <button
        class="ds-btn sm {arrangeMode ? 'primary' : ''}"
        onclick={() => { arrangeMode = !arrangeMode; if (!arrangeMode) drawerOpen = false; }}
      >{arrangeMode ? 'done' : 'arrange'}</button>
    </div>
  </div>

  <div class="grid-wrap" class:arrange={arrangeMode}>
    <div class="grid-stack" bind:this={gridEl}></div>
  </div>
</div>

<WidgetDrawer
  open={drawerOpen}
  onClose={() => drawerOpen = false}
  onAdd={handleAddWidget}
  onUpdate={handleUpdateWidget}
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

  .collections-bar {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 8px var(--panel-padding);
    border-bottom: 1px solid var(--border, var(--color-border-primary));
    flex-shrink: 0;
    flex-wrap: wrap;
  }

  .collections-label {
    font-family: var(--font-mono);
    font-size: 10px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--text-faint, var(--color-text-tertiary));
    flex-shrink: 0;
  }

  .collection-pills {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    flex: 1;
  }

  .pill-count {
    opacity: 0.55;
    margin-left: 5px;
  }

  .ds-btn.icon-only {
    padding: 3px 8px;
  }

  .collections-actions {
    display: flex;
    gap: 8px;
    margin-left: auto;
    flex-shrink: 0;
  }

  .grid-wrap {
    flex: 1;
    overflow-y: auto;
    padding: 8px;
  }

  /* In arrange mode, always show widget remove buttons */
  .grid-wrap.arrange :global(.widget-remove-btn) { opacity: 1; }

  /* gridstack overrides */
  :global(.grid-stack-item-content) {
    background: var(--bg-elev, var(--color-bg-secondary));
    border: 1px solid var(--border, var(--color-border-secondary));
    border-radius: var(--r-lg, var(--radius-lg));
    overflow: hidden;
    box-shadow: var(--shadow-sm, var(--shadow-card));
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

  /* Grip handle — header variant (flows inside .widget-header as first child) */
  :global(.widget-grip) {
    display: none;
    align-items: center;
    color: var(--text-faint);
    cursor: grab;
    flex-shrink: 0;
    margin-right: 2px;
    margin-left: -2px;
    transition: color 120ms ease;
    user-select: none;
  }
  :global(.widget-grip:active) { cursor: grabbing; }
  :global(.widget-grip:hover) { color: var(--text-muted); }
  .grid-wrap.arrange :global(.widget-grip) { display: flex; }

  /* Grip handle — overlay variant (drag-strip widgets, no visible header) */
  :global(.widget-grip-overlay) {
    position: absolute;
    top: 7px;
    left: 7px;
    z-index: 20;
    margin: 0;
  }

  :global(.grid-stack) { background: transparent; }
</style>

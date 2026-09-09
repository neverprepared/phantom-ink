<script lang="ts">
  import { getApi, safe } from '../utils/api';
  import { formatClock, timeAgoOrDate } from '../utils/format';
  import { onAgentEvent } from '../utils/agentEvents';
  import { onCollectUpdate } from '../utils/collectEvents';
  import { onMount, tick } from 'svelte';
  import { profileState } from '../stores.svelte';
  import { notifications } from '../notifications.svelte';
  import Spinner from '../components/Spinner.svelte';
  import EmptyState from '../components/EmptyState.svelte';
  import Badge from '../components/Badge.svelte';

  // ── Types (mirror app/app_timeline.go JSON) ─────────────────────────────
  interface PlannedItem {
    id: string;
    source: string;      // "schedule" | "collect"
    source_id: string;
    title: string;
    profile: string;
    fire_at_ms: number;
    recurrence: string;  // "once" | "interval" | "timeofday" | "cron"
    draggable: boolean;
    kind: string;
  }
  interface TimelineEvent {
    id: string;
    title: string;
    type: string;
    status: string;
    start_at_ms: number;
    end_at_ms: number;
    source_id: string;
  }

  type Mode = 'both' | 'planned' | 'executed';

  // ── State ────────────────────────────────────────────────────────────────
  const profile = $derived(profileState.active?.name ?? '');

  let mode = $state<Mode>('both');
  let rangeDays = $state(7);
  let planned = $state<PlannedItem[]>([]);
  let executed = $state<TimelineEvent[]>([]);
  let loading = $state(false);
  let loadError = $state<string | null>(null);
  let nowMs = $state(Date.now());

  // Drag (one-shot reschedule)
  let dragId = $state<string | null>(null);
  let dragPreviewMs = $state<number | null>(null);
  let innerEl = $state<HTMLDivElement | null>(null);
  let scrollEl = $state<HTMLDivElement | null>(null);

  const DAY_MS = 86_400_000;
  const PX_PER_DAY = 200;

  // ── Window / geometry ──────────────────────────────────────────────────
  // The toggle drives the axis direction: planned looks forward, executed back,
  // both straddles now. Future is rendered ABOVE the now-line, past below it.
  let win = $derived.by(() => {
    const span = rangeDays * DAY_MS;
    if (mode === 'planned') return { startMs: nowMs, endMs: nowMs + span };
    if (mode === 'executed') return { startMs: nowMs - span, endMs: nowMs };
    return { startMs: nowMs - span, endMs: nowMs + span };
  });
  let spanMs = $derived(Math.max(1, win.endMs - win.startMs));
  let totalHeight = $derived(Math.max(400, Math.round((spanMs / DAY_MS) * PX_PER_DAY)));

  // ms → y (px from top of the inner track). endMs (far future) is at the top.
  function yFor(ms: number): number {
    const clamped = Math.min(win.endMs, Math.max(win.startMs, ms));
    return ((win.endMs - clamped) / spanMs) * totalHeight;
  }
  // y (px) → ms (inverse of yFor).
  function msForY(y: number): number {
    const frac = Math.min(1, Math.max(0, y / totalHeight));
    return Math.round(win.endMs - frac * spanMs);
  }

  // Day gridlines: local midnights inside the window.
  let gridlines = $derived.by(() => {
    const out: { ms: number; label: string }[] = [];
    const d = new Date(win.startMs);
    d.setHours(0, 0, 0, 0);
    let t = d.getTime();
    if (t < win.startMs) t += DAY_MS;
    for (let i = 0; i < 64 && t <= win.endMs; i++) {
      out.push({
        ms: t,
        label: new Date(t).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' }),
      });
      t += DAY_MS;
    }
    return out;
  });

  let visiblePlanned = $derived(mode === 'executed' ? [] : planned);
  let visibleExecuted = $derived(mode === 'planned' ? [] : executed);

  // ── Loading ───────────────────────────────────────────────────────────
  async function load() {
    const a = await getApi();
    if (!a) { loadError = 'API bindings unavailable'; return; }
    loading = true;
    nowMs = Date.now();
    const w = win;
    try {
      const [p, e] = await Promise.all([
        mode === 'executed'
          ? Promise.resolve([] as PlannedItem[])
          : safe((a as any).ListPlannedItems(profile, w.startMs, w.endMs), [] as PlannedItem[], 'ListPlannedItems'),
        mode === 'planned'
          ? Promise.resolve([] as TimelineEvent[])
          : safe((a as any).ListExecutedItems(profile, w.startMs, w.endMs, 500), [] as TimelineEvent[], 'ListExecutedItems'),
      ]);
      planned = (p ?? []) as PlannedItem[];
      executed = (e ?? []) as TimelineEvent[];
      loadError = null;
    } catch (err: any) {
      loadError = `${err?.message ?? err}`;
    } finally {
      loading = false;
    }
  }

  // Reload when the profile, mode, or range changes.
  $effect(() => {
    // reference the deps so the effect tracks them
    void profile; void mode; void rangeDays;
    void load();
  });

  // Debounced live refresh so a burst of bus events coalesces into one reload.
  let reloadTimer: number | undefined;
  function scheduleReload() {
    clearTimeout(reloadTimer);
    reloadTimer = window.setTimeout(() => void load(), 400);
  }

  onMount(() => {
    const offAgent = onAgentEvent(scheduleReload);
    const offCollect = onCollectUpdate(scheduleReload);
    // Keep the now-line honest without a hard reload.
    const nowTimer = window.setInterval(() => { nowMs = Date.now(); }, 30_000);
    void centerOnNow();
    return () => { offAgent(); offCollect(); window.clearInterval(nowTimer); clearTimeout(reloadTimer); };
  });

  async function centerOnNow() {
    await tick();
    if (!scrollEl) return;
    const y = yFor(nowMs);
    scrollEl.scrollTop = Math.max(0, y - scrollEl.clientHeight / 2);
  }

  // ── Drag to reschedule (one-shot only) ──────────────────────────────────
  function startDrag(ev: PointerEvent, item: PlannedItem) {
    if (!item.draggable) return;
    ev.preventDefault();
    dragId = item.id;
    dragPreviewMs = item.fire_at_ms;
    (ev.target as HTMLElement).setPointerCapture?.(ev.pointerId);
  }
  function onDragMove(ev: PointerEvent) {
    if (dragId === null || !innerEl) return;
    const rect = innerEl.getBoundingClientRect();
    dragPreviewMs = msForY(ev.clientY - rect.top);
  }
  async function endDrag(ev: PointerEvent, item: PlannedItem) {
    if (dragId === null) return;
    (ev.target as HTMLElement).releasePointerCapture?.(ev.pointerId);
    const target = dragPreviewMs;
    dragId = null;
    dragPreviewMs = null;
    if (target === null) return;
    // Require at least a minute out so a drop doesn't fire instantly; snap to the minute.
    const snapped = Math.max(nowMs + 60_000, Math.round(target / 60_000) * 60_000);
    if (snapped === item.fire_at_ms) return;
    const a = await getApi();
    if (!a) return;
    try {
      await (a as any).RescheduleOneShot(item.source_id, snapped);
      notifications.success(`Rescheduled “${item.title}” to ${formatClock(snapped)} ${new Date(snapped).toLocaleDateString()}`);
      await load();
    } catch (e: any) {
      notifications.error(`Reschedule failed: ${e?.message ?? e}`);
    }
  }

  function recurrenceLabel(r: string): string {
    switch (r) {
      case 'once': return 'once';
      case 'interval': return 'repeats';
      case 'timeofday': return 'daily';
      case 'cron': return 'cron';
      default: return r;
    }
  }
  function statusVariant(s: string): string {
    if (s === 'failed') return 'error';
    if (s === 'done') return 'success';
    if (s === 'active' || s === 'needs_action' || s === 'blocked') return 'warning';
    return 'default';
  }
</script>

<div class="timeline-panel">
  <header class="tl-header">
    <div class="tl-title">
      <h2>Timeline</h2>
      {#if profile}<span class="tl-profile">{profile}</span>{/if}
    </div>
    <div class="tl-controls">
      <div class="seg">
        {#each (['both', 'planned', 'executed'] as Mode[]) as m}
          <button class="seg-btn" class:active={mode === m} onclick={() => (mode = m)}>{m}</button>
        {/each}
      </div>
      <select class="tl-range" bind:value={rangeDays}>
        <option value={1}>±1 day</option>
        <option value={3}>±3 days</option>
        <option value={7}>±7 days</option>
        <option value={30}>±30 days</option>
      </select>
      <button class="tl-btn" onclick={() => void load()} title="Refresh">↻</button>
    </div>
  </header>

  {#if loadError}
    <div class="tl-error">{loadError}</div>
  {/if}

  {#if loading && planned.length === 0 && executed.length === 0}
    <div class="tl-center"><Spinner /></div>
  {:else if visiblePlanned.length === 0 && visibleExecuted.length === 0}
    <EmptyState
      title="Nothing on the timeline"
      message={mode === 'planned'
        ? 'No upcoming schedules, jobs, or timed todos in this window.'
        : mode === 'executed'
          ? 'No recorded activity in this window.'
          : 'No planned items or recorded activity in this window.'} />
  {:else}
    <div class="tl-scroll" bind:this={scrollEl}>
      <div class="tl-inner" bind:this={innerEl} style="height:{totalHeight}px">
        <!-- gridlines -->
        {#each gridlines as g (g.ms)}
          <div class="tl-grid" style="top:{yFor(g.ms)}px">
            <span class="tl-grid-label">{g.label}</span>
          </div>
        {/each}

        <!-- now line -->
        <div class="tl-now" style="top:{yFor(nowMs)}px">
          <span class="tl-now-label">now</span>
        </div>

        <!-- planned (future, above now) -->
        {#each visiblePlanned as item (item.id)}
          <div
            class="tl-item planned"
            class:draggable={item.draggable}
            class:dragging={dragId === item.id}
            style="top:{dragId === item.id && dragPreviewMs !== null ? yFor(dragPreviewMs) : yFor(item.fire_at_ms)}px"
            role={item.draggable ? 'button' : undefined}
            tabindex={item.draggable ? 0 : undefined}
            aria-label={item.draggable ? `Reschedule ${item.title}` : undefined}
            onpointerdown={item.draggable ? (e) => startDrag(e, item) : undefined}
            onpointermove={item.draggable ? onDragMove : undefined}
            onpointerup={item.draggable ? (e) => endDrag(e, item) : undefined}
          >
            <span class="dot planned-dot"></span>
            <div class="tl-item-body">
              <span class="tl-item-title">{item.title || '(untitled)'}</span>
              <span class="tl-item-meta">
                {formatClock(dragId === item.id && dragPreviewMs !== null ? dragPreviewMs : item.fire_at_ms)}
                · <Badge text={recurrenceLabel(item.recurrence)} variant={item.draggable ? 'default' : 'info'} />
                {#if item.kind}<span class="tl-kind">{item.kind}</span>{/if}
              </span>
            </div>
            {#if item.draggable}<span class="grip" title="Drag to reschedule">⣿</span>{/if}
          </div>
        {/each}

        <!-- executed (past, below now) -->
        {#each visibleExecuted as ev (ev.id)}
          <div class="tl-item executed" style="top:{yFor(ev.start_at_ms)}px">
            <span class="dot executed-dot {statusVariant(ev.status)}"></span>
            <div class="tl-item-body">
              <span class="tl-item-title">{ev.title || ev.type || ev.id}</span>
              <span class="tl-item-meta">
                {timeAgoOrDate(ev.start_at_ms)}
                {#if ev.status}· <Badge text={ev.status} variant={statusVariant(ev.status)} />{/if}
              </span>
            </div>
          </div>
        {/each}
      </div>
    </div>
  {/if}
</div>

<style>
  .timeline-panel { display: flex; flex-direction: column; height: 100%; min-height: 0; }

  .tl-header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 12px 16px; border-bottom: 1px solid var(--color-border, #2a2a2a); gap: 12px;
  }
  .tl-title { display: flex; align-items: baseline; gap: 10px; }
  .tl-title h2 { font-size: 15px; font-weight: 600; margin: 0; }
  .tl-profile { font-size: 11px; color: var(--color-text-tertiary); }
  .tl-controls { display: flex; align-items: center; gap: 8px; }

  .seg { display: inline-flex; border: 1px solid var(--color-muted-border, #333); border-radius: 6px; overflow: hidden; }
  .seg-btn {
    background: transparent; border: none; color: var(--color-text-tertiary);
    padding: 4px 10px; font-size: 11px; text-transform: capitalize; cursor: pointer;
  }
  .seg-btn.active { background: var(--color-accent, #3b82f6); color: #fff; }

  .tl-range {
    background: var(--color-muted-bg, #1c1c1c); color: var(--color-text, #ddd);
    border: 1px solid var(--color-muted-border, #333); border-radius: 6px; padding: 3px 6px; font-size: 11px;
  }
  .tl-btn {
    background: var(--color-muted-bg, #1c1c1c); color: var(--color-text, #ddd);
    border: 1px solid var(--color-muted-border, #333); border-radius: 6px;
    width: 28px; height: 26px; cursor: pointer;
  }

  .tl-error { padding: 8px 16px; color: var(--color-error, #f87171); font-size: 12px; }
  .tl-center { display: flex; align-items: center; justify-content: center; padding: 60px; }

  .tl-scroll { flex: 1; overflow-y: auto; min-height: 0; padding: 0 16px; }
  .tl-inner { position: relative; margin-left: 96px; }

  .tl-grid { position: absolute; left: -96px; right: 0; border-top: 1px dashed var(--color-muted-border, #2a2a2a); }
  .tl-grid-label {
    position: absolute; left: 0; top: -8px; font-size: 10px;
    color: var(--color-text-tertiary); background: var(--color-bg, #121212); padding-right: 6px;
  }

  .tl-now { position: absolute; left: -96px; right: 0; border-top: 2px solid var(--color-accent, #3b82f6); z-index: 3; }
  .tl-now-label {
    position: absolute; left: 0; top: -8px; font-size: 10px; font-weight: 600;
    color: var(--color-accent, #3b82f6); background: var(--color-bg, #121212); padding-right: 6px;
  }

  .tl-item {
    position: absolute; left: 0; right: 0; display: flex; align-items: center; gap: 8px;
    transform: translateY(-50%); padding: 4px 8px; border-radius: 6px;
    background: var(--color-muted-bg, #1b1b1b); border: 1px solid var(--color-muted-border, #2c2c2c);
    z-index: 2;
  }
  .tl-item.planned { border-left: 2px solid var(--color-accent, #3b82f6); }
  .tl-item.executed { opacity: 0.9; }
  .tl-item.draggable { cursor: grab; }
  .tl-item.dragging { cursor: grabbing; z-index: 5; box-shadow: 0 2px 12px rgba(0,0,0,0.4); }

  .dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
  .planned-dot { background: var(--color-accent, #3b82f6); }
  .executed-dot { background: var(--color-text-tertiary, #888); }
  .executed-dot.error { background: var(--color-error, #f87171); }
  .executed-dot.success { background: var(--color-success, #34d399); }
  .executed-dot.warning { background: var(--color-warning, #fbbf24); }

  .tl-item-body { display: flex; flex-direction: column; min-width: 0; flex: 1; }
  .tl-item-title { font-size: 12px; color: var(--color-text, #e4e4e4); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .tl-item-meta { font-size: 10px; color: var(--color-text-tertiary); display: flex; align-items: center; gap: 4px; }
  .tl-kind { opacity: 0.7; }
  .grip { color: var(--color-text-tertiary); font-size: 11px; cursor: grab; }
</style>

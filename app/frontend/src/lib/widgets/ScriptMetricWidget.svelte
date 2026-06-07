<script lang="ts" module>
  // Module-level cache so values survive widget unmount when the user
  // navigates away from the dashboard. Keyed by collect-job id.
  const valueCache = new Map<string, string>();
  // Per-job last-trigger timestamps so opening the dashboard fires the
  // job immediately (fresh values via collect:update) without spamming
  // when the user quickly bounces between panels.
  const lastTriggeredMs = new Map<string, number>();
  // Short window — only suppress rapid back-to-back triggers (e.g. layout
  // remounts that re-mount the same widget). Anything beyond 1.5s should
  // still fire so profile switches and panel re-entry refresh the value.
  const TRIGGER_DEBOUNCE_MS = 1500;
</script>

<script lang="ts">
  import { onMount } from 'svelte';
  import { getApi } from '../utils/api';
  import { onCollectUpdate } from '../utils/collectEvents';
  import { profileState } from '../stores.svelte';
  import type { ScriptMetricConfig } from './types';

  let { config, widgetId, onConfigUpdate }: {
    config: ScriptMetricConfig;
    widgetId?: string;
    onConfigUpdate?: (patch: Partial<ScriptMetricConfig>) => void;
  } = $props();

  // Seed from cache so navigation back to the dashboard shows the last
  // known value instantly instead of flashing `…`.
  const cached = config.jobId ? valueCache.get(config.jobId) ?? null : null;
  let value   = $state<string | null>(cached);
  let error   = $state<string | null>(null);
  let loading = $state(cached === null);

  const isString = $derived(config.valueType === 'string');
  const profile  = $derived(profileState.active?.name ?? '');

  async function fetchValue() {
    const a = await getApi();
    if (!a) { error = 'no api'; loading = false; return; }

    // If we have a jobId, read from the store
    if (config.jobId) {
      try {
        const entry = await a.GetLatestCollectedEntry(config.jobId, config.label);
        if (entry) {
          value = entry.value || null;
          error = null;
          if (value != null) valueCache.set(config.jobId, value);
        }
      } catch (e: any) {
        error = e?.message ?? String(e);
      } finally {
        loading = false;
      }
      return;
    }

    // No jobId yet — run live and auto-register as a collect job
    try {
      value = await a.RunMetricScript(profile, config.command);
      error = null;
      // Auto-register so future renders read from store. Prefer matching
      // by owner_widget_id (stable across renames); fall back to legacy
      // (name+command) fingerprint and backfill the link.
      if (config.command && onConfigUpdate) {
        try {
          const all: any[] = (await (a as any).ListCollectJobs(profile)) ?? [];
          let existing = widgetId ? all.find(j => j.owner_widget_id === widgetId) : null;
          if (!existing) {
            existing = all.find(j => j.name === config.label && j.command === config.command);
          }
          if (existing) {
            onConfigUpdate({ jobId: existing.id });
            const needsBackfill = !existing.source || (widgetId && existing.owner_widget_id !== widgetId);
            if (needsBackfill) {
              try { await (a as any).SaveCollectJob({ ...existing, source: 'widget', owner_widget_id: widgetId ?? '' }); } catch {}
            }
          } else {
            const job = await a.SaveCollectJob({
              id: '', profile, name: config.label, command: config.command,
              interval_s: config.interval ?? 60, enabled: true,
              default_actions: '[]', last_error: '', created_at: 0,
              source: 'widget', owner_widget_id: widgetId ?? '',
            } as any);
            onConfigUpdate({ jobId: job.id });
          }
        } catch { /* non-fatal */ }
      }
    } catch (e: any) {
      error = e?.message ?? String(e);
    } finally {
      loading = false;
    }
  }

  // Fire the underlying collect job so the widget shows fresh data shortly
  // after mount. Debounced so quick panel-switching doesn't trigger storms.
  async function triggerJobIfStale() {
    if (!config.jobId) return;
    const now = Date.now();
    const last = lastTriggeredMs.get(config.jobId) ?? 0;
    if (now - last < TRIGGER_DEBOUNCE_MS) return;
    lastTriggeredMs.set(config.jobId, now);
    const a = await getApi();
    try { await (a as any)?.RunCollectJobNow?.(config.jobId); } catch {}
  }

  onMount(() => {
    void fetchValue();
    void triggerJobIfStale();
    const ms = (config.interval ?? 60) * 1000;
    const interval = setInterval(fetchValue, ms);

    // Also refresh when the collect scheduler emits an update
    const off = onCollectUpdate(() => void fetchValue());
    return () => {
      clearInterval(interval);
      off();
    };
  });
</script>

<div class="drag-strip widget-drag-handle" aria-hidden="true"></div>

<div class="stat-card">
  <span class="stat-label">» {config.label}</span>
  {#if loading}
    <span class="stat-value muted">…</span>
  {:else if error}
    <span class="stat-value err" title={error}>!</span>
    <span class="stat-err">{error}</span>
  {:else if isString}
    <span class="stat-str" style={config.color ? `color: ${config.color}` : ''}>{value}</span>
  {:else}
    <span class="stat-value" style={config.color ? `color: ${config.color}` : ''}>{value}</span>
  {/if}
  <span class="stat-sub">script · {config.interval ?? 60}s{config.jobId ? ' · ●' : ''}</span>
</div>

<style>
  .drag-strip {
    height: 6px;
    cursor: grab;
    flex-shrink: 0;
  }

  .stat-card {
    width: 100%;
    height: calc(100% - 6px);
    display: flex;
    flex-direction: column;
    gap: var(--spacing-xs);
    padding: var(--spacing-lg) var(--spacing-xl);
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
  .stat-value.muted { color: var(--color-text-muted); }
  .stat-value.err   { color: var(--color-error); }

  .stat-str {
    font-family: var(--font-mono);
    font-size: 16px;
    font-weight: 600;
    line-height: 1.3;
    color: var(--color-text-primary);
    word-break: break-word;
    overflow: hidden;
    display: -webkit-box;
    -webkit-line-clamp: 3;
    -webkit-box-orient: vertical;
  }

  .stat-err {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-error);
    opacity: 0.8;
    word-break: break-word;
    white-space: pre-wrap;
  }

  .stat-sub {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-muted);
  }
</style>

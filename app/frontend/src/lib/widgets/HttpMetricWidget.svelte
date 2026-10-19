<script lang="ts" module>
  // Module-level cache so values survive widget unmount when the user
  // navigates away from the dashboard. Keyed by collect-job id.
  const valueCache = new Map<string, string>();
  // Per-job last-trigger timestamps so opening the dashboard fires the
  // job immediately (fresh values via collect:update) without spamming
  // when the user quickly bounces between panels.
  const lastTriggeredMs = new Map<string, number>();
  const TRIGGER_DEBOUNCE_MS = 1500;
</script>

<script lang="ts">
  import { onMount } from 'svelte';
  import { getApi } from '../utils/api';
  import { onCollectUpdate } from '../utils/collectEvents';
  import { profileState } from '../stores.svelte';
  import type { HttpMetricConfig } from './types';
  import { resolveCollectJob } from './collectJob';

  let { config, widgetId, onConfigUpdate }: {
    config: HttpMetricConfig;
    widgetId?: string;
    onConfigUpdate?: (patch: Partial<HttpMetricConfig>) => void;
  } = $props();

  const cached = config.jobId ? valueCache.get(config.jobId) ?? null : null;
  let value   = $state<string | null>(cached);
  // A message, not a boolean. This was `boolean`, so an HTTP metric that
  // failed showed a bare "!" with nothing to act on, while the otherwise
  // identical ScriptMetricWidget captured and rendered the reason.
  let error   = $state<string | null>(null);
  let loading = $state(cached === null);

  const isString = $derived(config.valueType === 'string');
  const profile  = $derived(profileState.active?.name ?? '');

  // The synthetic shell command this metric is scheduled as. Built by GO
  // (HTTPMetricCommand) rather than here: the Go scheduler executes this
  // string, and it was previously assembled in two separate frontend files,
  // so any drift would make the dashboard stop recognising its own jobs.
  let syntheticCommand = $state('');
  $effect(() => {
    const url = config.url;
    const header = config.header ?? '';
    const path = config.path ?? '';
    void (async () => {
      const a = await getApi();
      if (!a) return;
      try {
        syntheticCommand = await a.HTTPMetricCommand(url, header, path);
      } catch {
        /* leave empty: registration below waits for a real command */
      }
    })();
  });

  // Resolved collect-job id for THIS instance — cached locally so later ticks
  // read from the store instead of re-registering (which spawned dup jobs).
  let resolvedJobId = $state<string | null>(config.jobId ?? null);
  let registering = false;

  async function fetchValue() {
    const a = await getApi();
    if (!a) return;

    if (resolvedJobId) {
      try {
        const entry = await a.GetLatestCollectedEntry(resolvedJobId, config.label);
        if (entry) {
          value = entry.value || null;
          error = null;
          if (value != null) valueCache.set(resolvedJobId, value);
        }
      } catch (e: any) {
        error = e?.message ?? String(e);
      } finally {
        loading = false;
      }
      return;
    }

    // Live fetch + register once (guarded; server dedupes per owner too).
    if (registering) { loading = false; return; }
    // Without the Go-derived command we would register a job whose
    // fingerprint can never match, so wait for the next tick instead.
    if (!syntheticCommand) { loading = false; return; }
    registering = true;
    try {
      value = await a.FetchMetricUrl(profile, config.url, config.path ?? '', config.header ?? '');
      error = null;
      // Shared with ScriptMetricWidget — see lib/widgets/collectJob.ts.
      const jobId = await resolveCollectJob({
        profile,
        label: config.label,
        command: syntheticCommand,
        widgetId,
        intervalS: config.interval,
      });
      if (jobId) {
        resolvedJobId = jobId;
        onConfigUpdate?.({ jobId });
      }
    } catch (e: any) {
      error = e?.message ?? String(e);
    } finally {
      registering = false;
      loading = false;
    }
  }

  async function triggerJobIfStale() {
    if (!resolvedJobId) return;
    const now = Date.now();
    const last = lastTriggeredMs.get(resolvedJobId) ?? 0;
    if (now - last < TRIGGER_DEBOUNCE_MS) return;
    lastTriggeredMs.set(resolvedJobId, now);
    const a = await getApi();
    try { await (a as any)?.RunCollectJobNow?.(resolvedJobId); } catch {}
  }

  onMount(() => {
    void fetchValue();
    void triggerJobIfStale();
    const ms = (config.interval ?? 60) * 1000;
    const interval = setInterval(fetchValue, ms);

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
  <span class="stat-sub">
    {config.url.replace(/^https?:\/\//, '').slice(0, 24)}{config.url.length > 31 ? '…' : ''}
    · {config.interval ?? 60}s{resolvedJobId ? ' · ●' : ''}
  </span>
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

  .stat-sub {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-muted);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
</style>

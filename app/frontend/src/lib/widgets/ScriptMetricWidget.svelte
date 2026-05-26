<script lang="ts">
  import { onMount } from 'svelte';
  import { getApi } from '../utils/api';
  import { profileState } from '../stores.svelte';
  import type { ScriptMetricConfig } from './types';

  let { config, onConfigUpdate }: {
    config: ScriptMetricConfig;
    onConfigUpdate?: (patch: Partial<ScriptMetricConfig>) => void;
  } = $props();

  let value   = $state<string | null>(null);
  let error   = $state<string | null>(null);
  let loading = $state(true);

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
      // Auto-register so future renders read from store
      if (config.command && onConfigUpdate) {
        try {
          const job = await a.SaveCollectJob({
            id: '', profile, name: config.label, command: config.command,
            interval_s: config.interval ?? 60, enabled: true,
            default_actions: '[]', last_error: '', created_at: 0,
          });
          onConfigUpdate({ jobId: job.id });
        } catch { /* non-fatal */ }
      }
    } catch (e: any) {
      error = e?.message ?? String(e);
    } finally {
      loading = false;
    }
  }

  onMount(() => {
    void fetchValue();
    const ms = (config.interval ?? 60) * 1000;
    const interval = setInterval(fetchValue, ms);

    // Also refresh when the collect scheduler emits an update
    const handler = () => void fetchValue();
    window.runtime?.EventsOn('collect:update', handler);
    return () => {
      clearInterval(interval);
      window.runtime?.EventsOff('collect:update');
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

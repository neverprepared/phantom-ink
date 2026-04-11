<script lang="ts">
  import { getApi } from '../utils/api';
  import { onMount } from 'svelte';
  import { brainboxEvents } from '../events.svelte';
  import { notifications } from '../notifications.svelte';
  import EmptyState from '../components/EmptyState.svelte';
  import Badge from '../components/Badge.svelte';


  let pipelines = $state<any[]>([]);
  let runs = $state<any[]>([]);
  let loading = $state(true);
  let activeTab = $state<'definitions' | 'runs'>('definitions');

  async function refresh() {
    const a = await getApi();
    if (!a) return;
    try {
      const [p, r] = await Promise.all([a.ListPipelines(), a.ListPipelineRuns()]);
      pipelines = p ?? [];
      runs = r ?? [];
    } catch (err) {
      console.error('Pipelines refresh failed:', err);
    } finally {
      loading = false;
    }
  }

  onMount(() => { refresh(); });

  $effect(() => {
    const ev = brainboxEvents.last;
    if (!ev) return;
    refresh();
  });

  async function handleRun(name: string) {
    const a = await getApi();
    if (!a) return;
    try {
      await a.StartPipelineRun(name, {});
      notifications.success(`Started pipeline: ${name}`);
      refresh();
    } catch (err: any) {
      notifications.error(`Failed to start pipeline: ${err}`);
    }
  }

  async function handleCancel(runId: string) {
    const a = await getApi();
    if (!a) return;
    try {
      await a.CancelPipelineRun(runId);
      notifications.success('Pipeline run cancelled');
      refresh();
    } catch (err: any) {
      notifications.error(`Failed to cancel: ${err}`);
    }
  }
</script>

<div class="panel">
  <header>
    <h1><span class="accent">pipelines</span></h1>
    <button class="refresh-btn" onclick={refresh} title="Refresh">
      <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/></svg>
    </button>
  </header>

  <div class="tabs">
    <button class="tab" class:active={activeTab === 'definitions'} onclick={() => activeTab = 'definitions'}>
      Definitions ({pipelines.length})
    </button>
    <button class="tab" class:active={activeTab === 'runs'} onclick={() => activeTab = 'runs'}>
      Runs ({runs.length})
    </button>
  </div>

  {#if loading}
    <div class="loading">loading pipelines...</div>
  {:else if activeTab === 'definitions'}
    {#if pipelines.length === 0}
      <EmptyState title="No pipelines defined" message="Add YAML pipeline files to the brainbox pipelines/ directory." />
    {:else}
      <div class="list">
        {#each pipelines as pipeline (pipeline.Name ?? pipeline.name)}
          {@const name = pipeline.Name ?? pipeline.name}
          <div class="row">
            <div class="row-main">
              <span class="row-name">{name}</span>
              {#if pipeline.Description ?? pipeline.description}
                <span class="row-desc">{pipeline.Description ?? pipeline.description}</span>
              {/if}
            </div>
            <button class="btn-run" onclick={() => handleRun(name)}>run</button>
          </div>
        {/each}
      </div>
    {/if}
  {:else}
    {#if runs.length === 0}
      <EmptyState title="No pipeline runs yet" />
    {:else}
      <div class="list">
        {#each runs as run (run.ID ?? run.id)}
          {@const id = run.ID ?? run.id}
          {@const status = run.Status ?? run.status}
          <div class="row">
            <div class="row-main">
              <span class="row-name">{run.Pipeline ?? run.pipeline}</span>
              <div class="row-meta">
                <Badge text={status} variant={status} />
                <span class="meta-text">{id.slice(0, 8)}</span>
                {#if run.StartedAt ?? run.started_at}
                  <span class="meta-text muted">{run.StartedAt ?? run.started_at}</span>
                {/if}
              </div>
              {#if run.Error ?? run.error}
                <span class="row-error">{run.Error ?? run.error}</span>
              {/if}
            </div>
            {#if status === 'running'}
              <button class="btn-cancel" onclick={() => handleCancel(id)}>cancel</button>
            {/if}
          </div>
        {/each}
      </div>
    {/if}
  {/if}
</div>

<style>
  .panel { padding-bottom: 24px; }
  header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
  h1 { font-size: 22px; font-weight: 600; }
  .accent { color: var(--color-accent); }

  .refresh-btn {
    background: none;
    border: 1px solid var(--color-border-secondary);
    color: var(--color-text-tertiary);
    padding: 6px;
    border-radius: var(--radius-md);
    display: flex;
    transition: all 0.15s;
  }
  .refresh-btn:hover { color: var(--color-text-primary); }

  .tabs {
    display: flex;
    gap: 4px;
    margin-bottom: 20px;
    border-bottom: 1px solid var(--color-border-primary);
    padding-bottom: 2px;
  }

  .tab {
    background: none;
    border: none;
    border-bottom: 2px solid transparent;
    color: var(--color-text-muted);
    padding: 7px 14px;
    font-size: 13px;
    cursor: pointer;
    transition: all 0.15s;
    position: relative;
    bottom: -2px;
  }
  .tab:hover { color: var(--color-text-primary); }
  .tab.active { color: var(--color-text-primary); border-bottom-color: var(--color-info); }

  .loading { color: var(--color-text-tertiary); font-size: 13px; padding: 24px 0; }

  .list { display: flex; flex-direction: column; gap: 8px; }

  .row {
    display: flex;
    align-items: center;
    gap: 12px;
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-lg);
    padding: 12px 14px;
  }

  .row-main { flex: 1; min-width: 0; }
  .row-name {
    display: block;
    font-size: 14px;
    font-weight: 500;
    color: var(--color-text-primary);
    margin-bottom: 4px;
  }
  .row-desc {
    display: block;
    font-size: 12px;
    color: var(--color-text-tertiary);
    margin-bottom: 4px;
  }
  .row-meta { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .meta-text { font-size: 11px; color: var(--color-text-secondary); font-family: ui-monospace, Menlo, monospace; }
  .meta-text.muted { color: var(--color-text-tertiary); }
  .row-error { font-size: 11px; color: var(--color-error); margin-top: 4px; }

  .btn-run {
    background: rgba(16, 185, 129, 0.1);
    border: 1px solid rgba(16, 185, 129, 0.3);
    color: var(--color-success);
    padding: 4px 12px;
    border-radius: var(--radius-md);
    font-size: 12px;
    flex-shrink: 0;
    transition: all 0.15s;
  }
  .btn-run:hover { background: rgba(16, 185, 129, 0.2); }

  .btn-cancel {
    background: rgba(239, 68, 68, 0.1);
    border: 1px solid rgba(239, 68, 68, 0.2);
    color: #f87171;
    padding: 4px 10px;
    border-radius: var(--radius-md);
    font-size: 11px;
    flex-shrink: 0;
    transition: all 0.15s;
  }
  .btn-cancel:hover { background: rgba(239, 68, 68, 0.2); }
</style>

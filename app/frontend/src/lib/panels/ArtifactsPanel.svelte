<script lang="ts">
  import { getApi } from '../utils/api';
  import { onMount } from 'svelte';
  import { notifications } from '../notifications.svelte';
  import EmptyState from '../components/EmptyState.svelte';


  let artifacts = $state<any[]>([]);
  let loading = $state(true);
  let prefix = $state('');

  async function refresh() {
    const a = await getApi();
    if (!a) return;
    try {
      artifacts = (await a.ListArtifacts(prefix)) ?? [];
    } catch (err) {
      console.error('Artifacts refresh failed:', err);
    } finally {
      loading = false;
    }
  }

  onMount(() => { refresh(); });

  async function handleDelete(key: string) {
    const a = await getApi();
    if (!a) return;
    try {
      await a.DeleteArtifact(key);
      notifications.success(`Deleted: ${key}`);
      refresh();
    } catch (err: any) {
      notifications.error(`Failed to delete: ${err}`);
    }
  }

  function formatSize(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  }
</script>

<div class="panel" aria-busy={loading}>
  <header>
    <h1><span class="accent">artifacts</span></h1>
    <button class="refresh-btn" onclick={refresh} title="Refresh" aria-label="Refresh">
      <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/></svg>
    </button>
  </header>

  <div class="filter-row">
    <input type="text" bind:value={prefix} placeholder="Filter by prefix..." oninput={() => refresh()} />
  </div>

  {#if loading}
    <div class="loading">loading artifacts...</div>
  {:else if artifacts.length === 0}
    <EmptyState title="No artifacts" message="Artifacts uploaded by agents will appear here." />
  {:else}
    <div class="list">
      <div class="list-header">
        <span class="col-key">key</span>
        <span class="col-size">size</span>
        <span class="col-date">modified</span>
        <span class="col-actions"></span>
      </div>
      {#each artifacts as artifact (artifact.Key ?? artifact.key)}
        {@const key = artifact.Key ?? artifact.key}
        {@const size = artifact.Size ?? artifact.size ?? 0}
        {@const modified = artifact.LastModified ?? artifact.last_modified ?? ''}

        <div class="list-row">
          <span class="col-key">{key}</span>
          <span class="col-size">{formatSize(size)}</span>
          <span class="col-date">{modified}</span>
          <div class="col-actions">
            <button class="btn-delete" onclick={() => handleDelete(key)}>delete</button>
          </div>
        </div>
      {/each}
    </div>
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

  .filter-row { margin-bottom: 16px; }
  .filter-row input { max-width: 320px; }

  .loading { color: var(--color-text-tertiary); font-size: 13px; padding: 24px 0; }

  .list {
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-lg);
    overflow: hidden;
  }

  .list-header, .list-row {
    display: grid;
    grid-template-columns: 1fr 80px 160px 80px;
    gap: 12px;
    padding: 10px 14px;
    align-items: center;
    font-size: 12px;
  }

  .list-header {
    background: var(--color-bg-tertiary);
    border-bottom: 1px solid var(--color-border-primary);
    color: var(--color-text-tertiary);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    font-size: 11px;
  }

  .list-row {
    border-bottom: 1px solid var(--color-border-primary);
    color: var(--color-text-secondary);
  }
  .list-row:last-child { border-bottom: none; }

  .col-key {
    font-family: ui-monospace, Menlo, monospace;
    font-size: 12px;
    color: var(--color-text-primary);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .col-size, .col-date { color: var(--color-text-tertiary); font-size: 11px; }

  .col-actions { display: flex; justify-content: flex-end; }

  .btn-delete {
    background: rgba(239, 68, 68, 0.08);
    border: 1px solid rgba(239, 68, 68, 0.15);
    color: #f87171;
    padding: 3px 8px;
    border-radius: var(--radius-sm);
    font-size: 11px;
    transition: all 0.15s;
  }
  .btn-delete:hover { background: rgba(239, 68, 68, 0.15); }
</style>

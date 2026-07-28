<script lang="ts">
  import { getApi } from '../utils/api';
  import { notifications } from '../notifications.svelte';

  interface DatabaseInfo { name: string; size: string; }

  let expanded = $state(false);
  let dbs = $state<DatabaseInfo[]>([]);
  let loading = $state(false);
  let error = $state<string | null>(null);
  let busy = $state<string | null>(null);            // db with an op in flight
  let confirming = $state<string | null>(null);      // inline restore confirm
  let lastResult = $state<string | null>(null);

  async function load() {
    loading = true; error = null;
    try {
      const a = await getApi();
      if (!a) return;
      dbs = (await a.ListPlatformDatabases()) ?? [];
    } catch (e: any) {
      error = e?.message ?? String(e);
      dbs = [];
    } finally {
      loading = false;
    }
  }

  function toggle() {
    expanded = !expanded;
    if (expanded && dbs.length === 0 && !error) void load();
  }

  async function backup(db: string) {
    busy = db; lastResult = null;
    try {
      const a = await getApi();
      const path = await a.BackupDatabase(db);
      if (path) {                       // empty = user cancelled the save dialog
        lastResult = `Backed up ${db} → ${path}`;
        notifications.success(`Backed up ${db}`);
      }
    } catch (e: any) {
      notifications.error(`Backup failed: ${e?.message ?? e}`);
    } finally {
      busy = null;
    }
  }

  async function restore(db: string) {
    confirming = null;
    busy = db; lastResult = null;
    try {
      const a = await getApi();
      const msg = await a.RestoreDatabase(db);
      if (msg) {                        // empty = cancelled the file dialog
        lastResult = msg;
        notifications.success(`Restore of ${db} finished`);
      }
    } catch (e: any) {
      notifications.error(`Restore failed: ${e?.message ?? e}`);
    } finally {
      busy = null;
    }
  }
</script>

<div class="service-card db-card">
  <div class="card-top">
    <button class="card-identity" onclick={toggle}>
      <svg class="expand-chevron" class:expanded xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg>
      <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14a9 3 0 0 0 18 0V5"/><path d="M3 12a9 3 0 0 0 18 0"/></svg>
      <span class="svc-name">Databases</span>
      <span class="svc-status">platform postgres</span>
    </button>
    {#if expanded}
      <button class="btn ghost sm" onclick={load} disabled={loading}>refresh</button>
    {/if}
  </div>

  {#if expanded}
    <div class="db-body">
      {#if loading}
        <div class="db-note">loading…</div>
      {:else if error}
        <div class="db-note err">{error}</div>
      {:else if dbs.length === 0}
        <div class="db-note">No platform databases found — is the platform postgres container running?</div>
      {:else}
        {#each dbs as db (db.name)}
          <div class="db-row">
            <span class="db-name">{db.name}</span>
            <span class="db-size">{db.size}</span>
            <div class="db-actions">
              {#if confirming === db.name}
                <span class="db-warn">overwrite {db.name}?</span>
                <button class="btn danger sm" disabled={busy !== null} onclick={() => restore(db.name)}>confirm restore</button>
                <button class="btn ghost sm" onclick={() => confirming = null}>cancel</button>
              {:else}
                <button class="btn sm" disabled={busy !== null} onclick={() => backup(db.name)}>
                  {busy === db.name ? '…' : 'backup'}
                </button>
                <button class="btn ghost sm" disabled={busy !== null} onclick={() => confirming = db.name}>restore</button>
              {/if}
            </div>
          </div>
        {/each}
      {/if}

      {#if lastResult}
        <pre class="db-result">{lastResult}</pre>
      {/if}
      <div class="db-hint">Backup uses pg_dump custom format (restorable via restore). Restore is destructive — it drops &amp; recreates objects, so run it when the owning service is idle.</div>
    </div>
  {/if}
</div>

<style>
  .db-card { display: flex; flex-direction: column; }
  .card-top { display: flex; align-items: center; gap: 8px; }
  .card-identity {
    display: flex; align-items: center; gap: 8px; flex: 1;
    background: none; border: none; cursor: pointer; color: var(--text);
    padding: 0; text-align: left;
  }
  .expand-chevron { color: var(--text-muted); transition: transform 0.15s ease; flex: none; }
  .expand-chevron.expanded { transform: rotate(90deg); }
  .svc-name { font-weight: 600; font-size: 13.5px; }
  .svc-status { color: var(--text-muted); font-size: 12px; margin-left: auto; }

  .db-body { display: flex; flex-direction: column; gap: 6px; margin-top: 12px; }
  .db-row {
    display: flex; align-items: center; gap: 10px;
    padding: 7px 4px; border-top: 1px solid var(--border);
  }
  .db-name { font-weight: 600; font-size: 13px; }
  .db-size { color: var(--text-muted); font-size: 12px; font-variant-numeric: tabular-nums; }
  .db-actions { margin-left: auto; display: flex; align-items: center; gap: 6px; }
  .db-warn { color: var(--fail); font-size: 12px; font-weight: 600; }
  .db-note { color: var(--text-muted); font-size: 12.5px; padding: 4px; }
  .db-note.err { color: var(--fail); white-space: pre-wrap; }
  .db-result {
    margin: 6px 0 0; padding: 8px 10px; background: var(--bg-hover);
    border-radius: 6px; font-size: 11.5px; line-height: 1.45;
    white-space: pre-wrap; word-break: break-word; max-height: 200px; overflow: auto;
  }
  .db-hint { color: var(--text-muted); font-size: 11.5px; line-height: 1.5; margin-top: 4px; }
</style>

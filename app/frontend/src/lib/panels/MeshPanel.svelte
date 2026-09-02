<script lang="ts">
  // p2p phantom-brain mesh: local node + each peer with sync stats. Reads a
  // single unauthenticated GET {url}/admin/mesh/status via the Go bridge
  // (GetMeshStatus), against the "phantom-brain-mesh" integration URL (default
  // http://127.0.0.1:9998 — a DIFFERENT backend than the brainbox API).
  import { onMount, onDestroy } from 'svelte';
  import { getApi } from '../utils/api';
  import { timeAgo } from '../utils/format';
  import Spinner from '../components/Spinner.svelte';
  import EmptyState from '../components/EmptyState.svelte';

  interface MeshPeer {
    id: string;
    base_url: string;
    profile: string;
    live: boolean;
    live_note: string;
    db_lag: string;
    links_lag: string;
    cursor_age: string;
  }
  interface MeshMetrics {
    pb_sync_rounds_total: string;
    pb_sync_rows_merged_total: string;
    pb_sync_blobs_fetched_total: string;
    pb_sync_orphan_blobs_gc_total: string;
    pb_sync_errors_total: string;
    pb_sync_last_tick_ms: string;
  }
  interface MeshStatus {
    node_id: string;
    sync_enabled: boolean;
    peers: MeshPeer[];
    metrics: MeshMetrics;
  }

  const DEFAULT_URL = 'http://127.0.0.1:9998';

  let status = $state<MeshStatus | null>(null);
  let loaded = $state(false);
  let loadError = $state<string | null>(null);
  let refreshing = $state(false);

  let pollHandle: number | undefined;

  onMount(() => {
    void refresh();
    pollHandle = window.setInterval(() => void refresh(), 10_000);
  });
  onDestroy(() => { if (pollHandle !== undefined) window.clearInterval(pollHandle); });

  async function refresh() {
    const a = await getApi();
    if (!a) { loaded = true; loadError = 'API bindings unavailable'; return; }
    refreshing = true;
    try {
      status = (await (a as any).GetMeshStatus()) as MeshStatus;
      loadError = null;
    } catch (e: any) {
      loadError = e?.message ?? String(e);
    } finally {
      loaded = true;
      refreshing = false;
    }
  }

  // Metrics helpers — daemon emits strings; keep display robust to junk.
  function metric(key: keyof MeshMetrics): string {
    return status?.metrics?.[key] ?? '0';
  }
  function lastTick(): string {
    const raw = status?.metrics?.pb_sync_last_tick_ms ?? '0';
    const ms = Number(raw);
    if (!Number.isFinite(ms) || ms <= 0) return 'never';
    return timeAgo(ms);
  }

  const peers = $derived(status?.peers ?? []);
  const liveCount = $derived(peers.filter((p) => p.live).length);
</script>

<div class="mesh">
  <header class="head">
    <div>
      <h1>Brain Mesh</h1>
      <p class="sub">The p2p phantom-brain memory mesh — this node plus every peer, with sync stats.</p>
    </div>
    <div class="head-actions">
      {#if status}
        <span class="node-badge">
          <span class="node-id">{status.node_id || 'unknown'}</span>
          <span class="sync-badge" class:on={status.sync_enabled}>
            {status.sync_enabled ? 'sync on' : 'sync off'}
          </span>
        </span>
      {/if}
      <button class="btn" onclick={() => void refresh()} disabled={refreshing}>
        {refreshing ? 'refreshing…' : 'refresh'}
      </button>
    </div>
  </header>

  {#if !loaded}
    <Spinner />
  {:else if loadError}
    <EmptyState
      title="Brain daemon unreachable"
      message={`${loadError}\n\nConfigured URL: ${DEFAULT_URL}. Check the Phantom-Brain Mesh integration in Integrations.`}
    />
  {:else if status}
    <!-- Sync counters -->
    <section class="card counters">
      <h2>Sync counters</h2>
      <div class="counter-strip">
        <div class="counter">
          <span class="c-val">{metric('pb_sync_rounds_total')}</span>
          <span class="c-label">rounds</span>
        </div>
        <div class="counter">
          <span class="c-val">{metric('pb_sync_rows_merged_total')}</span>
          <span class="c-label">rows merged</span>
        </div>
        <div class="counter">
          <span class="c-val">{metric('pb_sync_blobs_fetched_total')}</span>
          <span class="c-label">blobs fetched</span>
        </div>
        <div class="counter">
          <span class="c-val err">{metric('pb_sync_errors_total')}</span>
          <span class="c-label">errors</span>
        </div>
        <div class="counter">
          <span class="c-val">{lastTick()}</span>
          <span class="c-label">last tick</span>
        </div>
      </div>
    </section>

    <!-- Peers -->
    <section class="card peers">
      <h2>Peers <span class="peer-count">{liveCount}/{peers.length} live</span></h2>
      {#if peers.length === 0}
        <EmptyState
          title={status.sync_enabled ? 'No peers' : 'Sync disabled'}
          message={status.sync_enabled
            ? 'This node has no configured peers in the mesh.'
            : 'Sync is off on this node — no peers are being tracked.'}
        />
      {:else}
        <div class="table-wrap">
          <table class="peer-table">
            <thead>
              <tr>
                <th>peer</th>
                <th>profile</th>
                <th>live</th>
                <th class="num">db-lag</th>
                <th class="num">links-lag</th>
                <th class="num">cursor-age</th>
              </tr>
            </thead>
            <tbody>
              {#each peers as p (p.id + p.profile)}
                <tr>
                  <td>
                    <div class="peer-id">{p.id}</div>
                    {#if p.base_url}<div class="peer-url">{p.base_url}</div>{/if}
                  </td>
                  <td>{p.profile}</td>
                  <td>
                    <span class="dot" class:up={p.live} class:down={!p.live}></span>
                    <span class="live-txt">{p.live ? 'up' : 'down'}</span>
                    {#if !p.live && p.live_note}
                      <span class="live-note">{p.live_note}</span>
                    {/if}
                  </td>
                  <td class="num">{p.db_lag}</td>
                  <td class="num">{p.links_lag}</td>
                  <td class="num">{p.cursor_age}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {/if}
    </section>
  {/if}
</div>

<style>
  .mesh { padding: var(--panel-padding); color: var(--color-text-primary); }
  .head { display: flex; justify-content: space-between; align-items: flex-start; gap: var(--spacing-lg); margin-bottom: var(--spacing-lg); }
  h1 { font-size: 1.4rem; margin: 0; }
  .sub { color: var(--color-text-muted); margin: 2px 0 0; font-size: 0.85rem; max-width: 56ch; }
  h2 {
    font-size: 0.95rem; margin: 0 0 var(--spacing-md); color: var(--color-text-secondary);
    display: flex; align-items: baseline; gap: 8px;
  }

  .head-actions { display: flex; align-items: center; gap: 0.6rem; white-space: nowrap; }
  .node-badge { display: inline-flex; align-items: center; gap: 8px; }
  .node-id { font-family: var(--font-mono); font-size: 0.85rem; color: var(--color-text-secondary); }
  .sync-badge {
    font-size: 0.68rem; padding: 2px 8px; border-radius: 999px; text-transform: lowercase;
    border: 1px solid var(--color-border-secondary); color: var(--color-text-muted);
  }
  .sync-badge.on { color: var(--color-success); border-color: var(--color-success); }
  .btn {
    background: var(--color-bg-secondary); border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm); color: var(--color-text-muted);
    cursor: pointer; font-size: 0.72rem; padding: 0.25rem 0.6rem;
  }
  .btn:hover:not(:disabled) { color: var(--color-text-primary); }
  .btn:disabled { opacity: 0.5; cursor: default; }

  .card {
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-md);
    padding: var(--spacing-lg);
    margin-bottom: var(--spacing-lg);
  }

  .counter-strip { display: flex; flex-wrap: wrap; gap: var(--spacing-lg); }
  .counter { display: flex; flex-direction: column; gap: 2px; min-width: 88px; }
  .c-val { font-size: 1.3rem; font-weight: 600; font-variant-numeric: tabular-nums; }
  .c-val.err { color: var(--color-error); }
  .c-label { font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--color-text-tertiary); }

  .peer-count { font-size: 0.72rem; font-weight: 400; color: var(--color-text-muted); }

  .table-wrap { overflow-x: auto; }
  .peer-table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
  .peer-table th {
    text-align: left; font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.04em;
    color: var(--color-text-tertiary); font-weight: 500; padding: 0 12px 8px 0; white-space: nowrap;
  }
  .peer-table th.num, .peer-table td.num { text-align: right; font-variant-numeric: tabular-nums; }
  .peer-table td { padding: 8px 12px 8px 0; border-top: 1px solid var(--color-border-secondary); vertical-align: top; }
  .peer-id { font-family: var(--font-mono); color: var(--color-text-primary); }
  .peer-url { font-family: var(--font-mono); font-size: 0.7rem; color: var(--color-text-muted); margin-top: 2px; }

  .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; vertical-align: middle; }
  .dot.up { background: var(--color-success); }
  .dot.down { background: var(--color-error); }
  .live-txt { color: var(--color-text-secondary); }
  .live-note { display: block; font-size: 0.7rem; color: var(--color-text-muted); margin-top: 2px; }
</style>

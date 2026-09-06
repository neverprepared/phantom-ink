<script lang="ts">
  // p2p phantom-brain mesh. Two tabs over ONE plane (the mesh daemon, default
  // http://127.0.0.1:9998):
  //   • Nodes  — plane health: this node + peers with sync stats, grouped by
  //              profile (each profile rolls up to a status dot + live/total and
  //              expands to its itemized peer×vault rows). Reads the
  //              unauthenticated GET {url}/admin/mesh/status.
  //   • Agents — plane content: the p2p-synced "agents" vault browser (a verbatim
  //              sibling of skills). Authenticated per-vault (see AgentsPanel).
  import { onMount, onDestroy } from 'svelte';
  import { getApi } from '../utils/api';
  import { timeAgo } from '../utils/format';
  import Spinner from '../components/Spinner.svelte';
  import EmptyState from '../components/EmptyState.svelte';
  import VaultBrowser from './VaultBrowser.svelte';

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
  interface ProfileGroup {
    profile: string;
    peers: MeshPeer[];
    live: number;
    total: number;
    status: 'up' | 'partial' | 'down';
  }

  const DEFAULT_URL = 'http://127.0.0.1:9998';

  let activeTab = $state<'nodes' | 'agents' | 'skills' | 'todo'>('nodes');
  let status = $state<MeshStatus | null>(null);
  let loaded = $state(false);
  let loadError = $state<string | null>(null);
  let refreshing = $state(false);
  let expanded = $state<Record<string, boolean>>({});

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

  // A peer label is "<host>-<profile>-<vault>" by convention. Strip the profile
  // token (shown in the group header) → "<host> · <vault>"; fall back to the raw
  // id if the convention doesn't hold.
  function peerLabel(p: MeshPeer): string {
    const parts = p.id.split('-').filter((x) => x && x !== p.profile);
    return parts.length ? parts.join(' · ') : p.id;
  }

  const peers = $derived(status?.peers ?? []);
  const profileGroups = $derived.by<ProfileGroup[]>(() => {
    const map = new Map<string, MeshPeer[]>();
    for (const p of peers) {
      const key = p.profile || '(unknown)';
      (map.get(key) ?? map.set(key, []).get(key)!).push(p);
    }
    return [...map.entries()]
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([profile, ps]) => {
        const live = ps.filter((x) => x.live).length;
        const total = ps.length;
        const st: ProfileGroup['status'] = live === total ? 'up' : live === 0 ? 'down' : 'partial';
        return { profile, peers: ps, live, total, status: st };
      });
  });

  function toggle(profile: string) {
    expanded = { ...expanded, [profile]: !expanded[profile] };
  }
</script>

<div class="mesh">
  <header class="head">
    <div>
      <h1>Brain Mesh</h1>
      <p class="sub">The p2p phantom-brain memory mesh — node sync health plus the agents, skills, and todo vaults riding it.</p>
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
    </div>
  </header>

  <nav class="tabs">
    <button class="tab" class:active={activeTab === 'nodes'} onclick={() => (activeTab = 'nodes')}>Nodes</button>
    <button class="tab" class:active={activeTab === 'agents'} onclick={() => (activeTab = 'agents')}>Agents</button>
    <button class="tab" class:active={activeTab === 'skills'} onclick={() => (activeTab = 'skills')}>Skills</button>
    <button class="tab" class:active={activeTab === 'todo'} onclick={() => (activeTab = 'todo')}>Todo</button>
  </nav>

  {#if activeTab === 'nodes'}
    {#if !loaded}
      <Spinner />
    {:else if loadError}
      <EmptyState
        title="Brain daemon unreachable"
        message={`${loadError}\n\nConfigured URL: ${DEFAULT_URL}. Check the Phantom-Brain Mesh integration in Integrations.`}
      />
    {:else if status}
      <div class="tab-actions">
        <button class="btn" onclick={() => void refresh()} disabled={refreshing}>
          {refreshing ? 'refreshing…' : 'refresh'}
        </button>
      </div>

      <!-- Sync counters (daemon-wide) -->
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

      <!-- Peers, grouped by profile -->
      <section class="card peers">
        <h2>Sync by profile <span class="peer-count">{profileGroups.length} profile{profileGroups.length === 1 ? '' : 's'}</span></h2>
        {#if peers.length === 0}
          <EmptyState
            title={status.sync_enabled ? 'No peers' : 'Sync disabled'}
            message={status.sync_enabled
              ? 'This node has no configured peers in the mesh.'
              : 'Sync is off on this node — no peers are being tracked.'}
          />
        {:else}
          <div class="groups">
            {#each profileGroups as g (g.profile)}
              <div class="pgroup">
                <button class="pgroup-head" onclick={() => toggle(g.profile)} aria-expanded={!!expanded[g.profile]}>
                  <span class="chevron" class:open={expanded[g.profile]}>▸</span>
                  <span class="dot" class:up={g.status === 'up'} class:partial={g.status === 'partial'} class:down={g.status === 'down'}></span>
                  <span class="pname">{g.profile}</span>
                  <span class="pcount" class:warn={g.status !== 'up'}>{g.live}/{g.total} live</span>
                </button>
                {#if expanded[g.profile]}
                  <div class="table-wrap">
                    <table class="peer-table">
                      <thead>
                        <tr>
                          <th>peer</th>
                          <th>live</th>
                          <th class="num">db-lag</th>
                          <th class="num">links-lag</th>
                          <th class="num">cursor-age</th>
                        </tr>
                      </thead>
                      <tbody>
                        {#each g.peers as p (p.id)}
                          <tr>
                            <td>
                              <div class="peer-id">{peerLabel(p)}</div>
                              {#if p.base_url}<div class="peer-url">{p.base_url}</div>{/if}
                            </td>
                            <td>
                              <span class="dot sm" class:up={p.live} class:down={!p.live}></span>
                              <span class="live-txt">{p.live ? 'up' : 'down'}</span>
                              {#if !p.live && p.live_note}<span class="live-note">{p.live_note}</span>{/if}
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
              </div>
            {/each}
          </div>
        {/if}
      </section>
    {/if}
  {:else if activeTab === 'agents'}
    <VaultBrowser vault="agents" />
  {:else if activeTab === 'skills'}
    <VaultBrowser vault="skills" />
  {:else}
    <VaultBrowser vault="todo" />
  {/if}
</div>

<style>
  .mesh { padding: var(--panel-padding); color: var(--color-text-primary); }
  .head { display: flex; justify-content: space-between; align-items: flex-start; gap: var(--spacing-lg); margin-bottom: var(--spacing-md); }
  h1 { font-size: 1.4rem; margin: 0; }
  .sub { color: var(--color-text-muted); margin: 2px 0 0; font-size: 0.85rem; max-width: 60ch; }
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

  .tabs { display: flex; gap: 2px; border-bottom: 1px solid var(--color-border-primary); margin-bottom: var(--spacing-lg); }
  .tab {
    background: transparent; border: none; border-bottom: 2px solid transparent;
    color: var(--color-text-muted); cursor: pointer; font-size: 0.85rem;
    padding: 0.45rem 0.9rem; margin-bottom: -1px;
  }
  .tab:hover { color: var(--color-text-primary); }
  .tab.active { color: var(--color-text-primary); border-bottom-color: var(--color-accent, var(--color-text-primary)); }

  .tab-actions { display: flex; justify-content: flex-end; margin-bottom: var(--spacing-md); }
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

  /* Per-profile accordion */
  .groups { display: flex; flex-direction: column; gap: 6px; }
  .pgroup { border: 1px solid var(--color-border-secondary); border-radius: var(--radius-sm); overflow: hidden; }
  .pgroup-head {
    display: flex; align-items: center; gap: 10px; width: 100%; text-align: left;
    background: transparent; border: none; color: var(--color-text-primary);
    cursor: pointer; padding: 10px 12px; font-size: 0.9rem;
  }
  .pgroup-head:hover { background: var(--color-bg-hover, rgba(255,255,255,0.03)); }
  .chevron { display: inline-block; transition: transform 0.12s ease; color: var(--color-text-tertiary); font-size: 0.7rem; }
  .chevron.open { transform: rotate(90deg); }
  .pname { font-family: var(--font-mono); font-size: 0.85rem; }
  .pcount { margin-left: auto; font-size: 0.72rem; color: var(--color-text-muted); font-variant-numeric: tabular-nums; }
  .pcount.warn { color: var(--color-warning, var(--color-error)); }

  .dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; flex: none; }
  .dot.sm { width: 8px; height: 8px; margin-right: 6px; vertical-align: middle; }
  .dot.up { background: var(--color-success); }
  .dot.partial { background: var(--color-warning, #d19a24); }
  .dot.down { background: var(--color-error); }

  .table-wrap { overflow-x: auto; border-top: 1px solid var(--color-border-secondary); }
  .peer-table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
  .peer-table th {
    text-align: left; font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.04em;
    color: var(--color-text-tertiary); font-weight: 500; padding: 8px 12px 8px; white-space: nowrap;
  }
  .peer-table th.num, .peer-table td.num { text-align: right; font-variant-numeric: tabular-nums; }
  .peer-table td { padding: 8px 12px; border-top: 1px solid var(--color-border-secondary); vertical-align: top; }
  .peer-id { font-family: var(--font-mono); color: var(--color-text-primary); }
  .peer-url { font-family: var(--font-mono); font-size: 0.7rem; color: var(--color-text-muted); margin-top: 2px; }
  .live-txt { color: var(--color-text-secondary); }
  .live-note { display: block; font-size: 0.7rem; color: var(--color-text-muted); margin-top: 2px; }
</style>

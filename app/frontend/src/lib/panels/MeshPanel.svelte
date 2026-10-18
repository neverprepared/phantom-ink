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
  import VaultBrowser from './VaultBrowser.svelte';

  // One retained sync failure from the daemon's ring. Optional throughout:
  // an older daemon sends none, and the panel must still work against it.
  interface SyncError {
    at: string;
    peer: string;
    profile: string;
    phase: string;
    class: string;
    err: string;
  }
  interface MeshPeer {
    id: string;
    base_url: string;
    profile: string;
    live: boolean;
    live_note: string;
    db_lag: string;
    links_lag: string;
    cursor_age: string;
    last_error?: SyncError;
    errors_24h?: number;
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
    recent_errors?: SyncError[];
    errors_15m?: number;
  }
  // Per-vault AUTH reachability for the active profile — whether the stored
  // bearer token actually authenticates against the daemon. This is separate
  // from GetMeshStatus (unauthenticated transport/sync); a peer can be live
  // while our token is missing or rejected (401).
  interface VaultAuth {
    vault: string;
    has_token: boolean;
    ok: boolean;
    detail: string;
  }

  const DEFAULT_URL = 'http://127.0.0.1:9998';

  let status = $state<MeshStatus | null>(null);
  let loaded = $state(false);
  let loadError = $state<string | null>(null);
  let refreshing = $state(false);
  let vaultAuth = $state<VaultAuth[]>([]);

  // Tabs: the mesh status view, plus a per-feature browser for each verbatim
  // p2p-synced vault. Each vault tab renders VaultBrowser, which scopes to the
  // app's ACTIVE profile (global switcher) and can move records across profiles
  // — that's the per-profile dimension. (memory is the synth vault, not browsed here.)
  type MeshTab = 'mesh' | 'agents' | 'skills' | 'todo';
  const TABS: { id: MeshTab; label: string }[] = [
    { id: 'mesh', label: 'Mesh' },
    { id: 'agents', label: 'Agents' },
    { id: 'skills', label: 'Skills' },
    { id: 'todo', label: 'Todo' },
  ];
  let activeTab = $state<MeshTab>('mesh');

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
    // Auth reachability is independent of the transport status — probe it
    // separately so a mesh-status failure doesn't hide the token state (and
    // vice versa). Best-effort: leave the last snapshot on error.
    try {
      vaultAuth = ((await (a as any).VaultAuthStatus()) as VaultAuth[]) ?? [];
    } catch {
      /* keep previous auth snapshot */
    }
    // Whether the remediation buttons can work at all. Probed every refresh so
    // saving a key elsewhere takes effect without reloading the panel.
    try {
      adminKeySet = Boolean(await (a as any).GetMeshAdminKeySet());
    } catch {
      adminKeySet = false;
    }
  }

  // Auth-indicator helpers, shared by the tab dots and the overview summary.
  const authFor = (vault: string): VaultAuth | undefined =>
    vaultAuth.find((v) => v.vault === vault);
  function authClass(vault: string): string {
    const a = authFor(vault);
    if (!a) return '';
    if (a.ok) return 'auth-ok';
    return a.has_token ? 'auth-fail' : 'auth-none';
  }
  function authTitle(vault: string): string {
    const a = authFor(vault);
    if (!a) return '';
    if (a.ok) return `${vault}: token authenticates`;
    return a.has_token ? `${vault}: ${a.detail}` : `${vault}: no token set`;
  }
  function authGlyph(a: VaultAuth): string {
    return a.ok ? '✓' : a.has_token ? '✗' : '—';
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

  // The counter is cumulative since daemon start and never decays, so a healed
  // outage stays red forever. errors_15m is the live signal; the counter alone
  // is history.
  const recentErrors = $derived(status?.recent_errors ?? []);
  const errorsAreLive = $derived((status?.errors_15m ?? 0) > 0);
  const lastErrorAge = $derived.by(() => {
    if (recentErrors.length === 0) return '';
    const ms = Date.parse(recentErrors[0].at);
    return Number.isFinite(ms) ? timeAgo(ms) : '';
  });

  // The class is the daemon's closed vocabulary; the diagnosis is what the
  // operator actually needs to read. Keep these in step with ClassifyError.
  const DIAGNOSIS: Record<string, string> = {
    dns: "The daemon container's resolver cannot reach this peer's hostname. Usually Docker DNS churn, not a peer outage.",
    timeout: 'The peer accepted the connection but did not answer in time.',
    refused: 'Nothing is listening at the peer URL.',
    auth: "This node's peer token is missing, wrong, or revoked.",
    proto: 'Breaking wire-version gap — finish the staggered upgrade on both nodes.',
    fenced: 'The peer is reconciling and withholding its op-log. Benign.',
    blob: 'Record metadata merged; attachment reconciliation failed.',
    pool: 'This node could not open its local database for the profile.',
    other: 'Unclassified — the message below is the whole diagnosis.',
  };

  interface ErrorGroup {
    key: string;
    peer: string;
    cls: string;
    count: number;
    latest: SyncError;
  }

  // Group by (peer × class): one row per distinct failure mode per peer, which
  // is the unit an operator acts on. recentErrors is newest-first, so the first
  // entry per key is the latest.
  const errorGroups = $derived.by<ErrorGroup[]>(() => {
    const byKey = new Map<string, ErrorGroup>();
    for (const e of recentErrors) {
      const key = `${e.peer}::${e.class}`;
      const existing = byKey.get(key);
      if (existing) existing.count++;
      else byKey.set(key, { key, peer: e.peer, cls: e.class, count: 1, latest: e });
    }
    // Count descending, then a deterministic tie-break on peer then class. The
    // rows are keyed, so Svelte MOVES them on a reorder rather than rebuilding
    // — smooth enough to miss mid-click, which would land "reset cursor" on a
    // row the operator did not aim at. Equal counts must therefore hold a fixed
    // order rather than inheriting Map insertion order.
    return [...byKey.values()].sort(
      (a, b) => b.count - a.count || a.peer.localeCompare(b.peer) || a.cls.localeCompare(b.cls),
    );
  });

  let errorsOpen = $state(false);
  let errorPeerFilter = $state<string | null>(null);
  let adminKeySet = $state(false);
  let actionBusy = $state<string | null>(null);
  let actionNote = $state<string | null>(null);

  const shownGroups = $derived(
    errorPeerFilter ? errorGroups.filter((g) => g.peer === errorPeerFilter) : errorGroups,
  );

  function errAge(e: SyncError): string {
    const ms = Date.parse(e.at);
    return Number.isFinite(ms) ? timeAgo(ms) : '';
  }

  async function runAction(label: string, fn: () => Promise<any>, note: (r: any) => string) {
    actionBusy = label;
    actionNote = null;
    try {
      actionNote = note(await fn());
    } catch (e: any) {
      actionNote = e?.message ?? String(e);
    } finally {
      actionBusy = null;
      await refresh();
    }
  }

  // The action is per-peer, but the in-flight LABEL is keyed by group so only
  // the clicked row reads "retrying…". Keyed by peer, a peer with both a dns
  // and an auth group lit up both rows — which, combined with a reorder, makes
  // the clicked row look idle while a sibling looks busy.
  async function retryPeer(id: string, groupKey: string) {
    const a = await getApi();
    if (!a) return;
    // A retry that fails must say why — reporting failure is the button's whole
    // point, so the daemon's own error text wins over the merged counts.
    await runAction(`retry:${groupKey}`, () => (a as any).RetryMeshPeer(id), (r) =>
      r?.errors?.length
        ? `${id}: retry failed — ${r.errors[0].err}`
        : `${id}: merged ${r?.rows_merged ?? 0} rows, ${r?.links_merged ?? 0} links`,
    );
  }

  async function resetCursor(id: string, groupKey: string) {
    if (
      !window.confirm(
        `Reset the sync cursor for ${id}?\n\nThe next pull re-reads its whole op-log. Costs bandwidth; cannot lose data.`,
      )
    )
      return;
    const a = await getApi();
    if (!a) return;
    await runAction(`reset:${groupKey}`, () => (a as any).ResetMeshPeerCursor(id), () => `${id}: cursor reset`);
  }

  // A note describes one click against one filter view, so it must not outlive
  // either — closing the card or changing the filter retires it.
  function toggleErrors() {
    errorsOpen = !errorsOpen;
    if (!errorsOpen) actionNote = null;
  }

  function setErrorPeerFilter(peer: string | null) {
    errorPeerFilter = peer;
    actionNote = null;
  }

  async function clearErrors() {
    const a = await getApi();
    if (!a) return;
    await runAction('clear', () => (a as any).ClearMeshErrors(), (r) => `cleared ${r?.cleared ?? 0} recorded errors`);
  }

  // Mesh-view sub-tab filters: by machine (mesh node) and by profile. Values
  // are derived from the data, not hardcoded. Filters AND together.
  // Peer ids are <machine>-<profile>-<vault> (e.g. m3-gsa-agents), so the
  // machine is the first segment — every peer on m3 folds into one "m3" chip.
  const machineOf = (id: string): string => id.split('-')[0];
  let machineFilter = $state<string>('all');
  let profileFilter = $state<string>('all');
  const machines = $derived([...new Set(peers.map((p) => machineOf(p.id)))].sort());
  const profiles = $derived([...new Set(peers.map((p) => p.profile))].sort());
  const filteredPeers = $derived(
    peers.filter(
      (p) =>
        (machineFilter === 'all' || machineOf(p.id) === machineFilter) &&
        (profileFilter === 'all' || p.profile === profileFilter),
    ),
  );
  const liveCount = $derived(filteredPeers.filter((p) => p.live).length);
</script>

<div class="mesh">
  <header class="head">
    <div>
      <h1>Brain Mesh</h1>
      <p class="sub">The p2p phantom-brain memory mesh — this node plus every peer, with sync stats.</p>
    </div>
    <div class="head-actions">
      {#if activeTab === 'mesh'}
        {#if status}
          <span class="node-badge">
            <span class="node-id">{status.node_id || 'unknown'}</span>
            <span class="sync-badge" class:on={status.sync_enabled}>
              {status.sync_enabled ? 'sync on' : 'sync off'}
            </span>
          </span>
        {/if}
        {#if vaultAuth.length}
          <span class="auth-summary" title="vault token auth for the active profile">
            <span class="auth-label">auth</span>
            {#each vaultAuth as va (va.vault)}
              <span class="auth-chip {authClass(va.vault)}" title={authTitle(va.vault)}>
                {va.vault}&nbsp;{authGlyph(va)}
              </span>
            {/each}
          </span>
        {/if}
        <button class="btn" onclick={() => void refresh()} disabled={refreshing}>
          {refreshing ? 'refreshing…' : 'refresh'}
        </button>
      {/if}
    </div>
  </header>

  <div class="tabs" role="tablist" aria-label="Brain mesh sections">
    {#each TABS as t (t.id)}
      <button
        class="tab"
        class:active={activeTab === t.id}
        role="tab"
        aria-selected={activeTab === t.id}
        onclick={() => {
          activeTab = t.id;
          actionNote = null;
        }}
      >{t.label}{#if t.id !== 'mesh' && authFor(t.id)}<span
            class="auth-dot {authClass(t.id)}"
            title={authTitle(t.id)}
            aria-hidden="true"
          ></span>{/if}</button>
    {/each}
  </div>

  {#if activeTab === 'mesh'}
  <!-- Remediation feedback lives at panel level, not in the errors card: a
       successful clear empties the ring and unmounts that card, and an action
       against a dead daemon swaps the whole chain for the unreachable state.
       Either would take the note with it and leave the click unconfirmed. -->
  {#if actionNote}
    <p class="action-note" role="status" aria-live="polite">
      {actionNote}
      <button class="note-x" onclick={() => (actionNote = null)} aria-label="dismiss">✕</button>
    </p>
  {/if}
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
        <button
          class="counter counter-btn"
          onclick={toggleErrors}
          disabled={recentErrors.length === 0}
          aria-expanded={errorsOpen}
          title={recentErrors.length ? 'show recorded errors' : 'no recorded error detail to show'}
        >
          <span class="c-val" class:err={errorsAreLive} class:stale={!errorsAreLive && metric('pb_sync_errors_total') !== '0'}
            >{metric('pb_sync_errors_total')}</span>
          <span class="c-label">errors{#if recentErrors.length} {errorsOpen ? '▾' : '▸'}{/if}</span>
          {#if !errorsAreLive && lastErrorAge}
            <span class="c-note">none in {lastErrorAge}</span>
          {/if}
        </button>
        <div class="counter">
          <span class="c-val">{lastTick()}</span>
          <span class="c-label">last tick</span>
        </div>
      </div>
    </section>

    <!-- Recorded sync errors, grouped by peer and cause -->
    {#if errorsOpen && errorGroups.length}
      <section class="card errors-card">
        <h2>
          Errors
          <span class="peer-count">{errorGroups.length} grouped by peer and cause</span>
          {#if errorPeerFilter}
            <button class="pill active" onclick={() => setErrorPeerFilter(null)}>{errorPeerFilter} ✕</button>
          {/if}
          <button class="btn sm" onclick={() => void clearErrors()} disabled={!adminKeySet || actionBusy !== null}>
            {actionBusy === 'clear' ? 'clearing…' : 'clear errors'}
          </button>
        </h2>
        {#if !adminKeySet}
          <p class="no-match">
            No mesh admin key configured — actions are disabled. Save one under
            <strong>brain mesh admin key</strong> in Settings, or set an ambient
            <code>PB_ADMIN_KEY</code> in the app's environment, matching the daemon's.
          </p>
        {/if}
        {#if shownGroups.length === 0}
          <p class="no-match">No recorded errors for {errorPeerFilter}.</p>
        {/if}
        <div class="err-list">
          {#each shownGroups as g (g.key)}
            <div class="err-row">
              <div class="err-head">
                <span class="err-peer">{g.peer}</span>
                <span class="err-class">{g.cls}</span>
                <span class="err-count">×{g.count}</span>
                <span class="err-age">{errAge(g.latest)}</span>
              </div>
              <div class="err-diag">{DIAGNOSIS[g.cls] ?? DIAGNOSIS.other}</div>
              <div class="err-msg">{g.latest.err}</div>
              <div class="err-actions">
                <button
                  class="btn sm"
                  onclick={() => void retryPeer(g.peer, g.key)}
                  disabled={!adminKeySet || actionBusy !== null}
                >
                  {actionBusy === `retry:${g.key}` ? 'retrying…' : 'retry peer'}
                </button>
                {#if g.latest.phase === 'pull' || g.latest.phase === 'links'}
                  <button
                    class="btn sm danger"
                    onclick={() => void resetCursor(g.peer, g.key)}
                    disabled={!adminKeySet || actionBusy !== null}
                  >
                    {actionBusy === `reset:${g.key}` ? 'resetting…' : 'reset cursor'}
                  </button>
                {/if}
              </div>
            </div>
          {/each}
        </div>
      </section>
    {/if}

    <!-- Peers -->
    <section class="card peers">
      <h2>Peers <span class="peer-count">{liveCount}/{filteredPeers.length} live</span></h2>
      {#if peers.length === 0}
        <EmptyState
          title={status.sync_enabled ? 'No peers' : 'Sync disabled'}
          message={status.sync_enabled
            ? 'This node has no configured peers in the mesh.'
            : 'Sync is off on this node — no peers are being tracked.'}
        />
      {:else}
        <div class="filters">
          <div class="filter-row">
            <span class="filter-label">machine</span>
            <button class="pill" class:active={machineFilter === 'all'} onclick={() => (machineFilter = 'all')}>all</button>
            {#each machines as m (m)}
              <button class="pill" class:active={machineFilter === m} onclick={() => (machineFilter = m)}>{m}</button>
            {/each}
          </div>
          <div class="filter-row">
            <span class="filter-label">profile</span>
            <button class="pill" class:active={profileFilter === 'all'} onclick={() => (profileFilter = 'all')}>all</button>
            {#each profiles as pr (pr)}
              <button class="pill" class:active={profileFilter === pr} onclick={() => (profileFilter = pr)}>{pr}</button>
            {/each}
          </div>
        </div>
        {#if filteredPeers.length === 0}
          <p class="no-match">No peers match the current filters.</p>
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
              {#each filteredPeers as p (p.id + p.profile)}
                <tr>
                  <td>
                    <div class="peer-id">{p.id}</div>
                    {#if p.base_url}<div class="peer-url">{p.base_url}</div>{/if}
                    {#if p.errors_24h}
                      <button
                        class="peer-warn"
                        title={`${p.errors_24h} recorded errors — show them`}
                        onclick={() => {
                          setErrorPeerFilter(p.id);
                          errorsOpen = true;
                        }}>⚠ {p.errors_24h}</button>
                    {/if}
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
      {/if}
    </section>
  {/if}
  {:else}
    {#key activeTab}
      <VaultBrowser vault={activeTab} />
    {/key}
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
  /* Svelte styles are component-scoped, so the modifiers other panels define
     do not reach here — they are declared locally, matching VaultBrowser. */
  .btn.sm { font-size: 0.68rem; padding: 0.15rem 0.45rem; }
  .btn.danger { color: #fff; background: var(--color-error); border-color: var(--color-error); }

  .tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--color-border-primary); margin-bottom: var(--spacing-lg); }
  .tab {
    background: none; border: none; border-bottom: 2px solid transparent;
    color: var(--color-text-muted); cursor: pointer; font-size: 0.82rem;
    padding: 0.4rem 0.8rem; margin-bottom: -1px;
  }
  .tab:hover { color: var(--color-text-secondary); }
  .tab.active { color: var(--color-text-primary); border-bottom-color: var(--color-text-primary); }
  /* Per-vault auth-reachability dot on each vault tab. */
  .auth-dot {
    display: inline-block; width: 7px; height: 7px; border-radius: 50%;
    margin-left: 6px; vertical-align: middle;
    background: var(--color-text-tertiary);
  }
  .auth-dot.auth-ok { background: var(--color-success); }
  .auth-dot.auth-fail { background: var(--color-error); }
  .auth-dot.auth-none { background: var(--color-text-tertiary); }
  /* One-line auth summary in the mesh-tab header. */
  .auth-summary { display: inline-flex; align-items: center; gap: 6px; margin-left: 4px; }
  .auth-label {
    font-size: 0.62rem; text-transform: uppercase; letter-spacing: 0.05em;
    color: var(--color-text-tertiary);
  }
  .auth-chip {
    font-family: var(--font-mono); font-size: 0.68rem; padding: 1px 6px;
    border-radius: 999px; border: 1px solid var(--color-border-secondary);
    color: var(--color-text-muted); white-space: nowrap;
  }
  .auth-chip.auth-ok { color: var(--color-success); border-color: var(--color-success); }
  .auth-chip.auth-fail { color: var(--color-error); border-color: var(--color-error); }
  .auth-chip.auth-none { color: var(--color-text-tertiary); }

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
  .c-val.stale { color: var(--color-warning); }
  .c-note { font-size: 0.62rem; color: var(--color-text-tertiary); }
  .c-label { font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--color-text-tertiary); }

  .counter-btn { background: none; border: none; padding: 0; text-align: left; cursor: pointer; font: inherit; color: inherit; }
  .counter-btn:disabled { cursor: default; }

  .peer-count { font-size: 0.72rem; font-weight: 400; color: var(--color-text-muted); }

  /* Recorded-errors card. NOTE: --color-border is NOT defined in tokens.css
     (only --color-border-primary / -secondary), so use the real tokens. */
  .errors-card h2 { display: flex; align-items: center; gap: var(--spacing-sm); flex-wrap: wrap; }
  .err-list { display: flex; flex-direction: column; gap: var(--spacing-sm); }
  .err-row { border: 1px solid var(--color-border-primary); border-radius: 6px; padding: var(--spacing-sm); }
  .err-head { display: flex; align-items: baseline; gap: var(--spacing-sm); flex-wrap: wrap; }
  .err-peer { font-family: var(--font-mono); font-weight: 600; }
  .err-class {
    font-size: 0.66rem; text-transform: uppercase; letter-spacing: 0.04em;
    padding: 1px 6px; border-radius: 999px; border: 1px solid var(--color-border-secondary);
    color: var(--color-text-muted);
  }
  .err-count, .err-age { font-size: 0.72rem; color: var(--color-text-muted); }
  .err-diag { margin-top: 4px; font-size: 0.82rem; }
  .err-msg {
    margin-top: 4px; font-family: var(--font-mono); font-size: 0.7rem;
    color: var(--color-text-tertiary); overflow-wrap: anywhere;
  }
  .err-actions { margin-top: 6px; display: flex; gap: 6px; }
  /* Panel-level, so it outlives both the errors card and the unreachable swap. */
  .action-note {
    display: flex; align-items: center; gap: var(--spacing-sm);
    font-size: 0.78rem; color: var(--color-text-secondary);
    margin: 0 0 var(--spacing-lg); padding: var(--spacing-sm) var(--spacing-md);
    background: var(--color-bg-secondary); border: 1px solid var(--color-border-secondary);
    border-radius: var(--radius-sm);
  }
  .note-x {
    margin-left: auto; background: none; border: none; cursor: pointer;
    color: var(--color-text-tertiary); font-size: 0.7rem; padding: 0 2px; line-height: 1;
  }
  .note-x:hover { color: var(--color-text-primary); }
  .errors-card code { font-family: var(--font-mono); font-size: 0.78rem; }
  .peer-warn {
    display: inline-block; margin-top: 2px; font-size: 0.66rem; background: none; cursor: pointer;
    border: 1px solid var(--color-border-secondary); border-radius: 999px; padding: 0 6px;
    color: var(--color-warning);
  }

  .filters { display: flex; flex-direction: column; gap: 6px; margin-bottom: var(--spacing-md); }
  .filter-row { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
  .filter-label { font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--color-text-tertiary); width: 56px; }
  .pill {
    background: var(--color-bg-primary); border: 1px solid var(--color-border-secondary);
    border-radius: 999px; color: var(--color-text-muted); cursor: pointer;
    font-size: 0.72rem; padding: 2px 10px;
  }
  .pill:hover { color: var(--color-text-secondary); }
  .pill.active { color: var(--color-text-primary); border-color: var(--color-text-primary); }
  .no-match { color: var(--color-text-muted); font-size: 0.85rem; margin: 0; }

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

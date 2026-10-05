<script lang="ts">
  // The Delivery tab: DORA metrics for this profile, plus fleet runner health.
  //
  // Two different data shapes on purpose. The metrics come from local SQLite
  // (DORAOverview), so the 30/90-day toggle is instant and the tab works with
  // the providers unreachable; Sync is the only button that talks to a host.
  // Runner health is live from the hub and is hidden outright when there are no
  // runners — an empty table would read as "the fleet is idle".
  //
  // Every number here is a PROXY. The caveats block below is not decoration:
  // it is the design's requirement that the limitations travel with the
  // figures, so nobody reads this page as exact.
  import { getApi } from '../utils/api';
  import { notifications } from '../notifications.svelte';
  import { profileState, settingsState } from '../stores.svelte';
  import type { main } from '../../../wailsjs/go/models';

  const WINDOWS = [30, 90] as const;

  let windowDays = $state<number>(30);
  let loading = $state(false);
  let syncing = $state(false);
  let overview = $state<main.DORAOverview | null>(null);
  let runners = $state<main.RunnerMetrics | null>(null);
  let lastSync = $state<main.DORASyncResult | null>(null);

  let profile = $derived(profileState.active?.name ?? '');
  let metrics = $derived(overview?.metrics ?? null);
  let bands = $derived(overview?.metrics?.bands ?? {});
  // Sorted so the table does not reshuffle between loads.
  let perRepo = $derived(
    Object.entries(overview?.metrics?.per_repo ?? {}).sort((a, b) => a[0].localeCompare(b[0])),
  );
  let repoErrors = $derived(Object.entries(lastSync?.repo_errors ?? {}));

  async function load() {
    if (!profile) return;
    loading = true;
    const a = await getApi();
    if (!a) { loading = false; return; }
    const forProfile = profile;
    try {
      const ov = await a.DORAOverview(forProfile, windowDays);
      // A reply that lands after a profile switch belongs to the old profile;
      // rendering it would show one profile's delivery data under another's
      // name. The echoed profile is what makes that detectable.
      if (ov.profile !== profile) return;
      overview = ov;
    } catch (e: any) {
      notifications.error(`Delivery metrics: ${e?.message ?? e}`);
      overview = null;
    } finally {
      loading = false;
    }
    try {
      const rm = await a.RunnerMetrics(forProfile);
      if (rm.profile !== profile) return;
      runners = rm;
    } catch {
      // The hub being down costs the runner section, never the metrics above.
      runners = null;
    }
  }

  async function sync() {
    if (!profile || syncing) return;
    syncing = true;
    const a = await getApi();
    if (!a) { syncing = false; return; }
    try {
      const res = await a.SyncDORA(profile);
      lastSync = res;
      const failed = Object.keys(res.repo_errors ?? {}).length;
      if (failed > 0) {
        notifications.error(`Synced ${res.repos_synced} repos, ${failed} failed`);
      } else {
        notifications.success(`Synced ${res.repos_synced} repos`);
      }
      await load();
    } catch (e: any) {
      notifications.error(`Sync: ${e?.message ?? e}`);
    } finally {
      syncing = false;
    }
  }

  // Reload on profile or window change — metrics are per profile, like
  // everything else in this hub.
  $effect(() => {
    void profile;
    void windowDays;
    if (profile) void load();
  });

  function openProfiles() {
    settingsState.open('profiles');
  }

  /** Deploys per day, read the way people actually talk about cadence. */
  function formatFrequency(perDay: number): string {
    if (!perDay || perDay <= 0) return 'none';
    if (perDay >= 1) return `${perDay.toFixed(1)} / day`;
    const everyDays = Math.round(1 / perDay);
    if (everyDays <= 7) return `1 every ${everyDays} days`;
    const weeks = Math.round(everyDays / 7);
    return weeks <= 8 ? `1 every ${weeks} wk` : `1 every ${Math.round(everyDays / 30)} mo`;
  }

  /** Coarse duration: two units is all a percentile deserves. */
  function formatDuration(seconds: number): string {
    if (!seconds || seconds <= 0) return '—';
    if (seconds < 60) return `${Math.round(seconds)}s`;
    if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
    if (seconds < 86400) {
      const h = Math.floor(seconds / 3600);
      const m = Math.round((seconds % 3600) / 60);
      return m > 0 ? `${h}h ${m}m` : `${h}h`;
    }
    const d = Math.floor(seconds / 86400);
    const h = Math.round((seconds % 86400) / 3600);
    return h > 0 ? `${d}d ${h}h` : `${d}d`;
  }

  function formatRate(rate: number): string {
    if (rate === undefined || rate === null) return '—';
    return `${(rate * 100).toFixed(0)}%`;
  }

  function formatSyncedAt(iso: string): string {
    if (!iso) return 'never';
    const t = Date.parse(iso);
    if (Number.isNaN(t)) return iso;
    const mins = Math.round((Date.now() - t) / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.round(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.round(hrs / 24)}d ago`;
  }

  function backendSummary(b: Record<string, number> | undefined): string {
    const entries = Object.entries(b ?? {});
    if (entries.length === 0) return '—';
    return entries
      .sort((x, y) => y[1] - x[1])
      .map(([name, n]) => `${name} ${n}`)
      .join(', ');
  }
</script>

<div class="dora-tab">
  <div class="panel-header">
    <h1 class="page-title">delivery</h1>
    <div class="head-actions">
      <div class="window-toggle" role="group" aria-label="Metric window">
        {#each WINDOWS as w (w)}
          <button
            class="win"
            class:active={windowDays === w}
            aria-pressed={windowDays === w}
            onclick={() => (windowDays = w)}
          >{w}d</button>
        {/each}
      </div>
      <span class="synced">synced {formatSyncedAt(overview?.last_synced_at ?? '')}</span>
      <button class="btn ghost sm" onclick={sync} disabled={syncing || !profile}>
        {syncing ? 'syncing…' : 'sync'}
      </button>
      <button class="btn ghost sm" onclick={load} disabled={loading}>refresh</button>
    </div>
  </div>

  {#if !profile}
    <p class="muted-note">Select a profile to see its delivery metrics.</p>
  {:else if overview?.token_missing || overview?.token_invalid}
    <!-- Provider state is actionable, not an error: point at where it is edited. -->
    <div class="banner">
      <div>
        <strong>
          {overview?.token_invalid
            ? 'This profile’s git credential was rejected'
            : 'No git provider for this profile'}
        </strong>
        <p>
          {overview?.token_invalid
            ? 'The stored credential is expired or wrong. Re-curate it, then sync to bring this page back.'
            : 'Curate a GITHUB_TOKEN or ADO_ORG for this profile and its delivery metrics appear here.'}
        </p>
      </div>
      <button class="banner-action" onclick={openProfiles}>Open Profiles</button>
    </div>
  {:else}
    {#if overview?.sync_error}
      <p class="section-error">{overview.sync_error}</p>
    {/if}

    {#if lastSync && (lastSync.truncated ?? []).length > 0}
      <!-- A commit fetch that did not reach the watermark leaves a hole in the
           revert history, which UNDERSTATES the failure rate. Say so. -->
      <p class="warn-note">
        revert history may be incomplete for {(lastSync.truncated ?? []).join(', ')} —
        sync again to close the gap
      </p>
    {/if}

    {#each repoErrors as [repo, err] (repo)}
      <p class="section-error"><code>{repo}</code> {err}</p>
    {/each}

    {#if loading && !metrics}
      <p class="muted-note">loading…</p>
    {:else if metrics}
      <div class="cards">
        <div class="card">
          <div class="card-head">
            <span class="card-label">deployment frequency</span>
            {#if bands['deployment_frequency']}
              <span class="band {bands['deployment_frequency']}">{bands['deployment_frequency']}</span>
            {/if}
          </div>
          <div class="card-value">{formatFrequency(metrics.deploys_per_day)}</div>
          <div class="card-sub">{metrics.deploy_count} merges in {overview?.window_days}d</div>
          <div class="card-caveat">merge counted as deploy — can overcount</div>
        </div>

        <div class="card">
          <div class="card-head">
            <span class="card-label">lead time (p50)</span>
            {#if bands['lead_time']}
              <span class="band {bands['lead_time']}">{bands['lead_time']}</span>
            {/if}
          </div>
          <div class="card-value">{formatDuration(metrics.lead_time_p50_seconds)}</div>
          <div class="card-sub">p85 {formatDuration(metrics.lead_time_p85_seconds)}</div>
          <div class="card-caveat">PR opened → merged — reads optimistic</div>
        </div>

        <div class="card">
          <div class="card-head">
            <span class="card-label">change failure rate</span>
            {#if bands['change_failure_rate']}
              <span class="band {bands['change_failure_rate']}">{bands['change_failure_rate']}</span>
            {/if}
          </div>
          <div class="card-value">{formatRate(metrics.change_failure_rate)}</div>
          <div class="card-sub">{metrics.failure_count} reverts / {metrics.deploy_count} deploys</div>
          <div class="card-caveat">reverts only — a floor, not a rate</div>
        </div>

        <div class="card">
          <div class="card-head">
            <span class="card-label">time to restore (p50)</span>
            {#if bands['time_to_restore']}
              <span class="band {bands['time_to_restore']}">{bands['time_to_restore']}</span>
            {/if}
          </div>
          <div class="card-value">{formatDuration(metrics.restore_p50_seconds)}</div>
          <div class="card-sub">
            {metrics.restore_samples === 0
              ? 'no revert could be traced to its merge'
              : `${metrics.restore_samples} matched revert${metrics.restore_samples === 1 ? '' : 's'}`}
          </div>
          <div class="card-caveat">matched reverts only</div>
        </div>
      </div>

      {#if (overview?.caveats ?? []).length > 0}
        <!-- The authoritative wording from the approved design, rendered in
             full rather than hidden behind a tooltip. -->
        <div class="caveats">
          <span class="caveats-label">how to read these</span>
          <ul>
            {#each overview?.caveats ?? [] as c (c)}<li>{c}</li>{/each}
          </ul>
        </div>
      {/if}

      {#if metrics.deploy_count === 0}
        <p class="muted-note">
          No merges to a default branch in the last {overview?.window_days} days. Either nothing
          shipped, or this profile has not synced yet — try sync, or widen the window.
        </p>
      {:else if perRepo.length > 0}
        <h2 class="section-title">by repository</h2>
        <div class="table-scroll">
          <table class="repo-table">
            <thead>
              <tr>
                <th>repository</th>
                <th class="num">deploys</th>
                <th class="num">per day</th>
                <th class="num">lead p50</th>
                <th class="num">failures</th>
                <th class="num">cfr</th>
                <th class="num">restore p50</th>
              </tr>
            </thead>
            <tbody>
              {#each perRepo as [repo, m] (repo)}
                <tr>
                  <td><code>{repo}</code></td>
                  <td class="num">{m.deploy_count}</td>
                  <td class="num">{m.deploys_per_day.toFixed(2)}</td>
                  <td class="num">{formatDuration(m.lead_time_p50_seconds)}</td>
                  <td class="num">{m.failure_count}</td>
                  <td class="num">{m.deploy_count > 0 ? formatRate(m.change_failure_rate) : '—'}</td>
                  <td class="num">{formatDuration(m.restore_p50_seconds)}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {/if}
    {/if}

    <!-- Runners render ONLY when some are registered: absent, not
         present-and-empty. -->
    {#if runners?.available}
      <h2 class="section-title">runners</h2>
      {#if runners.unreachable}
        <p class="warn-note">task history unavailable: {runners.unreachable}</p>
      {/if}
      <div class="table-scroll">
        <table class="repo-table">
          <thead>
            <tr>
              <th>runner</th>
              <th>host</th>
              <th class="num">queue</th>
              <th class="num">in flight</th>
              <th class="num">done</th>
              <th class="num">failed</th>
              <th class="num">stranded</th>
              <th class="num">mean</th>
              <th>backends</th>
            </tr>
          </thead>
          <tbody>
            {#each runners.runners ?? [] as r (r.name)}
              <tr>
                <td>
                  <span class="dot" class:up={r.online} title={r.online ? 'online' : 'offline'}></span>
                  <code>{r.name}</code>
                  {#if r.version}<span class="ver">{r.version}</span>{/if}
                </td>
                <td class="dim">{r.host || '—'}</td>
                <td class="num">{r.queue_depth}</td>
                <td class="num">{r.in_flight}{r.max_concurrent > 0 ? ` / ${r.max_concurrent}` : ''}</td>
                <td class="num">{r.completed}</td>
                <td class="num">{r.failed}</td>
                <!-- Its own column: stranded tasks are a platform gap (no
                     liveness reconcile), not a verdict on the runner. -->
                <td class="num" class:bad={r.stranded > 0}>{r.stranded}</td>
                <td class="num">{formatDuration(r.mean_duration_seconds)}</td>
                <td class="dim">{backendSummary(r.backends)}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
      <p class="muted-note small">
        stranded = left running by a runner that died or went quiet; excluded from the mean so it
        cannot inflate it
      </p>
    {:else if runners?.unreachable}
      <p class="muted-note">runner health unavailable: {runners.unreachable}</p>
    {/if}
  {/if}
</div>

<style>
  .dora-tab { padding: var(--panel-padding); display: flex; flex-direction: column; gap: var(--spacing-sm); }
  .panel-header { display: flex; align-items: center; justify-content: space-between; gap: var(--spacing-md); flex-wrap: wrap; }
  .head-actions { display: flex; gap: 8px; align-items: center; }
  .synced { font-size: 10px; color: var(--color-text-tertiary); font-family: var(--font-mono); }

  .window-toggle { display: flex; border: 1px solid var(--color-border-secondary); border-radius: var(--radius-sm); overflow: hidden; }
  .win {
    background: none; border: none; padding: 3px 9px; font: inherit; font-size: 11px;
    color: var(--color-text-tertiary); cursor: pointer;
  }
  .win.active { background: var(--color-bg-tertiary); color: var(--color-text-primary); }

  .banner {
    display: flex; align-items: center; justify-content: space-between; gap: var(--spacing-lg);
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-warning, var(--color-border-primary));
    border-radius: var(--radius-md);
    padding: var(--spacing-md) var(--spacing-lg);
  }
  .banner p { margin: 3px 0 0; font-size: 0.82rem; color: var(--color-text-muted); }
  .banner-action {
    background: transparent; color: var(--color-accent);
    border: 1px solid var(--color-accent); border-radius: var(--radius-sm);
    padding: 5px 12px; font-size: 0.8rem; cursor: pointer; white-space: nowrap;
  }

  .section-error { color: var(--color-error); font-size: 12px; margin: 0; }
  .warn-note { color: var(--color-warning, #d29922); font-size: 12px; margin: 0; }
  .muted-note { color: var(--color-text-tertiary); font-size: 12px; margin: 0; }
  .muted-note.small { font-size: 10px; }
  .section-title { font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--color-text-tertiary); margin: var(--spacing-sm) 0 0; font-weight: 600; }

  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: var(--spacing-sm); }
  .card {
    border: 1px solid var(--color-border-primary); border-radius: var(--radius-md);
    background: var(--color-bg-secondary); padding: 10px 12px;
    display: flex; flex-direction: column; gap: 2px;
  }
  .card-head { display: flex; align-items: center; justify-content: space-between; gap: 6px; }
  .card-label { font-size: 10px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--color-text-tertiary); }
  .card-value { font-size: 22px; font-weight: 600; color: var(--color-text-primary); font-variant-numeric: tabular-nums; }
  .card-sub { font-size: 11px; color: var(--color-text-secondary); }
  .card-caveat { font-size: 10px; color: var(--color-text-tertiary); font-style: italic; margin-top: 2px; }

  .band {
    font-size: 9px; text-transform: uppercase; letter-spacing: 0.05em;
    border: 1px solid var(--color-border-primary); border-radius: 3px; padding: 0 5px;
    color: var(--color-text-tertiary);
  }
  .band.elite { border-color: #3fb950; color: #3fb950; }
  .band.high { border-color: #56a8f5; color: #56a8f5; }
  .band.medium { border-color: #d29922; color: #d29922; }
  .band.low { border-color: var(--color-error); color: var(--color-error); }

  .caveats { border-left: 2px solid var(--color-border-secondary); padding: 2px 0 2px 10px; }
  .caveats-label { font-size: 9px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--color-text-tertiary); }
  .caveats ul { margin: 3px 0 0; padding-left: 14px; }
  .caveats li { font-size: 11px; color: var(--color-text-tertiary); line-height: 1.5; }

  /* Wide tables scroll inside their own box; the panel never scrolls sideways. */
  .table-scroll { overflow-x: auto; }
  .repo-table { border-collapse: collapse; width: 100%; font-size: 11px; }
  .repo-table th, .repo-table td { text-align: left; padding: 4px 8px; border-top: 1px solid var(--color-border-primary); white-space: nowrap; }
  .repo-table th { font-size: 9px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--color-text-tertiary); font-weight: 600; border-top: none; }
  .repo-table td.num, .repo-table th.num { text-align: right; font-variant-numeric: tabular-nums; }
  .repo-table td.dim { color: var(--color-text-tertiary); }
  .repo-table td.bad { color: var(--color-error); font-weight: 600; }
  .repo-table code { font-family: var(--font-mono); font-size: 10px; }
  .ver { font-size: 9px; color: var(--color-text-tertiary); margin-left: 4px; }

  .dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: var(--color-error); margin-right: 5px; vertical-align: middle; }
  .dot.up { background: #3fb950; }
</style>

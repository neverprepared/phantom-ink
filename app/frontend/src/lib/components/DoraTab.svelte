<script lang="ts">
  // The Delivery tab: DORA metrics for this profile, plus fleet runner health.
  //
  // Metrics are mostly local SQLite (DORAOverview), though not purely: it also
  // makes a broker round-trip for the token banner, which is why the 30/90-day
  // toggle was never actually free. They are cached per profile AND per
  // window, so a window you have already seen paints immediately. Sync is the
  // only button that writes.
  //
  // Runner health used to be a second section here. It is gone: a runner is
  // fleet infrastructure, not this profile's delivery work, and the table
  // silently mixed fleet-wide queue depth with profile-scoped task counts in
  // one row. It lives with the rest of the fleet in Settings -> runners.
  //
  // Every number here is a PROXY. The caveats block below is not decoration:
  // it is the design's requirement that the limitations travel with the
  // figures, so nobody reads this page as exact.
  import { getApi } from '../utils/api';
  import { notifications } from '../notifications.svelte';
  import { profileState, settingsState } from '../stores.svelte';
  import { doraState } from '../stores/dora.svelte';
  import { timeAgo } from '../utils/format';
  import type { main } from '../../../wailsjs/go/models';

  const WINDOWS = [30, 90] as const;

  // Sync result is component-local: it describes one button press, not the
  // profile. Everything that describes the profile lives in doraState so it
  // survives this component being destroyed on every tab and profile switch.
  let syncing = $state(false);
  let lastSync = $state<main.DORASyncResult | null>(null);

  let profile = $derived(profileState.active?.name ?? '');
  let windowDays = $derived(doraState.windowDays);
  let metrics = $derived(doraState.metrics);
  let bands = $derived(doraState.bands);
  let perRepo = $derived(doraState.perRepo);
  let loading = $derived(doraState.loading);
  let authors = $derived(doraState.authors);
  let scope = $derived(doraState.scope);
  let repoErrors = $derived(Object.entries(lastSync?.repo_errors ?? {}));

  async function load(force = false) {
    await doraState.refresh(profile, force);
  }

  // Live age for the freshness label; one tick a minute while mounted.
  let now = $state(Date.now());
  $effect(() => {
    const t = setInterval(() => (now = Date.now()), 60_000);
    return () => clearInterval(t);
  });
  const fetchedAgo = $derived(
    doraState.fetchedAt ? timeAgo(Date.parse(doraState.fetchedAt), now) : ''
  );

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
      // A sync rewrites the deploy/revert rows, so EVERY window's cached
      // metrics for this profile are now wrong -- not just the visible one.
      doraState.invalidate(profile);
      await load();
    } catch (e: any) {
      notifications.error(`Sync: ${e?.message ?? e}`);
    } finally {
      syncing = false;
    }
  }

  // Re-read on mount and on every profile change. Cached metrics stay on
  // screen while this revalidates behind them; runner health is always live.
  $effect(() => {
    if (profile) void load();
  });

  /** Window toggle. A window already seen for this profile paints instantly. */
  function pickWindow(days: number) {
    void doraState.setWindow(profile, days);
  }

  /**
   * Scope toggle. Unlike the window this fetches nothing: the per-author
   * breakdown rides along in the payload already on screen, so switching is
   * instant and costs no round-trip.
   */
  function pickScope(e: Event) {
    doraState.setScope((e.currentTarget as HTMLSelectElement).value);
  }

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

</script>

<div class="dora-tab">
  <div class="panel-header">
    <h1 class="page-title">delivery</h1>
    <div class="head-actions">
      <!-- Only offered once the data names somebody. With one author it is
           still worth showing: it is how you learn the numbers are all yours. -->
      {#if authors.length > 0}
        <label class="scope-pick">
          <span class="vh">Scope metrics to</span>
          <select value={scope} onchange={pickScope}>
            <option value="">everyone</option>
            {#each authors as a (a)}<option value={a}>{a}</option>{/each}
          </select>
        </label>
      {/if}
      <div class="window-toggle" role="group" aria-label="Metric window">
        {#each WINDOWS as w (w)}
          <button
            class="win"
            class:active={windowDays === w}
            aria-pressed={windowDays === w}
            onclick={() => pickWindow(w)}
          >{w}d</button>
        {/each}
      </div>
      <span class="synced">synced {formatSyncedAt(doraState.lastSyncedAt)}</span>
      <button class="btn ghost sm" onclick={sync} disabled={syncing || !profile}>
        {syncing ? 'syncing…' : 'sync'}
      </button>
      <!-- Disabled only on a cold load: during a background check the metrics
           are on screen, so asking again is reasonable. -->
      <button
        class="btn ghost sm"
        onclick={() => load(true)}
        disabled={loading}
      >{loading || doraState.backgroundBusy ? 'refreshing…' : 'refresh'}</button>
    </div>
  </div>

  <!-- Same freshness affordance as the code and jira tabs. Metrics kept across
       a tab, profile or window switch must not read as live. Runner health is
       excluded on purpose: it is never cached, so it is always current. -->
  {#if profile && (fetchedAgo || doraState.backgroundBusy)}
    <p class="freshness" aria-live="polite">
      {#if doraState.backgroundBusy}
        <span class="dot" aria-hidden="true"></span>
        <span>Checking for updates…</span>
        {#if fetchedAgo}<span class="sep">·</span><span>showing metrics from {fetchedAgo}</span>{/if}
      {:else}
        <span>Updated {fetchedAgo}</span>
      {/if}
    </p>
  {/if}

  {#if !profile}
    <p class="muted-note">Select a profile to see its delivery metrics.</p>
  {:else if doraState.tokenMissing || doraState.tokenInvalid}
    <!-- Provider state is actionable, not an error: point at where it is edited. -->
    <div class="banner">
      <div>
        <strong>
          {doraState.tokenInvalid
            ? 'This profile’s git credential was rejected'
            : 'No git provider for this profile'}
        </strong>
        <p>
          {doraState.tokenInvalid
            ? 'The stored credential is expired or wrong. Re-curate it, then sync to bring this page back.'
            : 'Curate a GITHUB_TOKEN or ADO_ORG for this profile and its delivery metrics appear here.'}
        </p>
      </div>
      <button class="banner-action" onclick={openProfiles}>Open Profiles</button>
    </div>
  {:else}
    {#if doraState.syncError}
      <p class="section-error">{doraState.syncError}</p>
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
      <!-- Every figure below is filtered. Saying so next to them beats hoping
           the reader noticed the selector in the header. -->
      {#if scope}
        <p class="scope-note" aria-live="polite">
          Showing only <strong>{scope}</strong>’s merges.
          <button class="link-btn" onclick={() => doraState.setScope('')}>show everyone</button>
        </p>
      {/if}

      <div class="cards">
        <div class="card">
          <div class="card-head">
            <span class="card-label">deployment frequency</span>
            {#if bands['deployment_frequency']}
              <span class="band {bands['deployment_frequency']}">{bands['deployment_frequency']}</span>
            {/if}
          </div>
          <div class="card-value">{formatFrequency(metrics.deploys_per_day)}</div>
          <div class="card-sub">{metrics.deploy_count} merges in {doraState.windowOf}d</div>
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

      {#if doraState.activeCaveats.length > 0}
        <!-- The authoritative wording from the approved design, rendered in
             full rather than hidden behind a tooltip. -->
        <div class="caveats">
          <span class="caveats-label">how to read these</span>
          <ul>
            {#each doraState.activeCaveats as c (c)}<li>{c}</li>{/each}
          </ul>
        </div>
      {/if}

      {#if metrics.deploy_count === 0}
        <p class="muted-note">
          {#if scope}
            {scope} has no merges to a default branch in the last {doraState.windowOf} days.
            Widen the window, or show everyone.
          {:else}
            No merges to a default branch in the last {doraState.windowOf} days. Either nothing
            shipped, or this profile has not synced yet — try sync, or widen the window.
          {/if}
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

  {/if}
</div>

<style>
  .dora-tab { padding: var(--panel-padding); display: flex; flex-direction: column; gap: var(--spacing-sm); }
  .panel-header { gap: var(--spacing-md); flex-wrap: wrap; }
  .head-actions { display: flex; gap: 8px; align-items: center; }
  /* Freshness line — quieter than any content, matching the other tabs'. */
  .freshness {
    display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
    margin: 0; font-size: 0.75rem; color: var(--color-text-muted);
  }
  .freshness .sep { opacity: 0.5; }
  .freshness .dot {
    width: 6px; height: 6px; border-radius: 50%;
    background: var(--color-accent, var(--color-text-secondary));
    animation: fresh-pulse 1.4s ease-in-out infinite;
  }
  @keyframes fresh-pulse {
    0%, 100% { opacity: 0.25; }
    50% { opacity: 1; }
  }
  @media (prefers-reduced-motion: reduce) {
    .freshness .dot { animation: none; opacity: 0.8; }
  }
  .synced { font-size: 10px; color: var(--color-text-tertiary); font-family: var(--font-mono); }

  .scope-pick select {
    font: inherit; font-size: 11px;
    color: var(--color-text-primary);
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-secondary);
    border-radius: var(--radius-sm);
    padding: 3px 6px;
    max-width: 160px;
    cursor: pointer;
  }
  /* Visually hidden, still read aloud — the select has no visible label. */
  .vh {
    position: absolute; width: 1px; height: 1px;
    padding: 0; margin: -1px; overflow: hidden;
    clip-path: inset(50%); white-space: nowrap;
  }
  .scope-note {
    margin: 0 0 var(--spacing-md); font-size: 12px;
    color: var(--color-text-secondary);
  }
  .scope-note strong { color: var(--color-text-primary); }
  .link-btn {
    background: none; border: none; padding: 0; margin-left: 6px;
    font: inherit; font-size: 12px;
    color: var(--color-accent); cursor: pointer; text-decoration: underline;
  }

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
  .repo-table code { font-family: var(--font-mono); font-size: 10px; }

</style>

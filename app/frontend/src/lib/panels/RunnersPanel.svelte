<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { timeAgo } from '../utils/format';
  import { getApi } from '../utils/api';
  import { notifications } from '../notifications.svelte';
  import PairRunnerModal from '../components/PairRunnerModal.svelte';
  import Spinner from '../components/Spinner.svelte';
  import EmptyState from '../components/EmptyState.svelte';
  import { runnerQueueHistory } from '../metricsHistory.svelte';

  interface Runner {
    name: string;
    capabilities: Record<string, boolean>;
    tags: string[];
    version: string;
    registered_at: number;  // epoch ms
    last_seen: number;      // epoch ms
    queue_depth: number;
    in_flight: number;
    max_concurrent: number;
    host: string;
  }

  interface Pool { name: string; match_tags: string[]; policy: string; }

  let runners = $state<Runner[]>([]);
  let pools = $state<Pool[]>([]);
  let loaded = $state(false);
  let loadError: string | null = $state(null);
  let now = $state(Date.now());
  let showingPair = $state(false);
  let confirmRemove: Runner | null = $state(null);

  // pool create form
  let newPoolName = $state('');
  let newPoolTags = $state('');
  let newPoolPolicy = $state<'strict' | 'spill'>('strict');

  let pollHandle: number | undefined;
  let tickerHandle: number | undefined;

  const ONLINE_WINDOW_MS = 60_000;

  onMount(() => {
    void refresh();
    pollHandle = window.setInterval(refresh, 5_000);
    tickerHandle = window.setInterval(() => { now = Date.now(); }, 1_000);
  });

  onDestroy(() => {
    if (pollHandle !== undefined) window.clearInterval(pollHandle);
    if (tickerHandle !== undefined) window.clearInterval(tickerHandle);
  });

  async function refresh() {
    const a = await getApi();
    if (!a) { loaded = true; loadError = 'API bindings unavailable'; return; }
    try {
      const list = await a.ListRunners();
      runners = (list ?? []) as Runner[];
      // Record a queue-depth sample per runner so the inline sparkline and
      // dashboard peak card have history without any backend work.
      const ts = Date.now();
      const activeNames = new Set<string>();
      for (const r of runners) {
        activeNames.add(r.name);
        runnerQueueHistory.update(r.name, {
          ts,
          depth: r.queue_depth || 0,
          inflight: r.in_flight || 0,
        });
      }
      runnerQueueHistory.pruneKeys(activeNames);
      pools = ((await a.ListPools()) ?? []) as Pool[];
      loadError = null;
    } catch (err: any) {
      loadError = `${err?.message ?? err}`;
    } finally {
      loaded = true;
    }
  }

  // Build a polyline path for a 60x20 sparkline normalised by the max value
  // observed in the sample window. Returns '' when there isn't enough data.
  function sparkPath(name: string): string {
    const samples = runnerQueueHistory.value[name] ?? [];
    if (samples.length < 2) return '';
    const max = samples.reduce((acc, s) => Math.max(acc, s.depth), 0);
    if (max === 0) return '';
    const w = 60, h = 18;
    const stepX = w / (samples.length - 1);
    return samples
      .map((s, i) => {
        const x = (i * stepX).toFixed(1);
        const y = (h - (s.depth / max) * h).toFixed(1);
        return `${i === 0 ? 'M' : 'L'}${x},${y}`;
      })
      .join(' ');
  }

  function peakLabel(name: string): string {
    const peak = runnerQueueHistory.peak(name, 60 * 60_000);
    return peak > 0 ? `peak ${peak}` : '';
  }

  async function removeRunner(r: Runner) {
    const a = await getApi();
    if (!a) return;
    try {
      await a.DeleteRunner(r.name);
      notifications.success(`removed ${r.name}`);
      confirmRemove = null;
      await refresh();
    } catch (err: any) {
      notifications.error(`delete failed: ${err?.message ?? err}`);
    }
  }

  function isOnline(r: Runner): boolean {
    return now - r.last_seen < ONLINE_WINDOW_MS;
  }

  function relativeTime(epochMs: number): string {
    return timeAgo(epochMs, now);
  }

  function capabilities(r: Runner): string[] {
    if (r.host === 'local-process') return ['application'];
    return Object.entries(r.capabilities ?? {})
      .filter(([_, v]) => v)
      .map(([k]) => k);
  }

  function headroom(r: Runner): number {
    const max = r.max_concurrent || 4;
    return Math.max(0, max - (r.in_flight || 0));
  }

  function capacityPct(r: Runner): number {
    const max = r.max_concurrent || 4;
    return Math.round(((r.in_flight || 0) / max) * 100);
  }

  // A runner is in the pool when its tags include every match tag (superset).
  function poolMembers(p: Pool): Runner[] {
    return runners.filter(
      (r) => r.host !== 'local-process' && p.match_tags.every((t) => (r.tags ?? []).includes(t)),
    );
  }

  async function createPool() {
    const name = newPoolName.trim();
    if (!name) return;
    const tags = newPoolTags.split(',').map((t) => t.trim()).filter(Boolean);
    const a = await getApi();
    if (!a) return;
    try {
      await a.UpsertPool(name, tags, newPoolPolicy);
      notifications.success(`pool ${name} saved`);
      newPoolName = ''; newPoolTags = ''; newPoolPolicy = 'strict';
      await refresh();
    } catch (err: any) {
      notifications.error(`pool save failed: ${err?.message ?? err}`);
    }
  }

  async function deletePool(name: string) {
    const a = await getApi();
    if (!a) return;
    try {
      await a.DeletePool(name);
      notifications.success(`pool ${name} deleted`);
      await refresh();
    } catch (err: any) {
      notifications.error(`delete failed: ${err?.message ?? err}`);
    }
  }
</script>

<div class="panel">
  <header class="panel-header">
    <h1 class="page-title">runners</h1>
    <div class="header-actions">
      {#if !loaded}<Spinner />{/if}
      <button class="btn primary" onclick={() => (showingPair = true)}>+ pair a runner</button>
    </div>
  </header>

  {#if !loaded}
    <div class="empty">loading…</div>
  {:else if loadError}
    <EmptyState title="Failed to load runners" message={loadError} />
  {:else if runners.length === 0}
    <EmptyState title="No runners registered yet" message="Click + pair a runner above, then run Brainbox Runner.app on the target Mac and paste the token." />
  {:else}
    <table class="runner-table">
      <thead>
        <tr>
          <th></th>
          <th>name</th>
          <th>backends</th>
          <th>tags</th>
          <th>capacity</th>
          <th>version</th>
          <th>last seen</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        {#each runners as r (r.name)}
          {@const online = isOnline(r)}
          <tr class:online>
            <td><span class="dot" class:on={online}></span></td>
            <td class="name">{r.name}</td>
            <td class="caps">
              {#each capabilities(r) as cap (cap)}
                <span class="cap-pill">{cap}</span>
              {/each}
              {#if capabilities(r).length === 0}
                <span class="muted">—</span>
              {/if}
            </td>
            <td class="tags">
              {#if r.host}
                <span class="tag-pill host-pill">{r.host}</span>
              {/if}
              {#each r.tags ?? [] as tag (tag)}
                <span class="tag-pill">{tag}</span>
              {/each}
              {#if !r.host && (r.tags ?? []).length === 0}
                <span class="muted">—</span>
              {/if}
            </td>
            <td class="capacity-cell">
              <div class="cap-bar-wrap" title="{r.in_flight || 0}/{r.max_concurrent || 4} in flight, {headroom(r)} free">
                <div class="cap-bar" style="width: {capacityPct(r)}%" class:saturated={headroom(r) === 0}></div>
              </div>
              <span class="cap-label">{r.in_flight || 0}/{r.max_concurrent || 4}</span>
              {#if (r.queue_depth || 0) > 0}
                <span class="queue-badge">+{r.queue_depth} queued</span>
              {/if}
              {#if sparkPath(r.name)}
                <span class="spark" title="{peakLabel(r.name)} (1h)">
                  <svg width="60" height="18" viewBox="0 0 60 18" aria-hidden="true">
                    <path d={sparkPath(r.name)} fill="none" stroke="currentColor" stroke-width="1.2" />
                  </svg>
                </span>
              {/if}
            </td>
            <td class="muted">{r.version || '—'}</td>
            <td>{relativeTime(r.last_seen)}</td>
            <td class="remove-cell">
              {#if confirmRemove?.name === r.name}
                <span class="confirm-inline">
                  sure?
                  <button class="link danger" onclick={() => removeRunner(r)}>yes</button>
                  <button class="link" onclick={() => (confirmRemove = null)}>no</button>
                </span>
              {:else}
                <button class="link danger" onclick={() => (confirmRemove = r)}>remove</button>
              {/if}
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}

  <section class="pools">
    <h2 class="pools-title">pools <span class="dim">— machine-class routing (tag superset)</span></h2>
    {#if pools.length === 0}
      <p class="dim pools-empty">No pools. Create one to route a work class to matching runners; a runner joins by carrying all the match tags.</p>
    {:else}
      <div class="pool-list">
        {#each pools as p (p.name)}
          {@const members = poolMembers(p)}
          <div class="pool-row">
            <span class="pool-name">{p.name}</span>
            <span class="pool-policy {p.policy}" title={p.policy === 'strict' ? 'never routes outside the pool' : 'falls back to the global fleet when empty'}>{p.policy}</span>
            <span class="pool-tags">
              {#each p.match_tags as t (t)}<span class="tag-pill">{t}</span>{/each}
            </span>
            <span class="pool-members" class:none={members.length === 0}>
              {members.length} runner{members.length === 1 ? '' : 's'}{#if members.length}: {members.map((r) => r.name).join(', ')}{/if}
            </span>
            <button class="link danger pool-del" onclick={() => deletePool(p.name)}>delete</button>
          </div>
        {/each}
      </div>
    {/if}
    <div class="pool-create">
      <input class="pool-input" placeholder="pool name" bind:value={newPoolName} />
      <input class="pool-input wide" placeholder="match tags (comma-separated)" bind:value={newPoolTags} />
      <select class="pool-input" bind:value={newPoolPolicy}>
        <option value="strict">strict</option>
        <option value="spill">spill</option>
      </select>
      <button class="btn" onclick={createPool} disabled={!newPoolName.trim()}>+ pool</button>
    </div>
  </section>

  {#if showingPair}
    <PairRunnerModal onClose={async () => { showingPair = false; await refresh(); }} />
  {/if}
</div>

<style>
  .panel {
    padding: var(--panel-padding);
    max-width: 1100px;
  }
  .header-actions {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .empty {
    background: var(--color-bg-secondary);
    border: 1px dashed var(--color-border-secondary);
    border-radius: var(--radius-xl);
    padding: 28px 20px;
    color: var(--color-text-tertiary);
    text-align: center;
  }
  .empty.error {
    color: var(--color-danger, #e54);
    border-color: var(--color-danger, #e54);
  }
  .empty .hint {
    font-size: 12px;
    margin-top: 8px;
    color: var(--color-text-tertiary);
  }
  .empty code {
    background: var(--color-surface-subtle);
    padding: 1px 4px;
    border-radius: var(--radius-sm);
  }

  .runner-table {
    width: 100%;
    border-collapse: separate;
    border-spacing: 0;
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-xl);
    overflow: hidden;
  }
  .runner-table th,
  .runner-table td {
    text-align: left;
    padding: 10px 12px;
    font-size: 12px;
    border-bottom: 1px solid var(--color-border-primary);
  }
  .runner-table th {
    background: var(--color-surface-subtle);
    color: var(--color-text-tertiary);
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    font-size: 10px;
  }
  .runner-table tbody tr:last-child td {
    border-bottom: none;
  }
  .runner-table tbody tr:hover {
    background: var(--color-surface-hover);
  }

  .dot {
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--color-text-tertiary);
  }
  .dot.on {
    background: var(--color-success);
    box-shadow: var(--shadow-status-active);
  }
  .name {
    font-weight: 600;
    color: var(--color-text-primary);
    font-family: var(--font-mono);
    font-size: 12px;
  }
  .cap-pill,
  .tag-pill {
    display: inline-block;
    padding: 1px 7px;
    border-radius: var(--radius-sm);
    font-size: 10px;
    font-weight: 600;
    margin-right: 4px;
    background: var(--color-surface-subtle);
    color: var(--color-text-secondary);
  }
  .cap-pill {
    background: var(--color-accent-soft);
    color: var(--color-accent);
    text-transform: lowercase;
  }
  .host-pill {
    background: rgba(16, 185, 129, 0.1);
    color: var(--color-success, #10b981);
    border: 1px solid rgba(16, 185, 129, 0.25);
    font-family: var(--font-mono);
  }
  .capacity-cell {
    display: flex;
    align-items: center;
    gap: 6px;
    white-space: nowrap;
  }
  .cap-bar-wrap {
    width: 48px;
    height: 6px;
    background: var(--color-surface-subtle);
    border-radius: 3px;
    overflow: hidden;
    flex-shrink: 0;
  }
  .cap-bar {
    height: 100%;
    background: var(--color-info);
    border-radius: 3px;
    transition: width 300ms ease;
    min-width: 2px;
  }
  .cap-bar.saturated {
    background: var(--color-error);
  }
  .cap-label {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-secondary);
  }
  .spark {
    display: inline-flex;
    color: var(--color-info);
    opacity: 0.65;
    flex-shrink: 0;
  }
  .queue-badge {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-warning, #f59e0b);
    background: rgba(245, 158, 11, 0.1);
    border: 1px solid rgba(245, 158, 11, 0.2);
    border-radius: var(--radius-sm);
    padding: 0 5px;
  }

  .muted {
    color: var(--color-text-tertiary);
  }
  .link {
    background: transparent;
    border: none;
    color: var(--color-info);
    font-size: 12px;
    cursor: pointer;
    padding: 2px 4px;
  }
  .link:hover { text-decoration: underline; }
  .link.danger { color: var(--color-danger, #e54); }
  .remove-cell { white-space: nowrap; }
  .confirm-inline {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    font-size: 12px;
    color: var(--color-text-tertiary);
  }
  .pools { margin-top: 28px; }
  .pools-title { font-size: 14px; font-weight: 600; margin: 0 0 10px; }
  .dim { color: var(--color-text-tertiary); font-weight: 400; }
  .pools-empty { font-size: 13px; margin: 0 0 12px; max-width: 640px; }
  .pool-list { display: flex; flex-direction: column; gap: 4px; margin-bottom: 12px; }
  .pool-row {
    display: flex; align-items: center; gap: 12px;
    padding: 8px 10px; border: 1px solid var(--color-border-primary);
    border-radius: 6px; font-size: 13px;
  }
  .pool-name { font-family: var(--font-mono); font-weight: 600; min-width: 90px; }
  .pool-policy {
    font-family: var(--font-mono); font-size: 10px; text-transform: uppercase;
    padding: 1px 6px; border-radius: 3px; border: 1px solid var(--color-border-primary);
  }
  .pool-policy.strict { color: var(--color-warning, #f59e0b); border-color: var(--color-warning, #f59e0b); }
  .pool-policy.spill { color: var(--color-text-tertiary); }
  .pool-tags { display: flex; gap: 4px; flex-wrap: wrap; }
  .pool-members { font-size: 12px; color: var(--color-text-secondary); margin-left: auto; }
  .pool-members.none { color: var(--color-text-tertiary); font-style: italic; }
  .pool-del { margin-left: 8px; }
  .pool-create { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .pool-input {
    font-size: 13px; padding: 5px 8px; border-radius: 6px;
    border: 1px solid var(--color-border-primary); background: transparent;
    color: var(--color-text-primary); font-family: inherit;
  }
  .pool-input.wide { flex: 1; min-width: 200px; }
</style>

<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { getApi } from '../utils/api';
  import { notifications } from '../notifications.svelte';
  import PairRunnerModal from '../components/PairRunnerModal.svelte';

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

  let runners = $state<Runner[]>([]);
  let loaded = $state(false);
  let loadError: string | null = $state(null);
  let now = $state(Date.now());
  let showingPair = $state(false);
  let confirmRemove: Runner | null = $state(null);

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
      loadError = null;
    } catch (err: any) {
      loadError = `${err?.message ?? err}`;
    } finally {
      loaded = true;
    }
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
    const diff = Math.max(0, now - epochMs);
    if (diff < 5_000) return 'just now';
    if (diff < 60_000) return `${Math.floor(diff / 1000)}s ago`;
    if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
    if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
    return `${Math.floor(diff / 86_400_000)}d ago`;
  }

  function capabilities(r: Runner): string[] {
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
</script>

<div class="panel">
  <header>
    <h1><span class="accent">runners</span></h1>
    <div class="header-actions">
      <button class="btn" onclick={refresh} title="Refresh">
        <svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/></svg>
        refresh
      </button>
      <button class="btn primary" onclick={() => (showingPair = true)}>+ pair a runner</button>
    </div>
  </header>

  {#if !loaded}
    <div class="empty">loading…</div>
  {:else if loadError}
    <div class="empty error">{loadError}</div>
  {:else if runners.length === 0}
    <div class="empty">
      <p>no runners registered yet</p>
      <p class="hint">click <strong>+ pair a runner</strong> above, then run <code>Brainbox Runner.app</code> on the target mac and paste the token</p>
    </div>
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

  {#if showingPair}
    <PairRunnerModal onClose={async () => { showingPair = false; await refresh(); }} />
  {/if}
</div>

<style>
  .panel {
    padding: 24px;
    max-width: 1100px;
  }
  header {
    display: flex;
    align-items: baseline;
    gap: 12px;
    margin-bottom: 16px;
  }
  h1 {
    font-size: 22px;
    font-weight: 600;
    margin: 0;
  }
  h1 .accent {
    color: var(--color-accent);
  }
  .header-actions {
    margin-left: auto;
    display: flex;
    gap: 8px;
  }
  .btn {
    background: var(--color-surface-hover);
    border: 1px solid var(--color-border-secondary);
    border-radius: var(--radius-md);
    color: var(--color-text-secondary);
    padding: 6px 12px;
    font-size: 12px;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .btn:hover { background: var(--color-surface-active); }
  .btn.primary {
    background: rgba(59, 130, 246, 0.1);
    border-color: rgba(59, 130, 246, 0.3);
    color: var(--color-info);
  }
  .btn.primary:hover {
    background: rgba(59, 130, 246, 0.2);
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
</style>

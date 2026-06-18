<script lang="ts">
  import { onMount } from 'svelte';
  import {
    connectionState,
    profileState,
    currentPanel,
    attentionStore,
    outboxStore,
    dashboardDataStore,
  } from '../stores.svelte';
  import { brainboxEvents } from '../events.svelte';
  import { getApi } from '../utils/api';

  let connected     = $derived(connectionState.connected);
  let activeProfile = $derived(profileState.active);
  let eventCount    = $derived(brainboxEvents.log.length);

  // Live operator readout chips. attentionStore is bootstrapped by AppShell;
  // outboxStore + the local runner-count poll are kicked off here.
  let attention = $derived(attentionStore.count);
  let outboxPending = $derived(outboxStore.pending);
  let outboxStuck = $derived(outboxStore.stuck);
  let outboxError = $derived(outboxStore.lastError);

  // Sessions / runner counts piggyback on dashboardDataStore when it's
  // populated (Dashboard panel sets it); otherwise we run a lightweight
  // poll so the StatusBar stays informative on cold start.
  let dashData = $derived(dashboardDataStore.value);
  let activeSessions = $state(0);
  let offlineRunners = $state(0);
  let totalRunners = $state(0);

  onMount(() => {
    outboxStore.start();
    let cancelled = false;

    async function pollOps() {
      if (cancelled) return;
      const a = await getApi();
      if (!a) return;
      try {
        const [sessions, runners] = await Promise.all([
          a.GetSessions(profileState.active?.name ?? '').catch(() => []),
          a.ListRunners().catch(() => []),
        ]);
        if (dashData == null) {
          activeSessions = ((sessions ?? []) as any[]).filter(s => s.active).length;
        }
        const now = Date.now();
        const rs = (runners ?? []) as any[];
        totalRunners = rs.length;
        offlineRunners = rs.filter(r => now - (r.last_seen ?? 0) >= 90_000).length;
      } catch {}
    }
    void pollOps();
    const handle = window.setInterval(pollOps, 15_000);
    return () => { cancelled = true; window.clearInterval(handle); };
  });

  // Prefer dashboardDataStore numbers (already filtered by profile) when present.
  let sessionsCount = $derived(dashData?.activeSessions ?? activeSessions);
  let runnersOffline = $derived(dashData?.offlineRunners ?? offlineRunners);

  function focus(panel: string) {
    currentPanel.value = panel;
  }
</script>

<div class="statusbar">
  <span class="chip" title={connected ? 'Connected to brainbox' : 'Disconnected — events may be stale'}>
    <span class="conn-dot" class:live={connected}></span>
    {connected ? 'connected' : 'offline'}
  </span>

  <span class="chip">
    <span class="dot"></span>
    {activeProfile ? activeProfile.name : 'all profiles'}
  </span>

  {#if sessionsCount > 0}
    <button class="chip clickable" onclick={() => focus('sessions')} title="Active sessions — click to open">
      <span class="dot ok"></span>
      {sessionsCount} session{sessionsCount === 1 ? '' : 's'}
    </button>
  {/if}

  {#if attention > 0}
    <button class="chip clickable warn" onclick={() => focus('stream')} title="Items need attention — click to open Stream">
      <span class="dot warn-dot"></span>
      {attention} attention
    </button>
  {/if}

  {#if runnersOffline > 0}
    <button class="chip clickable warn" onclick={() => focus('runners')} title="{runnersOffline} of {totalRunners || dashData?.offlineRunners ? totalRunners : '?'} runners offline">
      <span class="dot warn-dot"></span>
      {runnersOffline} runner{runnersOffline === 1 ? '' : 's'} offline
    </button>
  {/if}

  {#if outboxPending > 0 || outboxError}
    <button
      class="chip clickable"
      class:warn={outboxStuck || !!outboxError}
      class:err={!!outboxError && outboxPending > 0}
      onclick={() => focus('event-log')}
      title={outboxError
        ? `Outbox error: ${outboxError}`
        : outboxStuck
          ? `${outboxPending} envelopes pending — stuck for >2min`
          : `${outboxPending} envelopes queued for delivery to brainbox`}>
      <span class="dot" class:warn-dot={outboxStuck || !!outboxError}></span>
      outbox {outboxPending}{outboxStuck ? ' ⚠' : ''}
    </button>
  {/if}

  <div class="right">
    <span class="chip muted">{eventCount} events</span>
  </div>
</div>

<style>
  .statusbar {
    height: 34px;
    flex: none;
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 0 18px;
    background: var(--titlebar, var(--color-bg-primary));
    border-top: 1px solid var(--border, var(--color-border-primary));
    font-family: var(--font-mono);
    font-size: 11.5px;
    color: var(--text-muted, var(--color-text-secondary));
    flex-shrink: 0;
    user-select: none;
  }

  .right {
    margin-left: auto;
    display: flex;
    gap: 14px;
    align-items: center;
  }

  .chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    white-space: nowrap;
    background: transparent;
    border: none;
    color: inherit;
    font: inherit;
    padding: 0;
  }
  .chip.clickable {
    cursor: pointer;
    padding: 2px 8px;
    border-radius: var(--r-sm, var(--radius-sm));
    transition: background 0.12s, color 0.12s;
  }
  .chip.clickable:hover {
    background: var(--bg-hover, var(--color-surface-hover));
    color: var(--text, var(--color-text-primary));
  }
  .chip.warn { color: var(--color-warning, #ef6c00); }
  .chip.err  { color: var(--color-error, #b71c1c); }
  .chip.muted { color: var(--text-faint, var(--color-text-tertiary)); }

  .dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--text-faint, var(--color-text-tertiary));
    flex-shrink: 0;
  }
  .dot.ok { background: var(--color-success); }
  .dot.warn-dot { background: var(--color-warning, #ef6c00); }

  .conn-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--text-faint, var(--color-text-tertiary));
    flex-shrink: 0;
    transition: background 0.3s;
  }
  .conn-dot.live {
    background: var(--run, var(--color-success));
    box-shadow: 0 0 0 0 color-mix(in srgb, var(--run, #22c55e) 60%, transparent);
    animation: status-pulse 2.4s infinite;
  }

  @keyframes status-pulse {
    0%   { box-shadow: 0 0 0 0 color-mix(in srgb, var(--run, #22c55e) 55%, transparent); }
    70%  { box-shadow: 0 0 0 6px transparent; }
    100% { box-shadow: 0 0 0 0 transparent; }
  }
</style>

<script lang="ts">
  import { onMount } from 'svelte';
  import {
    connectionState,
    profileState,
    currentPanel,
    attentionStore,
    outboxStore,
    eventsState,
  } from '../stores.svelte';
  import { brainboxEvents } from '../events.svelte';
  import { sessionsRes, startFleetPolling, stopFleetPolling } from '../stores/fleet.svelte';
  import { runnersRes } from '../stores/runners.svelte';

  let connected     = $derived(connectionState.connected);
  let activeProfile = $derived(profileState.active);
  let eventCount    = $derived(brainboxEvents.log.length);

  // Live operator readout chips. attentionStore is bootstrapped by AppShell;
  // outboxStore + the local runner-count poll are kicked off here.
  let attention = $derived(attentionStore.count);
  let outboxPending = $derived(outboxStore.pending);
  let outboxStuck = $derived(outboxStore.stuck);
  let outboxError = $derived(outboxStore.lastError);

  // StatusBar OWNS the shared sessions/fleet poll, because it is the only
  // consumer always mounted — it lives in the shell, not under AppShell's
  // {#if}. Dashboard and Sessions read the same store instead of each running
  // their own timer, so this data is fetched once for the whole app.
  //
  // This also drops a straight waste: the previous poll fetched GetSessions
  // unconditionally and then used the result only `if (dashData == null)`,
  // so whenever the Dashboard had been visited the call was thrown away on a
  // 15s timer for the life of the app.
  //
  // No client-side workspace_profile filter here or anywhere else:
  // GetSessions already filters server-side on an exact match
  // (app_brainbox.go), so the panels' case-insensitive re-filter was a no-op.
  const OFFLINE_AFTER_MS = 90_000;
  let now = $state(Date.now());

  onMount(() => {
    outboxStore.start();
    // A ticker so "offline" ages without needing a fetch: liveness is a
    // function of last_seen and the clock, not of new data.
    const tick = window.setInterval(() => { now = Date.now(); }, 10_000);
    return () => {
      window.clearInterval(tick);
      stopFleetPolling();
    };
  });

  // Re-point the shared poll when the profile changes; startFleetPolling is
  // idempotent for the same profile, so this cannot stack timers.
  $effect(() => {
    startFleetPolling(activeProfile?.name ?? '');
  });

  let sessions = $derived(sessionsRes.for(activeProfile?.name ?? '').value);
  let runners = $derived(runnersRes.value);
  let sessionsCount = $derived(sessions.filter((s: any) => s.active).length);
  let totalRunners = $derived(runners.length);
  let runnersOffline = $derived(
    runners.filter((r: any) => now - (r.last_seen ?? 0) >= OFFLINE_AFTER_MS).length
  );

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
    <button class="chip clickable warn" onclick={() => focus('stream')} title="Items need attention — click to open Events">
      <span class="dot warn-dot"></span>
      {attention} attention
    </button>
  {/if}

  {#if runnersOffline > 0}
    <!-- Both numbers come from the one shared fleet read, so the total is
         always known. The previous expression mixed || and ?: and so could
         only ever print the total or a literal '?'. -->
    <button class="chip clickable warn" onclick={() => focus('runners')} title="{runnersOffline} of {totalRunners} runners offline">
      <span class="dot warn-dot"></span>
      {runnersOffline} runner{runnersOffline === 1 ? '' : 's'} offline
    </button>
  {/if}

  {#if outboxPending > 0 || outboxError}
    <button
      class="chip clickable"
      class:warn={outboxStuck || !!outboxError}
      class:err={!!outboxError && outboxPending > 0}
      onclick={() => eventsState.open('raw')}
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

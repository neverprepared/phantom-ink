<script lang="ts">
  import { connectionState, profileState } from '../stores.svelte';
  import { brainboxEvents } from '../events.svelte';
  import { authorityState } from '../authority.svelte';
  import CredentialsModal from '../components/CredentialsModal.svelte';

  let connected     = $derived(connectionState.connected);
  let activeProfile = $derived(profileState.active);
  let eventCount    = $derived(brainboxEvents.log.length);
  let authHealth    = $derived(authorityState.health);
  let showingCredentials = $state(false);

  function authTitle(h: string): string {
    switch (h) {
      case 'green':  return 'credential authority live — click for detail';
      case 'yellow': return 'credential authority live, recent failures — click for detail';
      case 'red':    return 'credential authority offline — click for detail';
      case 'none':   return 'no credential authority registered — click for detail';
      default:       return 'credential authority status unknown — click for detail';
    }
  }
</script>

<div class="statusbar">
  <span class="chip">
    <span class="conn-dot" class:live={connected}></span>
    {connected ? 'connected' : 'offline'}
  </span>
  <span class="chip">
    <span class="dot"></span>
    {activeProfile ? activeProfile.name : 'all profiles'}
  </span>
  <button
    type="button"
    class="auth-btn chip"
    title={authTitle(authHealth)}
    onclick={() => (showingCredentials = true)}
  >
    <span class="dot dot-{authHealth}"></span>
    cred-auth
  </button>
  <div class="right">
    <span class="chip">{eventCount} events</span>
  </div>
</div>

{#if showingCredentials}
  <CredentialsModal onClose={() => (showingCredentials = false)} />
{/if}

<style>
  .statusbar {
    height: 34px;
    flex: none;
    display: flex;
    align-items: center;
    gap: 18px;
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
    gap: 18px;
    align-items: center;
  }

  .chip {
    display: flex;
    align-items: center;
    gap: 6px;
    white-space: nowrap;
  }

  .dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--text-faint, var(--color-text-tertiary));
    flex-shrink: 0;
  }

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

  .dot-green  { background: var(--run,  var(--color-success)); }
  .dot-yellow { background: var(--sched, #e0a64a); }
  .dot-red    { background: var(--fail, var(--color-error)); }
  .dot-none, .dot-unknown { background: var(--text-faint, var(--color-text-tertiary)); }

  .auth-btn {
    background: transparent;
    border: none;
    padding: 0;
    cursor: pointer;
    color: inherit;
    font-family: inherit;
    font-size: inherit;
  }
  .auth-btn:hover {
    color: var(--text, var(--color-text-primary));
  }
</style>

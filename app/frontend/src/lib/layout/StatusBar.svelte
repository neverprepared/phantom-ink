<script lang="ts">
  import { connectionState } from '../stores.svelte';
  import { brainboxEvents } from '../events.svelte';
  import { authorityState } from '../authority.svelte';
  import CredentialsModal from '../components/CredentialsModal.svelte';

  let lastEvent = $derived(brainboxEvents.last);
  let lastEventText = $derived(connectionState.lastEventText);
  let eventCount = $derived(brainboxEvents.log.length);
  let authHealth = $derived(authorityState.health);
  let showingCredentials = $state(false);

  function authTitle(h: string): string {
    switch (h) {
      case 'green': return 'credential authority live — click for detail';
      case 'yellow': return 'credential authority live, recent failures — click for detail';
      case 'red': return 'credential authority offline — click for detail';
      case 'none': return 'no credential authority registered — click for detail';
      default: return 'credential authority status unknown — click for detail';
    }
  }
</script>

<div class="statusbar">
  <div class="status-left">
    {#if lastEventText}
      <span class="event-text">{lastEventText}</span>
    {:else}
      <span class="event-text muted">waiting for events...</span>
    {/if}
  </div>
  <div class="status-right">
    <button
      type="button"
      class="auth-pill"
      title={authTitle(authHealth)}
      onclick={() => (showingCredentials = true)}
    >
      <span class="dot dot-{authHealth}"></span>
      <span>cred-auth</span>
    </button>
    <span class="stat">{eventCount} events</span>
  </div>
</div>

{#if showingCredentials}
  <CredentialsModal onClose={() => (showingCredentials = false)} />
{/if}

<style>
  .statusbar {
    height: 26px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 var(--spacing-lg);
    background: var(--color-bg-primary);
    border-top: 1px solid var(--color-border-primary);
    flex-shrink: 0;
    font-size: 11px;
    color: var(--color-text-tertiary);
  }

  .status-left {
    display: flex;
    align-items: center;
    gap: var(--spacing-md);
    min-width: 0;
    flex: 1;
  }

  .event-text {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--color-text-secondary);
  }

  .event-text.muted {
    color: var(--color-text-muted);
  }

  .status-right {
    display: flex;
    align-items: center;
    gap: var(--spacing-lg);
    flex-shrink: 0;
  }

  .stat {
    color: var(--color-text-tertiary);
    font-variant-numeric: tabular-nums;
  }

  .auth-pill {
    background: transparent;
    border: 1px solid var(--color-border-secondary);
    border-radius: var(--radius-sm);
    color: var(--color-text-tertiary);
    font-size: 10px;
    padding: 2px 8px;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    line-height: 1.5;
  }
  .auth-pill:hover {
    background: var(--color-surface-hover);
    color: var(--color-text-secondary);
  }
  .dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    display: inline-block;
  }
  .dot-green { background: var(--color-success); box-shadow: var(--shadow-status-active); }
  .dot-yellow { background: var(--color-warning, #e0a64a); }
  .dot-red { background: var(--color-danger, #e54); }
  .dot-none, .dot-unknown { background: var(--color-text-tertiary); }
</style>

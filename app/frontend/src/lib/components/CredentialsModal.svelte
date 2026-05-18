<script lang="ts">
  import Modal from './Modal.svelte';
  import { authorityState, refreshAuthority } from '../authority.svelte';

  let { onClose }: { onClose: () => void } = $props();

  // Tick to keep relative timestamps fresh.
  let now = $state(Date.now());
  let ticker = window.setInterval(() => { now = Date.now(); }, 1_000);
  $effect(() => () => window.clearInterval(ticker));

  let status = $derived(authorityState.status);
  let health = $derived(authorityState.health);
  let loadError = $derived(authorityState.loadError);

  function rel(epochMs: number | null | undefined): string {
    if (!epochMs) return 'never';
    const diff = Math.max(0, now - epochMs);
    if (diff < 5_000) return 'just now';
    if (diff < 60_000) return `${Math.floor(diff / 1000)}s ago`;
    if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
    if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
    return `${Math.floor(diff / 86_400_000)}d ago`;
  }

  function headlineForHealth(): { label: string; sub: string } {
    switch (health) {
      case 'green':
        return { label: 'authority live', sub: 'a registered runner is online and ready to seal credential bundles' };
      case 'yellow':
        return { label: 'authority live (recent failures)', sub: 'an authority is online but the API has logged recent seal-request errors' };
      case 'red':
        return { label: 'authority offline', sub: 'one or more authorities are registered but stale — session creates will 503 until one comes back' };
      case 'none':
        return { label: 'no authority registered', sub: 'no runner advertises secret_authority. session creates that use delivery=bundle will fail.' };
      default:
        return { label: 'status unknown', sub: loadError ?? 'authority status not yet loaded' };
    }
  }
  let headline = $derived(headlineForHealth());
</script>

<Modal {onClose}>
  <header>
    <div class="title-row">
      <span class="dot dot-{health}"></span>
      <h2>credential authority</h2>
      <button class="link" onclick={() => refreshAuthority()}>refresh</button>
    </div>
    <p class="headline">{headline.label}</p>
    <p class="sub">{headline.sub}</p>
  </header>

  <section>
    <h3>authorities</h3>
    {#if !status || status.authorities.length === 0}
      <p class="empty">none registered</p>
    {:else}
      <ul class="list">
        {#each status.authorities as a (a.name)}
          <li>
            <div class="row1">
              <span class="dot dot-{a.online ? 'green' : 'red'}"></span>
              <span class="name">{a.name}</span>
              {#if a.version}<span class="muted">v{a.version}</span>{/if}
            </div>
            <div class="row2">
              <span>last seen <strong>{rel(a.last_seen)}</strong></span>
              <span>last seal <strong>{rel(a.last_seal_at)}</strong></span>
            </div>
          </li>
        {/each}
      </ul>
    {/if}
  </section>

  <section>
    <h3>recent failures</h3>
    {#if !status || status.recent_failures.length === 0}
      <p class="empty">none</p>
    {:else}
      <ul class="failures">
        {#each [...status.recent_failures].reverse() as f (f.when)}
          <li>
            <span class="muted">{rel(f.when)}</span>
            <span class="status-pill">{f.status}</span>
            <span class="err">{f.error}</span>
          </li>
        {/each}
      </ul>
    {/if}
  </section>

  <footer>
    <button class="btn" onclick={onClose}>close</button>
  </footer>
</Modal>

<style>
  header { margin-bottom: 16px; }
  .title-row {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  h2 {
    margin: 0;
    font-size: 16px;
    font-weight: 600;
    color: var(--color-text-primary);
  }
  h3 {
    margin: 0 0 8px;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--color-text-tertiary);
  }
  .headline {
    margin: 6px 0 0;
    font-size: 13px;
    color: var(--color-text-primary);
  }
  .sub {
    margin: 4px 0 0;
    font-size: 12px;
    color: var(--color-text-tertiary);
    line-height: 1.4;
  }
  section { margin-top: 18px; }
  .empty {
    color: var(--color-text-tertiary);
    font-size: 12px;
    margin: 0;
  }

  .list { list-style: none; padding: 0; margin: 0; }
  .list li {
    padding: 10px 0;
    border-bottom: 1px solid var(--color-border-primary);
  }
  .list li:last-child { border-bottom: none; }
  .row1 {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 12px;
  }
  .row2 {
    margin-top: 4px;
    margin-left: 16px;
    font-size: 11px;
    color: var(--color-text-tertiary);
    display: flex;
    gap: 12px;
  }
  .row2 strong {
    color: var(--color-text-secondary);
    font-weight: 500;
  }
  .name {
    font-family: var(--font-mono);
    font-weight: 600;
    color: var(--color-text-primary);
  }
  .muted { color: var(--color-text-tertiary); font-size: 11px; }

  .failures { list-style: none; padding: 0; margin: 0; }
  .failures li {
    padding: 6px 0;
    display: flex;
    gap: 8px;
    align-items: baseline;
    font-size: 11px;
    border-bottom: 1px solid var(--color-border-primary);
  }
  .failures li:last-child { border-bottom: none; }
  .status-pill {
    background: var(--color-surface-subtle);
    color: var(--color-text-secondary);
    padding: 0 6px;
    border-radius: var(--radius-sm);
    font-variant-numeric: tabular-nums;
    font-weight: 600;
  }
  .err { color: var(--color-text-secondary); }

  .dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    display: inline-block;
    flex-shrink: 0;
  }
  .dot-green { background: var(--color-success); box-shadow: var(--shadow-status-active); }
  .dot-yellow { background: var(--color-warning, #e0a64a); }
  .dot-red { background: var(--color-danger, #e54); }
  .dot-none, .dot-unknown { background: var(--color-text-tertiary); }

  footer { margin-top: 18px; display: flex; justify-content: flex-end; }
  .btn {
    background: var(--color-surface-hover);
    border: 1px solid var(--color-border-secondary);
    border-radius: var(--radius-md);
    color: var(--color-text-secondary);
    padding: 6px 14px;
    font-size: 12px;
    cursor: pointer;
  }
  .btn:hover { background: var(--color-surface-active); }
  .link {
    margin-left: auto;
    background: transparent;
    border: none;
    color: var(--color-info);
    font-size: 11px;
    cursor: pointer;
  }
  .link:hover { text-decoration: underline; }
</style>

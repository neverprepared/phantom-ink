<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import Modal from './Modal.svelte';
  import { getApi } from '../utils/api';
  import { notifications } from '../notifications.svelte';

  let { onClose }: { onClose: () => void } = $props();

  let token = $state('');
  let apiURL = $state('');
  let expiresAt = $state(0);
  let nameSuggestion = $state('');
  let loading = $state(true);
  let errorMsg: string | null = $state(null);
  let now = $state(Date.now() / 1000);
  let copied = $state(false);

  let ticker: number | undefined;

  const secondsLeft = $derived(Math.max(0, Math.floor(expiresAt - now)));
  const expired = $derived(expiresAt > 0 && secondsLeft === 0);

  onMount(async () => {
    await startPairing();
    ticker = window.setInterval(() => { now = Date.now() / 1000; }, 1000);
  });

  onDestroy(() => {
    if (ticker !== undefined) window.clearInterval(ticker);
  });

  async function startPairing() {
    loading = true;
    errorMsg = null;
    const a = await getApi();
    if (!a) { errorMsg = 'API bindings not available'; loading = false; return; }
    try {
      const ticket = await a.StartRunnerPairing(nameSuggestion, 300);
      token = ticket.token ?? '';
      apiURL = ticket.api_url ?? '';
      expiresAt = ticket.expires_at ?? 0;
    } catch (err: any) {
      errorMsg = `${err?.message ?? err}`;
    } finally {
      loading = false;
    }
  }

  async function copyToken() {
    if (!token) return;
    try {
      await navigator.clipboard.writeText(token);
      copied = true;
      window.setTimeout(() => { copied = false; }, 1500);
    } catch (err: any) {
      notifications.error(`copy failed: ${err}`);
    }
  }
</script>

<Modal {onClose}>
  <div class="header">
    <h2>Pair a runner</h2>
    <p>Run <code>brainbox-runner</code> on another Mac, choose Pair, and enter this token. The runner pulls the API URL + key on its own.</p>
  </div>

  {#if loading}
    <p class="hint">Generating token…</p>
  {:else if errorMsg}
    <p class="error">{errorMsg}</p>
    <div class="actions">
      <button onclick={startPairing}>Retry</button>
      <button onclick={onClose}>Close</button>
    </div>
  {:else}
    <div class="token-card">
      <div class="token-row">
        <code class="token">{token}</code>
        <button class="copy" onclick={copyToken}>{copied ? 'copied' : 'copy'}</button>
      </div>
      <div class="meta">
        <span>API URL: <code>{apiURL}</code></span>
        <span class="ttl" class:expired>
          {#if expired}
            expired — generate a new one
          {:else}
            expires in {secondsLeft}s
          {/if}
        </span>
      </div>
    </div>

    <details>
      <summary>Or pair from the command line</summary>
      <pre class="curl">curl -X POST {apiURL}/api/runners/pair/claim \
  -H "Content-Type: application/json" \
  -d '{`{`}"token":"{token}"{`}`}'</pre>
    </details>

    <div class="actions">
      <button onclick={startPairing}>Issue a new token</button>
      <button class="primary" onclick={onClose}>Done</button>
    </div>
  {/if}
</Modal>

<style>
  .header h2 {
    margin: 0 0 4px 0;
    font-size: 16px;
    font-weight: 600;
  }
  .header p {
    margin: 0 0 16px 0;
    font-size: 12px;
    color: var(--color-text-tertiary);
    line-height: 1.4;
  }
  .hint {
    color: var(--color-text-tertiary);
    font-size: 12px;
  }
  .error {
    color: var(--color-danger, #e54);
    font-size: 12px;
    background: var(--color-surface-hover);
    padding: 8px 12px;
    border-radius: var(--radius-md);
  }

  .token-card {
    background: var(--color-bg-primary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-md);
    padding: 14px;
    margin: 8px 0 14px 0;
  }
  .token-row {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .token {
    flex: 1;
    font-family: var(--font-mono);
    font-size: 16px;
    letter-spacing: 0.5px;
    padding: 8px 12px;
    background: var(--color-surface-subtle);
    border-radius: var(--radius-sm);
    user-select: all;
  }
  .copy {
    border: 1px solid var(--color-border-secondary);
    background: var(--color-surface-hover);
    border-radius: var(--radius-sm);
    padding: 6px 12px;
    font-size: 12px;
    cursor: pointer;
  }
  .copy:hover {
    background: var(--color-surface-active);
  }
  .meta {
    margin-top: 10px;
    display: flex;
    justify-content: space-between;
    font-size: 11px;
    color: var(--color-text-tertiary);
  }
  .meta code {
    color: var(--color-text-secondary);
    font-size: 11px;
  }
  .ttl.expired {
    color: var(--color-danger, #e54);
    font-weight: 600;
  }

  details {
    margin: 8px 0 14px 0;
    font-size: 12px;
    color: var(--color-text-tertiary);
  }
  details summary {
    cursor: pointer;
  }
  .curl {
    background: var(--color-bg-primary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm);
    padding: 8px 12px;
    font-size: 11px;
    white-space: pre-wrap;
    word-break: break-all;
    margin-top: 6px;
  }

  .actions {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
  }
  .actions button {
    border: 1px solid var(--color-border-secondary);
    background: var(--color-surface-hover);
    color: var(--color-text-secondary);
    border-radius: var(--radius-md);
    padding: 7px 14px;
    font-size: 12px;
    cursor: pointer;
  }
  .actions button:hover { background: var(--color-surface-active); }
  .actions button.primary {
    background: rgba(59, 130, 246, 0.1);
    border-color: rgba(59, 130, 246, 0.3);
    color: var(--color-info);
  }
  .actions button.primary:hover {
    background: rgba(59, 130, 246, 0.2);
    border-color: var(--color-info);
  }
</style>

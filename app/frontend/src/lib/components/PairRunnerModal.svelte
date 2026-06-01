<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import Modal from './Modal.svelte';
  import { getApi } from '../utils/api';
  import { notifications } from '../notifications.svelte';

  let { onClose }: { onClose: () => void } = $props();

  let token = $state('');
  let apiURL = $state('');
  // networkURL is what gets embedded in the token — the address the remote
  // runner uses to reach the API. Defaults to the Wails app's own API URL
  // but must be changed to a LAN/public IP for remote runners.
  let networkURL = $state('');
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
    const a = await getApi();
    if (a) {
      try {
        const cfg = await a.GetConfig();
        let url = cfg.api_url ?? '';
        // 0.0.0.0 and 127.0.0.1/localhost are not routable from a remote machine.
        // Replace the host with the machine's LAN IP so the token works remotely.
        const unroutable = /^https?:\/\/(0\.0\.0\.0|127\.0\.0\.1|localhost)(:\d+)?(\/.*)?$/;
        if (unroutable.test(url)) {
          const lanIP = await a.GetLANIP();
          if (lanIP) {
            url = url.replace(/^(https?:\/\/)[^/:]+/, `$1${lanIP}`);
          }
        }
        networkURL = url;
      } catch { /* ignore */ }
    }
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
      const ticket = await a.StartRunnerPairing(nameSuggestion, 300, networkURL);
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

  <div class="network-url-row">
    <label class="field-label" for="network-url">Runner connects to</label>
    <div class="network-url-input">
      <input
        id="network-url"
        type="text"
        bind:value={networkURL}
        placeholder="http://192.168.1.42:9999"
        spellcheck="false"
      />
      <button class="reissue" onclick={startPairing} disabled={loading || !networkURL}>
        {loading ? '…' : 'Issue'}
      </button>
    </div>
    <p class="field-hint">The address the remote runner will use to reach this API. Change from localhost to your LAN IP for remote Macs.</p>
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
    margin: 0 0 10px 0;
    font-size: 12px;
    color: var(--color-text-tertiary);
    line-height: 1.4;
  }
  .network-url-row {
    margin-bottom: 14px;
  }
  .field-label {
    display: block;
    font-size: 11px;
    font-weight: 600;
    color: var(--color-text-secondary);
    margin-bottom: 4px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .network-url-input {
    display: flex;
    gap: 6px;
  }
  .network-url-input input {
    flex: 1;
    font-size: 12px;
    font-family: var(--font-mono);
    padding: 6px 10px;
    background: var(--color-bg-primary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm);
    color: var(--color-text-primary);
  }
  .network-url-input input:focus {
    outline: none;
    border-color: var(--color-info);
  }
  .reissue {
    border: 1px solid var(--color-border-secondary);
    background: var(--color-surface-hover);
    color: var(--color-text-secondary);
    border-radius: var(--radius-sm);
    padding: 6px 12px;
    font-size: 12px;
    cursor: pointer;
    white-space: nowrap;
  }
  .reissue:hover:not(:disabled) { background: var(--color-surface-active); }
  .reissue:disabled { opacity: 0.4; cursor: default; }
  .field-hint {
    margin: 4px 0 0 0;
    font-size: 11px;
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

<script lang="ts">
  import { onMount } from 'svelte';
  import { getApi } from '../utils/api';
  import { notifications } from '../notifications.svelte';
  import EmptyState from '../components/EmptyState.svelte';
  import type { brainbox } from '../../../wailsjs/go/models';

  // API / profile tokens (T11). Persistent, revocable per-profile brainbox
  // API/bus tokens keyed by `capabilities` — distinct from the Tier-0 gateway
  // tokens minted in the Profiles panel (those are TTL'd MCP tool scopes).
  // Mirrors the brainbox dashboard TokensPanel; talks to the app's Wails
  // bindings (MintProfileToken / ListProfileTokens / RevokeProfileToken +
  // the two catalog helpers) which delegate to the on-disk API key.

  let tokens = $state<brainbox.ProfileTokenInfo[]>([]);
  let capabilities = $state<string[]>([]);
  let profiles = $state<string[]>([]);
  let loading = $state(true);

  // Inline revoke confirmation. Native window.confirm() is an unreliable no-op in
  // the Wails webview (it returns undefined, so a `!confirm()` guard bails silently
  // and "nothing happens"), so we confirm with a two-step inline button instead —
  // matching the pattern used by ConversationsPanel.
  let confirmRevokeId = $state<string | null>(null);

  // Mint form state
  let profileInput = $state('');
  let selectedCaps = $state<Set<string>>(new Set());
  let label = $state('');
  let minting = $state(false);

  // Show-once reveal of the freshly minted raw token.
  let minted = $state<brainbox.ProfileToken | null>(null);
  let copied = $state(false);

  onMount(async () => {
    const a = await getApi();
    if (!a) { loading = false; return; }
    // Catalog + profiles are best-effort — a failure there shouldn't blank the
    // page (free-text profile + no capabilities still mints).
    try { capabilities = (await a.ProfileTokenCapabilities()) ?? []; } catch { /* leave empty */ }
    try { profiles = (await a.ProfileTokenProfiles()) ?? []; } catch { /* free-text still works */ }
    await refresh();
  });

  async function refresh() {
    const a = await getApi();
    if (!a) { loading = false; return; }
    try {
      tokens = (await a.ListProfileTokens()) ?? [];
    } catch (err: any) {
      notifications.error(`Failed to load tokens: ${err?.message ?? err}`);
    } finally {
      loading = false;
    }
  }

  function toggleCap(cap: string) {
    // Reassign a new Set so Svelte reactivity fires.
    const next = new Set(selectedCaps);
    if (next.has(cap)) next.delete(cap);
    else next.add(cap);
    selectedCaps = next;
  }

  async function handleMint() {
    const profile = profileInput.trim();
    if (!profile) {
      notifications.error('A workspace profile is required.');
      return;
    }
    const a = await getApi();
    if (!a) return;
    minting = true;
    try {
      minted = await a.MintProfileToken(profile, Array.from(selectedCaps), label.trim());
      copied = false;
      // Reset the form; keep `minted` visible until the operator dismisses it.
      profileInput = '';
      selectedCaps = new Set();
      label = '';
      notifications.success(`Minted token for ${minted.workspace_profile}`);
      await refresh();
    } catch (err: any) {
      notifications.error(`Mint failed: ${err?.message ?? err}`);
    } finally {
      minting = false;
    }
  }

  async function copyToken() {
    if (!minted) return;
    try {
      await navigator.clipboard.writeText(minted.token);
      copied = true;
    } catch {
      notifications.error('Copy failed — select the token text manually.');
    }
  }

  function dismissMinted() {
    minted = null;
    copied = false;
  }

  async function handleRevoke(tokenId: string, profile: string) {
    confirmRevokeId = null;
    const a = await getApi();
    if (!a) return;
    try {
      await a.RevokeProfileToken(tokenId);
      notifications.success(`Revoked token for ${profile}`);
      await refresh();
    } catch (err: any) {
      notifications.error(`Revoke failed: ${err?.message ?? err}`);
    }
  }

  function fmtTime(ms: number): string {
    if (!ms) return '—';
    return new Date(ms).toLocaleString();
  }
</script>

<div class="tokens-panel">
  <header class="panel-header">
    <div>
      <h1>API / profile tokens</h1>
      <p class="subtitle">
        Persistent, revocable bearer tokens for the brainbox API/event bus, scoped to a
        workspace profile by capability. They live until you revoke them here — distinct
        from the TTL'd gateway tokens minted per-profile in <strong>Profiles</strong>.
      </p>
    </div>
    <div class="header-actions">
      <button class="btn" onclick={refresh} disabled={loading}>refresh</button>
    </div>
  </header>

  {#if minted}
    <div class="minted">
      <div class="minted-title">Token minted — copy it now</div>
      <p class="minted-hint">
        This is the only time the raw value is shown. Store it in 1Password now;
        brainbox keeps only a hash and cannot show it again.
      </p>
      <div class="minted-row">
        <code class="minted-token">{minted.token}</code>
        <button class="btn-copy" onclick={copyToken}>{copied ? 'Copied' : 'Copy'}</button>
      </div>
      <div class="minted-meta">
        profile <strong>{minted.workspace_profile}</strong>
        {#if minted.capabilities?.length}
          · {minted.capabilities.join(', ')}
        {/if}
      </div>
      <button class="btn-dismiss" onclick={dismissMinted}>I've stored it — dismiss</button>
    </div>
  {/if}

  <form class="mint-form" onsubmit={(e) => { e.preventDefault(); handleMint(); }}>
    <label>
      <span>Workspace profile</span>
      <input
        type="text"
        list="profile-token-options"
        bind:value={profileInput}
        placeholder="e.g. personal"
        spellcheck="false"
        required
      />
      <datalist id="profile-token-options">
        {#each profiles as p}
          <option value={p}></option>
        {/each}
      </datalist>
    </label>

    <div class="caps">
      <span class="caps-label">Capabilities</span>
      {#if capabilities.length === 0}
        <span class="caps-empty">No capabilities available.</span>
      {:else}
        <div class="caps-list">
          {#each capabilities as cap (cap)}
            <label class="cap-toggle">
              <input
                type="checkbox"
                checked={selectedCaps.has(cap)}
                onchange={() => toggleCap(cap)}
              />
              <span>{cap}</span>
            </label>
          {/each}
        </div>
      {/if}
    </div>

    <label>
      <span>Label (optional)</span>
      <input type="text" bind:value={label} placeholder="what/where this token is used" spellcheck="false" />
    </label>

    <button type="submit" class="btn-mint" disabled={minting || !profileInput.trim()}>
      {minting ? 'Minting…' : 'Mint token'}
    </button>
  </form>

  <h2 class="section-title">Active tokens</h2>

  {#if loading}
    <p class="hint">loading…</p>
  {:else if tokens.length === 0}
    <EmptyState
      title="No tokens minted yet"
      message="Mint a profile token above to give an agent or service scoped access to the brainbox API/bus."
    />
  {:else}
    <div class="table-scroll">
      <table class="token-table">
        <thead>
          <tr>
            <th>Profile</th>
            <th>Capabilities</th>
            <th>Label</th>
            <th>Issued</th>
            <th>Last used</th>
            <th>Status</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {#each tokens as t (t.token_id)}
            <tr>
              <td class="mono">{t.workspace_profile}</td>
              <td>
                {#if t.capabilities?.length}
                  <div class="cap-chips">
                    {#each t.capabilities as cap}
                      <span class="cap-chip">{cap}</span>
                    {/each}
                  </div>
                {:else}
                  <span class="muted">—</span>
                {/if}
              </td>
              <td>{t.label || '—'}</td>
              <td class="muted">{fmtTime(t.issued)}</td>
              <td class="muted">{fmtTime(t.last_used)}</td>
              <td>
                <span class="status status-active">active</span>
              </td>
              <td>
                {#if confirmRevokeId === t.token_id}
                  <button
                    class="btn-revoke"
                    onclick={() => handleRevoke(t.token_id, t.workspace_profile)}
                    aria-label={`Confirm revoke token for ${t.workspace_profile}`}
                  >Confirm?</button>
                  <button
                    class="btn-cancel-revoke"
                    onclick={() => (confirmRevokeId = null)}
                  >Cancel</button>
                {:else}
                  <button
                    class="btn-revoke"
                    onclick={() => (confirmRevokeId = t.token_id)}
                    aria-label={`Revoke token for ${t.workspace_profile}`}
                  >Revoke</button>
                {/if}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</div>

<style>
  .tokens-panel { padding: 1.25rem 1.5rem; max-width: 820px; }
  .panel-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem; margin-bottom: 1.25rem; }
  h1 { font-size: 1.1rem; margin: 0 0 0.2rem; }
  .subtitle { font-size: 0.75rem; color: var(--text-muted); margin: 0; max-width: 60ch; }
  .header-actions { display: flex; align-items: center; gap: 0.6rem; white-space: nowrap; }
  .btn {
    background: var(--bg-sunken); border: 1px solid var(--border);
    border-radius: 5px; color: var(--text-muted); cursor: pointer; font-size: 0.72rem; padding: 0.25rem 0.6rem;
  }
  .btn:hover:not(:disabled) { color: var(--text); }
  .hint { font-size: 0.72rem; color: var(--text-muted); padding: 1rem 0; }

  .minted {
    background: var(--color-success-bg);
    border: 1px solid var(--color-success-border);
    border-radius: 8px;
    padding: 1rem;
    margin-bottom: 1.25rem;
  }
  .minted-title { font-size: 0.85rem; font-weight: 600; color: var(--color-status-success-text); margin-bottom: 0.25rem; }
  .minted-hint { font-size: 0.72rem; color: var(--text-muted); margin-bottom: 0.75rem; }
  .minted-row { display: flex; gap: 0.5rem; align-items: stretch; margin-bottom: 0.5rem; }
  .minted-token {
    flex: 1;
    background: var(--bg-sunken);
    border: 1px solid var(--border);
    color: var(--text);
    padding: 0.5rem 0.75rem;
    border-radius: 6px;
    font-family: var(--font-mono, ui-monospace, monospace);
    font-size: 0.8rem;
    word-break: break-all;
    overflow-x: auto;
  }
  .btn-copy {
    background: var(--color-success);
    border: none;
    color: #fff;
    font-weight: 600;
    padding: 0.5rem 1rem;
    border-radius: 6px;
    cursor: pointer;
    font-size: 0.78rem;
    white-space: nowrap;
  }
  .minted-meta { font-size: 0.72rem; color: var(--text-muted); margin-bottom: 0.75rem; }
  .btn-dismiss {
    background: none; border: 1px solid var(--border);
    color: var(--text-muted); padding: 0.35rem 0.75rem; border-radius: 6px; cursor: pointer; font-size: 0.72rem;
  }
  .btn-dismiss:hover { color: var(--text); border-color: var(--border-strong); }

  .mint-form {
    background: var(--bg-sunken);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 1rem;
    margin-bottom: 1.75rem;
    display: flex;
    flex-direction: column;
    gap: 0.85rem;
    max-width: 640px;
  }
  .mint-form label { display: flex; flex-direction: column; gap: 0.25rem; }
  .mint-form label > span, .caps-label {
    font-size: 0.68rem; color: var(--text-muted);
    text-transform: uppercase; letter-spacing: 0.05em;
  }
  .mint-form input[type="text"] {
    background: var(--bg); border: 1px solid var(--border);
    color: var(--text); padding: 0.5rem 0.75rem; border-radius: 6px; font-size: 0.8rem;
  }

  .caps { display: flex; flex-direction: column; gap: 0.5rem; }
  .caps-empty { font-size: 0.78rem; color: var(--text-faint); }
  .caps-list { display: flex; flex-wrap: wrap; gap: 0.75rem; }
  .cap-toggle { display: flex; flex-direction: row; align-items: center; gap: 0.4rem; cursor: pointer; }
  .cap-toggle span {
    font-size: 0.78rem; color: var(--text);
    font-family: var(--font-mono, ui-monospace, monospace);
  }

  .btn-mint {
    background: var(--accent); border: none; color: #fff;
    padding: 0.5rem 1rem; border-radius: 6px; cursor: pointer; font-size: 0.8rem; align-self: flex-start;
  }
  .btn-mint:disabled { opacity: 0.5; cursor: not-allowed; }

  .section-title { font-size: 0.85rem; font-weight: 600; margin: 0 0 0.75rem; }

  .table-scroll { overflow-x: auto; }
  .token-table { width: 100%; border-collapse: collapse; font-size: 0.78rem; }
  .token-table th {
    text-align: left; color: var(--text-muted); font-size: 0.65rem;
    text-transform: uppercase; letter-spacing: 0.05em;
    padding: 0.5rem 0.75rem; border-bottom: 1px solid var(--border); white-space: nowrap;
  }
  .token-table td {
    padding: 0.6rem 0.75rem; border-bottom: 1px solid var(--border);
    color: var(--text); vertical-align: top;
  }
  .mono { font-family: var(--font-mono, ui-monospace, monospace); }
  .muted { color: var(--text-muted); }

  .cap-chips { display: flex; flex-wrap: wrap; gap: 0.25rem; }
  .cap-chip {
    background: var(--accent-soft); border: 1px solid var(--accent-line, var(--border));
    color: var(--accent); padding: 0.05rem 0.4rem; border-radius: 10px;
    font-size: 0.68rem; font-family: var(--font-mono, ui-monospace, monospace); white-space: nowrap;
  }

  .status {
    font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.04em;
    font-weight: 600; padding: 0.1rem 0.5rem; border-radius: 10px;
  }
  .status-active { background: var(--color-success-bg); color: var(--color-status-success-text); }

  .btn-revoke {
    background: var(--color-error-bg); border: 1px solid var(--color-error-border);
    color: var(--color-status-error-text); padding: 0.25rem 0.75rem; border-radius: 6px; cursor: pointer; font-size: 0.72rem;
  }
  .btn-revoke:hover { filter: brightness(1.15); }
  .btn-cancel-revoke {
    background: transparent; border: 1px solid var(--color-border);
    color: var(--color-text-muted); padding: 0.25rem 0.6rem; border-radius: 6px; cursor: pointer; font-size: 0.72rem; margin-left: 0.35rem;
  }
  .btn-cancel-revoke:hover { filter: brightness(1.15); }
</style>

<script>
  import { onMount } from 'svelte';
  import {
    fetchProfileTokens,
    fetchTokenCapabilities,
    fetchTokenProfiles,
    mintProfileToken,
    revokeProfileToken,
  } from './api.js';

  let tokens = $state([]);
  let capabilities = $state([]);
  let profiles = $state([]);
  let loading = $state(true);
  let error = $state(null);

  // Mint form state
  let profileInput = $state('');
  let selectedCaps = $state(new Set());
  let label = $state('');
  let minting = $state(false);

  // Show-once reveal of the freshly minted raw token
  let minted = $state(null); // { token, workspace_profile, capabilities, label }
  let copied = $state(false);

  async function refresh() {
    try {
      tokens = await fetchProfileTokens();
      error = null;
    } catch (e) {
      error = e.message;
    } finally {
      loading = false;
    }
  }

  function toggleCap(cap) {
    // Reassign a new Set so Svelte reactivity fires.
    const next = new Set(selectedCaps);
    if (next.has(cap)) next.delete(cap);
    else next.add(cap);
    selectedCaps = next;
  }

  async function handleMint() {
    const profile = profileInput.trim();
    if (!profile) {
      error = 'A workspace profile is required.';
      return;
    }
    minting = true;
    error = null;
    try {
      minted = await mintProfileToken({
        workspace_profile: profile,
        capabilities: Array.from(selectedCaps),
        label: label.trim(),
      });
      copied = false;
      // Reset the form; keep `minted` visible until the operator dismisses it.
      profileInput = '';
      selectedCaps = new Set();
      label = '';
      await refresh();
    } catch (e) {
      error = e.message;
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
      error = 'Copy failed — select the token text manually.';
    }
  }

  function dismissMinted() {
    minted = null;
    copied = false;
  }

  async function handleRevoke(tokenId) {
    if (!confirm('Revoke this token? Any client using it will immediately 401.')) return;
    try {
      await revokeProfileToken(tokenId);
      await refresh();
    } catch (e) {
      error = e.message;
    }
  }

  function fmtTime(ms) {
    if (!ms) return '—';
    return new Date(ms).toLocaleString();
  }

  onMount(async () => {
    // Catalog + profiles are best-effort — a failure there shouldn't blank the page.
    try {
      capabilities = await fetchTokenCapabilities();
    } catch { /* leave empty */ }
    try {
      profiles = await fetchTokenProfiles();
    } catch { /* free-text still works */ }
    await refresh();
  });
</script>

<div class="tokens-panel">
  <div class="panel-header">
    <h2>Profile Tokens</h2>
  </div>

  <p class="intro">
    Mint a persistent, revocable bearer token scoped to a workspace profile.
    Tokens never expire — they work until you revoke them here.
  </p>

  {#if error}
    <div class="error">{error}</div>
  {/if}

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
      <span>Workspace Profile</span>
      <input
        type="text"
        list="profile-options"
        bind:value={profileInput}
        placeholder="e.g. personal"
        required
      />
      <datalist id="profile-options">
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
      <input type="text" bind:value={label} placeholder="what/where this token is used" />
    </label>

    <button type="submit" class="btn-mint" disabled={minting || !profileInput.trim()}>
      {minting ? 'Minting…' : 'Mint Token'}
    </button>
  </form>

  <h3 class="section-title">Active Tokens</h3>

  {#if loading}
    <div class="loading">Loading tokens…</div>
  {:else if tokens.length === 0}
    <div class="empty">No tokens minted yet.</div>
  {:else}
    <div class="table-scroll">
      <table class="token-table">
        <thead>
          <tr>
            <th>Profile</th>
            <th>Capabilities</th>
            <th>Label</th>
            <th>Issued</th>
            <th>Last Used</th>
            <th>Status</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {#each tokens as t (t.token_id)}
            <tr class:revoked={t.revoked}>
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
                {#if t.revoked}
                  <span class="status status-revoked">revoked</span>
                {:else}
                  <span class="status status-active">active</span>
                {/if}
              </td>
              <td>
                {#if !t.revoked}
                  <button
                    class="btn-revoke"
                    onclick={() => handleRevoke(t.token_id)}
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
  .tokens-panel { padding: 24px; }
  .panel-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 8px;
  }
  h2 {
    font-size: 18px;
    font-weight: 600;
    color: #e2e8f0;
    margin: 0;
  }
  .intro {
    color: #94a3b8;
    font-size: 13px;
    margin-bottom: 20px;
    max-width: 640px;
  }

  .error {
    background: rgba(239, 68, 68, 0.1);
    border: 1px solid rgba(239, 68, 68, 0.3);
    color: #fca5a5;
    padding: 10px 14px;
    border-radius: 6px;
    font-size: 13px;
    margin-bottom: 16px;
  }

  .minted {
    background: rgba(16, 185, 129, 0.08);
    border: 1px solid rgba(16, 185, 129, 0.35);
    border-radius: 8px;
    padding: 16px;
    margin-bottom: 20px;
  }
  .minted-title {
    font-size: 14px;
    font-weight: 600;
    color: #6ee7b7;
    margin-bottom: 4px;
  }
  .minted-hint {
    font-size: 12px;
    color: #94a3b8;
    margin-bottom: 12px;
  }
  .minted-row {
    display: flex;
    gap: 8px;
    align-items: stretch;
    margin-bottom: 8px;
  }
  .minted-token {
    flex: 1;
    background: #0f172a;
    border: 1px solid #334155;
    color: #e2e8f0;
    padding: 8px 12px;
    border-radius: 6px;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, monospace;
    font-size: 13px;
    word-break: break-all;
    overflow-x: auto;
  }
  .btn-copy {
    background: #10b981;
    border: none;
    color: #052e22;
    font-weight: 600;
    padding: 8px 16px;
    border-radius: 6px;
    cursor: pointer;
    font-size: 13px;
    white-space: nowrap;
  }
  .minted-meta {
    font-size: 12px;
    color: #94a3b8;
    margin-bottom: 12px;
  }
  .btn-dismiss {
    background: none;
    border: 1px solid #334155;
    color: #94a3b8;
    padding: 6px 12px;
    border-radius: 6px;
    cursor: pointer;
    font-size: 12px;
  }
  .btn-dismiss:hover { color: #e2e8f0; border-color: #475569; }

  .mint-form {
    background: #111827;
    border: 1px solid #1e293b;
    border-radius: 8px;
    padding: 16px;
    margin-bottom: 28px;
    display: flex;
    flex-direction: column;
    gap: 14px;
    max-width: 640px;
  }
  .mint-form label {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .mint-form label > span {
    font-size: 12px;
    color: #94a3b8;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }
  .mint-form input[type="text"] {
    background: #0f172a;
    border: 1px solid #334155;
    color: #e2e8f0;
    padding: 8px 12px;
    border-radius: 6px;
    font-size: 13px;
  }

  .caps { display: flex; flex-direction: column; gap: 8px; }
  .caps-label {
    font-size: 12px;
    color: #94a3b8;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }
  .caps-empty { font-size: 13px; color: #64748b; }
  .caps-list {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
  }
  .cap-toggle {
    display: flex !important;
    flex-direction: row !important;
    align-items: center;
    gap: 6px;
    cursor: pointer;
  }
  .cap-toggle span {
    font-size: 13px;
    color: #cbd5e1;
    text-transform: none;
    letter-spacing: normal;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, monospace;
  }

  .btn-mint {
    background: #3b82f6;
    border: none;
    color: white;
    padding: 8px 16px;
    border-radius: 6px;
    cursor: pointer;
    font-size: 13px;
    align-self: flex-start;
  }
  .btn-mint:disabled { opacity: 0.5; cursor: not-allowed; }

  .section-title {
    font-size: 14px;
    font-weight: 600;
    color: #e2e8f0;
    margin: 0 0 12px;
  }
  .loading, .empty { color: #64748b; padding: 24px 0; }

  .table-scroll { overflow-x: auto; }
  .token-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }
  .token-table th {
    text-align: left;
    color: #64748b;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    padding: 8px 12px;
    border-bottom: 1px solid #1e293b;
    white-space: nowrap;
  }
  .token-table td {
    padding: 10px 12px;
    border-bottom: 1px solid #161e2e;
    color: #cbd5e1;
    vertical-align: top;
  }
  .token-table tr.revoked td { opacity: 0.5; }
  .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, monospace; }
  .muted { color: #64748b; }

  .cap-chips { display: flex; flex-wrap: wrap; gap: 4px; }
  .cap-chip {
    background: rgba(59, 130, 246, 0.12);
    border: 1px solid rgba(59, 130, 246, 0.25);
    color: #93c5fd;
    padding: 1px 6px;
    border-radius: 10px;
    font-size: 11px;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, monospace;
    white-space: nowrap;
  }

  .status {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 10px;
  }
  .status-active { background: rgba(16, 185, 129, 0.12); color: #6ee7b7; }
  .status-revoked { background: rgba(100, 116, 139, 0.15); color: #94a3b8; }

  .btn-revoke {
    background: rgba(239, 68, 68, 0.1);
    border: 1px solid rgba(239, 68, 68, 0.3);
    color: #fca5a5;
    padding: 4px 12px;
    border-radius: 6px;
    cursor: pointer;
    font-size: 12px;
  }
  .btn-revoke:hover { background: rgba(239, 68, 68, 0.2); }
</style>

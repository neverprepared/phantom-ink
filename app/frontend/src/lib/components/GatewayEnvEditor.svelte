<script lang="ts">
  import { getApi } from '../utils/api';
  import { notifications } from '../notifications.svelte';

  // Per-profile MCP gateway secrets editor (ADR-002 phase 3). Edits the
  // age-encrypted env store the gateway injects into a profile's MCP server
  // processes, and mints Tier-0 tokens so a local client can reach the
  // gateway scoped to this profile. Operator-only — these are plaintext
  // secrets the operator owns; agents never see this surface.

  let { profile, unlocked }: { profile: string; unlocked: boolean } = $props();

  let open = $state(false);
  let loading = $state(false);
  let saving = $state(false);
  let loaded = $state(false);
  type Row = { key: string; value: string; reveal: boolean };
  let rows = $state<Row[]>([]);

  // mint-token state
  let scopeInput = $state('');
  let ttlHours = $state(1);
  let minting = $state(false);
  let mintedToken = $state('');

  async function toggle() {
    open = !open;
    if (open && !loaded) await load();
  }

  async function load() {
    loading = true;
    const a = await getApi();
    if (!a) { loading = false; return; }
    try {
      const env = await a.GetGatewayEnv(profile);
      rows = Object.entries(env ?? {}).map(([key, value]) => ({ key, value, reveal: false }));
      loaded = true;
    } catch (err: any) {
      notifications.error(`Failed to load gateway secrets: ${err?.message ?? err}`);
    } finally {
      loading = false;
    }
  }

  function addRow() {
    rows = [...rows, { key: '', value: '', reveal: true }];
  }

  function removeRow(i: number) {
    rows = rows.filter((_, idx) => idx !== i);
  }

  async function save() {
    // collapse rows → map; last key wins; skip blank keys
    const env: Record<string, string> = {};
    for (const r of rows) {
      const k = r.key.trim();
      if (k) env[k] = r.value;
    }
    saving = true;
    const a = await getApi();
    if (!a) { saving = false; return; }
    try {
      await a.SetGatewayEnv(profile, env);
      notifications.success(`Saved ${Object.keys(env).length} secret(s) for ${profile}`);
    } catch (err: any) {
      notifications.error(`Save failed: ${err?.message ?? err}`);
    } finally {
      saving = false;
    }
  }

  async function clearAll() {
    saving = true;
    const a = await getApi();
    if (!a) { saving = false; return; }
    try {
      await a.DeleteGatewayEnv(profile);
      rows = [];
      notifications.success(`Cleared gateway secrets for ${profile}`);
    } catch (err: any) {
      notifications.error(`Clear failed: ${err?.message ?? err}`);
    } finally {
      saving = false;
    }
  }

  async function mint() {
    const scope = scopeInput.split(',').map((s) => s.trim()).filter(Boolean);
    minting = true;
    mintedToken = '';
    const a = await getApi();
    if (!a) { minting = false; return; }
    try {
      const tok = await a.MintGatewayToken(profile, scope, Math.round(ttlHours * 3600));
      mintedToken = tok.token;
      notifications.success(`Minted token for ${profile} (${scope.length ? scope.join(', ') : 'all tools'})`);
    } catch (err: any) {
      notifications.error(`Mint failed: ${err?.message ?? err}`);
    } finally {
      minting = false;
    }
  }

  function copyToken() {
    navigator.clipboard.writeText(mintedToken);
    notifications.success('Token copied to clipboard');
  }
</script>

<button class="gw-toggle" onclick={toggle}>
  {open ? '▾' : '▸'} gateway secrets{loaded && rows.length ? ` (${rows.length})` : ''}
</button>

{#if open}
  <div class="gw-body">
    {#if !unlocked}
      <p class="gw-hint">
        Gateway locked — set <code>CL_GATEWAY__SECRET_KEY</code> in brainbox.env to edit secrets.
      </p>
    {:else if loading}
      <p class="gw-hint">loading…</p>
    {:else}
      <div class="gw-rows">
        {#each rows as row, i (i)}
          <div class="gw-row">
            <input class="gw-key" placeholder="KEY" bind:value={row.key} spellcheck="false" />
            <input
              class="gw-val"
              placeholder="value"
              type={row.reveal ? 'text' : 'password'}
              bind:value={row.value}
              spellcheck="false"
            />
            <button class="gw-icon" onclick={() => (row.reveal = !row.reveal)} title={row.reveal ? 'hide' : 'reveal'} aria-label="toggle reveal">
              {row.reveal ? '🙈' : '👁'}
            </button>
            <button class="gw-icon danger" onclick={() => removeRow(i)} title="remove" aria-label="remove variable">✕</button>
          </div>
        {/each}
        {#if rows.length === 0}
          <p class="gw-hint">no secrets stored.</p>
        {/if}
      </div>

      <div class="gw-actions">
        <button class="gw-btn" onclick={addRow}>+ variable</button>
        <button class="gw-btn primary" onclick={save} disabled={saving}>{saving ? 'saving…' : 'save'}</button>
        {#if rows.length > 0}
          <button class="gw-btn danger" onclick={clearAll} disabled={saving}>clear all</button>
        {/if}
      </div>

      <div class="gw-mint">
        <div class="gw-mint-label">mint Tier-0 token</div>
        <div class="gw-mint-row">
          <input class="gw-scope" placeholder="scope (e.g. phantom-brain__*) — blank = all" bind:value={scopeInput} spellcheck="false" />
          <input class="gw-ttl" type="number" min="0.25" step="0.25" bind:value={ttlHours} title="TTL (hours)" />
          <span class="gw-unit">h</span>
          <button class="gw-btn" onclick={mint} disabled={minting}>{minting ? 'minting…' : 'mint'}</button>
        </div>
        {#if mintedToken}
          <div class="gw-token-row">
            <code class="gw-token">{mintedToken}</code>
            <button class="gw-icon" onclick={copyToken} title="copy" aria-label="copy token">⧉</button>
          </div>
          <p class="gw-hint">shown once — copy it now.</p>
        {/if}
      </div>
    {/if}
  </div>
{/if}

<style>
  .gw-toggle {
    background: none;
    border: none;
    color: var(--text-secondary);
    font-size: 0.7rem;
    cursor: pointer;
    padding: 0.2rem 0;
    text-align: left;
    width: 100%;
  }
  .gw-toggle:hover { color: var(--text-primary); }
  .gw-body {
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
    padding: 0.4rem 0 0.2rem;
  }
  .gw-hint {
    font-size: 0.68rem;
    color: var(--text-tertiary, var(--text-secondary));
    margin: 0;
  }
  .gw-hint code { font-size: 0.66rem; }
  .gw-rows { display: flex; flex-direction: column; gap: 0.25rem; }
  .gw-row { display: flex; gap: 0.25rem; align-items: center; }
  .gw-key, .gw-val, .gw-scope, .gw-ttl {
    background: var(--bg-input, var(--bg-secondary));
    border: 1px solid var(--border-subtle, var(--border));
    border-radius: 4px;
    color: var(--text-primary);
    font-size: 0.7rem;
    padding: 0.2rem 0.35rem;
    font-family: var(--font-mono, monospace);
  }
  .gw-key { width: 38%; }
  .gw-val { flex: 1; }
  .gw-scope { flex: 1; }
  .gw-ttl { width: 3.5rem; }
  .gw-unit { font-size: 0.68rem; color: var(--text-secondary); }
  .gw-icon {
    background: none;
    border: none;
    cursor: pointer;
    font-size: 0.7rem;
    padding: 0.1rem 0.2rem;
    color: var(--text-secondary);
  }
  .gw-icon:hover { color: var(--text-primary); }
  .gw-icon.danger:hover { color: var(--danger, #e55); }
  .gw-actions, .gw-mint-row { display: flex; gap: 0.3rem; align-items: center; flex-wrap: wrap; }
  .gw-btn {
    background: var(--bg-secondary);
    border: 1px solid var(--border-subtle, var(--border));
    border-radius: 4px;
    color: var(--text-secondary);
    cursor: pointer;
    font-size: 0.68rem;
    padding: 0.2rem 0.5rem;
  }
  .gw-btn:hover:not(:disabled) { color: var(--text-primary); border-color: var(--border); }
  .gw-btn.primary { color: var(--accent, var(--text-primary)); }
  .gw-btn.danger:hover:not(:disabled) { color: var(--danger, #e55); }
  .gw-btn:disabled { opacity: 0.5; cursor: default; }
  .gw-mint {
    border-top: 1px solid var(--border-subtle, var(--border));
    padding-top: 0.4rem;
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
  }
  .gw-mint-label { font-size: 0.66rem; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.03em; }
  .gw-token-row { display: flex; gap: 0.25rem; align-items: center; }
  .gw-token {
    flex: 1;
    font-size: 0.64rem;
    word-break: break-all;
    background: var(--bg-secondary);
    padding: 0.2rem 0.35rem;
    border-radius: 4px;
  }
</style>

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
  let ceilingInput = $state(''); // residency ceiling; '' = profile default (no restriction)
  let minting = $state(false);
  let mintedToken = $state('');

  // import state
  let showPaste = $state(false);
  let pasteText = $state('');

  // test-gateway state
  let testing = $state(false);
  let tested = $state(false);
  let testTools = $state<Array<{ name: string; description: string }>>([]);
  let testServers = $state<string[]>([]);

  async function testGateway() {
    testing = true;
    tested = false;
    const a = await getApi();
    if (!a) { testing = false; return; }
    try {
      const res = await a.TestGatewayTools(profile);
      testTools = res?.tools ?? [];
      testServers = res?.servers ?? [];
      tested = true;
      if (testTools.length === 0) {
        notifications.warning(`No tools for ${profile} — check the gateway allowlist (CL_GATEWAY__SERVERS) and that servers can spawn`);
      } else {
        notifications.success(`${profile}: ${testTools.length} gateway tool(s)`);
      }
    } catch (err: any) {
      notifications.error(`Gateway test failed: ${err?.message ?? err}`);
    } finally {
      testing = false;
    }
  }

  async function toggle() {
    open = !open;
    if (open && !loaded) await load();
  }

  // Parse .env text → [key, value] pairs. Handles `export `, blank lines,
  // `#` comments, and single/double-quoted values (with \n \t \" unescaped
  // inside double quotes). Unquoted values are taken verbatim (trimmed).
  function parseDotenv(text: string): Array<[string, string]> {
    const out: Array<[string, string]> = [];
    for (const raw of text.split(/\r?\n/)) {
      let line = raw.trim();
      if (!line || line.startsWith('#')) continue;
      if (line.startsWith('export ')) line = line.slice(7).trim();
      const eq = line.indexOf('=');
      if (eq <= 0) continue;
      const key = line.slice(0, eq).trim();
      if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) continue;
      let value = line.slice(eq + 1).trim();
      if (value.length >= 2 && (value[0] === '"' || value[0] === "'") && value[value.length - 1] === value[0]) {
        const q = value[0];
        value = value.slice(1, -1);
        if (q === '"') value = value.replace(/\\n/g, '\n').replace(/\\t/g, '\t').replace(/\\"/g, '"');
      }
      out.push([key, value]);
    }
    return out;
  }

  // Merge parsed pairs into rows: existing keys are updated in place, new
  // keys appended. Returns {added, updated} for the toast.
  function mergePairs(pairs: Array<[string, string]>): { added: number; updated: number } {
    let added = 0, updated = 0;
    const next = [...rows];
    for (const [key, value] of pairs) {
      const idx = next.findIndex((r) => r.key.trim() === key);
      if (idx >= 0) { next[idx] = { ...next[idx], value }; updated++; }
      else { next.push({ key, value, reveal: false }); added++; }
    }
    rows = next;
    return { added, updated };
  }

  function applyImport(text: string) {
    const pairs = parseDotenv(text);
    if (pairs.length === 0) {
      notifications.warning('No KEY=VALUE lines found to import');
      return;
    }
    const { added, updated } = mergePairs(pairs);
    notifications.success(`Imported ${added} new, updated ${updated} — review and save`);
  }

  async function importFromFile() {
    const a = await getApi();
    if (!a) return;
    try {
      const text = await a.ImportEnvFile();
      if (!text) return; // cancelled
      applyImport(text);
    } catch (err: any) {
      notifications.error(`Import failed: ${err?.message ?? err}`);
    }
  }

  function importFromPaste() {
    applyImport(pasteText);
    pasteText = '';
    showPaste = false;
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
      const tok = await a.MintGatewayToken(profile, scope, Math.round(ttlHours * 3600), ceilingInput);
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
        <button class="gw-btn" onclick={importFromFile} title="Import a .env file (merges into the list)">import .env</button>
        <button class="gw-btn" class:active={showPaste} onclick={() => (showPaste = !showPaste)} title="Paste .env contents">paste</button>
        <button class="gw-btn primary" onclick={save} disabled={saving}>{saving ? 'saving…' : 'save'}</button>
        {#if rows.length > 0}
          <button class="gw-btn danger" onclick={clearAll} disabled={saving}>clear all</button>
        {/if}
      </div>

      {#if showPaste}
        <div class="gw-paste">
          <textarea
            class="gw-paste-area"
            placeholder={"Paste .env lines:\nKEY=value\nexport OTHER=\"quoted value\""}
            bind:value={pasteText}
            spellcheck="false"
            rows="4"
          ></textarea>
          <div class="gw-actions">
            <button class="gw-btn primary" onclick={importFromPaste} disabled={!pasteText.trim()}>merge</button>
            <button class="gw-btn" onclick={() => { showPaste = false; pasteText = ''; }}>cancel</button>
          </div>
          <p class="gw-hint">merges into the list above — existing keys are updated. Review, then <strong>save</strong>.</p>
        </div>
      {/if}

      <div class="gw-test">
        <div class="gw-test-row">
          <div class="gw-mint-label">gateway tools</div>
          <button class="gw-btn" onclick={testGateway} disabled={testing} title="List the tools this profile sees through the gateway">
            {testing ? 'testing…' : 'test gateway'}
          </button>
        </div>
        {#if tested}
          {#if testTools.length === 0}
            <p class="gw-hint">no tools — check the operator allowlist (<code>CL_GATEWAY__SERVERS</code>) and that servers can spawn.</p>
          {:else}
            <ul class="gw-tools">
              {#each testTools as t (t.name)}
                <li class="gw-tool"><code>{t.name}</code>{#if t.description}<span class="gw-tool-desc"> — {t.description}</span>{/if}</li>
              {/each}
            </ul>
            <p class="gw-hint">servers: {testServers.join(', ') || '—'}</p>
          {/if}
        {/if}
      </div>

      <div class="gw-mint">
        <div class="gw-mint-label">mint Tier-0 token</div>
        <div class="gw-mint-row">
          <input class="gw-scope" placeholder="scope (e.g. phantom-brain__*) — blank = all" bind:value={scopeInput} spellcheck="false" />
          <select class="gw-ceiling" bind:value={ceilingInput} title="residency ceiling">
            <option value="">ceiling: default</option>
            <option value="local">local</option>
            <option value="infra">infra</option>
            <option value="vendor">vendor</option>
            <option value="public">public</option>
          </select>
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
    color: var(--text-muted);
    font-size: 0.7rem;
    cursor: pointer;
    padding: 0.2rem 0;
    text-align: left;
    width: 100%;
  }
  .gw-toggle:hover { color: var(--text); }
  .gw-body {
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
    padding: 0.4rem 0 0.2rem;
  }
  .gw-hint {
    font-size: 0.68rem;
    color: var(--text-faint, var(--text-muted));
    margin: 0;
  }
  .gw-hint code { font-size: 0.66rem; }
  .gw-rows { display: flex; flex-direction: column; gap: 0.25rem; }
  .gw-row { display: flex; gap: 0.25rem; align-items: center; }
  .gw-key, .gw-val, .gw-scope, .gw-ttl, .gw-ceiling {
    background: var(--bg-elev);
    border: 1px solid var(--border);
    border-radius: 4px;
    color: var(--text);
    font-size: 0.7rem;
    padding: 0.2rem 0.35rem;
    font-family: var(--font-mono, monospace);
  }
  .gw-key { width: 38%; }
  .gw-val { flex: 1; }
  .gw-scope { flex: 1; }
  .gw-ttl { width: 3.5rem; }
  .gw-unit { font-size: 0.68rem; color: var(--text-muted); }
  .gw-icon {
    background: none;
    border: none;
    cursor: pointer;
    font-size: 0.7rem;
    padding: 0.1rem 0.2rem;
    color: var(--text-muted);
  }
  .gw-icon:hover { color: var(--text); }
  .gw-icon.danger:hover { color: var(--danger, #e55); }
  .gw-actions, .gw-mint-row { display: flex; gap: 0.3rem; align-items: center; flex-wrap: wrap; }
  .gw-btn {
    background: var(--bg-sunken);
    border: 1px solid var(--border);
    border-radius: 4px;
    color: var(--text-muted);
    cursor: pointer;
    font-size: 0.68rem;
    padding: 0.2rem 0.5rem;
  }
  .gw-btn:hover:not(:disabled) { color: var(--text); border-color: var(--border); }
  .gw-btn.primary { color: var(--accent, var(--text)); }
  .gw-btn.danger:hover:not(:disabled) { color: var(--danger, #e55); }
  .gw-btn.active { color: var(--text); border-color: var(--border); }
  .gw-btn:disabled { opacity: 0.5; cursor: default; }
  .gw-paste { display: flex; flex-direction: column; gap: 0.3rem; }
  .gw-paste-area {
    background: var(--bg-elev);
    border: 1px solid var(--border);
    border-radius: 4px;
    color: var(--text);
    font-size: 0.7rem;
    padding: 0.35rem;
    font-family: var(--font-mono, monospace);
    resize: vertical;
    width: 100%;
    box-sizing: border-box;
  }
  .gw-mint, .gw-test {
    border-top: 1px solid var(--border);
    padding-top: 0.4rem;
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
  }
  .gw-mint-label { font-size: 0.66rem; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.03em; }
  .gw-test-row { display: flex; gap: 0.5rem; align-items: center; justify-content: space-between; }
  .gw-tools { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.15rem; }
  .gw-tool { font-size: 0.66rem; }
  .gw-tool code { font-size: 0.66rem; color: var(--accent, var(--text)); }
  .gw-tool-desc { color: var(--text-muted); }
  .gw-token-row { display: flex; gap: 0.25rem; align-items: center; }
  .gw-token {
    flex: 1;
    font-size: 0.64rem;
    word-break: break-all;
    background: var(--bg-sunken);
    padding: 0.2rem 0.35rem;
    border-radius: 4px;
  }
</style>

<script lang="ts">
  import { getApi } from '../utils/api';
  import { notifications } from '../notifications.svelte';
  import CardExpander from './CardExpander.svelte';

  // Per-profile phantom-brain memory binding. Shows whether the profile's
  // long-term memory (Postgres SoR + MinIO archives) is provisioned, and lets
  // the operator initialize it. Provisioning threads CL_BRAIN_* into the
  // profile's credentials server-side. The per-vault bearer tokens can be
  // revealed on demand (operator-gated) for wiring an MCP client to a vault.

  interface VaultToken { vault: string; token: string; is_default: boolean; }

  let { profile }: { profile: string } = $props();

  let loading = $state(false);
  let loaded = $state(false);
  let initializing = $state(false);
  let provisioned = $state(false);
  let bucket = $state('');
  let indexPrefix = $state('');
  let unavailable = $state(false); // brain facade not configured / router unreachable

  // Per-vault bearer tokens — lazily loaded (a secret + a router round-trip),
  // masked by default, revealed/copied per vault on demand.
  let tokensLoaded = $state(false);
  let tokensLoading = $state(false);
  let vaultTokens = $state<VaultToken[]>([]);
  let sessionURL = $state('');
  let revealed = $state<Set<string>>(new Set());

  async function load() {
    loading = true;
    const a = await getApi();
    if (!a) { loading = false; return; }
    try {
      const info = await a.GetBrainProfile(profile);
      provisioned = !!info?.provisioned;
      bucket = info?.bucket ?? '';
      indexPrefix = info?.index_prefix ?? '';
      unavailable = false;
    } catch {
      unavailable = true;
    } finally {
      loading = false;
      loaded = true;
    }
  }

  async function initialize() {
    initializing = true;
    const a = await getApi();
    if (!a) { initializing = false; return; }
    try {
      const res = await a.InitBrainProfile(profile);
      provisioned = !!res?.provisioned;
      bucket = res?.bucket ?? '';
      indexPrefix = res?.index_prefix ?? '';
      unavailable = false;
      notifications.success(
        res?.token_created ? `memory initialized for ${profile}` : `memory already provisioned for ${profile}`,
      );
    } catch (e) {
      notifications.error(`initialize memory failed: ${e}`);
    } finally {
      initializing = false;
    }
  }

  async function loadTokens() {
    tokensLoading = true;
    const a = await getApi();
    if (!a) { tokensLoading = false; return; }
    try {
      const res = await a.GetBrainProfileTokens(profile);
      vaultTokens = res?.tokens ?? [];
      sessionURL = res?.session_url ?? '';
      tokensLoaded = true;
    } catch (e) {
      notifications.error(`load vault tokens failed: ${e}`);
    } finally {
      tokensLoading = false;
    }
  }

  function toggleReveal(vault: string) {
    const next = new Set(revealed);
    if (next.has(vault)) next.delete(vault); else next.add(vault);
    revealed = next;
  }

  async function copyText(text: string, label: string) {
    try {
      await navigator.clipboard.writeText(text);
      notifications.success(`${label} copied`);
    } catch {
      notifications.error('copy failed');
    }
  }

  function mask(t: string): string {
    if (!t) return '';
    return t.length <= 10 ? '••••••••' : `${t.slice(0, 4)}…${t.slice(-4)}`;
  }

  // An MCP server config for THIS vault — one server per vault. The env var
  // names are the SAME for every vault (CL_BRAIN_API_TOKEN etc.); the token is
  // what scopes to (profile, vault). That's why you add one named server per
  // vault (phantom-brain-<vault>) rather than exporting one shared token — a
  // single client process talks to exactly one vault.
  //
  // ``api`` differs by WHERE the client runs: the daemon's session_url is
  // host.docker.internal (reachable only from inside a session container). A
  // host-run client (laptop/desktop Claude Code) can't resolve that — it needs
  // localhost. hostApi() rewrites the one to the other; we surface both so the
  // operator copies the right one instead of hand-editing.
  function mcpConfig(t: VaultToken, api: string): string {
    return JSON.stringify(
      {
        [`phantom-brain-${t.vault}`]: {
          command: 'pbrainctl',
          args: ['client', 'mcp'],
          env: {
            CL_BRAIN_API: api,
            CL_BRAIN_API_TOKEN: t.token,
            CL_WORKSPACE_PROFILE: profile,
            CL_BRAIN_VAULT: t.vault,
          },
        },
      },
      null,
      2,
    );
  }

  // The host-reachable endpoint: host.docker.internal only resolves inside a
  // container, so swap it for localhost for a host-run MCP client.
  function hostApi(): string {
    return sessionURL.replace('host.docker.internal', 'localhost');
  }
</script>

<CardExpander label="memory" count={loaded && provisioned ? '(ready)' : ''}
  hint="this profile's long-term brain memory binding"
  description="Whether this profile's long-term memory (Postgres system-of-record + MinIO archives) is provisioned, and lets you initialize it. Provisioning threads the CL_BRAIN_* config into the profile's credentials server-side; agents recall from it via brain_recall."
  onOpen={() => { if (!loaded) void load(); }}>
  <div class="mem-body">
    {#if loading}
      <p class="mem-hint">loading…</p>
    {:else if unavailable}
      <p class="mem-hint">brain facade not configured — set CL_BRAIN__ADMIN_URL + PB_ADMIN_KEY on the router.</p>
    {:else if provisioned}
      <div class="mem-row"><span class="mem-k">status</span><span class="mem-badge ok">provisioned</span></div>
      <div class="mem-row"><span class="mem-k">archives bucket</span><code>{bucket}</code></div>
      <div class="mem-row"><span class="mem-k">index prefix</span><code>{indexPrefix}</code></div>
      <button class="mem-btn" disabled={initializing} onclick={initialize}>
        {initializing ? 're-provisioning…' : 're-initialize'}
      </button>

      <div class="tok-section">
        {#if !tokensLoaded}
          <button class="mem-btn" disabled={tokensLoading} onclick={loadTokens}>
            {tokensLoading ? 'loading…' : 'show vault tokens'}
          </button>
        {:else}
          {#if sessionURL}
            <div class="mem-row"><span class="mem-k">session url</span><code>{sessionURL}</code></div>
          {/if}
          <p class="mem-hint">Per-vault bearer tokens — the token <em>is</em> the (profile, vault) scope. One MCP server per vault: reveal a token to get a ready-to-paste <code>phantom-brain-&lt;vault&gt;</code> server config in two flavors — <strong>host</strong> (<code>localhost</code>, for laptop/desktop Claude Code) and <strong>in-container</strong> (<code>host.docker.internal</code>, for a session agent). Copy the one matching where the client runs. (The env var names repeat across vaults — the <em>token</em> differs, not the names.) Secret — treat like an API key.</p>
          {#each vaultTokens as t (t.vault)}
            <div class="tok-item">
              <div class="tok-row">
                <span class="tok-vault">{t.vault}{#if t.is_default}<span class="tok-def">default</span>{/if}</span>
                <code class="tok-val">{revealed.has(t.vault) ? t.token : mask(t.token)}</code>
                <button class="tok-x" onclick={() => toggleReveal(t.vault)}>{revealed.has(t.vault) ? 'hide' : 'reveal'}</button>
                <button class="tok-x" onclick={() => copyText(t.token, 'token')}>copy token</button>
              </div>
              {#if revealed.has(t.vault)}
                <div class="tok-env">
                  <div class="tok-env-head">
                    <span>{profile}/{t.vault} — host <span class="tok-note">(localhost, laptop/desktop)</span></span>
                    <button class="tok-x" onclick={() => copyText(mcpConfig(t, hostApi()), 'host MCP config')}>copy config</button>
                  </div>
                  <pre class="tok-pre">{mcpConfig(t, hostApi())}</pre>
                  {#if sessionURL !== hostApi()}
                    <div class="tok-env-head">
                      <span>{profile}/{t.vault} — in-container <span class="tok-note">(session agent)</span></span>
                      <button class="tok-x" onclick={() => copyText(mcpConfig(t, sessionURL), 'in-container MCP config')}>copy config</button>
                    </div>
                    <pre class="tok-pre">{mcpConfig(t, sessionURL)}</pre>
                  {/if}
                </div>
              {/if}
            </div>
          {/each}
        {/if}
      </div>
    {:else}
      <p class="mem-hint">no memory binding yet for this profile.</p>
      <button class="mem-btn primary" disabled={initializing} onclick={initialize}>
        {initializing ? 'initializing…' : 'Initialize memory'}
      </button>
    {/if}
  </div>
</CardExpander>

<style>
  .mem-body { display: flex; flex-direction: column; gap: 8px; padding: 4px 2px; }
  .mem-hint { color: var(--color-text-tertiary); font-size: 0.85em; margin: 0; }
  .mem-row { display: flex; align-items: center; gap: 8px; font-size: 0.85em; }
  .mem-k { color: var(--color-text-secondary); min-width: 110px; }
  .mem-row code { font-family: var(--font-mono); color: var(--color-text-primary); }
  .mem-badge { padding: 1px 6px; border-radius: var(--radius-md, 4px); font-size: 0.8em; }
  .mem-badge.ok { background: var(--color-bg-secondary); color: var(--color-text-primary); }
  .mem-btn {
    align-self: flex-start; padding: 4px 10px; border-radius: var(--radius-md, 4px);
    border: 1px solid var(--color-border-primary); background: var(--color-bg-secondary);
    color: var(--color-text-primary); cursor: pointer; font-size: 0.85em;
  }
  .mem-btn.primary { border-color: var(--card-accent, var(--color-border-primary)); }
  .mem-btn:disabled { opacity: 0.6; cursor: default; }

  .tok-section { display: flex; flex-direction: column; gap: 6px; margin-top: 8px; padding-top: 8px; border-top: 1px solid var(--color-border-primary); }
  .tok-item { display: flex; flex-direction: column; gap: 4px; }
  .tok-row { display: flex; align-items: center; gap: 8px; font-size: 0.85em; }
  .tok-env { display: flex; flex-direction: column; gap: 3px; margin: 2px 0 4px 90px; }
  .tok-env-head { display: flex; align-items: center; justify-content: space-between; font-size: 0.75em; color: var(--color-text-tertiary); margin-top: 4px; }
  .tok-note { color: var(--color-text-tertiary); opacity: 0.75; }
  .tok-pre {
    margin: 0; padding: 6px 8px; border-radius: var(--radius-md, 4px);
    background: var(--color-bg-secondary); border: 1px solid var(--color-border-primary);
    font-family: var(--font-mono); font-size: 0.78em; color: var(--color-text-primary);
    overflow-x: auto; white-space: pre; user-select: all;
  }
  .tok-vault { min-width: 90px; color: var(--color-text-secondary); display: flex; align-items: center; gap: 6px; }
  .tok-def { font-size: 0.7em; padding: 0 4px; border-radius: var(--radius-md, 4px); background: var(--color-bg-secondary); color: var(--color-text-tertiary); }
  .tok-val { flex: 1; font-family: var(--font-mono); color: var(--color-text-primary); overflow-x: auto; white-space: nowrap; }
  .tok-x {
    padding: 2px 8px; border-radius: var(--radius-md, 4px); border: 1px solid var(--color-border-primary);
    background: var(--color-bg-secondary); color: var(--color-text-secondary); cursor: pointer; font-size: 0.8em;
  }
</style>

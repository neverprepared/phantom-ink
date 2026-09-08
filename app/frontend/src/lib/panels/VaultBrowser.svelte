<script lang="ts">
  // Generic browser for a p2p-synced verbatim phantom-brain vault (agents, skills).
  // Lists records via the Go bridge (GetVaultRecords(vault) → GET /api/brain/records
  // on the mesh daemon, Bearer-authed with the per-vault token), reads them, and can
  // delete one (ForgetVaultRecord → tombstone that propagates across the mesh). Because
  // the vault p2p-syncs, this shows records authored on ANY mesh node.
  import { onMount, onDestroy } from 'svelte';
  import { getApi } from '../utils/api';
  import { timeAgo } from '../utils/format';
  import { profileState } from '../stores.svelte';
  import Spinner from '../components/Spinner.svelte';
  import EmptyState from '../components/EmptyState.svelte';

  interface VaultRecord {
    sha: string;
    title: string;
    kind: string;
    body: string;
    topic?: string;
    tags?: string[];
    updated_at: string;
  }

  let { vault }: { vault: string } = $props();

  const DEFAULT_URL = 'http://127.0.0.1:9998';
  const noun = $derived(vault.replace(/s$/, '')); // agents→agent, skills→skill

  // Chip noise filters. Verbatim skills/agents skip the synth gate, so `topic`
  // uniformly defaults to "general" and carries no information — hide it. Same
  // for the self-referential vault tag ("skill" in skills, "agent" in agents).
  function showTopic(t?: string): boolean {
    return !!t && t !== 'general';
  }
  function meaningfulTags(tags?: string[]): string[] {
    return (tags ?? []).filter((t) => t && t !== noun && t !== vault);
  }

  let records = $state<VaultRecord[]>([]);
  let loaded = $state(false);
  let loadError = $state<string | null>(null);
  let needsToken = $state(false);
  let tokenRejected = $state(false); // a token is stored but the daemon returned 401
  let refreshing = $state(false);
  let selectedSha = $state<string | null>(null);
  let tokenInput = $state('');
  let savingToken = $state(false);
  let confirmingDelete = $state(false);
  let deleting = $state(false);
  let query = $state('');
  // copy/move
  let vaultProfiles = $state<string[]>([]);
  let destProfile = $state('');
  let transferring = $state(false);
  let transferMsg = $state<string | null>(null);

  // The vault the browser shows is scoped to the app's ACTIVE profile (the Go
  // bridge resolves that profile's token). Switching profiles must re-query.
  const activeProfile = $derived(profileState.active?.name ?? '');
  const destProfiles = $derived(vaultProfiles.filter((p) => p !== activeProfile));

  let pollHandle: number | undefined;

  onMount(() => {
    void refresh();
    void loadProfiles();
    pollHandle = window.setInterval(() => void refresh(), 15_000);
  });
  onDestroy(() => { if (pollHandle !== undefined) window.clearInterval(pollHandle); });

  // React to a profile switch: reset view state and re-query the new profile.
  $effect(() => {
    activeProfile; // dependency
    selectedSha = null;
    query = '';
    transferMsg = null;
    void refresh();
    void loadProfiles();
  });

  async function loadProfiles() {
    const a = await getApi();
    if (!a) return;
    try {
      vaultProfiles = ((await (a as any).GetVaultProfiles(vault)) as string[]) ?? [];
      if (!destProfiles.includes(destProfile)) destProfile = destProfiles[0] ?? '';
    } catch {
      vaultProfiles = [];
    }
  }

  async function transfer(move: boolean) {
    const a = await getApi();
    if (!a || !selectedSha || !destProfile) return;
    transferring = true;
    transferMsg = null;
    const sha = selectedSha;
    try {
      if (move) await (a as any).MoveVaultRecord(vault, sha, destProfile);
      else await (a as any).CopyVaultRecord(vault, sha, destProfile);
      transferMsg = `${move ? 'Moved' : 'Copied'} to ${destProfile}`;
      if (move) selectedSha = null;
      await refresh();
    } catch (e: any) {
      transferMsg = `Failed: ${e?.message ?? String(e)}`;
    } finally {
      transferring = false;
    }
  }

  async function refresh() {
    const a = await getApi();
    if (!a) { loaded = true; loadError = 'API bindings unavailable'; return; }
    refreshing = true;
    try {
      const list = (await (a as any).GetVaultRecords(vault)) as VaultRecord[];
      records = (list ?? []).slice().sort((x, y) => (y.updated_at ?? '').localeCompare(x.updated_at ?? ''));
      loadError = null;
      needsToken = false;
      tokenRejected = false;
      if (selectedSha && !records.some((r) => r.sha === selectedSha)) selectedSha = null;
      if (!selectedSha && records.length > 0) selectedSha = records[0].sha;
    } catch (e: any) {
      const msg = e?.message ?? String(e);
      const noToken = msg.includes('vault token for profile'); // nothing stored yet
      const rejected = msg.includes('token rejected');         // 401: stored token is wrong/stale
      if (noToken || rejected) {
        // Both cases need the operator to (re)enter a token, so surface the input.
        needsToken = true;
        tokenRejected = rejected;
        loadError = null;
      } else {
        loadError = msg;
      }
    } finally {
      loaded = true;
      refreshing = false;
    }
  }

  async function saveToken() {
    const a = await getApi();
    if (!a || !tokenInput.trim()) return;
    savingToken = true;
    try {
      await (a as any).SetVaultToken(vault, tokenInput.trim());
      tokenInput = '';
      await refresh();
    } catch (e: any) {
      loadError = e?.message ?? String(e);
    } finally {
      savingToken = false;
    }
  }

  async function deleteSelected() {
    const a = await getApi();
    if (!a || !selectedSha) return;
    deleting = true;
    try {
      await (a as any).ForgetVaultRecord(vault, selectedSha);
      confirmingDelete = false;
      selectedSha = null;
      await refresh();
    } catch (e: any) {
      loadError = e?.message ?? String(e);
    } finally {
      deleting = false;
    }
  }

  // Client-side filter over the already-loaded records (title / topic / tags / body).
  const filtered = $derived.by(() => {
    const q = query.trim().toLowerCase();
    if (!q) return records;
    return records.filter((r) =>
      (r.title || '').toLowerCase().includes(q) ||
      (r.topic || '').toLowerCase().includes(q) ||
      (r.tags ?? []).some((t) => t.toLowerCase().includes(q)) ||
      (r.body || '').toLowerCase().includes(q)
    );
  });

  const selected = $derived(records.find((r) => r.sha === selectedSha) ?? null);
  // reset the delete confirm + transfer notice whenever the selection changes
  $effect(() => { selectedSha; confirmingDelete = false; transferMsg = null; });
</script>

<div class="vault">
  <div class="toolbar">
    <p class="sub">Authored {noun} definitions in <strong>{activeProfile || '—'}</strong>'s p2p-synced <code>{vault}</code> vault — scoped to the active profile, synced across every mesh node.</p>
    <div class="toolbar-actions">
      {#if records.length > 0}
        <input
          class="search"
          type="text"
          placeholder="filter {noun}s…"
          bind:value={query}
          spellcheck="false"
        />
        {#if query.trim()}
          <span class="count">{filtered.length} of {records.length}</span>
        {:else}
          <span class="count">{records.length} {noun}{records.length === 1 ? '' : 's'}</span>
        {/if}
      {/if}
      <button class="btn" onclick={() => void refresh()} disabled={refreshing}>
        {refreshing ? 'refreshing…' : 'refresh'}
      </button>
    </div>
  </div>

  {#if !loaded}
    <Spinner />
  {:else if needsToken}
    <section class="card token-setup">
      <h2>{tokenRejected ? 'Re-enter' : 'Connect'} the {vault} vault</h2>
      {#if tokenRejected}
        <p class="token-help">
          The stored <code>{vault}</code>-vault token was <strong>rejected (401)</strong> — it's out
          of sync with the mesh daemon. Paste the current token to replace it.
        </p>
      {/if}
      <p class="token-help">
        Paste the <code>{vault}</code> vault bearer token. It's stored locally and sent as an
        <code>Authorization: Bearer</code> header to the mesh daemon ({DEFAULT_URL}). Same daemon as
        the Brain Mesh integration — only the vault token differs.
      </p>
      <div class="token-row">
        <input
          class="token-field"
          type="password"
          placeholder="{vault}-vault bearer token"
          bind:value={tokenInput}
          onkeydown={(e) => { if (e.key === 'Enter') void saveToken(); }}
        />
        <button class="btn primary" onclick={() => void saveToken()} disabled={savingToken || !tokenInput.trim()}>
          {savingToken ? 'saving…' : 'save & connect'}
        </button>
      </div>
    </section>
  {:else if loadError}
    <EmptyState
      title="{vault} vault unreachable"
      message={`${loadError}\n\nDaemon: ${DEFAULT_URL}. Check the Phantom-Brain Mesh integration and the ${vault}-vault token.`}
    />
  {:else if records.length === 0}
    <EmptyState
      title="No {noun}s yet"
      message={`The ${vault} vault is connected but empty. Authored ${noun} definitions will appear here as they sync across the mesh.`}
    />
  {:else}
    <div class="split">
      <ul class="list">
        {#if filtered.length === 0}
          <li class="no-match">No {noun}s match “{query.trim()}”.</li>
        {:else}
          {#each filtered as r (r.sha)}
            <li>
              <button class="row" class:active={r.sha === selectedSha} onclick={() => (selectedSha = r.sha)}>
                <span class="row-title">{r.title || '(untitled)'}</span>
                <span class="row-meta">
                  {#if showTopic(r.topic)}<span class="chip">{r.topic}</span>{/if}
                  <span class="row-time">{r.updated_at ? timeAgo(Date.parse(r.updated_at)) : ''}</span>
                </span>
              </button>
            </li>
          {/each}
        {/if}
      </ul>

      <section class="reader">
        {#if selected}
          <div class="reader-head">
            <h2>{selected.title || '(untitled)'}</h2>
            <div class="reader-meta">
              {#if showTopic(selected.topic)}<span class="chip">{selected.topic}</span>{/if}
              {#each meaningfulTags(selected.tags) as t}<span class="chip subtle">{t}</span>{/each}
              <span class="reader-sha" title={selected.sha}>{selected.sha.slice(0, 12)}</span>
              {#if confirmingDelete}
                <span class="del-confirm">
                  <span class="del-q">delete?</span>
                  <button class="btn danger" onclick={() => void deleteSelected()} disabled={deleting}>
                    {deleting ? 'deleting…' : 'confirm'}
                  </button>
                  <button class="btn" onclick={() => (confirmingDelete = false)} disabled={deleting}>cancel</button>
                </span>
              {:else}
                <button class="btn del" onclick={() => (confirmingDelete = true)} title="Delete this {noun}">delete</button>
              {/if}
            </div>
            {#if destProfiles.length > 0}
              <div class="transfer">
                <span class="t-label">copy / move to</span>
                <select class="t-select" bind:value={destProfile} disabled={transferring}>
                  {#each destProfiles as p}<option value={p}>{p}</option>{/each}
                </select>
                <button class="btn" onclick={() => void transfer(false)} disabled={transferring || !destProfile}>copy →</button>
                <button class="btn" onclick={() => void transfer(true)} disabled={transferring || !destProfile}>move →</button>
                {#if transferring}<span class="t-msg">working…</span>{/if}
                {#if transferMsg}<span class="t-msg" class:err={transferMsg.startsWith('Failed')}>{transferMsg}</span>{/if}
              </div>
            {/if}
          </div>
          <pre class="body">{selected.body}</pre>
        {:else}
          <div class="reader-empty">Select {vault === 'skills' ? 'a skill' : 'an ' + noun} to read its definition.</div>
        {/if}
      </section>
    </div>
  {/if}
</div>

<style>
  .vault { padding: 0; color: var(--color-text-primary); }
  .toolbar { display: flex; justify-content: space-between; align-items: center; gap: var(--spacing-lg); margin-bottom: var(--spacing-md); }
  .sub { color: var(--color-text-muted); margin: 0; font-size: 0.85rem; max-width: 64ch; }
  .sub code, .token-help code { font-family: var(--font-mono); font-size: 0.82em; }

  .toolbar-actions { display: flex; align-items: center; gap: 0.6rem; white-space: nowrap; }
  .count { font-size: 0.72rem; color: var(--color-text-muted); font-variant-numeric: tabular-nums; }
  .search {
    background: var(--color-bg-primary); border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm); color: var(--color-text-primary);
    font-size: 0.75rem; padding: 0.25rem 0.55rem; width: 200px;
  }
  .search::placeholder { color: var(--color-text-tertiary); }
  .search:focus { outline: none; border-color: var(--color-accent, var(--color-text-secondary)); }
  .no-match { padding: 14px 12px; font-size: 0.78rem; color: var(--color-text-muted); }
  .btn {
    background: var(--color-bg-secondary); border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm); color: var(--color-text-muted);
    cursor: pointer; font-size: 0.72rem; padding: 0.25rem 0.6rem;
  }
  .btn:hover:not(:disabled) { color: var(--color-text-primary); }
  .btn:disabled { opacity: 0.5; cursor: default; }
  .btn.primary { color: var(--color-text-primary); border-color: var(--color-accent, var(--color-border-primary)); }
  .btn.del { color: var(--color-text-tertiary); }
  .btn.del:hover:not(:disabled) { color: var(--color-error); border-color: var(--color-error); }
  .btn.danger { color: #fff; background: var(--color-error); border-color: var(--color-error); }

  .card {
    background: var(--color-bg-secondary); border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-md); padding: var(--spacing-lg);
  }
  h2 { font-size: 0.95rem; margin: 0 0 var(--spacing-md); color: var(--color-text-secondary); }
  .token-help { color: var(--color-text-muted); font-size: 0.82rem; max-width: 64ch; margin: 0 0 var(--spacing-md); }
  .token-row { display: flex; gap: 0.5rem; max-width: 640px; }
  .token-field {
    flex: 1; background: var(--color-bg-primary); border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm); color: var(--color-text-primary); padding: 0.4rem 0.6rem;
    font-family: var(--font-mono); font-size: 0.8rem;
  }

  .split { display: grid; grid-template-columns: minmax(220px, 300px) 1fr; gap: var(--spacing-lg); height: 62vh; min-height: 340px; }
  .list { list-style: none; margin: 0; padding: 0; overflow-y: auto; border: 1px solid var(--color-border-primary); border-radius: var(--radius-md); background: var(--color-bg-secondary); }
  .row {
    display: flex; flex-direction: column; gap: 3px; width: 100%; text-align: left;
    background: transparent; border: none; border-bottom: 1px solid var(--color-border-secondary);
    color: var(--color-text-primary); cursor: pointer; padding: 10px 12px;
  }
  .row:hover { background: var(--color-bg-hover, rgba(255,255,255,0.03)); }
  .row.active { background: var(--color-bg-hover, rgba(255,255,255,0.06)); box-shadow: inset 2px 0 0 var(--color-accent, var(--color-text-secondary)); }
  .row-title { font-size: 0.85rem; font-weight: 500; }
  .row-meta { display: flex; align-items: center; gap: 6px; }
  .row-time { font-size: 0.68rem; color: var(--color-text-tertiary); margin-left: auto; }

  .reader { border: 1px solid var(--color-border-primary); border-radius: var(--radius-md); background: var(--color-bg-secondary); padding: var(--spacing-lg); overflow-y: auto; min-height: 0; }
  .reader-head { margin-bottom: var(--spacing-md); }
  .reader-head h2 { color: var(--color-text-primary); margin-bottom: 6px; }
  .reader-meta { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
  .reader-sha { font-family: var(--font-mono); font-size: 0.7rem; color: var(--color-text-tertiary); margin-left: auto; }
  .del-confirm { display: inline-flex; align-items: center; gap: 6px; }
  .del-q { font-size: 0.72rem; color: var(--color-error); }

  .transfer { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-top: 8px; }
  .t-label { font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--color-text-tertiary); }
  .t-select {
    background: var(--color-bg-primary); border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm); color: var(--color-text-primary); font-size: 0.72rem; padding: 0.2rem 0.4rem;
  }
  .t-msg { font-size: 0.7rem; color: var(--color-text-muted); }
  .t-msg.err { color: var(--color-error); }
  .reader-empty { color: var(--color-text-muted); font-size: 0.85rem; }
  .body { white-space: pre-wrap; word-break: break-word; font-family: var(--font-mono); font-size: 0.8rem; line-height: 1.5; color: var(--color-text-secondary); margin: 0; }

  .chip { font-size: 0.66rem; padding: 1px 7px; border-radius: 999px; border: 1px solid var(--color-border-secondary); color: var(--color-text-muted); }
  .chip.subtle { opacity: 0.75; }
</style>

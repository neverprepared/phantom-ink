<script lang="ts">
  import { getApi } from '../utils/api';
  import { notifications } from '../notifications.svelte';
  import CardExpander from './CardExpander.svelte';

  // Per-profile vault transfer. Export a brain vault to a portable, re-importable
  // folder, or import one back — an idempotent, SHA-deduped union (the merge /
  // portability / archival path). Shells out to the tested `pbrainctl client
  // export|import` via Go (ExportBrainVault / ImportBrainVault), which resolves
  // the vault token + host-reachable brain API and opens a folder picker.

  let { profile }: { profile: string } = $props();

  let loading = $state(false);
  let loaded = $state(false);
  let vaults = $state<string[]>([]);
  let busy = $state(''); // "export:<vault>" | "import:<vault>"

  async function load() {
    loading = true;
    const a = await getApi();
    if (!a) { loading = false; return; }
    try {
      const res = await a.GetBrainProfileTokens(profile);
      vaults = (res?.tokens ?? []).map((t) => t.vault);
    } catch {
      // best-effort: an unprovisioned profile has no tokens
    } finally {
      loading = false;
      loaded = true;
    }
  }

  async function transfer(kind: 'export' | 'import', vault: string) {
    busy = `${kind}:${vault}`;
    const a = await getApi();
    if (!a) { busy = ''; return; }
    try {
      const msg = kind === 'export'
        ? await a.ExportBrainVault(profile, vault)
        : await a.ImportBrainVault(profile, vault);
      if (msg) notifications.success(msg);          // tool summary line
      // empty msg = the user cancelled the folder picker; stay quiet
    } catch (e) {
      notifications.error(`${kind} ${vault} failed: ${e}`);
    } finally {
      busy = '';
    }
  }
</script>

<CardExpander label="vault transfer"
  hint="export / import a brain vault (portable, SHA-deduped)"
  description="Export a brain vault to a portable, re-importable folder, or import one back as an idempotent, SHA-deduped union — the migration and archival path for moving memory/skills across daemons or hosts."
  onOpen={() => { if (!loaded) void load(); }}>
  <div class="vt-body">
    {#if loading}
      <p class="vt-hint">loading…</p>
    {:else if vaults.length === 0}
      <p class="vt-hint">no vaults — initialize memory for this profile first.</p>
    {:else}
      <p class="vt-hint">Export a vault to a portable folder, or import one back — import is an idempotent, SHA-deduped <strong>union</strong> (safe to merge, not destructive).</p>
      {#each vaults as v (v)}
        <div class="vt-row">
          <span class="vt-vault">{v}</span>
          <button class="vt-btn" disabled={!!busy} onclick={() => transfer('export', v)}>
            {busy === `export:${v}` ? 'exporting…' : 'export'}
          </button>
          <button class="vt-btn" disabled={!!busy} onclick={() => transfer('import', v)}>
            {busy === `import:${v}` ? 'importing…' : 'import (merge)'}
          </button>
        </div>
      {/each}
    {/if}
  </div>
</CardExpander>

<style>
  .vt-body { display: flex; flex-direction: column; gap: 8px; padding: 4px 2px; }
  .vt-hint { color: var(--color-text-tertiary); font-size: 0.85em; margin: 0; }
  .vt-row { display: flex; align-items: center; gap: 8px; font-size: 0.85em; }
  .vt-vault { min-width: 90px; color: var(--color-text-secondary); font-family: var(--font-mono); }
  .vt-btn {
    padding: 4px 10px; border-radius: var(--radius-md, 4px);
    border: 1px solid var(--color-border-primary); background: var(--color-bg-secondary);
    color: var(--color-text-primary); cursor: pointer; font-size: 0.85em;
  }
  .vt-btn:disabled { opacity: 0.6; cursor: default; }
</style>

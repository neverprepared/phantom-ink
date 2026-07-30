<script lang="ts">
  import { getApi } from '../utils/api';
  import { notifications } from '../notifications.svelte';
  import CardExpander from './CardExpander.svelte';

  // Per-profile phantom-brain memory binding. Shows whether the profile's
  // long-term memory (Postgres SoR + MinIO archives) is provisioned, and lets
  // the operator initialize it. Provisioning threads CL_BRAIN_* into the
  // profile's credentials server-side — the bearer token never reaches the UI.

  let { profile }: { profile: string } = $props();

  let loading = $state(false);
  let loaded = $state(false);
  let initializing = $state(false);
  let provisioned = $state(false);
  let bucket = $state('');
  let indexPrefix = $state('');
  let unavailable = $state(false); // brain facade not configured / router unreachable

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
</script>

<CardExpander label="memory" count={loaded && provisioned ? '(ready)' : ''} onOpen={() => { if (!loaded) void load(); }}>
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
</style>

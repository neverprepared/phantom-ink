<script lang="ts">
  import { getApi } from '../utils/api';
  import { notifications } from '../notifications.svelte';
  import CardExpander from './CardExpander.svelte';

  // Per-profile credential bundle: pick which credential sources (aws, kube,
  // git, … + custom) get captured from this machine and synced to MinIO for
  // gateway/session materialization. Everything defaults OFF — explicit
  // opt-in. "Sync now" captures + uploads without an image rebuild.

  let { profile }: { profile: string } = $props();

  type Row = {
    name: string;
    label: string;
    kind: string; // catalog | custom
    audience: string; // gateway | session | both
    enabled: boolean;
    detected: boolean;
    definition: string;
  };

  type Meta = {
    etag: string;
    captured_at: string;
    app_version: string;
    size: number;
  } | null;

  let loaded = $state(false);
  let loading = $state(false);
  let rows = $state<Row[]>([]);
  let meta = $state<Meta>(null);
  let busy = $state<Record<string, boolean>>({});
  let syncing = $state(false);

  // Add-custom-source form.
  let showForm = $state(false);
  let formName = $state('');
  let formGlobs = $state('');
  let formAudience = $state('session');
  let formEnvMap = $state('');

  let onCount = $derived(rows.filter((r) => r.enabled).length);

  async function load() {
    loading = true;
    const a = await getApi();
    if (!a) { loading = false; return; }
    try {
      rows = ((await a.ListBundleSources(profile)) ?? []) as Row[];
      meta = (await a.GetProfileBundleMeta(profile)) as Meta;
      loaded = true;
    } catch (err: any) {
      notifications.error(`Failed to load bundle sources: ${err?.message ?? err}`);
    } finally {
      loading = false;
    }
  }

  async function flip(r: Row) {
    busy[r.name] = true;
    const a = await getApi();
    if (!a) { busy[r.name] = false; return; }
    try {
      await a.SetBundleSourceEnabled(profile, r.name, !r.enabled);
      r.enabled = !r.enabled;
    } catch (err: any) {
      notifications.error(`Failed to toggle ${r.name}: ${err?.message ?? err}`);
    } finally {
      busy[r.name] = false;
    }
  }

  async function removeCustom(r: Row) {
    busy[r.name] = true;
    const a = await getApi();
    if (!a) { busy[r.name] = false; return; }
    try {
      await a.DeleteBundleSource(profile, r.name);
      rows = rows.filter((x) => x.name !== r.name);
    } catch (err: any) {
      notifications.error(`Failed to remove ${r.name}: ${err?.message ?? err}`);
    } finally {
      busy[r.name] = false;
    }
  }

  async function addCustom() {
    const a = await getApi();
    if (!a) return;
    const globs = formGlobs.split('\n').map((s) => s.trim()).filter(Boolean);
    const envMap: Record<string, string> = {};
    for (const line of formEnvMap.split('\n')) {
      const idx = line.indexOf('=');
      if (idx > 0) envMap[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
    }
    const def = JSON.stringify({ globs, audience: formAudience, env_map: envMap });
    try {
      await a.SaveCustomBundleSource(profile, formName.trim(), def);
      notifications.success(`Added ${formName.trim()}`);
      showForm = false;
      formName = ''; formGlobs = ''; formEnvMap = ''; formAudience = 'session';
      await load();
    } catch (err: any) {
      notifications.error(`Failed to add source: ${err?.message ?? err}`);
    }
  }

  async function syncNow() {
    syncing = true;
    const a = await getApi();
    if (!a) { syncing = false; return; }
    try {
      const res = await a.SyncProfileBundleNow(profile);
      notifications.success(`Bundle synced: ${res.sources?.join(', ') ?? ''}`);
      meta = (await a.GetProfileBundleMeta(profile)) as Meta;
    } catch (err: any) {
      notifications.error(`Bundle sync failed: ${err?.message ?? err}`);
    } finally {
      syncing = false;
    }
  }

  async function removeBundle() {
    if (!window.confirm(`Delete the stored credential bundle for ${profile}?`)) return;
    const a = await getApi();
    if (!a) return;
    try {
      await a.DeleteProfileBundle(profile);
      meta = null;
      notifications.success('Bundle deleted');
    } catch (err: any) {
      notifications.error(`Delete failed: ${err?.message ?? err}`);
    }
  }

  function fmtSynced(m: Meta): string {
    if (!m) return 'never synced';
    const t = m.captured_at ? new Date(m.captured_at).toLocaleString() : '';
    return `synced ${t}`;
  }
</script>

<CardExpander
  label="credential bundle"
  count={loaded && rows.length ? `(${onCount} on)` : ''}
  onOpen={() => { if (!loaded) void load(); }}
>
  <div class="cbe-body">
    {#if loading}
      <p class="cbe-hint">loading…</p>
    {:else}
      <ul class="cbe-list">
        {#each rows as r (r.name)}
          <li class="cbe-row" class:on={r.enabled}>
            <button
              class="cbe-switch"
              class:on={r.enabled}
              onclick={() => flip(r)}
              disabled={busy[r.name]}
              role="switch"
              aria-checked={r.enabled}
              aria-label="Toggle {r.name} bundle source for {profile}"
            ><span class="cbe-knob"></span></button>
            <span class="cbe-dot" class:found={r.detected} title={r.detected ? 'files found on this machine' : 'no files found'}></span>
            <span class="cbe-name">{r.label}</span>
            <span class="cbe-aud aud-{r.audience}">{r.audience}</span>
            {#if r.kind === 'custom'}
              <button class="cbe-remove" onclick={() => removeCustom(r)} title="remove custom source">×</button>
            {/if}
          </li>
        {/each}
      </ul>

      {#if showForm}
        <div class="cbe-form">
          <input class="cbe-input" placeholder="source name (e.g. snowflake)" bind:value={formName} />
          <textarea class="cbe-input mono" rows="2" placeholder={'globs, one per line\n$WORKSPACE_HOME/.snowsql/config'} bind:value={formGlobs}></textarea>
          <select class="cbe-input" bind:value={formAudience}>
            <option value="session">session (containers only)</option>
            <option value="gateway">gateway (API host only)</option>
            <option value="both">both</option>
          </select>
          <textarea class="cbe-input mono" rows="2" placeholder={'env mappings, VAR=relative/path (optional)\nSNOWSQL_CONFIG=snowflake/config'} bind:value={formEnvMap}></textarea>
          <div class="cbe-form-actions">
            <button class="cbe-btn" onclick={addCustom} disabled={!formName.trim() || !formGlobs.trim()}>add</button>
            <button class="cbe-btn ghost" onclick={() => (showForm = false)}>cancel</button>
          </div>
        </div>
      {:else}
        <button class="cbe-add" onclick={() => (showForm = true)}>+ add custom source</button>
      {/if}

      <div class="cbe-footer">
        <span class="cbe-hint">{fmtSynced(meta)}</span>
        <span class="cbe-spacer"></span>
        {#if meta}
          <button class="cbe-btn ghost" onclick={removeBundle}>delete bundle</button>
        {/if}
        <button class="cbe-btn" onclick={syncNow} disabled={syncing || onCount === 0}>
          {syncing ? 'syncing…' : 'sync now'}
        </button>
      </div>
      <p class="cbe-hint">captured from this machine, encrypted, and stored in MinIO; the gateway (and future sessions) materialize it per profile. profile env vars always override bundle mappings.</p>
    {/if}
  </div>
</CardExpander>

<style>
  .cbe-body { display: flex; flex-direction: column; gap: 0.4rem; padding: 0.3rem 0; }
  .cbe-hint { font-size: 0.66rem; color: var(--text-muted); margin: 0; }
  .cbe-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.25rem; }
  .cbe-row {
    display: flex; align-items: center; gap: 0.5rem;
    padding: 0.25rem 0.4rem; border-radius: var(--r-sm);
  }
  .cbe-row.on { background: var(--accent-soft); }
  .cbe-switch {
    width: 26px; height: 15px; border-radius: 8px; border: 1px solid var(--border);
    background: var(--bg-sunken); position: relative; cursor: pointer; padding: 0; flex: none;
  }
  .cbe-switch.on { background: var(--accent); border-color: var(--accent); }
  .cbe-knob {
    position: absolute; top: 1px; left: 1px; width: 11px; height: 11px;
    border-radius: 50%; background: var(--bg); transition: transform 0.12s ease;
  }
  .cbe-switch.on .cbe-knob { transform: translateX(11px); }
  .cbe-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--border); flex: none; }
  .cbe-dot.found { background: var(--ok, #3fb950); }
  .cbe-name { font-size: 0.72rem; }
  .cbe-aud {
    font-family: var(--font-mono); font-size: 0.6rem; color: var(--text-muted);
    border: 1px solid var(--border); border-radius: 3px; padding: 0 4px;
  }
  .cbe-remove {
    margin-left: auto; background: transparent; border: none; color: var(--text-muted);
    cursor: pointer; font-size: 0.8rem; padding: 0 4px; border-radius: 3px;
  }
  .cbe-remove:hover { color: var(--fail); background: var(--fail-soft); }
  .cbe-add {
    align-self: flex-start; background: transparent; border: 1px dashed var(--border);
    color: var(--text-muted); font-size: 0.66rem; padding: 2px 8px;
    border-radius: var(--r-sm); cursor: pointer;
  }
  .cbe-add:hover { color: var(--text); border-color: var(--text-muted); }
  .cbe-form { display: flex; flex-direction: column; gap: 0.3rem; }
  .cbe-input {
    background: var(--bg-sunken); border: 1px solid var(--border); color: var(--text);
    border-radius: var(--r-sm); padding: 4px 8px; font-size: 0.7rem;
  }
  .cbe-input.mono { font-family: var(--font-mono); font-size: 0.66rem; }
  .cbe-form-actions { display: flex; gap: 0.4rem; }
  .cbe-btn {
    background: var(--accent-soft); border: 1px solid var(--accent); color: var(--accent);
    font-size: 0.66rem; padding: 2px 10px; border-radius: var(--r-sm); cursor: pointer;
  }
  .cbe-btn:disabled { opacity: 0.5; cursor: default; }
  .cbe-btn.ghost { background: transparent; border-color: var(--border); color: var(--text-muted); }
  .cbe-btn.ghost:hover { color: var(--fail); border-color: var(--fail); }
  .cbe-footer { display: flex; align-items: center; gap: 0.4rem; }
  .cbe-spacer { flex: 1; }
</style>

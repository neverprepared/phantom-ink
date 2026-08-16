<script lang="ts">
  import { getApi } from '../utils/api';
  import { notifications } from '../notifications.svelte';
  import CardExpander from './CardExpander.svelte';

  // Per-profile MCP gateway server toggles. Each gateway-enabled server has a
  // default (seeded by the residency resolution: its trust zone ≤ the profile
  // ceiling); the user toggles it on/off to override — a judgement call about
  // which servers this profile may reach. Styled like the global Gateway panel.

  let { profile }: { profile: string } = $props();

  type Row = {
    name: string;
    zone: string;
    default_enabled: boolean;
    override: boolean | null;
    effective: boolean;
  };

  let loaded = $state(false);
  let loading = $state(false);
  let rows = $state<Row[]>([]);
  let busy = $state<Record<string, boolean>>({});

  let onCount = $derived(rows.filter((r) => r.effective).length);

  async function load() {
    loading = true;
    const a = await getApi();
    if (!a) { loading = false; return; }
    try {
      rows = ((await a.GetProfileServers(profile)) ?? []).map((s: any) => ({
        name: s.name,
        zone: s.zone,
        default_enabled: s.default_enabled,
        override: s.override ?? null,
        effective: s.effective,
      }));
      loaded = true;
    } catch (err: any) {
      notifications.error(`Failed to load servers: ${err?.message ?? err}`);
    } finally {
      loading = false;
    }
  }

  async function flip(r: Row) {
    const next = !r.effective;
    busy[r.name] = true;
    const a = await getApi();
    if (!a) { busy[r.name] = false; return; }
    try {
      await a.SetProfileServerOverride(profile, r.name, next);
      r.effective = next;
      r.override = next;
    } catch (err: any) {
      notifications.error(`Failed to toggle ${r.name}: ${err?.message ?? err}`);
    } finally {
      busy[r.name] = false;
    }
  }

  async function reset(r: Row) {
    busy[r.name] = true;
    const a = await getApi();
    if (!a) { busy[r.name] = false; return; }
    try {
      await a.ClearProfileServerOverride(profile, r.name);
      r.override = null;
      r.effective = r.default_enabled;
    } catch (err: any) {
      notifications.error(`Failed to reset ${r.name}: ${err?.message ?? err}`);
    } finally {
      busy[r.name] = false;
    }
  }
</script>

<CardExpander
  label="gateway servers"
  hint="toggle which MCP servers this profile may reach"
  description="Enable or disable each gateway MCP server for this profile. Defaults come from residency resolution (a server's trust zone must sit within the profile's ceiling); toggling overrides that. A server also needs its credentials set under gateway secrets to actually connect."
  count={loaded && rows.length ? `(${onCount}/${rows.length})` : ''}
  onOpen={() => { if (!loaded) void load(); }}
>
  <div class="pse-body">
    {#if loading}
      <p class="pse-hint">loading…</p>
    {:else if rows.length === 0}
      <p class="pse-hint">no servers enabled in the gateway.</p>
    {:else}
      <ul class="pse-list">
        {#each rows as r (r.name)}
          <li class="pse-row" class:on={r.effective}>
            <button
              class="pse-switch"
              class:on={r.effective}
              onclick={() => flip(r)}
              disabled={busy[r.name]}
              role="switch"
              aria-checked={r.effective}
              aria-label="Toggle {r.name} for {profile}"
            ><span class="pse-knob"></span></button>
            <span class="pse-name">{r.name}</span>
            <span class="pse-zone zone-{r.zone}">{r.zone}</span>
            {#if r.override !== null}
              <button class="pse-reset" onclick={() => reset(r)} title="revert to default ({r.default_enabled ? 'on' : 'off'})">reset</button>
            {/if}
          </li>
        {/each}
      </ul>
      <p class="pse-hint">default from residency zone; toggle to override.</p>
    {/if}
  </div>
</CardExpander>

<style>
  .pse-body { display: flex; flex-direction: column; gap: 0.4rem; padding: 0.3rem 0; }
  .pse-hint { font-size: 0.66rem; color: var(--text-muted); margin: 0; }
  .pse-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.25rem; }
  .pse-row {
    display: flex; align-items: center; gap: 0.5rem;
    padding: 0.35rem 0.5rem; border: 1px solid var(--border);
    border-radius: 6px; background: var(--bg-sunken);
  }
  .pse-row.on { border-color: var(--accent-line); }
  .pse-name { flex: 1; font-size: 0.74rem; color: var(--text); min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .pse-zone { font-size: 0.6rem; padding: 0.05rem 0.35rem; border-radius: 3px; font-family: var(--font-mono, monospace); color: var(--text); }
  .zone-local  { background: color-mix(in srgb, #4caf72 24%, transparent); }
  .zone-infra  { background: color-mix(in srgb, #5a96dc 24%, transparent); }
  .zone-vendor { background: color-mix(in srgb, #dcaa46 24%, transparent); }
  .zone-public { background: color-mix(in srgb, #d25a5a 24%, transparent); }
  .pse-reset { background: none; border: none; color: var(--text-muted); font-size: 0.62rem; cursor: pointer; padding: 0 0.2rem; }
  .pse-reset:hover { color: var(--text); }
  .pse-switch {
    flex: none; width: 32px; height: 18px; border-radius: 999px;
    border: 1px solid var(--border-strong); background: var(--bg-elev);
    position: relative; cursor: pointer; padding: 0; transition: background 0.15s, border-color 0.15s;
  }
  .pse-switch.on { background: var(--accent); border-color: var(--accent); }
  .pse-switch:disabled { opacity: 0.5; cursor: default; }
  .pse-knob {
    position: absolute; top: 1px; left: 1px; width: 14px; height: 14px;
    border-radius: 50%; background: #fff; transition: transform 0.15s;
  }
  .pse-switch.on .pse-knob { transform: translateX(14px); }
</style>

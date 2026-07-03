<script lang="ts">
  import { getApi } from '../utils/api';
  import { notifications } from '../notifications.svelte';

  // Declarative-orchestration trust editor (per profile). Manages the trust map
  // (destination → zone) + default residency ceiling, shows how providers/tools
  // classify, and previews a resolved step plan. Operator-only.

  let { profile }: { profile: string } = $props();

  const ZONES = ['local', 'infra', 'vendor', 'public'];

  let open = $state(false);
  let loaded = $state(false);
  let loading = $state(false);

  let defaultCeiling = $state('public');
  type Rule = { pattern: string; zone: string };
  let rules = $state<Rule[]>([]);
  let newPattern = $state('');
  let newZone = $state('infra');

  // zones classification
  let providers = $state<Array<{ name: string; zone: string }>>([]);
  let tools = $state<Array<{ name: string; zone: string }>>([]);

  // plan preview
  let planCeiling = $state('');
  let planRequires = $state('');
  let planPrefers = $state('');
  let plan = $state<any | null>(null);
  let planning = $state(false);

  async function toggle() {
    open = !open;
    if (open && !loaded) await load();
  }

  async function load() {
    loading = true;
    const a = await getApi();
    if (!a) { loading = false; return; }
    try {
      const t = await a.GetTrust(profile);
      defaultCeiling = t?.default_ceiling ?? 'public';
      rules = (t?.rules ?? []).map((r: any) => ({ pattern: r.pattern, zone: r.zone }));
      const z = await a.GetOrchestrationZones(profile);
      providers = (z?.providers ?? []).map((p: any) => ({ name: p.name, zone: p.zone }));
      tools = (z?.tools ?? []).map((tt: any) => ({ name: tt.name, zone: tt.zone }));
      loaded = true;
    } catch (err: any) {
      notifications.error(`Failed to load trust config: ${err?.message ?? err}`);
    } finally {
      loading = false;
    }
  }

  async function setCeiling(zone: string) {
    const a = await getApi();
    if (!a) return;
    try {
      await a.SetDefaultCeiling(profile, zone);
      defaultCeiling = zone;
      notifications.success(`${profile} default ceiling → ${zone}`);
      await refreshZones();
    } catch (err: any) {
      notifications.error(`Failed: ${err?.message ?? err}`);
    }
  }

  async function addRule() {
    const p = newPattern.trim();
    if (!p) return;
    const a = await getApi();
    if (!a) return;
    try {
      await a.SetTrustRule(profile, p, newZone);
      const i = rules.findIndex((r) => r.pattern === p);
      if (i >= 0) rules[i] = { pattern: p, zone: newZone };
      else rules = [...rules, { pattern: p, zone: newZone }];
      newPattern = '';
      notifications.success(`rule ${p} → ${newZone}`);
      await refreshZones();
    } catch (err: any) {
      notifications.error(`Failed: ${err?.message ?? err}`);
    }
  }

  async function removeRule(pattern: string) {
    const a = await getApi();
    if (!a) return;
    try {
      await a.DeleteTrustRule(profile, pattern);
      rules = rules.filter((r) => r.pattern !== pattern);
      await refreshZones();
    } catch (err: any) {
      notifications.error(`Failed: ${err?.message ?? err}`);
    }
  }

  async function refreshZones() {
    const a = await getApi();
    if (!a) return;
    try {
      const z = await a.GetOrchestrationZones(profile);
      providers = (z?.providers ?? []).map((p: any) => ({ name: p.name, zone: p.zone }));
      tools = (z?.tools ?? []).map((tt: any) => ({ name: tt.name, zone: tt.zone }));
    } catch { /* leave prior */ }
  }

  async function preview() {
    planning = true;
    plan = null;
    const a = await getApi();
    if (!a) { planning = false; return; }
    const requires = planRequires.split(',').map((s) => s.trim()).filter(Boolean);
    const prefers = planPrefers.split(',').map((s) => s.trim()).filter(Boolean);
    try {
      plan = await a.PreviewPlan(profile, planCeiling, requires, prefers);
    } catch (err: any) {
      notifications.error(`Plan failed: ${err?.message ?? err}`);
    } finally {
      planning = false;
    }
  }

  function zoneClass(z: string) {
    return `zone zone-${z}`;
  }
</script>

<button class="te-toggle" onclick={toggle}>
  {open ? '▾' : '▸'} trust & residency
</button>

{#if open}
  <div class="te-body">
    {#if loading}
      <p class="te-hint">loading…</p>
    {:else}
      <!-- default ceiling -->
      <div class="te-row">
        <span class="te-label">default ceiling</span>
        <select value={defaultCeiling} onchange={(e) => setCeiling((e.target as HTMLSelectElement).value)}>
          {#each ZONES as z}<option value={z}>{z}</option>{/each}
        </select>
      </div>

      <!-- trust rules -->
      <div class="te-section-label">trust map (destination → zone)</div>
      {#each rules as r (r.pattern)}
        <div class="te-rule">
          <code class="te-pat">{r.pattern}</code>
          <span class={zoneClass(r.zone)}>{r.zone}</span>
          <button class="te-x" onclick={() => removeRule(r.pattern)} aria-label="remove {r.pattern}">✕</button>
        </div>
      {/each}
      <div class="te-rule add">
        <input class="te-pat-input" placeholder="*.corp.internal" bind:value={newPattern} spellcheck="false" />
        <select bind:value={newZone}>{#each ZONES as z}<option value={z}>{z}</option>{/each}</select>
        <button class="te-btn" onclick={addRule} disabled={!newPattern.trim()}>+ rule</button>
      </div>

      <!-- classification -->
      <div class="te-section-label">classification (zone ≤ ceiling = usable)</div>
      <div class="te-chips">
        {#each providers as p}<span class={zoneClass(p.zone)} title="provider">{p.name}·{p.zone}</span>{/each}
        {#each tools as t}<span class={zoneClass(t.zone)} title="tool">{t.name}·{t.zone}</span>{/each}
        {#if providers.length === 0 && tools.length === 0}<span class="te-hint">nothing enabled</span>{/if}
      </div>

      <!-- plan preview -->
      <div class="te-section-label">plan preview</div>
      <div class="te-rule">
        <select bind:value={planCeiling} title="ceiling (blank = default)">
          <option value="">default</option>{#each ZONES as z}<option value={z}>{z}</option>{/each}
        </select>
        <input class="te-pat-input" placeholder="requires (coding)" bind:value={planRequires} spellcheck="false" />
        <input class="te-pat-input" placeholder="prefers (cheap)" bind:value={planPrefers} spellcheck="false" />
        <button class="te-btn" onclick={preview} disabled={planning}>{planning ? '…' : 'resolve'}</button>
      </div>
      {#if plan}
        <div class="te-plan" class:blocked={plan.blocked}>
          {#if plan.blocked}
            <strong>BLOCKED</strong> — {plan.reason}
          {:else}
            provider <strong>{plan.provider?.name}</strong> <span class={zoneClass(plan.provider?.zone)}>{plan.provider?.zone}</span>
            · ceiling {plan.ceiling} · tools: {plan.eligible_tools?.join(', ') || '—'}
            {#if plan.excluded_tools?.length}<div class="te-hint">excluded: {plan.excluded_tools.map((t: any) => `${t.name}(${t.zone})`).join(', ')}</div>{/if}
          {/if}
        </div>
      {/if}
    {/if}
  </div>
{/if}

<style>
  .te-toggle { background: none; border: none; color: var(--text-secondary); font-size: 0.7rem; cursor: pointer; padding: 0.2rem 0; text-align: left; width: 100%; }
  .te-toggle:hover { color: var(--text-primary); }
  .te-body { display: flex; flex-direction: column; gap: 0.4rem; padding: 0.3rem 0; }
  .te-hint { font-size: 0.66rem; color: var(--text-secondary); margin: 0; }
  .te-label { font-size: 0.7rem; color: var(--text-secondary); }
  .te-row { display: flex; align-items: center; gap: 0.5rem; }
  .te-section-label { font-size: 0.62rem; text-transform: uppercase; letter-spacing: 0.03em; color: var(--text-secondary); margin-top: 0.2rem; }
  .te-rule { display: flex; align-items: center; gap: 0.3rem; flex-wrap: wrap; }
  .te-pat { font-size: 0.68rem; flex: 1; }
  .te-pat-input, .te-rule select, .te-row select {
    background: var(--bg-input, var(--bg-secondary)); border: 1px solid var(--border-subtle, var(--border));
    border-radius: 4px; color: var(--text-primary); font-size: 0.68rem; padding: 0.15rem 0.3rem; font-family: var(--font-mono, monospace);
  }
  .te-pat-input { flex: 1; min-width: 5rem; }
  .te-btn, .te-x { background: var(--bg-secondary); border: 1px solid var(--border-subtle, var(--border)); border-radius: 4px; color: var(--text-secondary); cursor: pointer; font-size: 0.66rem; padding: 0.15rem 0.4rem; }
  .te-btn:hover:not(:disabled), .te-x:hover { color: var(--text-primary); }
  .te-btn:disabled { opacity: 0.5; cursor: default; }
  .te-chips { display: flex; flex-wrap: wrap; gap: 0.25rem; }
  .zone { font-size: 0.62rem; padding: 0.05rem 0.35rem; border-radius: 3px; font-family: var(--font-mono, monospace); }
  .zone-local  { background: rgba(80,180,120,0.22); }
  .zone-infra  { background: rgba(90,150,220,0.22); }
  .zone-vendor { background: rgba(220,170,70,0.22); }
  .zone-public { background: rgba(210,90,90,0.22); }
  .te-plan { font-size: 0.68rem; padding: 0.3rem 0.4rem; border-radius: 4px; background: var(--bg-secondary); }
  .te-plan.blocked { background: rgba(210,90,90,0.15); }
</style>

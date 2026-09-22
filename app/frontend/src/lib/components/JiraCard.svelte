<script lang="ts">
  // Jira is a SaaS integration: there is no compose stack to place, so unlike
  // IntegrationsCard (ADR-003) "enabled" means credentials work.
  //
  // Credentials are PER PROFILE and live in that profile's gateway env
  // (JIRA_URL / JIRA_USERNAME / JIRA_API_TOKEN) — the same vars mcp-atlassian
  // consumes, so a profile is configured once for both. This card deliberately
  // does NOT write them: SetGatewayEnv is a full overwrite and a blank secret
  // field would clobber a real token. Editing happens in the gateway env
  // editor; here we read, report, and toggle.
  import { getApi } from '../utils/api';
  import { notifications } from '../notifications.svelte';
  import { profileState } from '../stores.svelte';
  import type { main } from '../../../wailsjs/go/models';

  let expanded = $state(false);
  let loading = $state(false);
  let enabled = $state(false);
  let testing = $state('');
  let rows = $state<main.JiraProfileStatus[]>([]);

  let configuredCount = $derived(rows.filter((r) => r.configured).length);
  let liveCount = $derived(rows.filter((r) => r.configured && r.opted_in).length);

  async function load() {
    loading = true;
    const a = await getApi();
    if (!a) { loading = false; return; }
    try {
      enabled = await a.JiraGloballyEnabled();
      const next: main.JiraProfileStatus[] = [];
      for (const p of profileState.visible) {
        try {
          next.push(await a.JiraStatus(p.name));
        } catch {
          // One profile's env being unreachable must not blank the others.
          next.push({ profile: p.name, configured: false, opted_in: false, url: '', username: '' } as main.JiraProfileStatus);
        }
      }
      rows = next;
    } catch (e: any) {
      notifications.error(`Failed to load Jira status: ${e?.message ?? e}`);
    } finally {
      loading = false;
    }
  }

  function toggle() {
    expanded = !expanded;
    if (expanded && rows.length === 0) void load();
  }

  async function setEnabled(on: boolean) {
    const a = await getApi();
    if (!a) return;
    try {
      await a.SetJiraEnabled(on);
      enabled = on;
      notifications.success(`Jira integration ${on ? 'enabled' : 'disabled'}`);
    } catch (e: any) {
      notifications.error(`${e?.message ?? e}`);
      await load();
    }
  }

  async function setOptIn(profile: string, on: boolean) {
    const a = await getApi();
    if (!a) return;
    try {
      await a.SetJiraEnabledForProfile(profile, on);
      rows = rows.map((r) => (r.profile === profile ? { ...r, opted_in: on } : r));
      notifications.success(`${profile}: Jira ${on ? 'on' : 'off'}`);
    } catch (e: any) {
      notifications.error(`${profile}: ${e?.message ?? e}`);
    }
  }

  async function test(profile: string) {
    testing = profile;
    const a = await getApi();
    if (!a) { testing = ''; return; }
    try {
      await a.VerifyJira(profile);
      notifications.success(`${profile}: Jira credentials verified`);
    } catch (e: any) {
      notifications.error(`${profile}: ${e?.message ?? e}`);
    } finally {
      testing = '';
    }
  }
</script>

<div class="service-card jira-card">
  <div class="card-top">
    <button class="card-identity" onclick={toggle}>
      <svg class="expand-chevron" class:expanded xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg>
      <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 11l3 3 8-8"/><path d="M20 12v6a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h9"/></svg>
      <span class="svc-name">Jira</span>
      {#if expanded}
        <span class="svc-status">{enabled ? `on · ${liveCount}/${rows.length} profiles` : 'off'}</span>
      {/if}
    </button>
    {#if expanded}
      <button class="btn ghost sm" onclick={load} disabled={loading}>refresh</button>
    {/if}
  </div>

  {#if expanded}
    <div class="jira-body">
      <p class="jira-lead">
        Issue status appears beside pull requests whose title carries a key (ABC-123).
        Links are derived from the title — nothing is stored, nothing is written back to Jira.
      </p>

      <label class="jira-toggle">
        <input type="checkbox" checked={enabled} onchange={(e) => setEnabled((e.currentTarget as HTMLInputElement).checked)} />
        <span>Integration enabled</span>
        <span class="jira-hint">app-wide; each profile still opts in below</span>
      </label>

      <div class="jira-profiles">
        <div class="jira-sub">per profile — credentials, site and opt-in</div>
        {#if loading}
          <div class="jira-note">loading…</div>
        {:else if !enabled}
          <div class="jira-note">Turn the integration on to configure profiles.</div>
        {:else if rows.length === 0}
          <div class="jira-note">No profiles.</div>
        {:else}
          {#each rows as r (r.profile)}
            <div class="jira-row">
              <span class="jira-dot {r.configured && r.opted_in ? 'up' : 'down'}"></span>
              <span class="jira-profile-name">{r.profile}</span>
              {#if r.configured}
                <span class="jira-site" title={r.username}>{r.url.replace(/^https?:\/\//, '')}</span>
              {:else}
                <span class="jira-unset">no credentials</span>
              {/if}
              <div class="jira-actions">
                <label class="jira-optin">
                  <input type="checkbox" checked={r.opted_in} disabled={!r.configured} onchange={(e) => setOptIn(r.profile, (e.currentTarget as HTMLInputElement).checked)} />
                  <span>on</span>
                </label>
                <button class="btn ghost sm" disabled={!r.configured || testing !== ''} onclick={() => test(r.profile)}>
                  {testing === r.profile ? '…' : 'test'}
                </button>
              </div>
            </div>
            {#if !r.configured}
              <div class="jira-fix">
                set <code>JIRA_URL</code>, <code>JIRA_USERNAME</code> and <code>JIRA_API_TOKEN</code>
                in this profile's gateway env — the same vars <code>mcp-atlassian</code> uses
              </div>
            {/if}
          {/each}
        {/if}
      </div>
    </div>
  {/if}
</div>

<style>
  .jira-card {
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-primary);
    border-left: 3px solid var(--color-accent, var(--color-info));
    border-radius: var(--radius-xl);
    padding: 14px 18px;
    margin-bottom: 20px;
  }
  .card-top { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }
  .card-identity {
    display: flex; align-items: center; gap: 8px;
    background: none; border: none; padding: 0; cursor: pointer;
    color: inherit; font-family: inherit; text-align: left;
  }
  .expand-chevron { color: var(--color-text-tertiary); transition: transform 0.15s; flex-shrink: 0; }
  .expand-chevron.expanded { transform: rotate(90deg); }
  .svc-name { font-weight: 500; font-size: 14px; color: var(--color-text-primary); }
  .svc-status { font-size: 11px; color: var(--color-text-tertiary); }

  .jira-body { display: flex; flex-direction: column; gap: 8px; margin-top: 10px; }
  .jira-lead { font-size: 11px; color: var(--color-text-tertiary); margin: 0; }
  .jira-note { font-size: 12px; color: var(--color-text-tertiary); padding: 4px 2px; }

  .jira-toggle { display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--color-text-primary); }
  .jira-hint { font-size: 10px; color: var(--color-text-tertiary); font-style: italic; }

  .jira-profiles { border-top: 1px solid var(--color-border-primary); margin-top: 4px; padding-top: 8px; }
  .jira-sub { font-size: 10px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--color-text-tertiary); margin-bottom: 6px; }
  .jira-row { display: flex; align-items: center; gap: 10px; padding: 3px 0; font-size: 12px; }
  .jira-dot { width: 8px; height: 8px; border-radius: 50%; flex: none; background: var(--color-text-tertiary); }
  .jira-dot.up { background: var(--color-success); box-shadow: 0 0 6px rgba(16,185,129,0.4); }
  .jira-profile-name { font-family: var(--font-mono); color: var(--color-text-primary); }
  .jira-site { font-family: var(--font-mono); font-size: 11px; color: var(--color-text-tertiary); }
  .jira-unset { font-size: 11px; color: var(--color-text-tertiary); font-style: italic; }
  .jira-actions { margin-left: auto; display: flex; gap: 8px; align-items: center; }
  .jira-optin { display: flex; align-items: center; gap: 4px; font-size: 11px; color: var(--color-text-tertiary); }
  .jira-fix { font-size: 10px; color: var(--color-text-tertiary); margin: 0 0 6px 18px; }
  .jira-fix code { font-family: var(--font-mono); font-size: 10px; }
</style>

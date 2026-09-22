<script lang="ts">
  // Jira is a SaaS integration: there is no compose stack to place, so unlike
  // IntegrationsCard (ADR-003) "enabled" means the credentials work. Three
  // gates must all pass before a profile sees any Jira data — the integration
  // row, complete credentials, and that profile's own opt-in.
  import { getApi } from '../utils/api';
  import { notifications } from '../notifications.svelte';
  import { profileState } from '../stores.svelte';

  let expanded = $state(false);
  let loading = $state(false);
  let saving = $state(false);
  let testing = $state(false);

  let url = $state('');
  let username = $state('');
  // Never populated from the backend — GetJiraSettings deliberately omits the
  // token. Blank means "leave whatever is stored alone".
  let token = $state('');
  let enabled = $state(false);
  let tokenStored = $state(false);

  // profile name -> opted in
  let optIn = $state<Record<string, boolean>>({});

  let configured = $derived(Boolean(url.trim() && username.trim() && (tokenStored || token.trim())));
  let optedInCount = $derived(Object.values(optIn).filter(Boolean).length);

  async function load() {
    loading = true;
    const a = await getApi();
    if (!a) { loading = false; return; }
    try {
      const s = await a.GetJiraSettings();
      url = s?.url ?? '';
      username = s?.username ?? '';
      enabled = s?.enabled === 'true';
      // The token itself never crosses the boundary — the backend reports only
      // whether one is stored, so the placeholder cannot claim a token that
      // isn't there.
      tokenStored = s?.token_set === 'true';

      const next: Record<string, boolean> = {};
      for (const p of profileState.visible) {
        next[p.name] = await a.JiraEnabledForProfile(p.name);
      }
      optIn = next;
    } catch (e: any) {
      notifications.error(`Failed to load Jira settings: ${e?.message ?? e}`);
    } finally {
      loading = false;
    }
  }

  function toggle() {
    expanded = !expanded;
    if (expanded && !url && !username) void load();
  }

  async function save() {
    saving = true;
    const a = await getApi();
    if (!a) { saving = false; return; }
    try {
      await a.SetJiraSettings(url.trim(), username.trim(), token.trim(), enabled);
      if (token.trim()) { tokenStored = true; token = ''; }
      notifications.success('Jira settings saved');
      await load();
    } catch (e: any) {
      notifications.error(`Save failed: ${e?.message ?? e}`);
    } finally {
      saving = false;
    }
  }

  async function test() {
    testing = true;
    const a = await getApi();
    if (!a) { testing = false; return; }
    try {
      await a.VerifyJira();
      notifications.success('Jira credentials verified');
    } catch (e: any) {
      notifications.error(`Jira check failed: ${e?.message ?? e}`);
    } finally {
      testing = false;
    }
  }

  async function setOptIn(profile: string, on: boolean) {
    const a = await getApi();
    if (!a) return;
    try {
      await a.SetJiraEnabledForProfile(profile, on);
      optIn = { ...optIn, [profile]: on };
      notifications.success(`${profile}: Jira ${on ? 'enabled' : 'disabled'}`);
    } catch (e: any) {
      notifications.error(`${profile}: ${e?.message ?? e}`);
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
        <span class="svc-status">
          {#if !configured}not configured{:else if !enabled}off{:else}on · {optedInCount} profile{optedInCount === 1 ? '' : 's'}{/if}
        </span>
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
        Links are derived from the title — nothing is stored, and nothing is written back to Jira.
      </p>

      {#if loading}
        <div class="jira-note">loading…</div>
      {:else}
        <label class="jira-field">
          <span>site url</span>
          <input type="url" bind:value={url} placeholder="https://yoursite.atlassian.net" spellcheck="false" />
        </label>
        <label class="jira-field">
          <span>email</span>
          <input type="email" bind:value={username} placeholder="you@example.com" spellcheck="false" />
        </label>
        <label class="jira-field">
          <span>api token</span>
          <input type="password" bind:value={token} placeholder={tokenStored ? '•••••••• stored — leave blank to keep' : 'paste an API token'} spellcheck="false" />
        </label>

        <label class="jira-toggle">
          <input type="checkbox" bind:checked={enabled} />
          <span>Integration enabled</span>
          <span class="jira-hint">the global switch; each profile still opts in below</span>
        </label>

        <div class="jira-actions">
          <button class="btn sm" onclick={save} disabled={saving}>{saving ? 'saving…' : 'save'}</button>
          <button class="btn ghost sm" onclick={test} disabled={testing || !configured}>{testing ? 'checking…' : 'test connection'}</button>
        </div>

        <div class="jira-profiles">
          <div class="jira-sub">per-profile opt-in</div>
          {#if !enabled}
            <div class="jira-note">Turn the integration on to opt profiles in.</div>
          {:else if profileState.visible.length === 0}
            <div class="jira-note">No profiles.</div>
          {:else}
            {#each profileState.visible as p (p.name)}
              <label class="jira-profile-row">
                <input
                  type="checkbox"
                  checked={optIn[p.name] ?? false}
                  onchange={(e) => setOptIn(p.name, (e.currentTarget as HTMLInputElement).checked)}
                />
                <span class="jira-profile-name">{p.name}</span>
              </label>
            {/each}
          {/if}
        </div>
      {/if}
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
  .jira-lead { font-size: 11px; color: var(--color-text-tertiary); margin: 0 0 4px; }
  .jira-note { font-size: 12px; color: var(--color-text-tertiary); padding: 4px 2px; }

  .jira-field { display: flex; align-items: center; gap: 10px; font-size: 11px; color: var(--color-text-tertiary); }
  .jira-field span { width: 70px; flex: none; }
  .jira-field input {
    flex: 1; font-size: 12px; padding: 4px 8px; font-family: var(--font-mono);
    background: var(--color-bg-tertiary); color: var(--color-text-primary);
    border: 1px solid var(--color-border-secondary); border-radius: var(--radius-sm);
  }

  .jira-toggle { display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--color-text-primary); margin-top: 4px; }
  .jira-hint { font-size: 10px; color: var(--color-text-tertiary); font-style: italic; }
  .jira-actions { display: flex; gap: 6px; margin-top: 4px; }

  .jira-profiles { border-top: 1px solid var(--color-border-primary); margin-top: 6px; padding-top: 8px; }
  .jira-sub { font-size: 10px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--color-text-tertiary); margin-bottom: 6px; }
  .jira-profile-row { display: flex; align-items: center; gap: 8px; padding: 2px 0; font-size: 12px; }
  .jira-profile-name { font-family: var(--font-mono); color: var(--color-text-primary); }
</style>

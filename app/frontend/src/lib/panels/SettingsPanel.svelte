<script lang="ts">
  import { getApi } from '../utils/api';
  import { onMount } from 'svelte';
  import { notifications } from '../notifications.svelte';
  import { profileState, type Profile } from '../stores.svelte';


  // --- Connection settings ---
  let baseURL = $state('http://127.0.0.1:9999');
  let apiKey = $state('');

  // --- Workspace settings ---
  let workspacesRoot = $state('');

  // --- Registry settings ---
  let registryUsername = $state('');
  let registryPassword = $state('');
  let savingRegistry = $state(false);

  // --- Observability (OTLP / Data Prepper) ---
  let otlpHost = $state('');
  let savingOTLP = $state(false);

  // --- Local Runner ---
  type LocalRunnerStatus = { enabled: boolean; running: boolean; name: string; work_dir: string };
  let runnerStatus = $state<LocalRunnerStatus>({ enabled: false, running: false, name: 'local-mac', work_dir: '' });
  let runnerName = $state('local-mac');
  let savingRunner = $state(false);

  // --- General ---
  let platform = $state('');
  let saving = $state(false);
  let loaded = $state(false);
  let theme = $state('dark');

  onMount(async () => {
    const a = await getApi();
    if (!a) { loaded = true; return; }
    try {
      const [cfg, plat, reg, olh, rs] = await Promise.all([a.GetConfig(), a.GetPlatform(), a.GetRegistrySettings(), a.GetOTLPHost(), a.GetLocalRunnerStatus()]);
      baseURL = cfg?.base_url ?? 'http://127.0.0.1:9999';
      apiKey = cfg?.api_key ?? '';
      workspacesRoot = cfg?.workspaces_root ?? '';
      theme = cfg?.theme ?? 'dark';
      platform = plat ?? 'unknown';
      registryUsername = reg?.username ?? '';
      registryPassword = reg?.password ?? '';
      otlpHost = olh ?? '';
      runnerStatus = rs ?? runnerStatus;
      runnerName = rs?.name ?? 'local-mac';
      applyTheme(theme);
    } catch (err: any) {
      notifications.error(`Failed to load settings: ${err?.message ?? err}`);
    } finally {
      loaded = true;
    }
  });

  function resolveAutoTheme(): 'dark' | 'light' {
    const hour = new Date().getHours();
    return (hour >= 7 && hour < 19) ? 'light' : 'dark';
  }

  function applyTheme(t: string) {
    document.documentElement.dataset.theme = t === 'auto' ? resolveAutoTheme() : t;
  }

  async function setTheme(newTheme: string) {
    const a = await getApi();
    if (!a) return;
    try {
      await a.SetTheme(newTheme);
      theme = newTheme;
      applyTheme(newTheme);
    } catch (err: any) {
      notifications.error(`Failed to set theme: ${err}`);
    }
  }

  // Auto theme: re-evaluate every minute
  onMount(() => {
    if (theme === 'auto') applyTheme('auto');
    const interval = setInterval(() => {
      if (theme === 'auto') applyTheme('auto');
    }, 60_000);
    return () => clearInterval(interval);
  });

  async function handleSave() {
    saving = true;
    const a = await getApi();
    if (!a) { saving = false; return; }
    try {
      await a.SetConfig(baseURL, apiKey, workspacesRoot);
      notifications.success('Settings saved');
    } catch (err: any) {
      notifications.error(`Failed to save: ${err}`);
    } finally {
      saving = false;
    }
  }

  async function handleSaveRegistry() {
    savingRegistry = true;
    const a = await getApi();
    if (!a) { savingRegistry = false; return; }
    try {
      await a.SetRegistrySettings(registryUsername, registryPassword);
      notifications.success('Registry settings saved');
    } catch (err: any) {
      notifications.error(`Failed to save registry settings: ${err}`);
    } finally {
      savingRegistry = false;
    }
  }

  async function handleSaveOTLP() {
    savingOTLP = true;
    const a = await getApi();
    if (!a) { savingOTLP = false; return; }
    try {
      await a.SetOTLPHost(otlpHost);
      notifications.success('Observability settings saved');
    } catch (err: any) {
      notifications.error(`Failed to save observability settings: ${err}`);
    } finally {
      savingOTLP = false;
    }
  }

  async function handleEnableRunner() {
    savingRunner = true;
    const a = await getApi();
    if (!a) { savingRunner = false; return; }
    try {
      await a.EnableLocalRunner(runnerName);
      runnerStatus = await a.GetLocalRunnerStatus();
      notifications.success('Local runner started');
    } catch (err: any) {
      notifications.error(`Failed to enable runner: ${err}`);
    } finally {
      savingRunner = false;
    }
  }

  async function handleDisableRunner() {
    savingRunner = true;
    const a = await getApi();
    if (!a) { savingRunner = false; return; }
    try {
      await a.DisableLocalRunner();
      runnerStatus = await a.GetLocalRunnerStatus();
      notifications.success('Local runner stopped');
    } catch (err: any) {
      notifications.error(`Failed to disable runner: ${err}`);
    } finally {
      savingRunner = false;
    }
  }
</script>

<div class="panel">
  <header>
    <h1 class="page-title">settings</h1>
  </header>

  {#if !loaded}
    <div class="loading">loading settings...</div>
  {:else}
    <div class="settings-form">

      <!-- Appearance -->
      <div class="section">
        <h2>appearance</h2>
        <div class="theme-toggle">
          <button class="theme-opt" class:active={theme === 'dark'} onclick={() => setTheme('dark')}>
            <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>
            dark
          </button>
          <button class="theme-opt" class:active={theme === 'light'} onclick={() => setTheme('light')}>
            <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>
            light
          </button>
          <button class="theme-opt" class:active={theme === 'muse'} onclick={() => setTheme('muse')}>
            <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3c-1.2 0-2.4.6-3 1.7A3.6 3.6 0 0 0 4.6 9c-1 .6-1.7 1.8-1.5 3.2.1 1.4 1.1 2.5 2.3 2.9l.3.1h12.5c1.5-.2 2.7-1.5 2.8-3 0-1.3-.7-2.5-1.8-3.1A4 4 0 0 0 15 4.7 3.7 3.7 0 0 0 12 3z"/></svg>
            muse
          </button>
          <button class="theme-opt" class:active={theme === 'vision'} onclick={() => setTheme('vision')}>
            <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
            vision
          </button>
          <button class="theme-opt" class:active={theme === 'paper'} onclick={() => setTheme('paper')}>
            <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
            paper
          </button>
          <button class="theme-opt" class:active={theme === 'auto'} onclick={() => setTheme('auto')}>
            <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M12 2a7 7 0 1 0 0 14 7 7 0 0 0 0-14z" fill="currentColor" opacity="0.3"/></svg>
            auto
          </button>
        </div>
        {#if theme === 'auto'}
          <p class="hint">light 7am–7pm, dark 7pm–7am</p>
        {/if}
      </div>

      <!-- Brainbox Connection -->
      <div class="section">
        <h2>brainbox connection</h2>

        <div class="field">
          <label for="url">api url</label>
          <input id="url" type="url" bind:value={baseURL} placeholder="http://127.0.0.1:9999" />
          <p class="hint">the brainbox API server address</p>
        </div>

        <div class="field">
          <label for="key">api key</label>
          <input id="key" type="password" bind:value={apiKey} placeholder="loaded from ~/.config/phantom-ink/brainbox/.api-key" autocomplete="off" />
          <p class="hint">leave blank to use the key from ~/.config/phantom-ink/brainbox/.api-key</p>
        </div>
      </div>

      <!-- Workspaces -->
      <div class="section">
        <h2>workspaces</h2>

        <div class="field">
          <label for="wsroot">profiles directory</label>
          <input id="wsroot" type="text" bind:value={workspacesRoot} placeholder="~/workspaces/profiles" />
          <p class="hint">root directory to scan for shell-profiler workspaces (reads ~/.profile-manager by default)</p>
        </div>

        <p class="hint">manage profiles, disk usage, and backups in the <strong>Profiles</strong> panel</p>
      </div>

      <!-- Save -->
      <div class="form-actions">
        <button class="btn-save" onclick={handleSave} disabled={saving}>
          {saving ? 'saving...' : 'save settings'}
        </button>
      </div>


      <!-- Registry -->
      <div class="section">
        <h2>profile image registry</h2>
        <p class="hint" style="margin-bottom: 14px;">private Docker registry for pre-built profile images — set <code>CL_REGISTRY_URL</code> on the brainbox server</p>

        <div class="field">
          <label for="reg-user">username</label>
          <input id="reg-user" type="text" bind:value={registryUsername} placeholder="registry username" autocomplete="off" />
        </div>

        <div class="field">
          <label for="reg-pass">password</label>
          <input id="reg-pass" type="password" bind:value={registryPassword} placeholder="registry password" autocomplete="new-password" />
        </div>

        <div class="form-actions">
          <button class="btn-save" onclick={handleSaveRegistry} disabled={savingRegistry}>
            {savingRegistry ? 'saving...' : 'save registry settings'}
          </button>
        </div>
      </div>

      <!-- Observability -->
      <div class="section">
        <h2>observability</h2>
        <p class="hint" style="margin-bottom: 14px;">Data Prepper host for OpenTelemetry ingest — baked into profile images at build time. Set <code>CLAUDE_CODE_ENABLE_TELEMETRY=1</code> in your profile <code>.env</code> to opt in.</p>

        <div class="field">
          <label for="otlp-host">OTLP host</label>
          <input id="otlp-host" type="text" bind:value={otlpHost} placeholder="e.g. storage.example.com" autocomplete="off" />
        </div>

        <div class="form-actions">
          <button class="btn-save" onclick={handleSaveOTLP} disabled={savingOTLP}>
            {savingOTLP ? 'saving...' : 'save observability settings'}
          </button>
        </div>
      </div>

      <!-- Local Runner -->
      <div class="section">
        <h2>local runner</h2>
        <p class="hint" style="margin-bottom: 14px;">runs playbook and chain steps as one-shot <code>claude</code> invocations on this Mac — no Docker, no mounts required</p>

        <div class="field">
          <label for="runner-name">runner name</label>
          <input id="runner-name" type="text" bind:value={runnerName} placeholder="local-mac" disabled={runnerStatus.running} />
          <p class="hint">appears in the session dispatch dropdown as this name</p>
        </div>

        <div class="runner-status-row">
          {#if runnerStatus.running}
            <span class="badge badge-ok">running · {runnerStatus.name}</span>
            <button class="btn-save" onclick={handleDisableRunner} disabled={savingRunner}>
              {savingRunner ? 'stopping...' : 'stop runner'}
            </button>
          {:else}
            <span class="badge badge-off">disabled</span>
            <button class="btn-save" onclick={handleEnableRunner} disabled={savingRunner}>
              {savingRunner ? 'starting...' : 'enable runner'}
            </button>
          {/if}
        </div>
      </div>

      <!-- About -->
      <div class="section info-section">
        <h2>about</h2>
        <div class="info-row">
          <span class="info-label">version</span>
          <span class="info-val">0.1.0</span>
        </div>
        <div class="info-row">
          <span class="info-label">platform</span>
          <span class="info-val">{platform}</span>
        </div>
        <div class="info-row">
          <span class="info-label">config</span>
          <span class="info-val">~/.config/phantom-ink/config.json</span>
        </div>
        <div class="info-row">
          <span class="info-label">api key source</span>
          <span class="info-val">~/.config/phantom-ink/brainbox/.api-key</span>
        </div>
      </div>
    </div>
  {/if}
</div>

<style>
  .panel { padding: var(--panel-padding); }
  header { margin-bottom: 24px; }
  .loading { color: var(--color-text-tertiary); font-size: 13px; padding: 24px 0; }

  .settings-form { max-width: 560px; }

  .section { margin-bottom: 32px; }

  h2 {
    font-size: 13px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--color-text-tertiary);
    margin-bottom: 16px;
  }

  h3 {
    font-size: 12px;
    font-weight: 600;
    color: var(--color-text-secondary);
  }

  .field { margin-bottom: 16px; }

  .hint {
    font-size: 11px;
    color: var(--color-text-tertiary);
    margin-top: 4px;
  }

  code {
    font-family: var(--font-mono);
    font-size: 10px;
    background: var(--color-bg-tertiary);
    border-radius: 3px;
    padding: 1px 4px;
  }

  /* Theme toggle */
  .theme-toggle {
    display: inline-flex;
    gap: 4px;
    background: var(--color-bg-tertiary);
    border-radius: var(--radius-lg);
    padding: 4px;
  }

  .theme-opt {
    display: flex;
    align-items: center;
    gap: 6px;
    background: transparent;
    border: none;
    border-radius: var(--radius-md);
    color: var(--color-text-tertiary);
    font-size: 12px;
    padding: 6px 14px;
    transition: all 0.2s;
  }
  .theme-opt:hover { color: var(--color-text-secondary); }
  .theme-opt.active {
    background: var(--color-bg-secondary);
    color: var(--color-text-primary);
    box-shadow: var(--shadow-button);
    font-weight: 500;
  }

  /* Save button */
  .form-actions { margin-bottom: 32px; }

  .btn-save {
    background: rgba(59, 130, 246, 0.1);
    border: 1px solid rgba(59, 130, 246, 0.3);
    color: var(--color-info);
    padding: 9px 20px;
    border-radius: var(--radius-lg);
    font-size: 13px;
    font-weight: 500;
    transition: all 0.15s;
  }
  .btn-save:hover { background: rgba(59, 130, 246, 0.2); border-color: var(--color-info); }

  /* Runners */
  .runner-list {
    list-style: none;
    padding: 0;
    margin: 0 0 10px 0;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .runner-list li {
    display: flex;
    align-items: center;
    gap: 10px;
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-md);
    padding: 8px 12px;
  }
  .runner-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--color-text-tertiary);
  }
  .runner-dot.on { background: var(--color-success); }
  .runner-info {
    display: flex;
    flex-direction: column;
  }
  .runner-name {
    font-size: 13px;
    font-weight: 600;
    color: var(--color-text-primary);
  }
  .runner-meta {
    font-size: 11px;
    color: var(--color-text-tertiary);
    font-family: var(--font-mono);
  }
  .btn-add {
    background: transparent;
    border: 1px dashed var(--color-border-secondary);
    border-radius: var(--radius-md);
    color: var(--color-text-tertiary);
    padding: 7px 12px;
    font-size: 12px;
    cursor: pointer;
  }
  .btn-add:hover {
    border-color: var(--color-accent);
    color: var(--color-accent);
  }

  /* Local runner */
  .input-row {
    display: flex;
    gap: 8px;
  }
  .input-row input { flex: 1; }
  .btn-browse {
    background: transparent;
    border: 1px solid var(--color-border-secondary);
    border-radius: var(--radius-md);
    color: var(--color-text-secondary);
    padding: 6px 10px;
    font-size: 12px;
    cursor: pointer;
    white-space: nowrap;
  }
  .btn-browse:hover { border-color: var(--color-accent); color: var(--color-accent); }

  .runner-status-row {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-top: 12px;
  }
  .badge {
    font-size: 11px;
    font-family: var(--font-mono);
    padding: 3px 8px;
    border-radius: var(--radius-sm);
    border: 1px solid;
  }
  .badge-ok {
    color: var(--color-success);
    border-color: var(--color-success);
    background: rgba(34, 197, 94, 0.08);
  }
  .badge-off {
    color: var(--color-text-tertiary);
    border-color: var(--color-border-secondary);
    background: transparent;
  }

  /* About section */
  .info-section {
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-xl);
    padding: 16px;
  }

  .info-section h2 { margin-bottom: 12px; }

  .info-row {
    display: flex;
    gap: 16px;
    padding: 6px 0;
    border-bottom: 1px solid var(--color-border-primary);
    font-size: 12px;
  }
  .info-row:last-child { border-bottom: none; }

  .info-label {
    color: var(--color-text-tertiary);
    min-width: 120px;
    flex-shrink: 0;
  }

  .info-val {
    color: var(--color-text-secondary);
    font-family: var(--font-mono);
    font-size: 11px;
  }
</style>

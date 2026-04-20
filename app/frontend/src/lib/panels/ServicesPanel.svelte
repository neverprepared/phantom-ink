<script lang="ts">
  import { getApi, openInBrowser } from '../utils/api';
  import { onMount } from 'svelte';
  import { notifications } from '../notifications.svelte';
  import { featureFlags, profileState } from '../stores.svelte';

  interface Service {
    name: string;
    label: string;
    description: string;
    default_url: string;
    port: number;
    native: boolean;
    enabled: boolean;
    remote: boolean;
    local_url: string;
    remote_url: string;
    url: string;
    running: boolean;
  }

  let services = $state<Service[]>([]);
  let loading = $state(true);
  let busyServices = $state<Set<string>>(new Set());
  let expandedServices = $state<Set<string>>(new Set());
  let editingService = $state<string | null>(null);
  let editURL = $state('');

  // Secrets status per profile
  interface SecretKey { key: string; has_value: boolean; source: string; }
  let secretsProfile = $state<string | null>(null);
  let secretKeys = $state<SecretKey[]>([]);
  let loadingSecrets = $state(false);
  let showOpGuide = $state(false);
  let credsExpanded = $state(false);

  async function refresh() {
    const a = await getApi();
    if (!a) { loading = false; return; }
    try {
      services = (await a.ListServices()) ?? [];
      featureFlags.services = services.map(s => ({
        name: s.name,
        enabled: s.enabled,
        running: s.running,
      }));
    } catch (err: any) {
      notifications.error(`Failed to load services: ${err?.message ?? err}`);
    } finally {
      loading = false;
    }
  }

  onMount(async () => {
    await refresh();
    if (featureFlags.isEnabled('ollama')) refreshOllamaModels();
  });

  function setBusy(name: string) { busyServices = new Set([...busyServices, name]); }
  function clearBusy(name: string) { const next = new Set(busyServices); next.delete(name); busyServices = next; }

  async function handleStart(name: string) {
    setBusy(name);
    const a = await getApi();
    if (!a) { clearBusy(name); return; }
    try {
      await a.StartService(name);
      notifications.success(`Started ${name}`);
      await refresh();
    } catch (err: any) {
      notifications.error(`Failed to start ${name}: ${err}`);
    } finally { clearBusy(name); }
  }

  async function handleStop(name: string) {
    setBusy(name);
    const a = await getApi();
    if (!a) { clearBusy(name); return; }
    try {
      await a.StopService(name);
      notifications.success(`Stopped ${name}`);
      await refresh();
    } catch (err: any) {
      notifications.error(`Failed to stop ${name}: ${err}`);
    } finally { clearBusy(name); }
  }

  async function handleRestart(name: string) {
    setBusy(name);
    const a = await getApi();
    if (!a) { clearBusy(name); return; }
    try {
      await a.StopService(name);
      await a.StartService(name);
      notifications.success(`Restarted ${name}`);
      await refresh();
    } catch (err: any) {
      notifications.error(`Failed to restart ${name}: ${err}`);
    } finally { clearBusy(name); }
  }

  function saveSvc(svc: Service, overrides: Partial<Service> = {}) {
    const s = { ...svc, ...overrides };
    return getApi().then(a => a?.SetServiceConfig(s.name, s.enabled, s.local_url, s.remote_url, s.remote));
  }

  function toggleExpanded(name: string) {
    const next = new Set(expandedServices);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    expandedServices = next;
  }

  async function handleToggle(svc: Service) {
    try {
      await saveSvc(svc, { enabled: !svc.enabled });
      toggleExpanded(svc.name);
      await refresh();
      notifications.success(`${svc.label} ${!svc.enabled ? 'enabled' : 'disabled'}`);
    } catch (err: any) { notifications.error(`Failed to toggle ${svc.label}: ${err}`); }
  }

  async function handleRemoteToggle(svc: Service) {
    try {
      await saveSvc(svc, { remote: !svc.remote });
      await refresh();
      notifications.success(`${svc.label} set to ${!svc.remote ? 'remote' : 'local'}`);
    } catch (err: any) { notifications.error(`Failed to update ${svc.label}: ${err}`); }
  }

  function startEditURL(svc: Service) {
    editingService = svc.name;
    editURL = svc.remote ? svc.remote_url : svc.local_url;
  }

  async function saveURL(svc: Service) {
    try {
      const overrides = svc.remote ? { remote_url: editURL } : { local_url: editURL };
      await saveSvc(svc, overrides);
      editingService = null;
      await refresh();
      notifications.success(`Updated ${svc.label} URL`);
    } catch (err: any) { notifications.error(`Failed to save URL: ${err}`); }
  }

  function cancelEditURL() { editingService = null; }

  async function loadSecrets(profileName: string) {
    if (secretsProfile === profileName) { secretsProfile = null; return; }
    loadingSecrets = true;
    secretsProfile = profileName;
    const a = await getApi();
    if (!a) { loadingSecrets = false; return; }
    try {
      secretKeys = (await a.GetProfileSecrets(profileName)) ?? [];
    } catch { secretKeys = []; }
    finally { loadingSecrets = false; }
  }

  async function copySecretsTemplate(profileName: string) {
    const a = await getApi();
    if (!a) return;
    try {
      const tpl = await a.ExportSecretsTemplate(profileName);
      await (window as any).runtime?.ClipboardSetText(tpl);
      notifications.success('Secrets template copied to clipboard');
    } catch (err: any) { notifications.error(`Failed to copy template: ${err}`); }
  }

  // ---------------------------------------------------------------------------
  // Ollama model management
  // ---------------------------------------------------------------------------

  interface OllamaModel { name: string; size: number; modified_at: string; digest: string; }

  let ollamaModels = $state<OllamaModel[]>([]);
  let ollamaLoading = $state(false);
  let ollamaPullName = $state('');
  let ollamaPulling = $state(false);
  let ollamaDeleting = $state<Set<string>>(new Set());

  async function refreshOllamaModels() {
    ollamaLoading = true;
    const a = await getApi();
    if (!a) { ollamaLoading = false; return; }
    try {
      ollamaModels = (await a.ListOllamaModels()) ?? [];
    } catch (err: any) {
      notifications.error(`Failed to list Ollama models: ${err}`);
    } finally { ollamaLoading = false; }
  }

  async function handleOllamaPull() {
    const name = ollamaPullName.trim();
    if (!name) return;
    ollamaPulling = true;
    const a = await getApi();
    if (!a) { ollamaPulling = false; return; }
    try {
      await a.PullOllamaModel(name);
      notifications.success(`Pulled ${name}`);
      ollamaPullName = '';
      await refreshOllamaModels();
    } catch (err: any) {
      notifications.error(`Failed to pull ${name}: ${err}`);
    } finally { ollamaPulling = false; }
  }

  async function handleOllamaDelete(name: string) {
    ollamaDeleting = new Set([...ollamaDeleting, name]);
    const a = await getApi();
    if (!a) { const next = new Set(ollamaDeleting); next.delete(name); ollamaDeleting = next; return; }
    try {
      await a.DeleteOllamaModel(name);
      notifications.success(`Deleted ${name}`);
      await refreshOllamaModels();
    } catch (err: any) {
      notifications.error(`Failed to delete ${name}: ${err}`);
    } finally { const next = new Set(ollamaDeleting); next.delete(name); ollamaDeleting = next; }
  }

  function formatBytes(bytes: number): string {
    if (bytes >= 1_073_741_824) return `${(bytes / 1_073_741_824).toFixed(1)} GB`;
    if (bytes >= 1_048_576) return `${(bytes / 1_048_576).toFixed(0)} MB`;
    return `${bytes} B`;
  }
</script>

<div class="panel" aria-busy={loading}>
  <header class="panel-header">
    <h1><span class="panel-accent">integrations</span></h1>
    <button class="btn-refresh" onclick={refresh} title="Refresh status" aria-label="Refresh status">
      <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/></svg>
    </button>
  </header>

  <!-- Credentials card -->
  <div class="service-card creds-card">
    <div class="card-top">
      <button class="card-identity" onclick={() => credsExpanded = !credsExpanded}>
        <svg class="expand-chevron" class:expanded={credsExpanded} xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg>
        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect width="18" height="11" x="3" y="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
        <span class="svc-name">Credentials</span>
        <span class="svc-status">per profile</span>
      </button>
    </div>

    {#if credsExpanded}
    <div class="secrets-profiles">
      {#each profileState.profiles as p (p.name)}
        <div class="secrets-profile-row">
          <button class="secrets-profile-btn" class:expanded={secretsProfile === p.name} onclick={() => loadSecrets(p.name)}>
            <svg class="chevron" xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg>
            <span class="secrets-profile-name">{p.name}</span>
            {#if p.secrets_mode === '1password'}
              <span class="scope-badge info">1Password</span>
            {:else if p.secrets_mode === 'plaintext'}
              <span class="scope-badge">plaintext</span>
            {:else}
              <span class="scope-badge danger">not configured</span>
            {/if}
          </button>
          <button class="btn-icon" onclick={() => copySecretsTemplate(p.name)} title="Copy secrets template to clipboard" aria-label="Copy secrets template for {p.name}">
            <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/></svg>
          </button>
        </div>
        {#if secretsProfile === p.name}
          <div class="secrets-detail">
            {#if loadingSecrets}
              <p class="hint">loading...</p>
            {:else if secretKeys.length === 0}
              <p class="hint">no keys detected</p>
            {:else}
              <div class="secrets-key-list">
                {#each secretKeys as sk (sk.key)}
                  <div class="secrets-key-row">
                    <span class="secrets-key-name">{sk.key}</span>
                    {#if sk.has_value}
                      <span class="secrets-key-status ok">
                        <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>
                        {sk.source}
                      </span>
                    {:else}
                      <span class="secrets-key-status missing">missing</span>
                    {/if}
                  </div>
                {/each}
              </div>
            {/if}
          </div>
        {/if}
      {/each}
      {#if profileState.profiles.length === 0}
        <p class="hint">no profiles found</p>
      {/if}
    </div>

    <!-- Collapsible setup instructions -->
    <button class="op-toggle" onclick={() => showOpGuide = !showOpGuide}>
      <svg class="chevron" class:expanded={showOpGuide} xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg>
      <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg>
      <span>how to configure secrets</span>
    </button>
    {#if showOpGuide}
      <div class="op-guide-body">
        <p class="op-desc">
          Each profile uses a <code>.env.secrets</code> file for API keys and tokens.
          You can manage these manually or optionally use 1Password Environments for secure mounting.
        </p>
        <p class="op-step-title">Option 1: Manual (no dependencies)</p>
        <p class="op-desc">
          Create <code>.env.secrets</code> in your profile directory and add secrets as <code>KEY=VALUE</code> pairs.
          Use the copy button above to get a template with all known keys.
        </p>
        <p class="op-step-title">Option 2: 1Password Environments (optional)</p>
        <p class="op-desc">
          With 1Password, secrets are mounted as a FIFO — never written to disk. For each profile:
        </p>
        <ol class="op-steps">
          <li>Ensure <code>.env.secrets</code> does <strong>not</strong> exist at the profile path</li>
          <li>Open <strong>1Password</strong> → Developer → Environments</li>
          <li>Create an environment named after the profile</li>
          <li>Add secret key-value pairs (use the copy template button)</li>
          <li>Destinations → Configure → <strong>Mount .env file</strong></li>
          <li>Set path to: <code>&lt;workspace_home&gt;/.env.secrets</code></li>
        </ol>
      </div>
    {/if}
    {/if}
  </div>

  <!-- Service cards -->
  {#if loading}
    <div class="loading">checking services...</div>
  {:else}
    <div class="service-list">
      {#each services as svc (svc.name)}
        {@const busy = busyServices.has(svc.name)}
        {@const expanded = expandedServices.has(svc.name)}
        <div class="service-card" class:running={svc.running} class:disabled={!svc.enabled}>
          <div class="card-top">
            <button class="card-identity" onclick={() => toggleExpanded(svc.name)}>
              <svg class="expand-chevron" class:expanded xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg>
              <span class="status-dot" class:running={svc.running}></span>
              <span class="svc-name">{svc.label}</span>
              <span class="svc-status" class:running={svc.running}>
                {svc.running ? 'running' : 'stopped'}
              </span>
            </button>
            <label class="toggle-switch" title={svc.enabled ? 'Disable' : 'Enable'}>
              <input type="checkbox" checked={svc.enabled} onchange={() => handleToggle(svc)} />
              <span class="toggle-track"></span>
            </label>
          </div>

          {#if expanded}
            <p class="svc-desc">{svc.description}</p>

            {#if !svc.native}
              <div class="mode-row">
                <button class="mode-opt" class:active={!svc.remote} onclick={() => svc.remote && handleRemoteToggle(svc)}>local</button>
                <button class="mode-opt" class:active={svc.remote} onclick={() => !svc.remote && handleRemoteToggle(svc)}>remote</button>
              </div>
            {/if}

            <div class="card-url">
              <span class="url-mode-label">{svc.remote ? 'remote' : 'local'}</span>
              {#if editingService === svc.name}
                <input class="url-input" type="url" bind:value={editURL}
                  onkeydown={(e) => { if (e.key === 'Enter') saveURL(svc); if (e.key === 'Escape') cancelEditURL(); }} />
                <button class="url-btn save" onclick={() => saveURL(svc)}>save</button>
                <button class="url-btn" onclick={cancelEditURL}>cancel</button>
              {:else}
                {#if svc.url}
                  <button class="url-link" onclick={() => openInBrowser(svc.url)}>{svc.url}</button>
                {:else}
                  <span class="url-empty">not set</span>
                {/if}
                <button class="url-btn" onclick={() => startEditURL(svc)}>edit</button>
              {/if}
            </div>

            <div class="card-actions">
              {#if svc.native || svc.remote}
                <!-- Status shown in card header — no actions needed -->
              {:else if busy}
                <span class="busy-indicator">
                  <svg class="spinner" xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>
                  working...
                </span>
              {:else if svc.running}
                <button class="btn-primary btn-sm" onclick={() => handleRestart(svc.name)}>restart</button>
                <button class="btn-danger btn-sm" onclick={() => handleStop(svc.name)}>stop</button>
              {:else}
                <button class="btn-success btn-sm" onclick={() => handleStart(svc.name)}>start</button>
              {/if}
            </div>

            <!-- Ollama models inline (inside the ollama service card) -->
            {#if svc.name === 'ollama' && svc.enabled}
              <div class="ollama-section">
                <div class="ollama-header">
                  <span class="ollama-title">models</span>
                  <button class="btn-refresh" onclick={refreshOllamaModels} title="Refresh models" aria-label="Refresh Ollama models">
                    <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/></svg>
                  </button>
                </div>
                <div class="ollama-pull-row">
                  <input class="ollama-pull-input" type="text" placeholder="model name (e.g. llama3.2)"
                    bind:value={ollamaPullName}
                    onkeydown={(e) => { if (e.key === 'Enter') handleOllamaPull(); }}
                    disabled={ollamaPulling} />
                  <button class="btn-primary btn-sm" onclick={handleOllamaPull} disabled={ollamaPulling || !ollamaPullName.trim()}>
                    {ollamaPulling ? 'pulling...' : 'pull'}
                  </button>
                </div>
                {#if ollamaLoading}
                  <p class="hint">loading models...</p>
                {:else if ollamaModels.length === 0}
                  <p class="hint">no models — use pull to download one</p>
                {:else}
                  <div class="ollama-model-list">
                    {#each ollamaModels as model (model.name)}
                      {@const deleting = ollamaDeleting.has(model.name)}
                      <div class="ollama-model-row">
                        <span class="ollama-model-name">{model.name}</span>
                        <span class="ollama-model-size">{formatBytes(model.size)}</span>
                        <button class="btn-danger btn-sm btn-xs" onclick={() => handleOllamaDelete(model.name)}
                          disabled={deleting} title="Delete model" aria-label="Delete {model.name}">
                          {deleting ? '...' : 'delete'}
                        </button>
                      </div>
                    {/each}
                  </div>
                {/if}
              </div>
            {/if}
          {/if}
        </div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .panel { padding: var(--panel-padding); }

  /* Service card */
  .service-list { display: flex; flex-direction: column; gap: 12px; }

  .service-card {
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-primary);
    border-left: 3px solid var(--color-border-primary);
    border-radius: var(--radius-xl);
    padding: 14px 18px;
  }
  .service-card.running { border-left-color: var(--color-success); }
  .service-card.disabled { opacity: 0.6; }

  .creds-card { margin-bottom: 20px; border-left-color: var(--color-info); }

  .card-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 6px;
  }

  .card-identity {
    display: flex; align-items: center; gap: 8px;
    background: none; border: none; padding: 0; cursor: pointer;
    color: inherit; font-family: inherit; text-align: left;
  }

  .expand-chevron {
    color: var(--color-text-tertiary); transition: transform 0.15s; flex-shrink: 0;
  }
  .expand-chevron.expanded { transform: rotate(90deg); }

  .status-dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: #374151; flex-shrink: 0;
  }
  .status-dot.running {
    background: var(--color-success);
    box-shadow: 0 0 6px rgba(16, 185, 129, 0.4);
  }

  .svc-name { font-weight: 500; font-size: 14px; color: var(--color-text-primary); }
  .svc-status { font-size: 11px; color: var(--color-text-tertiary); }
  .svc-status.running { color: var(--color-success); }
  .svc-desc { font-size: 12px; color: var(--color-text-tertiary); margin-bottom: 10px; }

  /* Toggle switch */
  .toggle-switch { position: relative; display: inline-flex; cursor: pointer; }
  .toggle-switch input { opacity: 0; width: 0; height: 0; position: absolute; }
  .toggle-track {
    width: 32px; height: 18px;
    background: var(--color-bg-tertiary);
    border: 1px solid var(--color-border-secondary);
    border-radius: 9999px; position: relative; transition: all 0.2s;
  }
  .toggle-track::after {
    content: ''; width: 12px; height: 12px;
    background: var(--color-text-tertiary); border-radius: 50%;
    position: absolute; top: 2px; left: 2px; transition: all 0.2s;
  }
  .toggle-switch input:checked + .toggle-track {
    background: rgba(16, 185, 129, 0.2); border-color: rgba(16, 185, 129, 0.4);
  }
  .toggle-switch input:checked + .toggle-track::after {
    background: var(--color-success); left: 16px;
  }

  /* Local/remote toggle */
  .mode-row {
    display: inline-flex;
    border: 1px solid var(--color-border-secondary);
    border-radius: var(--radius-md); overflow: hidden; margin-bottom: 10px;
  }
  .mode-opt {
    background: transparent; border: none;
    border-right: 1px solid var(--color-border-secondary);
    color: var(--color-text-tertiary); padding: 3px 12px;
    font-size: 11px; transition: all 0.15s;
  }
  .mode-opt:last-child { border-right: none; }
  .mode-opt:hover { color: var(--color-text-secondary); background: rgba(255, 255, 255, 0.03); }
  .mode-opt.active { background: rgba(59, 130, 246, 0.1); color: var(--color-info); font-weight: 500; }

  /* URL row */
  .card-url { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
  .url-mode-label {
    font-size: 9px; font-weight: 600; text-transform: uppercase;
    letter-spacing: 0.04em; color: var(--color-text-tertiary);
    min-width: 42px; flex-shrink: 0;
  }
  .url-link {
    background: none; border: none; color: var(--color-accent);
    font-family: var(--font-mono); font-size: 11px; padding: 0; text-align: left; cursor: pointer;
  }
  .url-link:hover { text-decoration: underline; }
  .url-empty { font-size: 11px; color: var(--color-text-tertiary); font-style: italic; }
  .url-input { flex: 1; font-size: 12px; padding: 4px 8px; font-family: var(--font-mono); }
  .url-btn {
    background: rgba(255, 255, 255, 0.05); border: 1px solid var(--color-border-secondary);
    border-radius: var(--radius-sm); color: var(--color-text-tertiary);
    font-size: 10px; padding: 2px 8px; transition: all 0.15s;
  }
  .url-btn:hover { color: var(--color-text-secondary); background: rgba(255, 255, 255, 0.08); }
  .url-btn.save { color: var(--color-success); border-color: rgba(16, 185, 129, 0.3); }

  /* Actions */
  .card-actions {
    display: flex; gap: 8px; padding-top: 10px;
    border-top: 1px solid var(--color-border-primary);
  }
  .native-hint { font-size: 11px; color: var(--color-text-tertiary); font-style: italic; }

  /* Credentials / secrets */
  .secrets-profiles { display: flex; flex-direction: column; gap: 2px; }
  .secrets-profile-row { display: flex; align-items: center; gap: 4px; }
  .secrets-profile-btn {
    flex: 1; display: flex; align-items: center; gap: 8px;
    background: transparent; border: none; border-radius: var(--radius-md);
    padding: 6px 8px; text-align: left; transition: background 0.1s;
  }
  .secrets-profile-btn:hover { background: rgba(255, 255, 255, 0.03); }
  .chevron { color: var(--color-text-tertiary); transition: transform 0.15s; flex-shrink: 0; }
  .secrets-profile-btn.expanded .chevron { transform: rotate(90deg); }
  .secrets-profile-name { font-size: 13px; font-weight: 500; color: var(--color-text-primary); }

  .secrets-detail { padding: 8px 8px 8px 28px; }
  .secrets-key-list { display: flex; flex-direction: column; gap: 2px; }
  .secrets-key-row {
    display: flex; align-items: center; justify-content: space-between;
    padding: 3px 8px; border-radius: var(--radius-sm); font-size: 12px;
  }
  .secrets-key-row:nth-child(odd) { background: rgba(255, 255, 255, 0.015); }
  .secrets-key-name { font-family: var(--font-mono); font-size: 11px; color: var(--color-text-secondary); }
  .secrets-key-status { display: flex; align-items: center; gap: 4px; font-size: 10px; }
  .secrets-key-status.ok { color: var(--color-success); }
  .secrets-key-status.missing { color: var(--color-error); }

  /* 1Password guide */
  .op-toggle {
    display: flex; align-items: center; gap: 8px;
    background: transparent; border: none; color: var(--color-text-tertiary);
    font-size: 12px; padding: 8px 0; margin-top: 8px; transition: color 0.15s;
  }
  .op-toggle:hover { color: var(--color-text-secondary); }
  .op-toggle .chevron.expanded { transform: rotate(90deg); }

  .op-guide-body {
    background: rgba(59, 130, 246, 0.04);
    border: 1px solid rgba(59, 130, 246, 0.12);
    border-radius: var(--radius-lg); padding: 14px; margin-top: 6px;
  }
  .op-desc { font-size: 12px; color: var(--color-text-secondary); margin-bottom: 12px; line-height: 1.5; }
  .op-desc code, .op-steps code {
    background: rgba(255, 255, 255, 0.06); padding: 1px 5px;
    border-radius: var(--radius-sm); font-family: var(--font-mono); font-size: 11px;
  }
  .op-step-title { font-size: 12px; font-weight: 500; color: var(--color-text-primary); margin-bottom: 4px; }
  .op-steps {
    font-size: 12px; color: var(--color-text-secondary); line-height: 1.6;
    padding-left: 20px; margin: 6px 0;
  }
  .op-steps li { margin-bottom: 4px; }

  /* Ollama model management */
  .ollama-section {
    margin-top: 10px;
    padding-top: 10px;
    border-top: 1px solid var(--color-border-primary);
  }

  .ollama-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 8px;
  }

  .ollama-title {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--color-text-tertiary);
  }

  .ollama-pull-row {
    display: flex; gap: 8px; margin-bottom: 10px;
  }
  .ollama-pull-input {
    flex: 1; font-size: 12px; padding: 5px 10px;
    font-family: var(--font-mono);
    background: var(--color-bg-tertiary);
    border: 1px solid var(--color-border-secondary);
    border-radius: var(--radius-md);
    color: var(--color-text-primary);
  }
  .ollama-pull-input:disabled { opacity: 0.5; }
  .ollama-pull-input::placeholder { color: var(--color-text-tertiary); }

  .ollama-model-list { display: flex; flex-direction: column; gap: 2px; }
  .ollama-model-row {
    display: flex; align-items: center; justify-content: space-between;
    padding: 5px 8px; border-radius: var(--radius-sm);
  }
  .ollama-model-row:nth-child(odd) { background: rgba(255, 255, 255, 0.015); }

  .ollama-model-name {
    flex: 1; min-width: 0;
    font-family: var(--font-mono); font-size: 12px;
    color: var(--color-text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .ollama-model-size { font-size: 11px; color: var(--color-text-tertiary); flex-shrink: 0; }

  .btn-xs { padding: 2px 8px !important; font-size: 10px !important; }
  .hint { font-size: 12px; color: var(--color-text-tertiary); font-style: italic; margin: 6px 0; }
</style>

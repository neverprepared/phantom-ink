<script lang="ts">
  import { getApi, openInBrowser } from '../utils/api';
  import { onMount } from 'svelte';
  import { brainboxEvents } from '../events.svelte';
  import { notifications } from '../notifications.svelte';
  import { profileState, featureFlags } from '../stores.svelte';
  import EmptyState from '../components/EmptyState.svelte';
  import Badge from '../components/Badge.svelte';
  import Modal from '../components/Modal.svelte';
  import ProfilePicker from '../components/ProfilePicker.svelte';


  let allSessions = $state<any[]>([]);
  let localProcesses = $state<any[]>([]);
  let loading = $state(true);
  let showNewModal = $state(false);
  let terminalSession = $state<any | null>(null);
  let terminalUrl = $state('');
  let busySessions = $state<Set<string>>(new Set());

  // Defer iframe src to let the panel render first, avoids blank frame on first open
  $effect(() => {
    if (terminalSession?.url) {
      terminalUrl = '';
      const t = setTimeout(() => { terminalUrl = terminalSession?.url ?? ''; }, 100);
      return () => clearTimeout(t);
    } else {
      terminalUrl = '';
    }
  });

  // New session form
  let newName = $state('');
  let newRole = $state('developer');
  let newLLM = $state('claude');
  let newModel = $state('');
  let newBackend = $state('docker');
  let newVMTemplate = $state('');
  let newGuestOS = $state('linux');
  let newProfile = $state('');
  let isCreating = $state(false);

  // Volume mount selection
  let profileDirs = $state<string[]>([]);
  let selectedDirs = $state<Set<string>>(new Set());
  let loadingDirs = $state(false);

  let activeProfile = $derived(profileState.active);
  let profiles = $derived(profileState.profiles);

  const DOCKER_EVENTS = ['create', 'start', 'stop', 'die', 'destroy'];

  // Filter by active TitleBar profile
  let filtered = $derived.by(() => {
    if (!activeProfile) return allSessions;
    return allSessions.filter(s => {
      const sp = (s.workspace_profile ?? '').toLowerCase();
      return sp === activeProfile.name.toLowerCase();
    });
  });

  let activeSessions = $derived(filtered.filter(s => s.active));
  let stoppedCount = $derived(filtered.length - activeSessions.length);
  let filteredLocal = $derived.by(() => {
    if (!activeProfile) return localProcesses;
    return localProcesses.filter(p => p.workspace_profile?.toLowerCase() === activeProfile.name.toLowerCase());
  });

  async function refresh() {
    const a = await getApi();
    if (!a) return;
    try {
      const [sess, procs] = await Promise.all([a.GetSessions(), a.FindClaudeProcesses()]);
      allSessions = sess ?? [];
      localProcesses = procs ?? [];
    } catch (err: any) {
      console.error('Failed to fetch sessions:', err);
    } finally {
      loading = false;
    }
  }

  async function handleFocusTab(tty: string) {
    const a = await getApi();
    if (!a) return;
    try {
      await a.FocusTerminalTab(tty);
    } catch (err: any) {
      notifications.error(`Could not find terminal tab: ${err}`);
    }
  }

  onMount(() => { refresh(); });

  $effect(() => {
    const ev = brainboxEvents.last;
    if (!ev) return;
    const raw = ev.raw;
    if (DOCKER_EVENTS.includes(raw) || (ev.data && (ev.data as any).hub)) {
      refresh();
    }
  });

  function setBusy(name: string) {
    busySessions = new Set([...busySessions, name]);
  }
  function clearBusy(name: string) {
    const next = new Set(busySessions);
    next.delete(name);
    busySessions = next;
  }

  async function handleStart(name: string) {
    setBusy(name);
    const a = await getApi();
    if (!a) { clearBusy(name); return; }
    try {
      const resp = await a.StartSession(name);
      if (resp.success ?? resp.Success) notifications.success(`Started: ${name}`);
      else notifications.error(resp.error ?? resp.Error ?? 'Failed to start');
      refresh();
    } catch (err: any) {
      notifications.error(`Failed to start: ${err}`);
    } finally {
      clearBusy(name);
    }
  }

  async function handleStop(name: string) {
    setBusy(name);
    if (terminalSession?.name === name) terminalSession = null;
    const a = await getApi();
    if (!a) { clearBusy(name); return; }
    try {
      await a.StopSession(name);
      notifications.success(`Stopped: ${name}`);
      refresh();
    } catch (err: any) {
      notifications.error(`Failed to stop: ${err}`);
    } finally {
      clearBusy(name);
    }
  }

  async function handleDelete(name: string) {
    setBusy(name);
    if (terminalSession?.name === name) terminalSession = null;
    const a = await getApi();
    if (!a) { clearBusy(name); return; }
    try {
      await a.DeleteSession(name);
      notifications.success(`Deleted: ${name}`);
      refresh();
    } catch (err: any) {
      notifications.error(`Failed to delete: ${err}`);
    } finally {
      clearBusy(name);
    }
  }

  async function loadProfileDirs(profileName: string) {
    if (!profileName) { profileDirs = []; return; }
    loadingDirs = true;
    const a = await getApi();
    if (!a) { loadingDirs = false; return; }
    try {
      profileDirs = (await a.ListProfileDirs(profileName)) ?? [];
    } catch {
      profileDirs = [];
    } finally {
      loadingDirs = false;
    }
  }

  function openCreateModal() {
    newProfile = activeProfile?.name ?? profiles[0]?.name ?? '';
    selectedDirs = new Set();
    showNewModal = true;
    loadProfileDirs(newProfile);
  }

  function toggleDir(dir: string) {
    const next = new Set(selectedDirs);
    if (next.has(dir)) next.delete(dir);
    else next.add(dir);
    selectedDirs = next;
  }

  // Reload dirs when profile selection changes in the modal
  let prevProfile = '';
  $effect(() => {
    if (newProfile && newProfile !== prevProfile) {
      prevProfile = newProfile;
      selectedDirs = new Set();
      loadProfileDirs(newProfile);
    }
  });

  // Set a sensible default model when switching LLM provider
  let prevLLM = '';
  $effect(() => {
    if (newLLM !== prevLLM) {
      prevLLM = newLLM;
      if (newLLM === 'codex') {
        newModel = 'codex-mini-latest';
      } else if (newLLM === 'ollama') {
        newModel = '';
      } else {
        newModel = '';
      }
    }
  });

  async function handleCreate() {
    if (!newName.trim() || !newProfile) return;
    isCreating = true;
    const a = await getApi();
    if (!a) { isCreating = false; return; }
    try {
      const profile = profiles.find(p => p.name === newProfile);
      const wsHome = profile?.workspace_home ?? '';
      const volumes = [...selectedDirs].map(dir =>
        `${wsHome}/${dir}:/home/developer/${dir}`
      );
      const req: Record<string, any> = {
        name: newName.trim().replace(/\s+/g, '-').toLowerCase(),
        role: newRole,
        volumes: volumes.length > 0 ? volumes : undefined,
        llm_provider: newLLM,
        llm_model: newLLM === 'ollama' || newLLM === 'codex' ? newModel : '',
        backend: newBackend,
        workspace_profile: newProfile,
        workspace_home: wsHome,
      };
      if (newBackend === 'utm') {
        req.vm_template = newVMTemplate;
        req.guest_os = newGuestOS;
      }
      const resp = await a.CreateSession(req);
      if (resp.success ?? resp.Success) {
        notifications.success(`Created session: ${newName}`);
        showNewModal = false;
        newName = ''; newVMTemplate = ''; selectedDirs = new Set();
        refresh();
      } else {
        notifications.error(resp.error ?? resp.Error ?? 'Failed to create session');
      }
    } catch (err: any) {
      notifications.error(`Failed to create: ${err}`);
    } finally {
      isCreating = false;
    }
  }
</script>

<div class="panel" aria-busy={loading}>
  <header>
    <h1><span class="accent">sessions</span></h1>
    <div class="header-actions">
      <button class="new-btn" onclick={openCreateModal}>+ new session</button>
      <button class="refresh-btn" onclick={refresh} title="Refresh" aria-label="Refresh">
        <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/></svg>
      </button>
    </div>
  </header>

  {#if filtered.length > 0}
    <div class="stats-row">
      <span class="stat"><span class="stat-num">{filtered.length}</span> total</span>
      <span class="stat active"><span class="stat-num">{activeSessions.length}</span> active</span>
      <span class="stat stopped"><span class="stat-num">{stoppedCount}</span> stopped</span>
    </div>
  {/if}

  {#if loading}
    <div class="loading">loading sessions...</div>
  {:else if allSessions.length === 0}
    <EmptyState title="No sessions" message="Create a new session to get started." />
  {:else if filtered.length === 0}
    <EmptyState title="No sessions in this profile" />
  {:else}
    <div class="session-list">
      {#each filtered as session (session.name)}
        {@const active = session.active}
        {@const role = session.role ?? 'developer'}
        {@const backend = session.backend ?? 'docker'}
        {@const busy = busySessions.has(session.name)}

        <div class="session-card" class:active class:inactive={!active}>
          <div class="card-header">
            <span class="status-dot" class:active></span>
            <span class="session-name">{session.session_name ?? session.name}</span>
            <Badge text={role} variant={role} />
            <span class="backend-badge" class:vm={backend === 'utm'}>
              {backend === 'utm' ? 'vm' : 'container'}
            </span>
            {#if !activeProfile && session.workspace_profile}
              <span class="profile-badge">{session.workspace_profile}</span>
            {/if}
          </div>

          <div class="card-meta">
            {#if session.llm_provider}
              <span class="meta-item">{session.llm_provider}{session.llm_model ? ` / ${session.llm_model}` : ''}</span>
            {/if}
            {#if active && session.url}
              <a class="meta-url" href={session.url} target="_blank">{session.url.replace('http://', '')}</a>
            {/if}
          </div>

          <div class="card-actions">
            {#if busy}
              <span class="busy-indicator">
                <svg class="spinner" xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>
                working...
              </span>
            {:else if active}
              {#if session.url}
                <button class="btn-terminal" onclick={() => terminalSession = session}>terminal</button>
              {/if}
              <button class="btn-stop" onclick={() => handleStop(session.name)}>stop</button>
            {:else}
              <button class="btn-start" onclick={() => handleStart(session.name)}>start</button>
              <button class="btn-delete" onclick={() => handleDelete(session.name)}>delete</button>
            {/if}
          </div>
        </div>
      {/each}
    </div>
  {/if}

  <!-- Local agents -->
  {#if filteredLocal.length > 0}
    <div class="local-section">
      <h2 class="local-heading">local agents</h2>
      <div class="local-list">
        {#each filteredLocal as proc (proc.pid)}
          <div class="local-row">
            <span class="local-dot"></span>
            <span class="local-name">{proc.name}</span>
            {#if !activeProfile && proc.workspace_profile}
              <span class="local-profile-badge">{proc.workspace_profile}</span>
            {/if}
            <span class="local-stats">{proc.cpu_perc} cpu &middot; {proc.mem_mb} mem</span>
            <span class="local-meta">PID {proc.pid}</span>
            <button class="btn-focus" onclick={() => handleFocusTab(proc.tty)} title="Focus terminal tab ({proc.tty})">
              <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
              focus
            </button>
          </div>
        {/each}
      </div>
    </div>
  {/if}
</div>

{#if terminalSession}
  <div class="terminal-panel">
    <div class="terminal-header">
      <span class="terminal-title">
        <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>
        {terminalSession.session_name ?? terminalSession.name}
      </span>
      <div class="terminal-actions">
        <button class="terminal-pop" onclick={() => openInBrowser(terminalSession.url)} title="Open in browser" aria-label="Open terminal in browser">
          <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
        </button>
        <button class="terminal-close" onclick={() => terminalSession = null} title="Close terminal" aria-label="Close terminal">
          <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
      </div>
    </div>
    {#key terminalSession.name}
      {#if terminalUrl}
        <iframe
          class="terminal-frame"
          src={terminalUrl}
          title="Terminal — {terminalSession.session_name ?? terminalSession.name}"
        ></iframe>
      {:else}
        <div class="terminal-loading">connecting...</div>
      {/if}
    {/key}
  </div>
{/if}

{#if showNewModal}
  <Modal onClose={() => showNewModal = false}>
    {#snippet children()}
      <h2>new session</h2>
      <p class="modal-sub">create an isolated environment for agentic work</p>

      <div class="field">
        <label for="sname">name</label>
        <input id="sname" type="text" bind:value={newName} placeholder="my-session" />
      </div>

      <!-- Backend -->
      <div class="field">
        <label for="sbackend">backend</label>
        <div class="toggle-group" id="sbackend">
          <button class="toggle-opt" class:active={newBackend === 'docker'} onclick={() => newBackend = 'docker'}>
            <span class="toggle-icon">&#x1f4e6;</span> container
          </button>
          <button class="toggle-opt" class:active={newBackend === 'utm'} onclick={() => newBackend = 'utm'}>
            <span class="toggle-icon">&#x1f5a5;</span> vm
          </button>
        </div>
      </div>

      {#if newBackend === 'utm'}
        <div class="field">
          <label for="svmtpl">vm template</label>
          <input id="svmtpl" type="text" bind:value={newVMTemplate} placeholder="ubuntu-24.04-brainbox" />
          <p class="hint">name of the UTM template VM to clone</p>
        </div>
        <div class="field">
          <label for="sguestos">guest os</label>
          <select id="sguestos" bind:value={newGuestOS}>
            <option value="linux">linux</option>
            <option value="macos">macos</option>
            <option value="windows">windows</option>
          </select>
        </div>
      {/if}

      <!-- Profile -->
      <ProfilePicker bind:selected={newProfile} />

      <div class="field">
        <label for="srole">role</label>
        <select id="srole" bind:value={newRole}>
          <option value="developer">developer</option>
          <option value="supervisor">supervisor</option>
          <option value="worker">worker</option>
          <option value="merge-queue">merge-queue</option>
          <option value="pr-shepherd">pr-shepherd</option>
          <option value="reviewer">reviewer</option>
        </select>
      </div>

      <div class="field">
        <label for="sllm">llm provider</label>
        <select id="sllm" bind:value={newLLM}>
          <option value="claude">claude (anthropic)</option>
          <option value="codex">codex (openai)</option>
          {#if featureFlags.isEnabled('ollama')}
            <option value="ollama">ollama (local)</option>
          {/if}
        </select>
      </div>

      {#if newLLM === 'ollama'}
        <div class="field">
          <label for="smodel">model</label>
          <input id="smodel" type="text" bind:value={newModel} placeholder="qwen3-coder" />
        </div>
      {/if}

      {#if newLLM === 'codex'}
        <div class="field">
          <label for="scodexmodel">model</label>
          <input id="scodexmodel" type="text" bind:value={newModel} placeholder="codex-mini-latest" />
        </div>
      {/if}

      {#if newBackend === 'docker'}
        <div class="field">
          <label for="smounts">mount directories</label>
          {#if loadingDirs}
            <p class="hint">scanning directories...</p>
          {:else if profileDirs.length === 0}
            <p class="hint">{newProfile ? 'no directories found under workspace home' : 'select a profile first'}</p>
          {:else}
            <div class="dir-list" id="smounts">
              {#each profileDirs as dir (dir)}
                <label class="dir-item" class:selected={selectedDirs.has(dir)}>
                  <input type="checkbox" checked={selectedDirs.has(dir)} onchange={() => toggleDir(dir)} />
                  <span class="dir-icon">
                    <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z"/></svg>
                  </span>
                  <span class="dir-name">{dir}</span>
                </label>
              {/each}
            </div>
            {#if selectedDirs.size > 0}
              <p class="hint">{selectedDirs.size} director{selectedDirs.size === 1 ? 'y' : 'ies'} will be mounted into /home/developer/</p>
            {/if}
          {/if}
        </div>
      {/if}

      <div class="modal-actions">
        <button class="btn-cancel" onclick={() => showNewModal = false} disabled={isCreating}>cancel</button>
        <button class="btn-submit" onclick={handleCreate} disabled={isCreating || !newName.trim() || !newProfile}>
          {isCreating ? 'creating...' : 'create'}
        </button>
      </div>
    {/snippet}
  </Modal>
{/if}

<style>
  .panel { padding-bottom: 24px; }

  header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 20px;
  }

  h1 { font-size: 22px; font-weight: 600; }
  .accent { color: var(--color-accent); }

  .header-actions { display: flex; gap: 8px; align-items: center; }

  .new-btn {
    background: rgba(59, 130, 246, 0.1);
    border: 1px solid rgba(59, 130, 246, 0.3);
    color: var(--color-info);
    padding: 7px 14px;
    border-radius: var(--radius-lg);
    font-size: 13px;
    font-weight: 500;
    transition: all 0.15s;
  }
  .new-btn:hover { background: rgba(59, 130, 246, 0.2); border-color: var(--color-info); }

  .refresh-btn {
    background: none;
    border: 1px solid var(--color-border-secondary);
    color: var(--color-text-tertiary);
    padding: 6px;
    border-radius: var(--radius-md);
    display: flex;
    transition: all 0.15s;
  }
  .refresh-btn:hover { color: var(--color-text-primary); border-color: var(--color-text-tertiary); }

  .stats-row {
    display: flex;
    gap: 20px;
    margin-bottom: 20px;
    font-size: 13px;
  }
  .stat { color: var(--color-text-tertiary); }
  .stat-num { font-weight: 600; color: var(--color-text-secondary); }
  .stat.active .stat-num { color: var(--color-success); }
  .stat.stopped .stat-num { color: var(--color-text-tertiary); }

  .loading { color: var(--color-text-tertiary); font-size: 13px; padding: 24px 0; }

  .session-list { display: flex; flex-direction: column; gap: 12px; }

  .session-card {
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-primary);
    border-left: 3px solid var(--color-border-primary);
    border-radius: var(--radius-xl);
    padding: 14px 18px;
    transition: opacity 0.2s;
  }
  .session-card.active { border-left-color: var(--color-success); }
  .session-card.inactive { opacity: 0.55; }

  .card-header {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 8px;
  }

  .status-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: #374151;
    flex-shrink: 0;
  }
  .status-dot.active {
    background: var(--color-success);
    box-shadow: 0 0 6px rgba(16, 185, 129, 0.4);
  }

  .session-name {
    font-weight: 500;
    font-size: 14px;
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .backend-badge {
    font-size: 9px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    padding: 1px 6px;
    border-radius: 9999px;
    background: rgba(59, 130, 246, 0.1);
    color: var(--color-info);
    border: 1px solid rgba(59, 130, 246, 0.2);
    flex-shrink: 0;
  }
  .backend-badge.vm {
    background: rgba(168, 85, 247, 0.1);
    color: #d8b4fe;
    border-color: rgba(168, 85, 247, 0.2);
  }

  .profile-badge {
    font-size: 9px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    padding: 1px 6px;
    border-radius: 9999px;
    background: rgba(245, 158, 11, 0.1);
    color: var(--color-accent);
    border: 1px solid rgba(245, 158, 11, 0.2);
    flex-shrink: 0;
  }

  .card-meta {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 6px;
    font-size: 12px;
  }

  .meta-item {
    color: var(--color-text-tertiary);
  }

  .meta-url {
    color: var(--color-accent);
    text-decoration: none;
    font-family: var(--font-mono);
    font-size: 11px;
  }
  .meta-url:hover { text-decoration: underline; }

  .card-actions {
    margin-top: 10px;
    padding-top: 10px;
    border-top: 1px solid var(--color-border-primary);
    display: flex;
    gap: 8px;
  }

  .btn-stop, .btn-delete {
    background: rgba(239, 68, 68, 0.1);
    border: 1px solid rgba(239, 68, 68, 0.2);
    color: #f87171;
    padding: 4px 12px;
    border-radius: var(--radius-md);
    font-size: 12px;
    transition: all 0.15s;
  }
  .btn-stop { color: var(--color-error); border-color: rgba(239, 68, 68, 0.3); }
  .btn-stop:hover, .btn-delete:hover {
    background: rgba(239, 68, 68, 0.2);
    border-color: var(--color-error);
  }

  .btn-terminal {
    background: rgba(245, 158, 11, 0.1);
    border: 1px solid rgba(245, 158, 11, 0.3);
    color: var(--color-accent);
    padding: 4px 12px;
    border-radius: var(--radius-md);
    font-size: 12px;
    font-weight: 500;
    transition: all 0.15s;
  }
  .btn-terminal:hover { background: rgba(245, 158, 11, 0.2); border-color: var(--color-accent); }

  .btn-start {
    background: rgba(16, 185, 129, 0.1);
    border: 1px solid rgba(16, 185, 129, 0.3);
    color: var(--color-success);
    padding: 4px 12px;
    border-radius: var(--radius-md);
    font-size: 12px;
    transition: all 0.15s;
  }
  .btn-start:hover { background: rgba(16, 185, 129, 0.2); border-color: var(--color-success); }

  /* Modal */
  h2 { font-size: 18px; font-weight: 600; margin-bottom: 4px; }
  .modal-sub { font-size: 12px; color: var(--color-text-muted); margin-bottom: 20px; }
  .field { margin-bottom: 14px; }

  .hint {
    font-size: 11px;
    color: var(--color-text-tertiary);
    margin-top: 4px;
  }

  /* Backend toggle */
  .toggle-group {
    display: flex;
    gap: 8px;
  }

  .toggle-opt {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    background: var(--color-bg-tertiary);
    border: 1px solid var(--color-border-secondary);
    border-radius: var(--radius-md);
    color: var(--color-text-tertiary);
    padding: 8px 12px;
    font-size: 13px;
    transition: all 0.15s;
  }
  .toggle-opt:hover {
    color: var(--color-text-secondary);
    border-color: var(--color-text-tertiary);
  }
  .toggle-opt.active {
    background: rgba(59, 130, 246, 0.1);
    border-color: rgba(59, 130, 246, 0.4);
    color: var(--color-info);
    font-weight: 500;
  }

  .toggle-icon { font-size: 16px; }

  /* Directory list */
  .dir-list {
    display: flex;
    flex-direction: column;
    gap: 2px;
    max-height: 180px;
    overflow-y: auto;
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-md);
    padding: 4px;
    background: var(--color-bg-primary);
  }

  .dir-item {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 8px;
    border-radius: var(--radius-sm);
    font-size: 13px;
    color: var(--color-text-secondary);
    cursor: pointer;
    transition: background 0.1s;
    text-transform: none;
    letter-spacing: normal;
    font-weight: normal;
    margin-bottom: 0;
  }
  .dir-item:hover { background: rgba(255, 255, 255, 0.03); }
  .dir-item.selected {
    background: rgba(59, 130, 246, 0.08);
    color: var(--color-text-primary);
  }
  .dir-item input[type="checkbox"] { width: auto; flex-shrink: 0; }

  .dir-icon {
    color: var(--color-text-tertiary);
    display: flex;
    flex-shrink: 0;
  }
  .dir-item.selected .dir-icon { color: var(--color-info); }

  .dir-name { font-family: var(--font-mono); font-size: 12px; }

  /* Profile picker */
  .modal-actions {
    display: flex;
    gap: 8px;
    justify-content: flex-end;
    margin-top: 20px;
  }

  .btn-cancel {
    background: none;
    border: 1px solid var(--color-border-secondary);
    color: var(--color-text-secondary);
    padding: 7px 16px;
    border-radius: var(--radius-md);
    font-size: 13px;
    transition: all 0.15s;
  }
  .btn-cancel:hover { background: rgba(255,255,255,0.05); }

  .btn-submit {
    background: rgba(59, 130, 246, 0.1);
    border: 1px solid rgba(59, 130, 246, 0.3);
    color: var(--color-info);
    padding: 7px 16px;
    border-radius: var(--radius-md);
    font-size: 13px;
    font-weight: 500;
    transition: all 0.15s;
  }
  .btn-submit:hover { background: rgba(59, 130, 246, 0.2); border-color: var(--color-info); }

  /* === Local agents === */
  .local-section {
    margin-top: 28px;
  }

  .local-heading {
    font-size: 13px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--color-text-tertiary);
    margin-bottom: 12px;
  }

  .local-list {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .local-row {
    display: flex;
    align-items: center;
    gap: 10px;
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-primary);
    border-left: 3px solid var(--color-success);
    border-radius: var(--radius-xl);
    padding: 10px 14px;
  }

  .local-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--color-success);
    box-shadow: 0 0 6px rgba(34, 197, 94, 0.4);
    flex-shrink: 0;
  }

  .local-name {
    font-size: 13px;
    font-weight: 500;
    color: var(--color-text-primary);
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .local-profile-badge {
    font-size: 9px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    padding: 1px 6px;
    border-radius: 9999px;
    background: rgba(245, 158, 11, 0.1);
    color: var(--color-accent);
    border: 1px solid rgba(245, 158, 11, 0.2);
    flex-shrink: 0;
  }

  .local-stats {
    font-size: 11px;
    color: var(--color-text-secondary);
    font-family: var(--font-mono);
    flex-shrink: 0;
  }

  .local-meta {
    font-size: 11px;
    color: var(--color-text-tertiary);
    font-family: var(--font-mono);
    flex-shrink: 0;
  }

  .local-tty {
    font-size: 11px;
    color: var(--color-text-tertiary);
    font-family: var(--font-mono);
    flex-shrink: 0;
  }

  .btn-focus {
    display: flex;
    align-items: center;
    gap: 4px;
    background: rgba(245, 158, 11, 0.1);
    border: 1px solid rgba(245, 158, 11, 0.25);
    color: var(--color-accent);
    padding: 4px 10px;
    border-radius: var(--radius-md);
    font-size: 11px;
    font-weight: 500;
    flex-shrink: 0;
    transition: all 0.15s;
  }
  .btn-focus:hover { background: rgba(245, 158, 11, 0.2); border-color: var(--color-accent); }

  /* Terminal panel */
  .terminal-panel {
    position: fixed;
    bottom: 28px; /* above StatusBar */
    left: var(--sidebar-width);
    right: 0;
    height: 50vh;
    display: flex;
    flex-direction: column;
    background: #000;
    border-top: 1px solid var(--color-border-secondary);
    z-index: 100;
  }

  :global(.sidebar-collapsed) .terminal-panel {
    left: var(--sidebar-collapsed-width);
  }

  .terminal-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 6px 12px;
    background: var(--color-bg-secondary);
    border-bottom: 1px solid var(--color-border-primary);
    flex-shrink: 0;
  }

  .terminal-title {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 12px;
    font-weight: 500;
    color: var(--color-text-secondary);
  }

  .terminal-actions {
    display: flex;
    gap: 4px;
  }

  .terminal-pop,
  .terminal-close {
    background: transparent;
    border: none;
    color: var(--color-text-tertiary);
    padding: 4px;
    border-radius: var(--radius-sm);
    display: flex;
    transition: all 0.15s;
  }
  .terminal-pop:hover,
  .terminal-close:hover {
    color: var(--color-text-primary);
    background: rgba(255, 255, 255, 0.08);
  }

  .terminal-frame {
    flex: 1;
    border: none;
    width: 100%;
    background: #000;
  }

  .terminal-loading {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--color-text-tertiary);
    font-size: 13px;
    background: #000;
  }
</style>

<script lang="ts">
  /**
   * Create-session modal — extracted from SessionsPanel. Owns the whole new-
   * session form (backend/provider/model, mounts, task, continue-from, dispatch
   * preview) and the create action. Props feed the shared bits it can't own
   * (profiles, roles, handoff candidates, local-runner status); it signals the
   * parent via onClose / onCreated (the latter triggers the panel refresh).
   * Mounted only while open, so init here == the old openCreateModal reset.
   */
  import { getApi } from '../utils/api';
  import { notifications } from '../notifications.svelte';
  import { featureFlags } from '../stores.svelte';
  import Modal from './Modal.svelte';
  import ProfilePicker from './ProfilePicker.svelte';

  let {
    profiles,
    defaultProfile,
    availableRoles,
    handoffCandidates,
    localRunnerName,
    localRunnerActive,
    onClose,
    onCreated,
  }: {
    profiles: any[];
    defaultProfile: string;
    availableRoles: string[];
    handoffCandidates: string[];
    localRunnerName: string;
    localRunnerActive: boolean;
    onClose: () => void;
    onCreated: () => void;
  } = $props();

  const CODEX_MODELS = [
    'gpt-5.4',
    'gpt-5.2-codex',
    'gpt-5.1-codex-max',
    'gpt-5.4-mini',
    'gpt-5.3-codex',
    'gpt-5.2',
    'gpt-5.1-codex-mini',
  ];

  const CLAUDE_MODELS = [
    'claude-opus-4-7',
    'claude-opus-4-6',
    'claude-sonnet-4-6',
    'claude-haiku-4-5-20251001',
  ];

  let newName = $state('');
  let newRole = $state('assistant');
  let newLLM = $state('claude');
  let newModel = $state('');
  let ollamaModels = $state<string[]>([]);
  let ollamaModelError = $state('');
  let newBackend = $state('docker');
  let newVMTemplate = $state('');
  let newGuestOS = $state('linux');
  let newProfile = $state(defaultProfile);
  let newRunner = $state('');
  let isCreating = $state(false);
  let mountPaths = $state<string[]>([]);
  let localWorkDir = $state('');
  let localWorkDirTouched = $state(false);
  let localRecentDirs = $state<string[]>([]);
  let newTask = $state('');
  let continueFrom = $state('');

  interface DispatchCandidate {
    name: string;
    online: boolean;
    tags: string[];
    version: string;
    supports_backend: boolean;
  }
  interface DispatchPreview {
    selected_runner: string | null;
    in_process: boolean;
    reason: string;
    candidates: DispatchCandidate[];
    error?: string;
  }
  let preview = $state<DispatchPreview | null>(null);
  let previewError = $state<string | null>(null);

  function truncatePath(p: string, max = 28): string {
    if (p.length <= max) return p;
    const parts = p.split('/');
    const tail = parts.slice(-2).join('/');
    return tail.length + 4 >= max ? '…/' + parts[parts.length - 1] : '…/' + tail;
  }

  function loadLocalRecents() {
    try {
      const raw = localStorage.getItem('local_runner_recent_dirs');
      localRecentDirs = raw ? JSON.parse(raw) : [];
    } catch { localRecentDirs = []; }
  }

  function saveLocalRecent(dir: string) {
    if (!dir) return;
    const next = [dir, ...localRecentDirs.filter(d => d !== dir)].slice(0, 5);
    localRecentDirs = next;
    try { localStorage.setItem('local_runner_recent_dirs', JSON.stringify(next)); } catch {}
  }

  async function browseLocalWorkDir() {
    const a = await getApi();
    if (!a) return;
    try {
      const dir = await a.BrowseFolder();
      if (dir) { localWorkDir = dir; localWorkDirTouched = true; }
    } catch {}
  }

  function markWorkDirTouched() { localWorkDirTouched = true; }

  // Keep localWorkDir in sync with the selected profile's workspace_home until
  // the user types their own value.
  $effect(() => {
    if (localWorkDirTouched) return;
    const p = profiles.find(pp => pp.name === newProfile);
    const home = p?.workspace_home ?? '';
    if (home && home !== localWorkDir) localWorkDir = home;
  });

  async function refreshPreview() {
    const a = await getApi();
    if (!a) return;
    try {
      const dispatchBackend = newBackend === 'local' ? 'docker' : newBackend;
      const dispatchRunner = newBackend === 'local' ? localRunnerName : newRunner;
      preview = (await a.PreviewDispatch({
        backend: dispatchBackend,
        runner: dispatchRunner,
      })) as DispatchPreview;
      previewError = null;
    } catch (err: any) {
      previewError = `${err?.message ?? err}`;
      preview = null;
    }
  }

  // Re-preview whenever backend or runner changes.
  $effect(() => {
    void newBackend; void newRunner;
    void refreshPreview();
  });

  async function loadOllamaModels() {
    const a = await getApi();
    if (!a) return;
    try {
      const models = (await a.ListOllamaModels()) ?? [];
      ollamaModels = models.map((m: any) => m.name ?? m);
      ollamaModelError = '';
    } catch (err: any) {
      ollamaModels = [];
      ollamaModelError = `${err?.message ?? err}`;
    }
  }

  async function browseAndAddMount() {
    const a = await getApi();
    if (!a) return;
    try {
      const path = await a.BrowseFolder();
      if (path && !mountPaths.includes(path)) {
        mountPaths = [...mountPaths, path];
      }
    } catch {}
  }

  function removeMount(path: string) {
    mountPaths = mountPaths.filter(p => p !== path);
  }

  // Set a sensible default model when switching LLM provider.
  let prevLLM = $state('');
  $effect(() => {
    if (newLLM !== prevLLM) {
      prevLLM = newLLM;
      if (newLLM === 'codex') newModel = 'gpt-5.4';
      else if (newLLM === 'ollama') {
        loadOllamaModels().then(() => { newModel = ollamaModels[0] ?? ''; });
      }
      else newModel = CLAUDE_MODELS[0] ?? '';
    }
  });

  async function handleCreate() {
    if (!newProfile) return;
    if (newBackend !== 'local' && !newName.trim()) return;
    isCreating = true;
    const a = await getApi();
    if (!a) { isCreating = false; return; }

    // Local backend: open iTerm2 directly. The process surfaces in the local
    // section via ps detection, so no brainbox session / refresh is needed.
    if (newBackend === 'local') {
      try {
        const profile = profiles.find(p => p.name === newProfile);
        const dir = localWorkDir || profile?.workspace_home || '';
        await a.OpenLocalSession(dir);
        saveLocalRecent(dir);
        notifications.success('Local session opened');
        onClose();
      } catch (err: any) {
        notifications.error(`Failed to open local session: ${err}`);
      } finally {
        isCreating = false;
      }
      return;
    }

    try {
      const profile = profiles.find(p => p.name === newProfile);
      const wsHome = profile?.workspace_home ?? '';
      const volumes = mountPaths.map(p => {
        const name = p.split('/').pop() || p;
        return `${p}:/home/developer/${name}`;
      });
      const req: Record<string, any> = {
        name: newName.trim().replace(/\s+/g, '-').toLowerCase(),
        role: newRole,
        llm_provider: newLLM,
        llm_model: newLLM === 'ollama' || newLLM === 'codex' ? newModel : '',
        workspace_profile: newProfile,
        workspace_home: wsHome,
        task: newTask.trim() || undefined,
        continue_from: continueFrom || undefined,
        backend: newBackend,
        runner: newRunner || undefined,
        volumes: volumes.length > 0 ? volumes : undefined,
      };
      if (newBackend === 'utm') {
        req.vm_template = newVMTemplate;
        req.guest_os = newGuestOS;
      }
      const resp = await a.CreateSession(req);
      if (resp.success ?? resp.Success) {
        notifications.success(`Created session: ${newName}`);
        onCreated();
      } else {
        notifications.error(resp.error ?? resp.Error ?? 'Failed to create session');
      }
    } catch (err: any) {
      notifications.error(`Failed to create: ${err}`);
    } finally {
      isCreating = false;
    }
  }

  // Init (was openCreateModal): the component mounts fresh each time the modal
  // opens, so run the one-time setup here.
  loadLocalRecents();
  loadOllamaModels();
</script>

  <Modal onClose={onClose}>
    {#snippet children()}
      <h2>new session</h2>
      <p class="modal-sub">create an isolated environment for agentic work</p>

      {#if newBackend !== 'local'}
        <div class="field">
          <label for="sname">name</label>
          <input id="sname" type="text" bind:value={newName} placeholder="my-session" />
        </div>
      {/if}

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
          <button class="toggle-opt" class:active={newBackend === 'local'} onclick={() => newBackend = 'local'}>
            <span class="toggle-icon">&#x1f4bb;</span> local
          </button>
        </div>
      </div>

      {#if newBackend === 'local'}
        <!-- Local working directory -->
        <div class="field">
          <label for="slocaldir">working directory</label>
          <div class="input-row">
            <input id="slocaldir" type="text" bind:value={localWorkDir} oninput={markWorkDirTouched} placeholder="~/workspaces/profiles/personal" />
            <button class="btn-browse" onclick={browseLocalWorkDir}>browse</button>
          </div>
          {#if localRecentDirs.length > 0}
            <div class="recent-dirs">
              {#each localRecentDirs as dir (dir)}
                <button class="recent-dir" onclick={() => { localWorkDir = dir; localWorkDirTouched = true; }} title={dir}>
                  {truncatePath(dir, 40)}
                </button>
              {/each}
            </div>
          {/if}
          {#if !localRunnerActive}
            <p class="hint warn-hint">local runner not enabled — configure in Settings</p>
          {:else}
            <p class="hint">runs as <code>claude --dangerously-skip-permissions</code> on this Mac via runner <strong>{localRunnerName}</strong></p>
          {/if}
        </div>
      {:else}
        <!-- Dispatch (runner picker + preview) -->
        <div class="field">
          <label for="srunner">dispatch to</label>
          <select id="srunner" bind:value={newRunner}>
            <option value="">in-process (API host)</option>
            {#if preview}
              {#each preview.candidates as c (c.name)}
                <option value={c.name} disabled={!c.online}>
                  {c.name}{c.online ? '' : ' (offline)'}{c.tags.length ? ` · ${c.tags.join(',')}` : ''}
                </option>
              {/each}
            {/if}
          </select>
          {#if preview}
            <p
              class="preview"
              class:warn={preview.error === 'stale' || preview.error === 'missing_capability'}
              class:err={preview.error === 'not_registered'}
            >
              → {preview.reason}
            </p>
          {:else if previewError}
            <p class="preview err">→ preview failed: {previewError}</p>
          {/if}
        </div>
      {/if}

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
          {#each availableRoles as role (role)}
            <option value={role}>{role}</option>
          {/each}
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
          <select id="smodel" bind:value={newModel}>
            {#each ollamaModels as m}
              <option value={m}>{m}</option>
            {/each}
            {#if newModel && !ollamaModels.includes(newModel)}
              <option value={newModel}>{newModel}</option>
            {/if}
          </select>
          {#if ollamaModels.length === 0}
            <p class="field-hint" style="color: var(--color-error, #c33); font-size: 11px; margin-top: 4px;">
              {ollamaModelError
                ? `No models — ${ollamaModelError}`
                : 'No models found. Pull one from Integrations → Ollama, or check the service is running.'}
            </p>
          {/if}
        </div>
      {/if}

      {#if newLLM === 'codex'}
        <div class="field">
          <label for="scodexmodel">model</label>
          <select id="scodexmodel" bind:value={newModel}>
            {#each CODEX_MODELS as m}
              <option value={m}>{m}</option>
            {/each}
          </select>
        </div>
      {/if}

      {#if newLLM === 'claude'}
        <div class="field">
          <label for="sclaudemodel">model</label>
          <select id="sclaudemodel" bind:value={newModel}>
            <option value="">— default —</option>
            {#each CLAUDE_MODELS as m}
              <option value={m}>{m}</option>
            {/each}
          </select>
        </div>
      {/if}

      {#if newBackend === 'docker'}
        <div class="field">
          <label for="smounts">mounts</label>
          <div class="mount-list" id="smounts">
            {#each mountPaths as path (path)}
              <div class="mount-row">
                <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z"/></svg>
                <span class="mount-path" title={path}>{truncatePath(path, 40)}</span>
                <button class="mount-remove" onclick={() => removeMount(path)} title="Remove mount" aria-label="Remove {path}">
                  <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                </button>
              </div>
            {/each}
            <button class="mount-add" onclick={browseAndAddMount}>
              <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 5v14"/><path d="M5 12h14"/></svg>
              add folder
            </button>
          </div>
        </div>
      {/if}

      <!-- Optional task -->
      <div class="field">
        <label for="stask">task (optional)</label>
        <textarea id="stask" bind:value={newTask} rows="3" placeholder="Describe what the agent should do after starting..."></textarea>
        <p class="hint">if provided, the agent will start working on this immediately</p>
      </div>

      <!-- Continue from a prior session's handoff -->
      <div class="field">
        <label for="scontinue">continue from (optional)</label>
        <select id="scontinue" bind:value={continueFrom}>
          <option value="">— none —</option>
          {#each handoffCandidates as s (s)}
            <option value={s}>{s}</option>
          {/each}
        </select>
        <p class="hint">prepends that session's stored handoff into this task — fails if it never saved one</p>
      </div>

      <div class="modal-actions">
        <button class="btn-cancel" onclick={onClose} disabled={isCreating}>cancel</button>
        <button class="btn-submit" onclick={handleCreate} disabled={isCreating || (newBackend !== 'local' && !newName.trim()) || !newProfile}>
          {isCreating ? 'creating...' : 'create'}
        </button>
      </div>
    {/snippet}
  </Modal>

<style>
  h2 { font-size: 18px; font-weight: 600; margin-bottom: 4px; }
  .modal-sub { font-size: 12px; color: var(--color-text-muted); margin-bottom: 20px; }
  .field { margin-bottom: 14px; }

  .hint {
    font-size: 11px;
    color: var(--color-text-tertiary);
    margin-top: 4px;
  }
  .warn-hint { color: var(--color-warning, #e0a64a); }

  /* Local backend */
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

  .recent-dirs {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    margin-top: 6px;
  }
  .recent-dir {
    background: var(--color-bg-tertiary);
    border: 1px solid var(--color-border-secondary);
    border-radius: var(--radius-sm);
    color: var(--color-text-tertiary);
    font-size: 11px;
    font-family: var(--font-mono);
    padding: 2px 8px;
    cursor: pointer;
    max-width: 220px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .recent-dir:hover { color: var(--color-accent); border-color: var(--color-accent); }

  .preview {
    font-size: 11px;
    color: var(--color-text-secondary);
    margin-top: 6px;
    line-height: 1.4;
  }
  .preview.warn { color: var(--color-warning, #e0a64a); }
  .preview.err  { color: var(--color-danger, #e54); }

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

  /* Mount list */
  .mount-list {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .mount-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 8px;
    background: var(--color-bg-primary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm);
    color: var(--color-text-tertiary);
  }

  .mount-path {
    flex: 1;
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--color-text-secondary);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .mount-remove {
    background: transparent;
    border: none;
    color: var(--color-text-tertiary);
    padding: 2px;
    display: flex;
    border-radius: var(--radius-sm);
    transition: all 0.15s;
  }
  .mount-remove:hover { color: var(--color-error); }

  .mount-add {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    padding: 6px;
    background: transparent;
    border: 1px dashed var(--color-border-secondary);
    border-radius: var(--radius-sm);
    color: var(--color-text-tertiary);
    font-size: 12px;
    transition: all 0.15s;
  }
  .mount-add:hover {
    border-color: var(--color-text-tertiary);
    color: var(--color-text-secondary);
    background: rgba(255, 255, 255, 0.02);
  }

  /* Profile picker */
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
</style>

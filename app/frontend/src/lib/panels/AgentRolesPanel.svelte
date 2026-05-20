<script lang="ts">
  import { getApi } from '../utils/api';
  import { onMount } from 'svelte';
  import { notifications } from '../notifications.svelte';
  import { brainboxEvents } from '../events.svelte';
  import { profileState } from '../stores.svelte';
  import EmptyState from '../components/EmptyState.svelte';
  import Modal from '../components/Modal.svelte';
  import ProfilePicker from '../components/ProfilePicker.svelte';

  // --- CLI Tools (PATH-detected coding agents) ---
  interface CliAgent {
    id: string;
    binary: string;
    label: string;
    path: string;
    version: string;
    enabled: boolean;
    detected: boolean;
    detected_at: string;
  }
  let cliAgents = $state<CliAgent[]>([]);
  let cliLoading = $state(true);
  let cliRescanning = $state(false);
  let cliExpanded = $state(false);

  async function loadCliAgents() {
    const a = await getApi();
    if (!a) { cliLoading = false; return; }
    try {
      cliAgents = (await a.ListAgents()) ?? [];
    } catch {} finally {
      cliLoading = false;
    }
  }

  async function rescanCliAgents() {
    cliRescanning = true;
    const a = await getApi();
    if (!a) { cliRescanning = false; return; }
    try {
      cliAgents = (await a.RescanAgents()) ?? [];
      notifications.success('Agents rescanned');
    } catch (err: any) {
      notifications.error(`Rescan failed: ${err?.message ?? err}`);
    } finally {
      cliRescanning = false;
    }
  }

  async function toggleCliAgent(agent: CliAgent) {
    if (!agent.detected) return;
    const next = !agent.enabled;
    cliAgents = cliAgents.map(a => a.id === agent.id ? { ...a, enabled: next } : a);
    const a = await getApi();
    if (!a) return;
    try {
      await a.SetAgentEnabled(agent.id, next);
    } catch (err: any) {
      cliAgents = cliAgents.map(a => a.id === agent.id ? { ...a, enabled: !next } : a);
      notifications.error(`Failed to toggle ${agent.label}: ${err?.message ?? err}`);
    }
  }

  const BUILTIN_AGENTS = new Set(['assistant']);
  const ALL_CAPABILITIES = ['shell_exec', 'read_code', 'write_code', 'hub_messaging'];

  const CLAUDE_MODELS = [
    'claude-opus-4-6',
    'claude-sonnet-4-6',
    'claude-haiku-4-5-20251001',
  ];

  const CODEX_MODELS = [
    'gpt-5.4',
    'gpt-5.2-codex',
    'gpt-5.1-codex-max',
    'gpt-5.4-mini',
    'gpt-5.3-codex',
    'gpt-5.2',
    'gpt-5.1-codex-mini',
  ];

  let ollamaModels = $state<string[]>([]);

  let agents = $state<any[]>([]);
  let loading = $state(true);
  let showModal = $state(false);
  let editingAgent = $state<any | null>(null); // null = create mode
  let confirmDelete = $state<string | null>(null);
  let saving = $state(false);
  let deleting = $state<string | null>(null);

  const KNOWN_CATEGORIES = ['general', 'development', 'orchestration'];

  // Track which categories are expanded; everything else is collapsed by default
  let expandedCategories = $state(new Set<string>());

  // Derived: agents grouped by category (preserves insertion order within each group)
  let agentsByCategory = $derived.by(() => {
    const map = new Map<string, any[]>();
    for (const ag of agents) {
      const cat = ag.category || 'general';
      if (!map.has(cat)) map.set(cat, []);
      map.get(cat)!.push(ag);
    }
    // Sort categories: known ones first, then unknown alphabetically
    const sorted = new Map<string, any[]>();
    for (const cat of KNOWN_CATEGORIES) {
      if (map.has(cat)) sorted.set(cat, map.get(cat)!);
    }
    for (const [cat, list] of map) {
      if (!sorted.has(cat)) sorted.set(cat, list);
    }
    return sorted;
  });

  // Team launch modal
  let showTeamModal = $state(false);
  let teamCategory = $state('');
  let teamTask = $state('');
  let teamProvider = $state('claude');
  let teamModel = $state('');
  let teamProfile = $state('');
  let launchingTeam = $state(false);

  // Form state
  let formName = $state('');
  let formImage = $state('brainbox');
  let formDescription = $state('');
  let formCategory = $state('general');
  let formSpawnMode = $state('container');
  let formCapabilities = $state<string[]>([]);
  let formHardened = $state(false);
  let formPersistent = $state(false);
  let formPrompt = $state('');
  // Per-provider model/effort defaults
  let formClaudeModel = $state('');
  let formClaudeEffort = $state('');
  let formCodexModel = $state('');
  let formOllamaModel = $state('');

  let isEditMode = $derived(editingAgent !== null);

  async function refresh() {
    loading = true;
    const a = await getApi();
    if (!a) { loading = false; return; }
    try {
      agents = (await a.ListAgentRoles()) ?? [];
    } catch (err: any) {
      notifications.error(`Failed to load agents: ${err?.message ?? err}`);
    } finally {
      loading = false;
    }
  }

  function toggleCategory(cat: string) {
    const next = new Set(expandedCategories);
    if (next.has(cat)) next.delete(cat);
    else next.add(cat);
    expandedCategories = next;
  }

  function openCreate(defaultCategory = 'general') {
    editingAgent = null;
    formName = '';
    formImage = 'brainbox';
    formDescription = '';
    formCategory = defaultCategory;
    formSpawnMode = 'container';
    formCapabilities = [];
    formHardened = false;
    formPersistent = false;
    formPrompt = '';
    formClaudeModel = '';
    formClaudeEffort = '';
    formCodexModel = '';
    formOllamaModel = '';
    showModal = true;
  }

  async function openClone(agent: any) {
    editingAgent = null; // create mode
    formName = `${agent.name}-copy`;
    formImage = agent.image ?? 'brainbox';
    formDescription = agent.description ?? '';
    formCategory = agent.category ?? 'general';
    formSpawnMode = agent.spawn_mode ?? 'container';
    formCapabilities = [...(agent.capabilities ?? [])];
    formHardened = agent.hardened ?? false;
    formPersistent = agent.persistent ?? false;
    formClaudeModel = agent.claude_model ?? '';
    formClaudeEffort = agent.claude_effort ?? '';
    formCodexModel = agent.codex_model ?? '';
    formOllamaModel = agent.ollama_model ?? '';
    formPrompt = '';

    // Fetch prompt content from the source agent
    const a = await getApi();
    if (a) {
      try {
        const detail = await a.GetAgentRole(agent.name);
        formPrompt = detail.role_prompt_content ?? '';
        formClaudeModel = detail.claude_model ?? '';
        formClaudeEffort = detail.claude_effort ?? '';
        formCodexModel = detail.codex_model ?? '';
        formOllamaModel = detail.ollama_model ?? '';
      } catch {}
    }
    showModal = true;
  }

  async function openEdit(agent: any) {
    editingAgent = agent;
    formName = agent.name;
    formImage = agent.image ?? 'brainbox';
    formDescription = agent.description ?? '';
    formCategory = agent.category ?? 'general';
    formSpawnMode = agent.spawn_mode ?? 'container';
    formCapabilities = [...(agent.capabilities ?? [])];
    formHardened = agent.hardened ?? false;
    formPersistent = agent.persistent ?? false;
    formPrompt = agent.role_prompt_content ?? '';
    formClaudeModel = agent.claude_model ?? '';
    formClaudeEffort = agent.claude_effort ?? '';
    formCodexModel = agent.codex_model ?? '';
    formOllamaModel = agent.ollama_model ?? '';

    // Fetch full detail to get prompt content
    const a = await getApi();
    if (a) {
      try {
        const detail = await a.GetAgentRole(agent.name);
        formPrompt = detail.role_prompt_content ?? '';
        formClaudeModel = detail.claude_model ?? '';
        formClaudeEffort = detail.claude_effort ?? '';
        formCodexModel = detail.codex_model ?? '';
        formOllamaModel = detail.ollama_model ?? '';
      } catch {}
    }
    showModal = true;
  }

  function toggleCapability(cap: string) {
    if (formCapabilities.includes(cap)) {
      formCapabilities = formCapabilities.filter(c => c !== cap);
    } else {
      formCapabilities = [...formCapabilities, cap];
    }
  }

  async function handleSave() {
    if (!formName.trim() && !isEditMode) return;
    saving = true;
    const a = await getApi();
    if (!a) { saving = false; return; }
    try {
      if (isEditMode) {
        await a.UpdateAgent(editingAgent.name, {
          image: formImage || undefined,
          description: formDescription,
          category: formCategory || 'general',
          spawn_mode: formSpawnMode || 'container',
          capabilities: formCapabilities,
          hardened: formHardened,
          persistent: formPersistent,
          role_prompt_content: formPrompt,
          claude_model: formClaudeModel || '',
          claude_effort: formClaudeEffort || '',
          codex_model: formCodexModel || '',
          ollama_model: formOllamaModel || '',
        });
        notifications.success(`Agent '${editingAgent.name}' updated`);
      } else {
        await a.CreateAgent({
          name: formName.trim(),
          image: formImage || 'brainbox',
          description: formDescription,
          category: formCategory || 'general',
          spawn_mode: formSpawnMode || 'container',
          capabilities: formCapabilities,
          hardened: formHardened,
          persistent: formPersistent,
          role_prompt_content: formPrompt || undefined,
          claude_model: formClaudeModel || undefined,
          claude_effort: formClaudeEffort || undefined,
          codex_model: formCodexModel || undefined,
          ollama_model: formOllamaModel || undefined,
        });
        notifications.success(`Agent '${formName.trim()}' created`);
      }
      showModal = false;
      refresh();
    } catch (err: any) {
      notifications.error(`Failed to save agent: ${err?.message ?? err}`);
    } finally {
      saving = false;
    }
  }

  async function handleDelete(name: string) {
    if (confirmDelete !== name) {
      confirmDelete = name;
      return;
    }
    deleting = name;
    confirmDelete = null;
    const a = await getApi();
    if (!a) { deleting = null; return; }
    try {
      await a.DeleteAgent(name);
      notifications.success(`Agent '${name}' deleted`);
      refresh();
    } catch (err: any) {
      notifications.error(`Failed to delete agent: ${err?.message ?? err}`);
    } finally {
      deleting = null;
    }
  }

  function openTeamModal(category: string) {
    teamCategory = category;
    teamTask = '';
    teamProvider = 'claude';
    teamModel = '';
    teamProfile = profileState.active?.name ?? profileState.profiles[0]?.name ?? '';
    showTeamModal = true;
  }

  async function handleLaunchTeam() {
    if (!teamTask.trim()) return;
    launchingTeam = true;
    const a = await getApi();
    if (!a) { launchingTeam = false; return; }
    try {
      const profile = profileState.profiles.find(p => p.name === teamProfile);
      const wsHome = profile?.workspace_home ?? '';
      await a.LaunchTeam(teamCategory, teamTask.trim(), teamProvider, teamModel, teamProfile, wsHome);
      notifications.success(`Team '${teamCategory}' launched`);
      showTeamModal = false;
    } catch (err: any) {
      notifications.error(`Failed to launch team: ${err?.message ?? err}`);
    } finally {
      launchingTeam = false;
    }
  }

  $effect(() => {
    const lastEvent = brainboxEvents.last;
    if (!lastEvent) return;
    try {
      const parsed = typeof lastEvent === 'string' ? JSON.parse(lastEvent) : lastEvent;
      const action = parsed?.action;
      if (action === 'agent.created' || action === 'agent.updated' || action === 'agent.deleted') {
        refresh();
      }
    } catch {}
  });

  async function loadOllamaModels() {
    const a = await getApi();
    if (!a) return;
    try {
      const models = (await a.ListOllamaModels()) ?? [];
      ollamaModels = models.map((m: any) => m.name ?? m);
    } catch {}
  }

  onMount(() => {
    refresh();
    loadOllamaModels();
    loadCliAgents();
  });
</script>

<div class="panel">
  <div class="panel-header">
    <h1 class="panel-title">Agents</h1>
    <button class="btn-primary" onclick={() => openCreate()}>+ new agent</button>
  </div>

  {#if loading}
    <div class="loading">loading agents…</div>
  {:else if agents.length === 0}
    <EmptyState
      title="no agents"
      description="Create a custom agent role with a system prompt."
    />
  {:else}
    {#each agentsByCategory as [category, categoryAgents] (category)}
      <div class="category-section">
        <div class="category-header" role="button" tabindex="0"
          onclick={() => toggleCategory(category)}
          onkeydown={(e) => e.key === 'Enter' || e.key === ' ' ? toggleCategory(category) : null}
        >
          <span class="category-toggle-icon" class:collapsed={!expandedCategories.has(category)}>
            <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg>
          </span>
          <span class="category-label">{category}</span>
          <span class="category-count">{categoryAgents.length}</span>
          <div class="category-actions" role="none" onclick={(e) => e.stopPropagation()}>
            <button class="btn-launch-team" onclick={() => openTeamModal(category)} title="Launch a supervised team from this category">
              <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="5 3 19 12 5 21 5 3"/></svg>
              launch team
            </button>
            <button class="btn-add-to-category" onclick={() => openCreate(category)} title="Add agent to this category">+ add</button>
          </div>
        </div>
        {#if expandedCategories.has(category)}
        <div class="agent-list">
          {#each categoryAgents as agent (agent.name)}
            <div class="agent-card">
              <div class="agent-header">
                <div class="agent-meta">
                  <span class="agent-name">{agent.name}</span>
                  {#if agent.spawn_mode === 'subagent'}
                    <span class="badge badge-purple" title="Spawned internally by Claude Code / Codex">subagent</span>
                  {/if}
                  {#if agent.persistent}
                    <span class="badge badge-blue">persistent</span>
                  {/if}
                  {#if agent.hardened}
                    <span class="badge badge-orange">hardened</span>
                  {/if}
                  {#if BUILTIN_AGENTS.has(agent.name)}
                    <span class="badge badge-gray">built-in</span>
                  {/if}
                </div>
                <div class="agent-actions">
                  <button class="btn-icon" onclick={() => openClone(agent)} title="Clone agent">
                    <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
                  </button>
                  <button class="btn-icon" onclick={() => openEdit(agent)} title="Edit agent">
                    <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                  </button>
                  {#if !BUILTIN_AGENTS.has(agent.name)}
                    {#if confirmDelete === agent.name}
                      <button class="btn-danger-sm" onclick={() => handleDelete(agent.name)} disabled={deleting === agent.name}>
                        {deleting === agent.name ? 'deleting…' : 'confirm delete'}
                      </button>
                      <button class="btn-cancel-sm" onclick={() => confirmDelete = null}>cancel</button>
                    {:else}
                      <button class="btn-icon btn-icon-danger" onclick={() => handleDelete(agent.name)} title="Delete agent">
                        <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>
                      </button>
                    {/if}
                  {/if}
                </div>
              </div>
              {#if agent.description}
                <p class="agent-desc">{agent.description}</p>
              {/if}
              {#if agent.capabilities?.length}
                <div class="caps">
                  {#each agent.capabilities as cap}
                    <span class="cap-badge">{cap}</span>
                  {/each}
                </div>
              {/if}
              <div class="agent-footer">
                <span class="agent-image">{agent.image}</span>
                {#if agent.claude_model}
                  <span class="model-hint">claude: {agent.claude_model}{agent.claude_effort ? ` (${agent.claude_effort})` : ''}</span>
                {:else if agent.claude_effort}
                  <span class="model-hint">claude effort: {agent.claude_effort}</span>
                {/if}
                {#if agent.codex_model}
                  <span class="model-hint">codex: {agent.codex_model}</span>
                {/if}
                {#if agent.ollama_model}
                  <span class="model-hint">ollama: {agent.ollama_model}</span>
                {/if}
                {#if agent.role_prompt}
                  <span class="has-prompt">
                    <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                    role prompt
                  </span>
                {/if}
              </div>
            </div>
          {/each}
        </div>
        {/if}
      </div>
    {/each}
  {/if}

  <!-- CLI Tools section -->
  <div class="category-section" style="margin-top: 32px;">
    <div class="category-header" role="button" tabindex="0"
      onclick={() => cliExpanded = !cliExpanded}
      onkeydown={(e) => e.key === 'Enter' || e.key === ' ' ? (cliExpanded = !cliExpanded) : null}
    >
      <span class="category-toggle-icon" class:collapsed={!cliExpanded}>
        <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg>
      </span>
      <span class="category-label">cli tools</span>
      <span class="category-count">{cliAgents.filter(a => a.detected).length} / {cliAgents.length}</span>
      <div class="category-actions" role="none" onclick={(e) => e.stopPropagation()}>
        <button class="btn-add-to-category" onclick={rescanCliAgents} disabled={cliRescanning}>
          {cliRescanning ? 'scanning…' : 'rescan'}
        </button>
      </div>
    </div>
    {#if cliExpanded}
      <p class="cli-hint">Coding-agent CLIs detected on your PATH.</p>
      {#if cliLoading}
        <div class="loading">scanning…</div>
      {:else if cliAgents.length === 0}
        <div class="loading">No CLI agents found. Click rescan.</div>
      {:else}
        <div class="agent-list">
          {#each cliAgents as agent (agent.id)}
            <div class="cli-card" class:undetected={!agent.detected} class:cli-enabled={agent.enabled}>
              <div class="cli-top">
                <div class="cli-identity">
                  <span class="cli-dot" class:detected={agent.detected}></span>
                  <span class="cli-label">{agent.label}</span>
                  <span class="agent-image">{agent.binary}</span>
                </div>
                {#if agent.detected}
                  <label class="toggle-switch" title={agent.enabled ? 'Disable' : 'Enable'}>
                    <input type="checkbox" checked={agent.enabled} onchange={() => toggleCliAgent(agent)} />
                    <span class="toggle-track"></span>
                  </label>
                {:else}
                  <span class="not-installed">not installed</span>
                {/if}
              </div>
              {#if agent.detected}
                <div class="cli-detail">
                  <div class="cli-row"><span class="cli-key">path</span><code class="cli-val">{agent.path}</code></div>
                  {#if agent.version}<div class="cli-row"><span class="cli-key">version</span><span class="cli-val">{agent.version}</span></div>{/if}
                </div>
              {/if}
            </div>
          {/each}
        </div>
      {/if}
    {/if}
  </div>
</div>

{#if showModal}
  <Modal onClose={() => showModal = false}>
    {#snippet children()}
      <h2>{isEditMode ? `edit agent` : 'new agent'}</h2>
      {#if isEditMode}
        <p class="modal-sub">editing <strong>{editingAgent.name}</strong></p>
      {:else}
        <p class="modal-sub">create a custom agent role</p>
      {/if}

      {#if !isEditMode}
        <div class="field">
          <label for="aname">name</label>
          <input id="aname" type="text" value={formName} oninput={(e) => formName = (e.target as HTMLInputElement).value.toLowerCase().replace(/[^a-z0-9-]/g, '-')} placeholder="my-agent" />
          <span class="field-hint">lowercase letters, numbers, hyphens</span>
        </div>
      {/if}

      <div class="field">
        <label for="adesc">description</label>
        <input id="adesc" type="text" bind:value={formDescription} placeholder="What this agent does" />
      </div>

      <div class="field field-inline">
        <label for="acat">category</label>
        <div class="combo-wrap">
          <input id="acat" list="cat-options" bind:value={formCategory} placeholder="general" autocomplete="off" />
          <datalist id="cat-options">
            {#each [...new Set([...KNOWN_CATEGORIES, ...Array.from(agentsByCategory.keys())])] as cat}
              <option value={cat} />
            {/each}
          </datalist>
        </div>
      </div>

      <div class="field">
        <label>spawn mode</label>
        <div class="radio-group">
          <label class="radio">
            <input type="radio" bind:group={formSpawnMode} value="container" />
            <span class="radio-label">
              <strong>container</strong>
              <span class="radio-hint">full brainbox session (own Docker container + Claude Code)</span>
            </span>
          </label>
          <label class="radio">
            <input type="radio" bind:group={formSpawnMode} value="subagent" />
            <span class="radio-label">
              <strong>subagent</strong>
              <span class="radio-hint">spawned internally by Claude Code or Codex — no separate container</span>
            </span>
          </label>
        </div>
      </div>

      {#if formSpawnMode === 'container'}
      <div class="field">
        <label for="aimage">docker image</label>
        <input id="aimage" type="text" bind:value={formImage} placeholder="brainbox" />
      </div>
      {/if}

      <div class="field">
        <label>capabilities</label>
        <div class="checkboxes">
          {#each ALL_CAPABILITIES as cap}
            <label class="checkbox">
              <input type="checkbox" checked={formCapabilities.includes(cap)} onchange={() => toggleCapability(cap)} />
              {cap}
            </label>
          {/each}
        </div>
      </div>

      <div class="field">
        <label>options</label>
        <div class="checkboxes">
          <label class="checkbox">
            <input type="checkbox" bind:checked={formHardened} />
            hardened (security isolation)
          </label>
          <label class="checkbox">
            <input type="checkbox" bind:checked={formPersistent} />
            persistent (auto-restart)
          </label>
        </div>
      </div>

      <div class="field-group">
        <div class="field-group-label">model defaults <span class="field-hint-inline">(leave blank to use global config)</span></div>

        <div class="field field-inline">
          <label for="aclaudemodel">claude model</label>
          <select id="aclaudemodel" bind:value={formClaudeModel}>
            <option value="">— inherit —</option>
            {#each CLAUDE_MODELS as m}
              <option value={m}>{m}</option>
            {/each}
          </select>
        </div>
        <div class="field field-inline">
          <label for="aclaudeeffort">claude effort</label>
          <select id="aclaudeeffort" bind:value={formClaudeEffort}>
            <option value="">— inherit —</option>
            <option value="low">low</option>
            <option value="medium">medium</option>
            <option value="high">high</option>
          </select>
        </div>
        <div class="field field-inline">
          <label for="acodexmodel">codex model</label>
          <select id="acodexmodel" bind:value={formCodexModel}>
            <option value="">— inherit —</option>
            {#each CODEX_MODELS as m}
              <option value={m}>{m}</option>
            {/each}
          </select>
        </div>
        <div class="field field-inline">
          <label for="aollamamodel">ollama model</label>
          <select id="aollamamodel" bind:value={formOllamaModel}>
            <option value="">— inherit —</option>
            {#each ollamaModels as m}
              <option value={m}>{m}</option>
            {/each}
            {#if formOllamaModel && !ollamaModels.includes(formOllamaModel)}
              <option value={formOllamaModel}>{formOllamaModel}</option>
            {/if}
          </select>
        </div>
      </div>

      <div class="field">
        <label for="aprompt">role prompt <span class="field-hint-inline">(optional markdown)</span></label>
        <textarea id="aprompt" bind:value={formPrompt} placeholder="You are a specialized agent that...&#10;&#10;Your responsibilities:&#10;- ..." rows="10"></textarea>
      </div>

      <div class="modal-actions">
        <button class="btn-cancel" onclick={() => showModal = false} disabled={saving}>cancel</button>
        <button
          class="btn-submit"
          onclick={handleSave}
          disabled={saving || (!isEditMode && !formName.trim())}
        >
          {saving ? 'saving…' : isEditMode ? 'save changes' : 'create'}
        </button>
      </div>
    {/snippet}
  </Modal>
{/if}

{#if showTeamModal}
  <Modal onClose={() => showTeamModal = false}>
    {#snippet children()}
      <h2>launch team</h2>
      <p class="modal-sub">Spawn a supervisor with the <strong>{teamCategory}</strong> agents and a task.</p>

      <div class="field">
        <label for="ttask">task</label>
        <textarea id="ttask" bind:value={teamTask} placeholder="Describe what you want this team to accomplish…" rows="5"></textarea>
      </div>

      <ProfilePicker bind:selected={teamProfile} label="profile" />

      <div class="field field-inline">
        <label for="tprov">llm provider</label>
        <select id="tprov" bind:value={teamProvider}>
          <option value="claude">claude</option>
          <option value="codex">codex</option>
          <option value="ollama">ollama</option>
        </select>
      </div>

      <div class="field field-inline">
        <label for="tmodel">model <span class="field-hint-inline">(optional)</span></label>
        <input id="tmodel" type="text" bind:value={teamModel} placeholder="inherit from supervisor agent" />
      </div>

      <div class="modal-actions">
        <button class="btn-cancel" onclick={() => showTeamModal = false} disabled={launchingTeam}>cancel</button>
        <button
          class="btn-submit"
          onclick={handleLaunchTeam}
          disabled={launchingTeam || !teamTask.trim()}
        >
          {launchingTeam ? 'launching…' : 'launch'}
        </button>
      </div>
    {/snippet}
  </Modal>
{/if}

<style>
  .panel {
    padding: 24px;
  }

  .panel-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 24px;
  }

  .panel-title {
    font-size: 18px;
    font-weight: 600;
    color: var(--color-text-primary);
    margin: 0;
  }

  .loading {
    color: var(--color-text-secondary);
    font-size: 13px;
    padding: 24px 0;
  }

  .category-section {
    margin-bottom: 28px;
  }

  .category-header {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-bottom: 10px;
    padding-bottom: 6px;
    border-bottom: 1px solid var(--color-border-primary);
    cursor: pointer;
    user-select: none;
  }

  .category-header:hover .category-label {
    color: var(--color-text-secondary);
  }

  .category-toggle-icon {
    display: flex;
    align-items: center;
    color: var(--color-text-tertiary);
    transition: transform 0.15s;
    flex-shrink: 0;
  }

  .category-toggle-icon.collapsed {
    transform: rotate(-90deg);
  }

  .category-label {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--color-text-tertiary);
  }

  .category-count {
    font-size: 10px;
    color: var(--color-text-tertiary);
    background: var(--color-surface-hover);
    border-radius: 8px;
    padding: 0 5px;
    margin-right: auto;
  }

  .category-actions {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .btn-launch-team {
    display: flex;
    align-items: center;
    gap: 5px;
    padding: 3px 10px;
    background: rgba(var(--color-accent-rgb, 99, 102, 241), 0.12);
    border: 1px solid rgba(var(--color-accent-rgb, 99, 102, 241), 0.3);
    border-radius: var(--radius-sm);
    color: var(--color-accent);
    font-size: 11px;
    cursor: pointer;
    font-family: inherit;
    transition: all 0.15s;
  }

  .btn-launch-team:hover {
    background: rgba(var(--color-accent-rgb, 99, 102, 241), 0.2);
  }

  .btn-add-to-category {
    padding: 3px 8px;
    background: none;
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm);
    color: var(--color-text-secondary);
    font-size: 11px;
    cursor: pointer;
    font-family: inherit;
  }

  .btn-add-to-category:hover {
    background: var(--color-surface-hover);
  }

  .agent-list {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .agent-card {
    background: var(--color-surface);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-lg);
    padding: 14px 16px;
  }

  .agent-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }

  .agent-meta {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }

  .agent-name {
    font-size: 14px;
    font-weight: 600;
    color: var(--color-text-primary);
    font-family: var(--font-mono);
  }

  .agent-actions {
    display: flex;
    align-items: center;
    gap: 4px;
    flex-shrink: 0;
  }

  .agent-desc {
    margin: 6px 0 0 0;
    font-size: 12px;
    color: var(--color-text-secondary);
  }

  .caps {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    margin-top: 8px;
  }

  .cap-badge {
    font-size: 10px;
    font-family: var(--font-mono);
    padding: 2px 6px;
    background: var(--color-surface-hover);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm);
    color: var(--color-text-secondary);
  }

  .agent-footer {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-top: 8px;
  }

  .agent-image {
    font-size: 11px;
    font-family: var(--font-mono);
    color: var(--color-text-tertiary);
  }

  .has-prompt {
    display: flex;
    align-items: center;
    gap: 4px;
    font-size: 11px;
    color: var(--color-accent);
  }

  .badge {
    font-size: 10px;
    font-weight: 500;
    padding: 2px 6px;
    border-radius: var(--radius-sm);
    text-transform: lowercase;
  }

  .badge-blue {
    background: var(--color-role-blue-bg);
    color: var(--color-role-blue-text);
  }

  .badge-orange {
    background: var(--color-role-orange-bg);
    color: var(--color-role-orange-text);
  }

  .badge-gray {
    background: var(--color-surface-hover);
    color: var(--color-text-tertiary);
  }

  .badge-purple {
    background: var(--color-role-purple-bg);
    color: var(--color-role-purple-text);
  }

  .btn-primary {
    padding: 7px 14px;
    background: var(--color-accent);
    color: white;
    border: none;
    border-radius: var(--radius-md);
    font-size: 13px;
    cursor: pointer;
    font-family: inherit;
  }

  .btn-primary:hover {
    opacity: 0.85;
  }

  .btn-icon {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    padding: 0;
    background: none;
    border: none;
    border-radius: var(--radius-sm);
    color: var(--color-text-tertiary);
    cursor: pointer;
    transition: all 0.15s;
  }

  .btn-icon:hover {
    background: var(--color-surface-hover);
    color: var(--color-text-secondary);
  }

  .btn-icon-danger:hover {
    color: var(--color-danger, #ef4444);
  }

  .btn-danger-sm {
    padding: 4px 10px;
    background: var(--color-danger, #ef4444);
    color: white;
    border: none;
    border-radius: var(--radius-sm);
    font-size: 12px;
    cursor: pointer;
    font-family: inherit;
  }

  .btn-cancel-sm {
    padding: 4px 8px;
    background: none;
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm);
    font-size: 12px;
    cursor: pointer;
    color: var(--color-text-secondary);
    font-family: inherit;
  }

  /* Form fields */
  .field {
    display: flex;
    flex-direction: column;
    gap: 6px;
    margin-bottom: 16px;
  }

  .field label {
    font-size: 12px;
    font-weight: 500;
    color: var(--color-text-secondary);
    text-transform: lowercase;
  }

  .field input,
  .field textarea {
    padding: 8px 10px;
    background: var(--color-input-bg, var(--color-surface));
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-md);
    color: var(--color-text-primary);
    font-size: 13px;
    font-family: inherit;
    outline: none;
  }

  .field textarea {
    font-family: var(--font-mono);
    font-size: 12px;
    resize: vertical;
    line-height: 1.5;
  }

  .field input:focus,
  .field textarea:focus {
    border-color: var(--color-accent);
  }

  .field-hint {
    font-size: 11px;
    color: var(--color-text-tertiary);
  }

  .field-hint-inline {
    font-size: 11px;
    color: var(--color-text-tertiary);
    font-weight: 400;
  }

  .checkboxes {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .checkbox {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 13px;
    color: var(--color-text-primary);
    cursor: pointer;
  }

  .checkbox input {
    width: auto;
    padding: 0;
    border: none;
  }

  .modal-sub {
    margin: 0 0 20px 0;
    font-size: 13px;
    color: var(--color-text-secondary);
  }

  .modal-actions {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    margin-top: 24px;
  }

  .btn-cancel {
    padding: 8px 16px;
    background: none;
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-md);
    color: var(--color-text-secondary);
    font-size: 13px;
    cursor: pointer;
    font-family: inherit;
  }

  .btn-submit {
    padding: 8px 16px;
    background: var(--color-accent);
    color: white;
    border: none;
    border-radius: var(--radius-md);
    font-size: 13px;
    cursor: pointer;
    font-family: inherit;
  }

  .btn-submit:disabled,
  .btn-cancel:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .model-hint {
    font-size: 11px;
    font-family: var(--font-mono);
    color: var(--color-text-tertiary);
  }

  .field-group {
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-md);
    padding: 12px 14px;
    margin-bottom: 16px;
  }

  .field-group-label {
    font-size: 12px;
    font-weight: 500;
    color: var(--color-text-secondary);
    text-transform: lowercase;
    margin-bottom: 12px;
  }

  .field.field-inline {
    display: grid;
    grid-template-columns: 120px 1fr;
    align-items: center;
    margin-bottom: 10px;
  }

  .field.field-inline:last-child {
    margin-bottom: 0;
  }

  .field.field-inline label {
    margin: 0;
  }

  .combo-wrap {
    flex: 1;
  }

  .combo-wrap input {
    width: 100%;
    padding: 8px 10px;
    background: var(--color-input-bg, var(--color-surface));
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-md);
    color: var(--color-text-primary);
    font-size: 13px;
    font-family: inherit;
    outline: none;
    box-sizing: border-box;
  }

  .combo-wrap input:focus {
    border-color: var(--color-accent);
  }

  .field select {
    padding: 8px 10px;
    background: var(--color-input-bg, var(--color-surface));
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-md);
    color: var(--color-text-primary);
    font-size: 13px;
    font-family: inherit;
    outline: none;
  }

  .field select:focus {
    border-color: var(--color-accent);
  }

  .radio-group {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .radio {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    cursor: pointer;
  }

  .radio input[type="radio"] {
    margin-top: 3px;
    width: auto;
    padding: 0;
    border: none;
    flex-shrink: 0;
  }

  .radio-label {
    display: flex;
    flex-direction: column;
    gap: 2px;
    font-size: 13px;
    color: var(--color-text-primary);
  }

  .radio-hint {
    font-size: 11px;
    color: var(--color-text-tertiary);
    font-weight: 400;
  }

  /* CLI Tools section */
  .cli-hint {
    font-size: 12px;
    color: var(--color-text-tertiary);
    margin: 0 0 10px;
  }

  .cli-card {
    background: var(--color-surface);
    border: 1px solid var(--color-border-primary);
    border-left: 3px solid var(--color-border-primary);
    border-radius: var(--radius-lg);
    padding: 12px 16px;
  }
  .cli-card.cli-enabled { border-left-color: var(--color-success); }
  .cli-card.undetected { opacity: 0.55; }

  .cli-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  .cli-identity {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .cli-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: #374151;
    flex-shrink: 0;
  }
  .cli-dot.detected {
    background: var(--color-success);
    box-shadow: 0 0 6px rgba(16, 185, 129, 0.4);
  }

  .cli-label {
    font-weight: 500;
    font-size: 13px;
    color: var(--color-text-primary);
  }

  .not-installed {
    font-size: 10px;
    color: var(--color-text-tertiary);
    font-style: italic;
  }

  .cli-detail {
    margin-top: 8px;
    padding-top: 8px;
    border-top: 1px solid var(--color-border-primary);
    display: flex;
    flex-direction: column;
    gap: 3px;
  }

  .cli-row {
    display: flex;
    align-items: baseline;
    gap: 10px;
    font-size: 11px;
  }

  .cli-key {
    font-size: 9px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-tertiary);
    min-width: 56px;
    flex-shrink: 0;
  }

  .cli-val {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--color-text-secondary);
    word-break: break-all;
  }

  .toggle-switch { position: relative; display: inline-flex; cursor: pointer; }
  .toggle-switch input { opacity: 0; width: 0; height: 0; position: absolute; }
  .toggle-track {
    width: 32px;
    height: 18px;
    background: var(--color-bg-tertiary);
    border: 1px solid var(--color-border-secondary);
    border-radius: 9999px;
    position: relative;
    transition: all 0.2s;
  }
  .toggle-track::after {
    content: '';
    width: 12px;
    height: 12px;
    background: var(--color-text-tertiary);
    border-radius: 50%;
    position: absolute;
    top: 2px;
    left: 2px;
    transition: all 0.2s;
  }
  .toggle-switch input:checked + .toggle-track {
    background: rgba(16, 185, 129, 0.2);
    border-color: rgba(16, 185, 129, 0.4);
  }
  .toggle-switch input:checked + .toggle-track::after {
    background: var(--color-success);
    left: 16px;
  }
</style>

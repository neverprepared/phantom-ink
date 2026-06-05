<script lang="ts">
  import { getApi } from '../utils/api';
  import { onMount } from 'svelte';
  import { profileState, refreshTick } from '../stores.svelte';
  import { notifications } from '../notifications.svelte';
  import Spinner from '../components/Spinner.svelte';
  import EmptyState from '../components/EmptyState.svelte';

  // ── Types ──────────────────────────────────────────────────────────────

  interface AutomationRule {
    id: string;
    profile: string;
    name: string;
    description: string;
    enabled: boolean;
    trigger_type: string;
    trigger_config: string;
    action_type: string;
    action_config: string;
    created_at: number;
    last_triggered_at?: number;
    trigger_count: number;
  }

  type TriggerType = 'entry_created' | 'entry_status_change' | 'job_complete' | 'webhook';
  type ActionType  = 'fire_job' | 'run_playbook' | 'run_chain' | 'notify';

  interface JobItem      { id: string; name: string; profile: string; }
  interface PlaybookItem { id: string; name: string; workspace_profile: string; }
  interface ChainItem    { id: string; name: string; }

  // ── State ──────────────────────────────────────────────────────────────

  const profile = $derived(profileState.active?.name ?? '');

  let rules     = $state<AutomationRule[]>([]);
  let jobs      = $state<JobItem[]>([]);
  let playbooks = $state<PlaybookItem[]>([]);
  let chains    = $state<ChainItem[]>([]);
  let loading   = $state(false);
  let editingId = $state<string | null>(null);
  let statusMsg = $state('');
  let baseURL   = $state('http://127.0.0.1:9999');

  // Draft form state
  let draft = $state({
    name:             '',
    description:      '',
    profile:          '',
    enabled:          true,
    triggerType:      'entry_created' as TriggerType,
    // trigger config — entry / job
    trigKind:         '',
    trigTags:         '',        // comma-separated
    trigStatus:       '',
    trigJobID:        '',
    // trigger config — webhook
    trigWebhookKey:   '',
    // action
    actionType:       'notify' as ActionType,
    // action config — fire_job
    actJobID:         '',
    // action config — run_playbook
    actPlaybookID:    '',
    // action config — run_chain
    actChainID:       '',
    actChainInput:    '',
    // action config — notify
    actTitle:         '{title}',
    actBody:          '',
  });

  // ── Derived ────────────────────────────────────────────────────────────

  const allProfiles = $derived(profileState.profiles.map(p => p.name));

  // draftProfile must be declared before visible* so the closures resolve correctly
  let draftProfile = $derived.by(() => {
    if (editingId === 'new') return draft.profile;
    const rule = rules.find(r => r.id === editingId);
    return rule?.profile ?? '';
  });

  // When draftProfile is '' (global rule), show everything; otherwise show global + that profile
  let visibleJobs = $derived(
    !draftProfile ? jobs : jobs.filter(j => !j.profile || j.profile === draftProfile)
  );
  let visiblePlaybooks = $derived(
    !draftProfile ? playbooks : playbooks.filter(p => !p.workspace_profile || p.workspace_profile === draftProfile)
  );
  let visibleChains = $derived(chains);

  let webhookURL = $derived(
    draft.trigWebhookKey ? `${baseURL}/api/webhooks/${draft.trigWebhookKey}` : ''
  );

  // ── Loading ────────────────────────────────────────────────────────────

  async function load(silent = false) {
    const a = await getApi();
    if (!a) return;
    if (!silent) loading = true;
    try {
      const [r, j, p, c, cfg] = await Promise.all([
        (a.ListAutomationRules as any)('').catch(() => []),
        (a.ListCollectJobs as any)('').catch(() => []),
        (a.ListPlaybooks as any)('').catch(() => []),
        (a.ListChains as any)().catch(() => []),
        (a.GetConfig as any)().catch(() => null),
      ]);
      rules     = (r ?? []) as AutomationRule[];
      jobs      = ((j ?? []) as any[]).map((x: any): JobItem      => ({ id: x.id, name: x.name, profile:           x.profile           ?? '' }));
      playbooks = ((p ?? []) as any[]).map((x: any): PlaybookItem => ({ id: x.id, name: x.name, workspace_profile: x.workspace_profile  ?? '' }));
      chains    = ((c ?? []) as any[]).map((x: any): ChainItem    => ({ id: x.id, name: x.name }));
      if (cfg?.base_url) baseURL = cfg.base_url;
    } finally {
      loading = false;
    }
  }

  function genWebhookKey(): string {
    return crypto.randomUUID();
  }

  // ── CRUD ───────────────────────────────────────────────────────────────

  async function save() {
    const a = await getApi();
    if (!a || !draft.name.trim()) return;
    const isNew = editingId === 'new';
    const existing = rules.find(r => r.id === editingId);

    const triggerConfig = buildTriggerConfig();
    const actionConfig  = buildActionConfig();

    const rule: Partial<AutomationRule> = {
      id:             isNew ? '' : (editingId ?? ''),
      profile:        draft.profile,
      name:           draft.name.trim(),
      description:    draft.description.trim(),
      enabled:        draft.enabled,
      trigger_type:   draft.triggerType,
      trigger_config: JSON.stringify(triggerConfig),
      action_type:    draft.actionType,
      action_config:  JSON.stringify(actionConfig),
      created_at:     0,
    };

    try {
      await (a.SaveAutomationRule as any)(rule);
      editingId = null;
      await load();
    } catch (e: any) {
      flash(`Error: ${e?.message ?? 'save failed'}`);
    }
  }

  function buildTriggerConfig(): Record<string, any> {
    if (draft.triggerType === 'webhook') {
      return { key: draft.trigWebhookKey };
    }
    const cfg: Record<string, any> = {};
    if (draft.trigKind)   cfg.kind   = draft.trigKind;
    if (draft.trigStatus) cfg.status = draft.trigStatus;
    if (draft.trigJobID)  cfg.job_id = draft.trigJobID;
    const tags = draft.trigTags.split(',').map(t => t.trim()).filter(Boolean);
    if (tags.length) cfg.tags = tags;
    return cfg;
  }

  function buildActionConfig(): Record<string, any> {
    switch (draft.actionType) {
      case 'fire_job':     return { job_id: draft.actJobID };
      case 'run_playbook': return { playbook_id: draft.actPlaybookID };
      case 'run_chain':    return { chain_id: draft.actChainID, input: draft.actChainInput };
      case 'notify':       return { title: draft.actTitle, body: draft.actBody };
    }
  }

  async function remove(id: string) {
    const a = await getApi();
    if (!a) return;
    try {
      await (a.DeleteAutomationRule as any)(id);
      await load();
    } catch (e: any) {
      notifications.error(`Failed to delete rule: ${e?.message ?? 'unknown error'}`);
    }
  }

  async function toggle(rule: AutomationRule) {
    const a = await getApi();
    if (!a) return;
    try {
      await (a.SaveAutomationRule as any)({ ...rule, enabled: !rule.enabled });
      await load();
    } catch (e: any) {
      notifications.error(`Failed to update rule: ${e?.message ?? 'unknown error'}`);
    }
  }

  // ── Form helpers ───────────────────────────────────────────────────────

  function startNew() {
    editingId = 'new';
    draft = {
      name: '', description: '', profile: profile, enabled: true,
      triggerType: 'entry_created', trigKind: '', trigTags: '', trigStatus: '', trigJobID: '',
      trigWebhookKey: '',
      actionType: 'notify', actJobID: '', actPlaybookID: '', actChainID: '', actChainInput: '',
      actTitle: '{title}', actBody: '',
    };
  }

  function startEdit(rule: AutomationRule) {
    editingId = rule.id;
    let trigCfg: any = {};
    let actCfg: any  = {};
    try { trigCfg = JSON.parse(rule.trigger_config); } catch {}
    try { actCfg  = JSON.parse(rule.action_config);  } catch {}

    draft = {
      name:             rule.name,
      description:      rule.description,
      profile:          rule.profile,
      enabled:          rule.enabled,
      triggerType:      (rule.trigger_type || 'entry_created') as TriggerType,
      trigKind:         trigCfg.kind   ?? '',
      trigTags:         (trigCfg.tags ?? []).join(', '),
      trigStatus:       trigCfg.status ?? '',
      trigJobID:        trigCfg.job_id ?? '',
      trigWebhookKey:   trigCfg.key    ?? '',
      actionType:       (rule.action_type || 'notify') as ActionType,
      actJobID:         actCfg.job_id      ?? '',
      actPlaybookID:    actCfg.playbook_id ?? '',
      actChainID:       actCfg.chain_id    ?? '',
      actChainInput:    actCfg.input       ?? '',
      actTitle:         actCfg.title       ?? '{title}',
      actBody:       actCfg.body        ?? '',
    };
  }

  function cancelEdit() { editingId = null; }

  function flash(msg: string) {
    statusMsg = msg;
    setTimeout(() => { statusMsg = ''; }, 3000);
  }

  function isFormValid(): boolean {
    if (!draft.name.trim()) return false;
    if (draft.triggerType === 'webhook' && !draft.trigWebhookKey) return false;
    if (draft.actionType === 'fire_job' && !draft.actJobID) return false;
    if (draft.actionType === 'run_playbook' && !draft.actPlaybookID) return false;
    if (draft.actionType === 'run_chain' && !draft.actChainID) return false;
    return true;
  }

  // ── Display helpers ────────────────────────────────────────────────────

  function triggerLabel(rule: AutomationRule): string {
    let cfg: any = {};
    try { cfg = JSON.parse(rule.trigger_config); } catch {}
    if (rule.trigger_type === 'webhook') {
      return `webhook · key: ${cfg.key?.slice(0, 8) ?? '?'}…`;
    }
    const parts: string[] = [triggerTypeLabel(rule.trigger_type)];
    if (cfg.kind)   parts.push(`kind: ${cfg.kind}`);
    if (cfg.tags?.length) parts.push(`tags: ${cfg.tags.join(', ')}`);
    if (cfg.status) parts.push(`status → ${cfg.status}`);
    return parts.join(' · ');
  }

  function actionLabel(rule: AutomationRule): string {
    let cfg: any = {};
    try { cfg = JSON.parse(rule.action_config); } catch {}
    switch (rule.action_type) {
      case 'fire_job':     return `fire job · ${resolveJobName(cfg.job_id)}`;
      case 'run_playbook': return `run playbook · ${resolvePlaybookName(cfg.playbook_id)}`;
      case 'run_chain':    return `run chain · ${resolveChainName(cfg.chain_id)}`;
      case 'notify':       return `notify · "${cfg.title}"`;
      default:             return rule.action_type;
    }
  }

  function triggerTypeLabel(t: string): string {
    switch (t) {
      case 'entry_created':       return 'entry created';
      case 'entry_status_change': return 'status change';
      case 'job_complete':        return 'job complete';
      case 'webhook':             return 'webhook';
      default: return t;
    }
  }

  function resolveJobName(id: string): string {
    return jobs.find(j => j.id === id)?.name ?? id?.slice(0, 8) ?? '?';
  }
  function resolvePlaybookName(id: string): string {
    return playbooks.find(p => p.id === id)?.name ?? id?.slice(0, 8) ?? '?';
  }
  function resolveChainName(id: string): string {
    return chains.find(c => c.id === id)?.name ?? id?.slice(0, 8) ?? '?';
  }

  function fmtLastTriggered(rule: AutomationRule): string {
    if (!rule.last_triggered_at) return 'never';
    const diff = Date.now() - rule.last_triggered_at;
    if (diff < 60_000)     return 'just now';
    if (diff < 3_600_000)  return `${Math.floor(diff / 60_000)}m ago`;
    if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
    return new Date(rule.last_triggered_at).toLocaleDateString([], { month: 'short', day: 'numeric' });
  }

  onMount(() => { void load(); });

  $effect(() => {
    refreshTick.count;
    void load(true);
  });
</script>

<div class="automations">
  <div class="cpanel-header">
    <h1 class="page-title">automations</h1>
    <div class="cpanel-actions">
      {#if loading}<Spinner />{/if}
      {#if statusMsg}
        <span class="status-msg">{statusMsg}</span>
      {/if}
      {#if editingId !== 'new'}
        <button class="btn primary" onclick={startNew}>+ new rule</button>
      {/if}
    </div>
  </div>

  <!-- Create form -->
  {#if editingId === 'new'}
    <div class="rule-form">
      <div class="form-title">new automation</div>
      {#snippet formBody()}
        <label class="form-row">
          <span class="form-label">name</span>
          <input class="form-input" bind:value={draft.name} placeholder="Standup on calendar event" />
        </label>

        <label class="form-row">
          <span class="form-label">profile</span>
          <select class="form-select narrow" bind:value={draft.profile}>
            <option value="">global (all profiles)</option>
            {#each allProfiles as p (p)}
              <option value={p}>{p}</option>
            {/each}
          </select>
        </label>

        <!-- Trigger type -->
        <div class="form-row">
          <span class="form-label">trigger</span>
          <div class="seg-ctrl">
            {#each (['entry_created', 'entry_status_change', 'job_complete', 'webhook'] as TriggerType[]) as t (t)}
              <button class="seg-btn" class:active={draft.triggerType === t}
                onclick={() => draft.triggerType = t}>{triggerTypeLabel(t)}</button>
            {/each}
          </div>
        </div>

        <!-- Trigger config -->
        <div class="form-section">
          {#if draft.triggerType === 'webhook'}
            <div class="form-row">
              <span class="form-label">webhook url</span>
              <div class="webhook-url-row">
                {#if draft.trigWebhookKey}
                  <input class="form-input webhook-url-input" readonly value={webhookURL} />
                  <button class="btn sm ghost" onclick={() => navigator.clipboard.writeText(webhookURL)} title="copy">copy</button>
                  <button class="btn sm ghost" onclick={() => { draft.trigWebhookKey = genWebhookKey(); }} title="rotate key">rotate</button>
                {:else}
                  <button class="btn sm primary" onclick={() => { draft.trigWebhookKey = genWebhookKey(); }}>generate url</button>
                {/if}
              </div>
            </div>
            {#if draft.trigWebhookKey}
              <p class="webhook-hint">POST any JSON to this URL to fire this rule. Use <code>{"{payload.field}"}</code> in action templates to reference the request body.</p>
            {/if}
          {/if}
          {#if draft.triggerType !== 'job_complete' && draft.triggerType !== 'webhook'}
            <label class="form-row">
              <span class="form-label">tags</span>
              <input class="form-input" bind:value={draft.trigTags} placeholder="calendar, work (comma-separated)" />
            </label>
            <label class="form-row">
              <span class="form-label">kind</span>
              <select class="form-select narrow" bind:value={draft.trigKind}>
                <option value="">any</option>
                <option value="event">event</option>
                <option value="metric">metric</option>
              </select>
            </label>
          {/if}
          {#if draft.triggerType === 'entry_status_change'}
            <label class="form-row">
              <span class="form-label">status</span>
              <select class="form-select narrow" bind:value={draft.trigStatus}>
                <option value="">any</option>
                <option value="upcoming">upcoming</option>
                <option value="active">active</option>
                <option value="done">done</option>
                <option value="failed">failed</option>
              </select>
            </label>
          {/if}
          {#if draft.triggerType === 'job_complete'}
            <label class="form-row">
              <span class="form-label">job</span>
              <select class="form-select" bind:value={draft.trigJobID}>
                <option value="">any job</option>
                {#each visibleJobs as j (j.id)}
                  <option value={j.id}>{j.name}</option>
                {/each}
              </select>
            </label>
          {/if}
        </div>

        <!-- Action type -->
        <div class="form-row">
          <span class="form-label">action</span>
          <div class="seg-ctrl">
            {#each (['fire_job', 'run_playbook', 'run_chain', 'notify'] as ActionType[]) as t (t)}
              <button class="seg-btn" class:active={draft.actionType === t}
                onclick={() => { draft.actionType = t; }}>{t.replace('_', ' ')}</button>
            {/each}
          </div>
        </div>

        <!-- Action config -->
        <div class="form-section">
          {#if draft.actionType === 'fire_job'}
            <label class="form-row">
              <span class="form-label">job</span>
              <select class="form-select" bind:value={draft.actJobID}>
                <option value="">— select —</option>
                {#each visibleJobs as j (j.id)}
                  <option value={j.id}>{j.name}</option>
                {/each}
              </select>
            </label>
          {:else if draft.actionType === 'run_playbook'}
            <label class="form-row">
              <span class="form-label">playbook</span>
              <select class="form-select" bind:value={draft.actPlaybookID}>
                <option value="">— select —</option>
                {#each visiblePlaybooks as p (p.id)}
                  <option value={p.id}>{p.name}</option>
                {/each}
              </select>
            </label>
          {:else if draft.actionType === 'run_chain'}
            <label class="form-row">
              <span class="form-label">chain</span>
              <select class="form-select" bind:value={draft.actChainID}>
                <option value="">— select —</option>
                {#each visibleChains as c (c.id)}
                  <option value={c.id}>{c.name}</option>
                {/each}
              </select>
            </label>
            <label class="form-row">
              <span class="form-label">input</span>
              <input class="form-input" bind:value={draft.actChainInput} placeholder={"{title} — optional"} />
            </label>
          {:else if draft.actionType === 'notify'}
            <label class="form-row">
              <span class="form-label">title</span>
              <input class="form-input" bind:value={draft.actTitle} placeholder={"{title}"} />
            </label>
            <label class="form-row">
              <span class="form-label">body</span>
              <input class="form-input" bind:value={draft.actBody} placeholder={"{description} — optional"} />
            </label>
          {/if}
        </div>

        <label class="form-row form-row-inline">
          <input type="checkbox" bind:checked={draft.enabled} />
          <span class="form-label">enabled</span>
        </label>

        <div class="form-actions">
          <button class="btn sm primary" onclick={save} disabled={!isFormValid()}>save</button>
          <button class="btn sm ghost" onclick={cancelEdit}>cancel</button>
        </div>
      {/snippet}
      {@render formBody()}
    </div>
  {/if}

  <!-- Rule list -->
  {#if loading && rules.length === 0}
    <div class="empty">loading…</div>
  {:else if rules.length === 0 && editingId !== 'new'}
    <EmptyState title="No automation rules" message="Create one to trigger actions from events." />
  {:else}
    <div class="rule-list">
      {#each rules as rule (rule.id)}
        <div class="rule-card" class:editing={editingId === rule.id}>
          {#if editingId === rule.id}
            <div class="rule-form inline">
              <label class="form-row">
                <span class="form-label">name</span>
                <input class="form-input" bind:value={draft.name} />
              </label>
              <label class="form-row">
                <span class="form-label">profile</span>
                <select class="form-select narrow" bind:value={draft.profile}>
                  <option value="">global (all profiles)</option>
                  {#each allProfiles as p (p)}
                    <option value={p}>{p}</option>
                  {/each}
                </select>
              </label>
              <div class="form-row">
                <span class="form-label">trigger</span>
                <div class="seg-ctrl">
                  {#each (['entry_created', 'entry_status_change', 'job_complete', 'webhook'] as TriggerType[]) as t (t)}
                    <button class="seg-btn" class:active={draft.triggerType === t}
                      onclick={() => draft.triggerType = t}>{triggerTypeLabel(t)}</button>
                  {/each}
                </div>
              </div>
              <div class="form-section">
                {#if draft.triggerType === 'webhook'}
                  <div class="form-row">
                    <span class="form-label">webhook url</span>
                    <div class="webhook-url-row">
                      {#if draft.trigWebhookKey}
                        <input class="form-input webhook-url-input" readonly value={webhookURL} />
                        <button class="btn sm ghost" onclick={() => navigator.clipboard.writeText(webhookURL)} title="copy">copy</button>
                        <button class="btn sm ghost" onclick={() => { draft.trigWebhookKey = genWebhookKey(); }} title="rotate key">rotate</button>
                      {:else}
                        <button class="btn sm primary" onclick={() => { draft.trigWebhookKey = genWebhookKey(); }}>generate url</button>
                      {/if}
                    </div>
                  </div>
                  {#if draft.trigWebhookKey}
                    <p class="webhook-hint">POST any JSON to this URL to fire this rule. Use <code>{"{payload.field}"}</code> in action templates.</p>
                  {/if}
                {/if}
                {#if draft.triggerType !== 'job_complete' && draft.triggerType !== 'webhook'}
                  <label class="form-row">
                    <span class="form-label">tags</span>
                    <input class="form-input" bind:value={draft.trigTags} placeholder="calendar, work" />
                  </label>
                  <label class="form-row">
                    <span class="form-label">kind</span>
                    <select class="form-select narrow" bind:value={draft.trigKind}>
                      <option value="">any</option>
                      <option value="event">event</option>
                      <option value="metric">metric</option>
                    </select>
                  </label>
                {/if}
                {#if draft.triggerType === 'entry_status_change'}
                  <label class="form-row">
                    <span class="form-label">status</span>
                    <select class="form-select narrow" bind:value={draft.trigStatus}>
                      <option value="">any</option>
                      <option value="upcoming">upcoming</option>
                      <option value="active">active</option>
                      <option value="done">done</option>
                      <option value="failed">failed</option>
                    </select>
                  </label>
                {/if}
                {#if draft.triggerType === 'job_complete'}
                  <label class="form-row">
                    <span class="form-label">job</span>
                    <select class="form-select" bind:value={draft.trigJobID}>
                      <option value="">any job</option>
                      {#each visibleJobs as j (j.id)}
                        <option value={j.id}>{j.name}</option>
                      {/each}
                    </select>
                  </label>
                {/if}
              </div>
              <div class="form-row">
                <span class="form-label">action</span>
                <div class="seg-ctrl">
                  {#each (['fire_job', 'run_playbook', 'run_chain', 'notify'] as ActionType[]) as t (t)}
                    <button class="seg-btn" class:active={draft.actionType === t}
                      onclick={() => draft.actionType = t}>{t.replace('_', ' ')}</button>
                  {/each}
                </div>
              </div>
              <div class="form-section">
                {#if draft.actionType === 'fire_job'}
                  <label class="form-row">
                    <span class="form-label">job</span>
                    <select class="form-select" bind:value={draft.actJobID}>
                      <option value="">— select —</option>
                      {#each visibleJobs as j (j.id)}<option value={j.id}>{j.name}</option>{/each}
                    </select>
                  </label>
                {:else if draft.actionType === 'run_playbook'}
                  <label class="form-row">
                    <span class="form-label">playbook</span>
                    <select class="form-select" bind:value={draft.actPlaybookID}>
                      <option value="">— select —</option>
                      {#each visiblePlaybooks as p (p.id)}<option value={p.id}>{p.name}</option>{/each}
                    </select>
                  </label>
                {:else if draft.actionType === 'run_chain'}
                  <label class="form-row">
                    <span class="form-label">chain</span>
                    <select class="form-select" bind:value={draft.actChainID}>
                      <option value="">— select —</option>
                      {#each visibleChains as c (c.id)}<option value={c.id}>{c.name}</option>{/each}
                    </select>
                  </label>
                {:else if draft.actionType === 'notify'}
                  <label class="form-row">
                    <span class="form-label">title</span>
                    <input class="form-input" bind:value={draft.actTitle} />
                  </label>
                  <label class="form-row">
                    <span class="form-label">body</span>
                    <input class="form-input" bind:value={draft.actBody} />
                  </label>
                {/if}
              </div>
              <label class="form-row form-row-inline">
                <input type="checkbox" bind:checked={draft.enabled} />
                <span class="form-label">enabled</span>
              </label>
              <div class="form-actions">
                <button class="btn sm primary" onclick={save} disabled={!isFormValid()}>save</button>
                <button class="btn sm ghost" onclick={cancelEdit}>cancel</button>
              </div>
            </div>
          {:else}
            <div class="rule-row">
              <button class="rule-toggle" class:on={rule.enabled} onclick={() => toggle(rule)}
                title={rule.enabled ? 'disable' : 'enable'}>
                {rule.enabled ? '●' : '○'}
              </button>
              <div class="rule-info" role="button" tabindex="0"
                onclick={() => startEdit(rule)}
                onkeydown={(e) => e.key === 'Enter' && startEdit(rule)}>
                <span class="rule-name">{rule.name}</span>
                <div class="rule-meta-row">
                  {#if rule.profile}
                    <span class="rule-profile">{rule.profile}</span>
                  {/if}
                  <span class="rule-badge trigger">{triggerTypeLabel(rule.trigger_type)}</span>
                  <span class="rule-detail">{triggerLabel(rule)}</span>
                </div>
                <div class="rule-meta-row">
                  <span class="rule-badge action type-{rule.action_type}">{rule.action_type.replace('_', ' ')}</span>
                  <span class="rule-detail">{actionLabel(rule)}</span>
                </div>
              </div>
              <div class="rule-stats">
                {#if rule.trigger_count > 0}
                  <span class="rule-count" title="times triggered">{rule.trigger_count}×</span>
                {/if}
                <span class="rule-last">{fmtLastTriggered(rule)}</span>
                <button class="rule-btn danger" onclick={() => remove(rule.id)} title="delete">✕</button>
              </div>
            </div>
          {/if}
        </div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .automations {
    padding: var(--panel-padding);
    display: flex;
    flex-direction: column;
    gap: var(--spacing-md);
    min-height: 100%;
  }

  .status-msg { font-family: var(--font-mono); font-size: 11px; color: var(--color-success); }

  .empty { font-size: 13px; color: var(--color-text-tertiary); padding: var(--spacing-3xl) 0; line-height: 1.5; }

  /* Rule list */
  .rule-list { display: flex; flex-direction: column; gap: var(--spacing-sm); }

  .rule-card {
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-md);
    background: var(--color-bg-secondary);
    overflow: hidden; transition: border-color 100ms;
  }
  .rule-card:hover { border-color: var(--color-border-secondary); }
  .rule-card.editing { border-color: var(--color-accent); }

  .rule-row {
    display: grid; grid-template-columns: 24px 1fr auto;
    align-items: start; gap: var(--spacing-md);
    padding: var(--spacing-md) var(--spacing-lg);
  }

  .rule-toggle {
    background: none; border: none; cursor: pointer;
    font-size: 14px; font-family: var(--font-mono);
    color: var(--color-text-muted); padding: 0; line-height: 1.6;
    transition: color 100ms;
  }
  .rule-toggle.on { color: var(--color-success); }
  .rule-toggle:hover { opacity: 0.7; }

  .rule-info { display: flex; flex-direction: column; gap: 4px; min-width: 0; cursor: pointer; }
  .rule-name { font-size: 13px; font-weight: 500; color: var(--color-text-primary); }
  .rule-meta-row { display: flex; gap: var(--spacing-sm); align-items: center; flex-wrap: wrap; }
  .rule-detail { font-family: var(--font-mono); font-size: 10px; color: var(--color-text-tertiary); }
  .rule-profile { font-family: var(--font-mono); font-size: 10px; color: var(--color-accent); background: rgba(234,179,8,0.08); border: 1px solid rgba(234,179,8,0.2); border-radius: 999px; padding: 1px 6px; }

  .rule-badge {
    font-family: var(--font-mono); font-size: 10px;
    padding: 1px 6px; border-radius: 999px;
    border: 1px solid var(--color-border-primary);
    color: var(--color-text-muted);
  }
  .rule-badge.trigger { color: #6366f1; border-color: rgba(99,102,241,0.3); background: rgba(99,102,241,0.06); }
  .rule-badge.action.type-fire_job     { color: var(--color-accent); border-color: rgba(234,179,8,0.3); background: rgba(234,179,8,0.06); }
  .rule-badge.action.type-run_playbook { color: #10b981; border-color: rgba(16,185,129,0.3); background: rgba(16,185,129,0.06); }
  .rule-badge.action.type-run_chain    { color: #f59e0b; border-color: rgba(245,158,11,0.3); background: rgba(245,158,11,0.06); }
  .rule-badge.action.type-notify       { color: #60a5fa; border-color: rgba(96,165,250,0.3); background: rgba(96,165,250,0.06); }

  .rule-stats { display: flex; flex-direction: column; align-items: flex-end; gap: 4px; flex-shrink: 0; }
  .rule-count { font-family: var(--font-mono); font-size: 10px; color: var(--color-text-secondary); }
  .rule-last  { font-family: var(--font-mono); font-size: 10px; color: var(--color-text-tertiary); }
  .rule-btn {
    background: none; border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm); padding: 2px 6px;
    font-size: 10px; cursor: pointer; color: var(--color-text-muted);
    transition: all 100ms; font-family: var(--font-mono);
  }
  .rule-btn.danger:hover { border-color: var(--color-error); color: var(--color-error); }

  /* Form */
  .rule-form {
    display: flex; flex-direction: column; gap: var(--spacing-md);
    padding: var(--spacing-lg);
    border: 1px solid var(--color-accent);
    border-radius: var(--radius-md);
    background: var(--color-bg-secondary);
  }
  .rule-form.inline { border: none; border-radius: 0; }
  .form-title { font-size: 12px; font-weight: 600; color: var(--color-text-secondary); }
  .form-section { display: flex; flex-direction: column; gap: var(--spacing-sm); padding-left: var(--spacing-md); border-left: 2px solid var(--color-border-primary); }
  .form-row { display: flex; flex-direction: column; gap: 4px; }
  .form-row-inline { flex-direction: row; align-items: center; gap: var(--spacing-sm); }
  .form-label { font-family: var(--font-mono); font-size: 10px; color: var(--color-text-muted); }

  .form-input {
    background: var(--color-bg-primary); border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm); padding: 6px 8px;
    font-size: 12px; color: var(--color-text-primary); font-family: inherit; width: 100%;
  }
  .form-input:focus { outline: none; border-color: var(--color-accent); }

  .webhook-url-row { display: flex; gap: var(--spacing-sm); align-items: center; }
  .webhook-url-input { flex: 1; font-family: var(--font-mono); font-size: 11px; color: var(--color-text-secondary); }
  .webhook-hint { font-family: var(--font-mono); font-size: 10px; color: var(--color-text-tertiary); margin: 0; line-height: 1.5; }
  .webhook-hint code { color: var(--color-text-secondary); background: var(--color-bg-tertiary); padding: 1px 4px; border-radius: 3px; }

  .form-select {
    background: var(--color-bg-primary); border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm); padding: 6px 8px;
    font-size: 12px; color: var(--color-text-primary); width: 100%; cursor: pointer;
  }
  .form-select:focus { outline: none; border-color: var(--color-accent); }
  .form-select.narrow { width: auto; min-width: 120px; }

  .seg-ctrl { display: flex; }
  .seg-btn {
    font-family: var(--font-mono); font-size: 11px;
    padding: 4px 10px; background: none;
    border: 1px solid var(--color-border-primary);
    cursor: pointer; color: var(--color-text-muted);
    transition: all 100ms; margin-left: -1px;
  }
  .seg-btn:first-child { border-radius: var(--radius-sm) 0 0 var(--radius-sm); margin-left: 0; }
  .seg-btn:last-child  { border-radius: 0 var(--radius-sm) var(--radius-sm) 0; }
  .seg-btn.active { background: rgba(234,179,8,0.08); border-color: var(--color-accent); color: var(--color-accent); z-index: 1; position: relative; }
  .seg-btn:hover:not(.active) { color: var(--color-text-secondary); }

  .form-actions { display: flex; gap: var(--spacing-sm); }
</style>

<script lang="ts">
  import { getApi } from '../utils/api';
  import { onMount } from 'svelte';
  import { profileState, dashboardState } from '../stores.svelte';
  import { notifications } from '../notifications.svelte';
  import Spinner from '../components/Spinner.svelte';
  import EmptyState from '../components/EmptyState.svelte';

  // ── Types ──────────────────────────────────────────────────────────────

  interface CollectJob {
    id: string;
    profile: string;
    name: string;
    command: string;
    interval_s: number;
    enabled: boolean;
    default_actions: string;
    last_run_at?: number;
    last_error: string;
    created_at: number;
    target_type: string;
    target_id: string;
    target_prompt: string;
    run_at: string;
    days: string;
  }

  interface NamedItem { id: string; name: string; workspace_profile: string; }

  type TargetType = 'shell' | 'playbook' | 'chain' | 'runner';
  type ScheduleMode = 'interval' | 'time';

  // ── State ──────────────────────────────────────────────────────────────

  const profile = $derived(profileState.active?.name ?? '');

  let jobs         = $state<CollectJob[]>([]);
  let playbooks    = $state<NamedItem[]>([]);
  let chains       = $state<NamedItem[]>([]);
  let loading      = $state(false);
  let editingId    = $state<string | null>(null); // null=none, 'new'=create, id=edit
  let runningId    = $state<string | null>(null);
  let addedId      = $state<string | null>(null);
  let statusMsg    = $state('');
  let filterProfile = $state('');
  $effect(() => { filterProfile = profile; });

  // Edit form state
  let draft = $state({
    name: '',
    targetType: 'shell' as TargetType,
    command: '',
    targetId: '',
    targetPrompt: '',
    scheduleMode: 'interval' as ScheduleMode,
    interval_s: 300,
    run_at: '08:30',
    days: 'daily',
    enabled: true,
  });

  // ── Derived ────────────────────────────────────────────────────────────

  let jobProfiles = $derived.by(() => {
    const s = new Set<string>();
    for (const j of jobs) if (j.profile) s.add(j.profile);
    return [...s].sort();
  });

  let visible = $derived(
    filterProfile ? jobs.filter(j => j.profile === filterProfile) : jobs
  );

  let draftProfile = $derived.by(() => {
    if (editingId === 'new') return filterProfile || profile;
    const job = jobs.find(j => j.id === editingId);
    return job?.profile ?? profile;
  });

  let visiblePlaybooks = $derived(
    playbooks.filter(p => !p.workspace_profile || p.workspace_profile === draftProfile)
  );
  let visibleChains = $derived(
    chains.filter(c => !c.workspace_profile || c.workspace_profile === draftProfile)
  );

  // ── Data loading ───────────────────────────────────────────────────────

  async function load() {
    const a = await getApi();
    if (!a) return;
    loading = true;
    try {
      const [j, p, c] = await Promise.all([
        (a.ListCollectJobs as any)('').catch(() => []),
        (a.ListPlaybooks as any)('').catch(() => []),
        (a.ListChains as any)().catch(() => []),
      ]);
      jobs = (j ?? []) as CollectJob[];
      playbooks = ((p ?? []) as any[]).map((x: any) => ({
        id: x.id, name: x.name,
        workspace_profile: x.profile ?? x.workspace_profile ?? '',
      }));
      chains = ((c ?? []) as any[]).map((x: any) => ({
        id: x.id, name: x.name,
        workspace_profile: x.workspace_profile ?? '',
      }));
    } finally {
      loading = false;
    }
  }

  // ── CRUD ───────────────────────────────────────────────────────────────

  async function save() {
    const a = await getApi();
    if (!a || !draft.name.trim()) return;
    const isNew = editingId === 'new';
    const existing = jobs.find(j => j.id === editingId);
    const payload: Partial<CollectJob> = {
      id:           isNew ? '' : (editingId ?? ''),
      profile:      isNew ? (filterProfile || profile) : (existing?.profile ?? profile),
      name:         draft.name.trim(),
      command:      draft.targetType === 'shell' ? draft.command.trim() : '',
      interval_s:   draft.scheduleMode === 'interval' ? draft.interval_s : 0,
      enabled:      draft.enabled,
      default_actions: '[]',
      last_error:   '',
      created_at:   0,
      target_type:  draft.targetType,
      target_id:    draft.targetId,
      target_prompt: draft.targetPrompt.trim(),
      run_at:       draft.scheduleMode === 'time' ? draft.run_at : '',
      days:         draft.scheduleMode === 'time' ? draft.days : '',
    };
    // Validation
    if (draft.targetType === 'shell' && !payload.command) return;
    if ((draft.targetType === 'playbook' || draft.targetType === 'chain') && !payload.target_id) return;
    if (draft.targetType === 'runner' && !payload.target_prompt) return;
    try {
      await (a.SaveCollectJob as any)(payload);
      editingId = null;
      await load();
    } catch (e: any) {
      flash(`Error: ${e?.message ?? 'save failed'}`, true);
    }
  }

  async function remove(id: string) {
    const a = await getApi();
    if (!a) return;
    try {
      await (a.DeleteCollectJob as any)(id);
      await load();
    } catch (e: any) {
      notifications.error(`Failed to delete job: ${e?.message ?? 'unknown error'}`);
    }
  }

  async function runNow(id: string) {
    const a = await getApi();
    if (!a) return;
    runningId = id;
    try {
      await (a.RunCollectJobNow as any)(id);
      await load();
    } finally {
      runningId = null;
    }
  }

  async function toggle(job: CollectJob) {
    const a = await getApi();
    if (!a) return;
    try {
      await (a.SaveCollectJob as any)({ ...job, enabled: !job.enabled });
      await load();
    } catch (e: any) {
      notifications.error(`Failed to update job: ${e?.message ?? 'unknown error'}`);
    }
  }

  // ── Form helpers ───────────────────────────────────────────────────────

  function startNew() {
    editingId = 'new';
    draft = { name: '', targetType: 'shell', command: '', targetId: '', targetPrompt: '',
              scheduleMode: 'interval', interval_s: 300, run_at: '08:30', days: 'daily', enabled: true };
  }

  function startEdit(job: CollectJob) {
    editingId = job.id;
    draft = {
      name:         job.name,
      targetType:   (job.target_type || 'shell') as TargetType,
      command:      job.command,
      targetId:     job.target_id,
      targetPrompt: job.target_prompt,
      scheduleMode: job.run_at ? 'time' : 'interval',
      interval_s:   job.interval_s || 300,
      run_at:       job.run_at || '08:30',
      days:         job.days || 'daily',
      enabled:      job.enabled,
    };
  }

  function cancelEdit() { editingId = null; }

  function flash(msg: string, _err = false) {
    statusMsg = msg;
    setTimeout(() => { statusMsg = ''; }, 3000);
  }

  async function addToWidget(job: CollectJob) {
    const a = await getApi();
    if (!a) return;
    const id = `w-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    const widget = {
      id, kind: 'script-metric' as const,
      config: { label: job.name, command: job.command, interval: job.interval_s,
                jobId: job.id, valueType: 'number' as const },
      x: 0, y: 0, w: 3, h: 2, minW: 2, minH: 2,
    };
    const updated = [...dashboardState.widgets, widget];
    dashboardState.updateWidgets(updated);
    try {
      await a.SaveDashboardLayout(profile, JSON.stringify({ version: 1, widgets: updated }));
      addedId = job.id;
      setTimeout(() => { addedId = null; }, 2500);
    } catch {}
  }

  // ── Format helpers ─────────────────────────────────────────────────────

  function fmtSchedule(job: CollectJob): string {
    if (job.run_at) {
      return job.days === 'weekdays' ? `${job.run_at} weekdays` : `${job.run_at} daily`;
    }
    const s = job.interval_s;
    if (s >= 3600) return `every ${s / 3600}h`;
    if (s >= 60)   return `every ${Math.floor(s / 60)}m`;
    return `every ${s}s`;
  }

  function fmtLastRun(job: CollectJob): string {
    if (!job.last_run_at) return 'never';
    const diff = Date.now() - job.last_run_at;
    if (diff < 60_000)     return 'just now';
    if (diff < 3_600_000)  return `${Math.floor(diff / 60_000)}m ago`;
    if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
    return new Date(job.last_run_at).toLocaleDateString([], { month: 'short', day: 'numeric' });
  }

  function targetLabel(job: CollectJob): string {
    switch (job.target_type) {
      case 'playbook': {
        const pb = playbooks.find(p => p.id === job.target_id);
        return pb ? pb.name : job.target_id.slice(0, 8);
      }
      case 'chain': {
        const ch = chains.find(c => c.id === job.target_id);
        return ch ? ch.name : job.target_id.slice(0, 8);
      }
      case 'runner':  return job.target_prompt.slice(0, 40) + (job.target_prompt.length > 40 ? '…' : '');
      default:        return job.command.split('\n')[0].slice(0, 60) + (job.command.length > 60 ? '…' : '');
    }
  }

  function isFormValid(): boolean {
    if (!draft.name.trim()) return false;
    if (draft.targetType === 'shell' && !draft.command.trim()) return false;
    if ((draft.targetType === 'playbook' || draft.targetType === 'chain') && !draft.targetId) return false;
    if (draft.targetType === 'runner' && !draft.targetPrompt.trim()) return false;
    return true;
  }

  onMount(() => {
    void load();
    window.runtime?.EventsOn('collect:update', () => void load());
    return () => window.runtime?.EventsOff('collect:update');
  });
</script>

<div class="jobs">
  <div class="panel-header" style="margin-bottom:0;">
    <h1 class="page-title">jobs</h1>
    <div style="display:flex;align-items:center;gap:var(--spacing-md);">
      {#if loading}<Spinner />{/if}
      {#if statusMsg}
        <span class="status-msg">{statusMsg}</span>
      {/if}
      {#if editingId !== 'new'}
        <button class="btn primary" onclick={startNew}>+ new job</button>
      {/if}
    </div>
  </div>

  <!-- Profile filter -->
  {#if jobProfiles.length > 1}
    <div class="filter-row">
      <span class="filter-label">profile</span>
      <div class="tag-bar">
        <button class="tag" class:active={filterProfile === ''} onclick={() => filterProfile = ''}>all</button>
        {#each jobProfiles as p (p)}
          <button class="tag" class:active={filterProfile === p} onclick={() => filterProfile = filterProfile === p ? '' : p}>{p}</button>
        {/each}
      </div>
    </div>
  {/if}

  <!-- Create form -->
  {#if editingId === 'new'}
    <div class="job-form">
      <div class="form-title">new job</div>

      <label class="form-row">
        <span class="form-label">name</span>
        <input class="form-input" bind:value={draft.name} placeholder="Morning standup" />
      </label>

      <!-- Target type selector -->
      <div class="form-row">
        <span class="form-label">target</span>
        <div class="seg-ctrl">
          {#each (['shell', 'playbook', 'chain', 'runner'] as TargetType[]) as t (t)}
            <button class="seg-btn" class:active={draft.targetType === t} onclick={() => { draft.targetType = t; draft.targetId = ''; }}>{t}</button>
          {/each}
        </div>
      </div>

      <!-- Conditional target fields -->
      {#if draft.targetType === 'shell'}
        <label class="form-row">
          <span class="form-label">command</span>
          <textarea class="form-textarea" bind:value={draft.command} placeholder="script that outputs JSON array" rows="4"></textarea>
        </label>
      {:else if draft.targetType === 'playbook'}
        <label class="form-row">
          <span class="form-label">playbook</span>
          <select class="form-select" bind:value={draft.targetId}>
            <option value="">— select —</option>
            {#each visiblePlaybooks as pb (pb.id)}
              <option value={pb.id}>{pb.name}</option>
            {/each}
          </select>
        </label>
      {:else if draft.targetType === 'chain'}
        <label class="form-row">
          <span class="form-label">chain</span>
          <select class="form-select" bind:value={draft.targetId}>
            <option value="">— select —</option>
            {#each visibleChains as ch (ch.id)}
              <option value={ch.id}>{ch.name}</option>
            {/each}
          </select>
        </label>
      {:else if draft.targetType === 'runner'}
        <label class="form-row">
          <span class="form-label">prompt</span>
          <textarea class="form-textarea" bind:value={draft.targetPrompt} placeholder="Run the morning standup and write output to…" rows="4"></textarea>
        </label>
      {/if}

      <!-- Schedule mode -->
      <div class="form-row">
        <span class="form-label">schedule</span>
        <div class="seg-ctrl">
          <button class="seg-btn" class:active={draft.scheduleMode === 'interval'} onclick={() => draft.scheduleMode = 'interval'}>interval</button>
          <button class="seg-btn" class:active={draft.scheduleMode === 'time'}     onclick={() => draft.scheduleMode = 'time'}>time of day</button>
        </div>
      </div>

      {#if draft.scheduleMode === 'interval'}
        <label class="form-row">
          <span class="form-label">every</span>
          <div class="interval-row">
            <input class="form-input short" type="number" min="30" bind:value={draft.interval_s} />
            <span class="form-unit">seconds</span>
          </div>
        </label>
      {:else}
        <div class="form-row">
          <span class="form-label">time</span>
          <div class="time-row">
            <input class="form-input short" type="time" bind:value={draft.run_at} />
            <select class="form-select narrow" bind:value={draft.days}>
              <option value="daily">daily</option>
              <option value="weekdays">weekdays</option>
            </select>
          </div>
        </div>
      {/if}

      <div class="form-actions">
        <button class="btn sm primary" onclick={save} disabled={!isFormValid()}>save</button>
        <button class="btn sm ghost" onclick={cancelEdit}>cancel</button>
      </div>
    </div>
  {/if}

  <!-- Job list -->
  {#if loading && jobs.length === 0}
    <div class="empty">loading…</div>
  {:else if jobs.length === 0 && editingId !== 'new'}
    <EmptyState title="No jobs yet" message="Create one to schedule recurring work." />
  {:else if visible.length === 0 && editingId !== 'new'}
    <EmptyState title="No jobs for this profile" />
  {:else}
    <div class="job-list">
      {#each visible as job (job.id)}
        <div class="job-card" class:editing={editingId === job.id}>
          {#if editingId === job.id}
            <!-- Inline edit form -->
            <div class="job-form inline">
              <label class="form-row">
                <span class="form-label">name</span>
                <input class="form-input" bind:value={draft.name} />
              </label>

              <div class="form-row">
                <span class="form-label">target</span>
                <div class="seg-ctrl">
                  {#each (['shell', 'playbook', 'chain', 'runner'] as TargetType[]) as t (t)}
                    <button class="seg-btn" class:active={draft.targetType === t} onclick={() => { draft.targetType = t; draft.targetId = ''; }}>{t}</button>
                  {/each}
                </div>
              </div>

              {#if draft.targetType === 'shell'}
                <label class="form-row">
                  <span class="form-label">command</span>
                  <textarea class="form-textarea" bind:value={draft.command} rows="4"></textarea>
                </label>
              {:else if draft.targetType === 'playbook'}
                <label class="form-row">
                  <span class="form-label">playbook</span>
                  <select class="form-select" bind:value={draft.targetId}>
                    <option value="">— select —</option>
                    {#each playbooks as pb (pb.id)}
                      <option value={pb.id}>{pb.name}</option>
                    {/each}
                  </select>
                </label>
              {:else if draft.targetType === 'chain'}
                <label class="form-row">
                  <span class="form-label">chain</span>
                  <select class="form-select" bind:value={draft.targetId}>
                    <option value="">— select —</option>
                    {#each chains as ch (ch.id)}
                      <option value={ch.id}>{ch.name}</option>
                    {/each}
                  </select>
                </label>
              {:else if draft.targetType === 'runner'}
                <label class="form-row">
                  <span class="form-label">prompt</span>
                  <textarea class="form-textarea" bind:value={draft.targetPrompt} rows="4"></textarea>
                </label>
              {/if}

              <div class="form-row">
                <span class="form-label">schedule</span>
                <div class="seg-ctrl">
                  <button class="seg-btn" class:active={draft.scheduleMode === 'interval'} onclick={() => draft.scheduleMode = 'interval'}>interval</button>
                  <button class="seg-btn" class:active={draft.scheduleMode === 'time'}     onclick={() => draft.scheduleMode = 'time'}>time of day</button>
                </div>
              </div>

              {#if draft.scheduleMode === 'interval'}
                <label class="form-row">
                  <span class="form-label">every</span>
                  <div class="interval-row">
                    <input class="form-input short" type="number" min="30" bind:value={draft.interval_s} />
                    <span class="form-unit">seconds</span>
                  </div>
                </label>
              {:else}
                <div class="form-row">
                  <span class="form-label">time</span>
                  <div class="time-row">
                    <input class="form-input short" type="time" bind:value={draft.run_at} />
                    <select class="form-select narrow" bind:value={draft.days}>
                      <option value="daily">daily</option>
                      <option value="weekdays">weekdays</option>
                    </select>
                  </div>
                </div>
              {/if}

              <div class="form-actions">
                <button class="btn sm primary" onclick={save} disabled={!isFormValid()}>save</button>
                <button class="btn sm ghost" onclick={cancelEdit}>cancel</button>
              </div>
            </div>
          {:else}
            <div class="job-row">
              <button class="job-toggle" class:on={job.enabled} onclick={() => toggle(job)}
                title={job.enabled ? 'disable' : 'enable'}>
                {job.enabled ? '●' : '○'}
              </button>
              <div class="job-info" role="button" tabindex="0"
                onclick={() => startEdit(job)}
                onkeydown={(e) => e.key === 'Enter' && startEdit(job)}>
                <span class="job-name">{job.name}</span>
                <span class="job-cmd">{targetLabel(job)}</span>
                <div class="job-meta-row">
                  {#if job.profile}
                    <span class="job-profile">{job.profile}</span>
                  {/if}
                  <span class="job-type-badge type-{job.target_type || 'shell'}">{job.target_type || 'shell'}</span>
                  <span class="job-meta">{fmtSchedule(job)}</span>
                  <span class="job-meta">last run: {fmtLastRun(job)}</span>
                  {#if job.last_error}
                    <span class="job-err" title={job.last_error}>✗ error</span>
                  {/if}
                </div>
              </div>
              <div class="job-btns">
                {#if (job.target_type || 'shell') === 'shell'}
                  <button class="job-btn" onclick={() => addToWidget(job)} title="add to dashboard"
                    class:added={addedId === job.id}>
                    {addedId === job.id ? '✓' : '+'}
                  </button>
                {/if}
                <button class="job-btn" onclick={() => runNow(job.id)} disabled={runningId === job.id} title="run now">
                  {runningId === job.id ? '…' : '▶'}
                </button>
                <button class="job-btn danger" onclick={() => remove(job.id)} title="delete">✕</button>
              </div>
            </div>
          {/if}
        </div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .jobs {
    padding: var(--panel-padding);
    display: flex;
    flex-direction: column;
    gap: var(--spacing-md);
    min-height: 100%;
  }

  .status-msg {
    font-family: var(--font-mono); font-size: 11px;
    color: var(--color-success);
  }

  /* Filter */
  .filter-row { display: flex; align-items: center; gap: var(--spacing-sm); }
  .filter-label { font-family: var(--font-mono); font-size: 10px; color: var(--color-text-muted); width: 38px; flex-shrink: 0; }
  .tag-bar { display: flex; flex-wrap: wrap; gap: 6px; }
  .tag {
    font-family: var(--font-mono); font-size: 10px; padding: 2px 8px;
    border-radius: 999px; border: 1px solid var(--color-border-primary);
    background: none; cursor: pointer; color: var(--color-text-muted);
  }
  .tag:hover { border-color: var(--color-border-secondary); color: var(--color-text-secondary); }
  .tag.active { border-color: var(--color-accent); color: var(--color-accent); background: rgba(234,179,8,0.06); }

  .empty {
    font-size: 13px; color: var(--color-text-tertiary);
    padding: var(--spacing-3xl) 0; line-height: 1.5;
  }

  /* Job list */
  .job-list { display: flex; flex-direction: column; gap: var(--spacing-sm); }

  .job-card {
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-md);
    background: var(--color-bg-secondary);
    overflow: hidden; transition: border-color 100ms;
  }
  .job-card:hover { border-color: var(--color-border-secondary); }
  .job-card.editing { border-color: var(--color-accent); }

  .job-row {
    display: grid; grid-template-columns: 24px 1fr auto;
    align-items: center; gap: var(--spacing-md);
    padding: var(--spacing-md) var(--spacing-lg);
  }

  .job-toggle {
    background: none; border: none; cursor: pointer;
    font-size: 14px; font-family: var(--font-mono);
    color: var(--color-text-muted); padding: 0; line-height: 1; transition: color 100ms;
  }
  .job-toggle.on { color: var(--color-success); }
  .job-toggle:hover { opacity: 0.7; }

  .job-info { display: flex; flex-direction: column; gap: 3px; min-width: 0; cursor: pointer; }
  .job-name { font-size: 13px; font-weight: 500; color: var(--color-text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .job-cmd { font-family: var(--font-mono); font-size: 10px; color: var(--color-text-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .job-meta-row { display: flex; gap: var(--spacing-sm); align-items: center; flex-wrap: wrap; }
  .job-meta { font-family: var(--font-mono); font-size: 10px; color: var(--color-text-tertiary); }
  .job-profile { font-family: var(--font-mono); font-size: 10px; color: var(--color-accent); background: rgba(234,179,8,0.08); border: 1px solid rgba(234,179,8,0.2); border-radius: 999px; padding: 1px 6px; }
  .job-err { font-family: var(--font-mono); font-size: 10px; color: var(--color-error); }

  .job-type-badge {
    font-family: var(--font-mono); font-size: 10px;
    padding: 1px 6px; border-radius: 999px;
    border: 1px solid var(--color-border-primary);
    color: var(--color-text-muted);
  }
  .job-type-badge.type-playbook { color: #6366f1; border-color: rgba(99,102,241,0.3); background: rgba(99,102,241,0.06); }
  .job-type-badge.type-chain    { color: #10b981; border-color: rgba(16,185,129,0.3); background: rgba(16,185,129,0.06); }
  .job-type-badge.type-runner   { color: #f59e0b; border-color: rgba(245,158,11,0.3); background: rgba(245,158,11,0.06); }

  .job-btns { display: flex; gap: 4px; flex-shrink: 0; }
  .job-btn {
    background: none; border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm); padding: 3px 8px;
    font-size: 11px; cursor: pointer; color: var(--color-text-muted);
    transition: all 100ms; font-family: var(--font-mono);
  }
  .job-btn:hover:not(:disabled) { border-color: var(--color-accent); color: var(--color-accent); }
  .job-btn:disabled { opacity: 0.4; cursor: not-allowed; }
  .job-btn.danger:hover:not(:disabled) { border-color: var(--color-error); color: var(--color-error); }
  .job-btn.added { border-color: var(--color-success); color: var(--color-success); }

  /* Form */
  .job-form {
    display: flex; flex-direction: column; gap: var(--spacing-md);
    padding: var(--spacing-lg);
    border: 1px solid var(--color-accent);
    border-radius: var(--radius-md);
    background: var(--color-bg-secondary);
  }
  .job-form.inline { border: none; border-radius: 0; }
  .form-title { font-size: 12px; font-weight: 600; color: var(--color-text-secondary); }
  .form-row { display: flex; flex-direction: column; gap: 4px; }
  .form-label { font-family: var(--font-mono); font-size: 10px; color: var(--color-text-muted); }

  .form-input {
    background: var(--color-bg-primary); border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm); padding: 6px 8px;
    font-size: 12px; color: var(--color-text-primary); font-family: inherit; width: 100%;
  }
  .form-input:focus { outline: none; border-color: var(--color-accent); }
  .form-input.short { width: 100px; }

  .form-textarea {
    background: var(--color-bg-primary); border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm); padding: 6px 8px;
    font-size: 11px; font-family: var(--font-mono);
    color: var(--color-text-primary); width: 100%; resize: vertical;
  }
  .form-textarea:focus { outline: none; border-color: var(--color-accent); }

  .form-select {
    background: var(--color-bg-primary); border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm); padding: 6px 8px;
    font-size: 12px; color: var(--color-text-primary); font-family: inherit;
    width: 100%; cursor: pointer;
  }
  .form-select:focus { outline: none; border-color: var(--color-accent); }
  .form-select.narrow { width: auto; min-width: 100px; }

  /* Segmented control */
  .seg-ctrl { display: flex; gap: 0; }
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

  .interval-row { display: flex; align-items: center; gap: var(--spacing-sm); }
  .time-row { display: flex; align-items: center; gap: var(--spacing-sm); }
  .form-unit { font-family: var(--font-mono); font-size: 11px; color: var(--color-text-muted); }

  .form-actions { display: flex; gap: var(--spacing-sm); }
</style>

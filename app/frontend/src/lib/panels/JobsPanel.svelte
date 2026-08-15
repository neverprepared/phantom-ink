<script lang="ts">
  // Autonomous agent jobs: fan one agent spec across many machines, walk away,
  // come back to grouped per-target results. Thin surface over the router's
  // /api/hub/jobs helper (SubmitAgentJob / ListAgentJobs / GetAgentJob).
  import { onMount, onDestroy } from 'svelte';
  import { getApi } from '../utils/api';
  import { profileState } from '../stores.svelte';
  import { notifications } from '../notifications.svelte';
  import { timeAgo } from '../utils/format';
  import Spinner from '../components/Spinner.svelte';
  import EmptyState from '../components/EmptyState.svelte';

  interface JobSummary {
    job_id: string;
    agent_name: string;
    description: string;
    total: number;
    by_status: Record<string, number>;
    created_at: number;
  }
  interface HubTask {
    id: string; description: string; agent_name: string; status: string;
    runner_name: string; backend: string; docker_host: string; session_name: string;
    result: string; error: string; created_at: number;
  }
  interface JobDetail {
    job_id: string;
    summary: { total: number; by_status: Record<string, number> };
    tasks: HubTask[];
  }
  interface Runner { name: string; tags: string[]; }

  const TERMINAL = new Set(['completed', 'failed', 'cancelled']);

  let jobs = $state<JobSummary[]>([]);
  let runners = $state<Runner[]>([]);
  let agents = $state<string[]>(['worker']);
  let loaded = $state(false);
  let loadError = $state<string | null>(null);

  let selectedId = $state<string | null>(null);
  let detail = $state<JobDetail | null>(null);

  // submit form
  let task = $state('');
  let agent = $state('worker');
  let repo = $state('');
  let backend = $state<'docker' | 'utm'>('docker');
  let pool = $state('');
  let priority = $state(0);
  let useLocal = $state(false);
  let selectedRunners = $state<Set<string>>(new Set());
  let dockerHosts = $state('');  // remote Docker daemons over SSH, one ssh://user@host per line
  let submitting = $state(false);

  let pollHandle: number | undefined;

  onMount(() => {
    void bootstrap();
    void refresh();
    pollHandle = window.setInterval(() => { void refresh(); void refreshDetail(); }, 5_000);
  });
  onDestroy(() => { if (pollHandle !== undefined) window.clearInterval(pollHandle); });

  async function bootstrap() {
    const a = await getApi();
    if (!a) return;
    try {
      const rs = (await a.ListRunners()) ?? [];
      runners = rs.map((r: any) => ({ name: r.name, tags: r.tags ?? [] }));
    } catch { /* runners optional */ }
    try {
      const roles = (await (a as any).ListAgentRoles()) ?? [];
      const names = roles.map((r: any) => r.name).filter(Boolean);
      if (names.length) agents = names;
    } catch { /* fall back to ['worker'] */ }
  }

  async function refresh() {
    const a = await getApi();
    if (!a) { loaded = true; loadError = 'API bindings unavailable'; return; }
    try {
      jobs = ((await (a as any).ListAgentJobs()) ?? []) as JobSummary[];
      loadError = null;
    } catch (e) {
      loadError = String(e);
    } finally {
      loaded = true;
    }
  }

  async function refreshDetail() {
    if (!selectedId) return;
    const a = await getApi();
    if (!a) return;
    try {
      detail = (await (a as any).GetAgentJob(selectedId)) as JobDetail;
    } catch { /* job may have aged out */ }
  }

  async function selectJob(id: string) {
    selectedId = id;
    detail = null;
    await refreshDetail();
  }

  function toggleRunner(name: string) {
    const next = new Set(selectedRunners);
    next.has(name) ? next.delete(name) : next.add(name);
    selectedRunners = next;
  }

  function remoteHosts(): string[] {
    return dockerHosts.split('\n').map((h) => h.trim()).filter(Boolean);
  }

  function buildTargets(): any[] {
    const targets: any[] = [];
    for (const name of selectedRunners) targets.push({ runner: name, backend });
    for (const host of remoteHosts()) targets.push({ docker_host: host, backend: 'docker' });
    if (useLocal || targets.length === 0) {
      const t: any = { backend };
      if (pool.trim()) t.pool = pool.trim();
      targets.push(t);
    }
    return targets;
  }
  const targetCount = $derived(
    selectedRunners.size + remoteHosts().length +
    ((useLocal || selectedRunners.size + remoteHosts().length === 0) ? 1 : 0),
  );

  async function submit() {
    if (!task.trim() || submitting) return;
    const a = await getApi();
    if (!a) return;
    submitting = true;
    try {
      const targets = buildTargets();
      const res = await (a as any).SubmitAgentJob({
        description: task.trim(),
        agent_name: agent,
        repo_url: repo.trim() || undefined,
        workspace_profile: profileState.active?.name || undefined,
        targets,
        priority,
      });
      notifications.success(`Job fanned out to ${res.count} machine(s)`);
      task = '';
      await refresh();
      if (res.job_id) await selectJob(res.job_id);
    } catch (e) {
      notifications.error(`Submit failed: ${e}`);
    } finally {
      submitting = false;
    }
  }

  async function cancelJob() {
    if (!detail) return;
    const a = await getApi();
    if (!a) return;
    const live = detail.tasks.filter((t) => !TERMINAL.has(t.status));
    for (const t of live) {
      try { await (a as any).CancelHubTask(t.id); } catch { /* best effort */ }
    }
    notifications.success(`Cancelled ${live.length} task(s)`);
    await refreshDetail();
    await refresh();
  }

  function badgeClass(status: string): string {
    if (status === 'completed') return 'ok';
    if (status === 'failed') return 'err';
    if (status === 'running') return 'run';
    if (status === 'cancelled') return 'muted';
    return 'pend';
  }
  function rollup(by: Record<string, number>): [string, number][] {
    return Object.entries(by).sort((a, b) => a[0].localeCompare(b[0]));
  }
  function targetLabel(t: HubTask): string {
    return t.runner_name || t.docker_host || `auto/${t.backend || 'docker'}`;
  }
  const canSubmit = $derived(task.trim().length > 0 && !submitting);
  const jobLive = $derived(detail?.tasks.some((t) => !TERMINAL.has(t.status)) ?? false);
</script>

<div class="jobs">
  <header class="head">
    <div>
      <h1>Jobs</h1>
      <p class="sub">Fan an autonomous agent across machines — then walk away.</p>
    </div>
  </header>

  <div class="grid">
    <!-- Submit -->
    <section class="card submit">
      <h2>New job</h2>
      <label class="field">
        <span class="form-label">task</span>
        <textarea bind:value={task} rows="3" placeholder="What should each agent do?"></textarea>
      </label>
      <div class="row">
        <label class="field">
          <span class="form-label">agent</span>
          <select bind:value={agent}>
            {#each agents as name}<option value={name}>{name}</option>{/each}
          </select>
        </label>
        <label class="field">
          <span class="form-label">backend</span>
          <select bind:value={backend}>
            <option value="docker">docker</option>
            <option value="utm">utm</option>
          </select>
        </label>
      </div>
      <label class="field">
        <span class="form-label">repo url <span class="opt">(optional)</span></span>
        <input bind:value={repo} placeholder="https://github.com/org/repo.git" />
      </label>

      <span class="form-label">targets</span>
      <div class="targets">
        {#if runners.length === 0}
          <span class="muted-note">no runners registered</span>
        {:else}
          {#each runners as r}
            <button
              type="button"
              class="chip"
              class:on={selectedRunners.has(r.name)}
              onclick={() => toggleRunner(r.name)}
            >{r.name}</button>
          {/each}
        {/if}
        <label class="chip check" class:on={useLocal}>
          <input type="checkbox" bind:checked={useLocal} /> local / auto
        </label>
      </div>
      <label class="field">
        <span class="form-label">remote docker hosts <span class="opt">(ssh://user@host, one per line)</span></span>
        <textarea bind:value={dockerHosts} rows="2" placeholder="ssh://ops@build-1&#10;ssh://ops@build-2"></textarea>
      </label>
      <div class="row">
        <label class="field">
          <span class="form-label">pool <span class="opt">(auto targets)</span></span>
          <input bind:value={pool} placeholder="e.g. gpu" />
        </label>
        <label class="field narrow">
          <span class="form-label">priority</span>
          <input type="number" bind:value={priority} />
        </label>
      </div>
      <p class="hint">{targetCount} task(s) across selected targets</p>
      <button class="primary" disabled={!canSubmit} onclick={submit}>
        {submitting ? 'Submitting…' : 'Fan out job'}
      </button>
    </section>

    <!-- Jobs list -->
    <section class="card list">
      <h2>Recent jobs</h2>
      {#if !loaded}
        <Spinner />
      {:else if loadError}
        <EmptyState title="Couldn't load jobs" message={loadError} />
      {:else if jobs.length === 0}
        <EmptyState title="No jobs yet" message="Fan out a job to see it here." />
      {:else}
        <ul class="rows">
          {#each jobs as j}
            <li>
              <button class="job-row" class:sel={selectedId === j.job_id} onclick={() => selectJob(j.job_id)}>
                <div class="job-main">
                  <code class="jid">{j.job_id.slice(0, 8)}</code>
                  <span class="agent">{j.agent_name}</span>
                  <span class="count">{j.total} task{j.total === 1 ? '' : 's'}</span>
                </div>
                <div class="job-meta">
                  {#each rollup(j.by_status) as [st, n]}
                    <span class="badge {badgeClass(st)}">{st} {n}</span>
                  {/each}
                  <span class="ago">{timeAgo(j.created_at)}</span>
                </div>
              </button>
            </li>
          {/each}
        </ul>
      {/if}
    </section>

    <!-- Detail -->
    <section class="card detail">
      {#if !detail}
        <EmptyState title="Select a job" message="Per-machine results appear here." />
      {:else}
        <div class="detail-head">
          <div>
            <h2>Job <code>{detail.job_id.slice(0, 8)}</code></h2>
            <div class="detail-summary">
              total {detail.summary.total}
              {#each rollup(detail.summary.by_status) as [st, n]}
                <span class="badge {badgeClass(st)}">{st} {n}</span>
              {/each}
            </div>
          </div>
          {#if jobLive}
            <button class="danger" onclick={cancelJob}>Cancel live</button>
          {/if}
        </div>
        <ul class="tasks">
          {#each detail.tasks as t}
            <li class="task">
              <div class="task-top">
                <span class="badge {badgeClass(t.status)}">{t.status}</span>
                <span class="target">{targetLabel(t)}</span>
                {#if t.session_name}<code class="sess">{t.session_name}</code>{/if}
              </div>
              {#if t.error}<pre class="err-body">{t.error}</pre>{/if}
              {#if t.result && t.status === 'completed'}<pre class="res-body">{t.result}</pre>{/if}
            </li>
          {/each}
        </ul>
      {/if}
    </section>
  </div>
</div>

<style>
  .jobs { padding: var(--panel-padding); color: var(--color-text-primary); }
  .head { margin-bottom: var(--spacing-lg); }
  h1 { font-size: 1.4rem; margin: 0; }
  .sub { color: var(--color-text-muted); margin: 2px 0 0; font-size: 0.85rem; }
  h2 { font-size: 0.95rem; margin: 0 0 var(--spacing-md); color: var(--color-text-secondary); }

  .grid {
    display: grid;
    grid-template-columns: 320px 1fr;
    grid-template-areas: "submit list" "submit detail";
    gap: var(--spacing-lg);
    align-items: start;
  }
  .submit { grid-area: submit; }
  .list { grid-area: list; }
  .detail { grid-area: detail; min-height: 200px; }

  .card {
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-md);
    padding: var(--spacing-lg);
  }

  .field { display: flex; flex-direction: column; gap: 4px; margin-bottom: var(--spacing-md); }
  .field.narrow { max-width: 90px; }
  .row { display: flex; gap: var(--spacing-md); }
  .row .field { flex: 1; }
  .form-label { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--color-text-tertiary); }
  .opt { text-transform: none; letter-spacing: 0; color: var(--color-text-muted); }
  input, select, textarea {
    background: var(--color-bg-primary);
    border: 1px solid var(--color-border-secondary);
    border-radius: var(--radius-sm);
    color: var(--color-text-primary);
    padding: 6px 8px;
    font-size: 0.85rem;
    font-family: inherit;
  }
  textarea { resize: vertical; }

  .targets { display: flex; flex-wrap: wrap; gap: 6px; margin: 6px 0 var(--spacing-md); }
  .chip {
    background: var(--color-bg-primary);
    border: 1px solid var(--color-border-secondary);
    border-radius: 999px;
    color: var(--color-text-secondary);
    padding: 4px 10px;
    font-size: 0.78rem;
    cursor: pointer;
  }
  .chip.on { border-color: var(--color-accent); color: var(--color-accent); }
  .chip.check { display: inline-flex; align-items: center; gap: 5px; }
  .chip.check input { width: auto; padding: 0; }
  .muted-note { color: var(--color-text-muted); font-size: 0.8rem; }
  .hint { color: var(--color-text-muted); font-size: 0.78rem; margin: 0 0 var(--spacing-md); }

  button.primary {
    width: 100%;
    background: var(--color-accent);
    color: var(--color-bg-primary);
    border: none;
    border-radius: var(--radius-sm);
    padding: 8px;
    font-weight: 600;
    cursor: pointer;
  }
  button.primary:disabled { opacity: 0.5; cursor: not-allowed; }
  button.danger {
    background: transparent; color: var(--color-error);
    border: 1px solid var(--color-error); border-radius: var(--radius-sm);
    padding: 5px 10px; font-size: 0.8rem; cursor: pointer;
  }

  .rows { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
  .job-row {
    width: 100%; text-align: left; cursor: pointer;
    background: var(--color-bg-primary);
    border: 1px solid var(--color-border-secondary);
    border-radius: var(--radius-sm);
    padding: 8px 10px;
    display: flex; flex-direction: column; gap: 6px;
  }
  .job-row.sel { border-color: var(--color-accent); }
  .job-main { display: flex; align-items: center; gap: 10px; }
  .jid { font-family: var(--font-mono); color: var(--color-text-secondary); font-size: 0.8rem; }
  .agent { font-size: 0.85rem; }
  .count { color: var(--color-text-muted); font-size: 0.78rem; }
  .job-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
  .ago { color: var(--color-text-muted); font-size: 0.72rem; margin-left: auto; }

  .badge {
    font-size: 0.68rem; padding: 2px 7px; border-radius: 999px;
    text-transform: lowercase; white-space: nowrap;
    border: 1px solid var(--color-border-secondary); color: var(--color-text-secondary);
  }
  .badge.ok { color: var(--color-success); border-color: var(--color-success); }
  .badge.err { color: var(--color-error); border-color: var(--color-error); }
  .badge.run { color: var(--color-accent); border-color: var(--color-accent); }
  .badge.pend { color: var(--color-text-muted); }
  .badge.muted { color: var(--color-text-tertiary); }

  .detail-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: var(--spacing-md); }
  .detail-summary { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; color: var(--color-text-muted); font-size: 0.78rem; }
  .tasks { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
  .task { border: 1px solid var(--color-border-secondary); border-radius: var(--radius-sm); padding: 8px 10px; }
  .task-top { display: flex; align-items: center; gap: 10px; }
  .target { font-size: 0.85rem; }
  .sess { font-family: var(--font-mono); font-size: 0.72rem; color: var(--color-text-muted); margin-left: auto; }
  .err-body, .res-body {
    margin: 8px 0 0; padding: 6px 8px; border-radius: var(--radius-sm);
    background: var(--color-bg-primary); font-family: var(--font-mono);
    font-size: 0.72rem; white-space: pre-wrap; max-height: 140px; overflow: auto;
  }
  .err-body { color: var(--color-error); }
</style>

<script lang="ts">
  import { getApi } from '../utils/api';
  import { onMount } from 'svelte';
  import { brainboxEvents } from '../events.svelte';
  import { notifications } from '../notifications.svelte';
  import { profileState } from '../stores.svelte';
  import EmptyState from '../components/EmptyState.svelte';
  import Badge from '../components/Badge.svelte';

  let tasks = $state<any[]>([]);
  let loading = $state(true);
  let cancelling = $state<Set<string>>(new Set());

  let activeProfile = $derived(profileState.active);

  // Group tasks by job_id into job objects
  let jobs = $derived.by(() => {
    const map = new Map<string, any>();

    for (const t of tasks) {
      const jobId = t.job_id || t.id;
      if (!map.has(jobId)) {
        map.set(jobId, { id: jobId, tasks: [] });
      }
      map.get(jobId).tasks.push(t);
    }

    return Array.from(map.values()).map((job) => {
      const supervisor = job.tasks.find((t: any) => t.id === job.id) ?? job.tasks[0];
      const workers = job.tasks.filter((t: any) => t.id !== job.id);
      const statuses = job.tasks.map((t: any) => t.status);
      const status = statuses.some((s: string) => s === 'running')
        ? 'running'
        : statuses.some((s: string) => s === 'failed')
        ? 'failed'
        : statuses.every((s: string) => s === 'completed')
        ? 'completed'
        : statuses.some((s: string) => s === 'cancelled')
        ? 'cancelled'
        : 'pending';

      return {
        id: job.id,
        status,
        supervisor,
        workers,
        allTasks: job.tasks,
        repoUrl: supervisor?.repo_url ?? null,
        workspaceProfile: supervisor?.workspace_profile ?? '',
        createdAt: supervisor?.created_at ?? 0,
      };
    });
  });

  // Filter by active profile
  let filteredJobs = $derived.by(() => {
    const sorted = jobs.slice().sort((a, b) => (b.createdAt ?? 0) - (a.createdAt ?? 0));
    if (!activeProfile) return sorted;
    const target = activeProfile.name.toLowerCase();
    return sorted.filter((j) => (j.workspaceProfile ?? '').toLowerCase() === target);
  });

  let activeJobs = $derived(filteredJobs.filter((j) => j.status === 'running' || j.status === 'pending'));
  let completedJobs = $derived(filteredJobs.filter((j) => j.status !== 'running' && j.status !== 'pending'));

  async function refresh() {
    const a = await getApi();
    if (!a) { loading = false; return; }
    try {
      tasks = (await a.ListTasks('')) ?? [];
    } catch (err: any) {
      notifications.error(`Failed to load jobs: ${err?.message ?? err}`);
    } finally {
      loading = false;
    }
  }

  async function stopJob(job: any) {
    const a = await getApi();
    if (!a) return;
    const runningIds = job.allTasks
      .filter((t: any) => t.status === 'running' || t.status === 'pending')
      .map((t: any) => t.id);
    if (!runningIds.length) return;

    cancelling = new Set([...cancelling, job.id]);
    try {
      await Promise.all(runningIds.map((id: string) => a.CancelTask(id)));
      notifications.success(`Job ${job.id.slice(0, 8)} stopped`);
      refresh();
    } catch (err: any) {
      notifications.error(`Failed to stop job: ${err?.message ?? err}`);
    } finally {
      cancelling = new Set([...cancelling].filter((id) => id !== job.id));
    }
  }

  function repoName(url: any): string {
    if (!url || typeof url !== 'string') return '';
    return url.replace(/\/$/, '').split('/').slice(-2).join('/');
  }

  function timeAgo(ms: any): string {
    if (!ms) return '';
    const diff = Date.now() - Number(ms);
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.floor(hrs / 24)}d ago`;
  }

  $effect(() => {
    const ev = brainboxEvents.last;
    if (ev) refresh();
  });

  onMount(refresh);
</script>

<div class="panel" aria-busy={loading}>
  <header>
    <h1><span class="accent">jobs</span></h1>
    <button class="btn-refresh" onclick={refresh} title="Refresh" aria-label="Refresh jobs">
      <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/></svg>
    </button>
  </header>

  {#if loading}
    <div class="loading">loading jobs...</div>
  {:else if filteredJobs.length === 0}
    <EmptyState title="No jobs" message="Submit a supervisor task to start a multi-agent job." />
  {:else}
    {#if activeJobs.length > 0}
      <section>
        <h2 class="section-label">active</h2>
        <div class="jobs-list">
          {#each activeJobs as job (job.id)}
            <div class="job-card running">
              <div class="job-header">
                <div class="job-id-row">
                  <Badge variant={job.status === 'running' ? 'success' : 'muted'}>{job.status}</Badge>
                  <span class="job-id">{job.id.slice(0, 8)}</span>
                  {#if job.repoUrl}
                    <span class="repo-name">{repoName(job.repoUrl)}</span>
                  {/if}
                  <span class="time-ago">{timeAgo(job.createdAt)}</span>
                </div>
                <button
                  class="btn-stop"
                  onclick={() => stopJob(job)}
                  disabled={cancelling.has(job.id)}
                  title="Stop all tasks in this job"
                >
                  {cancelling.has(job.id) ? 'stopping…' : 'stop'}
                </button>
              </div>

              <div class="task-rows">
                <!-- Supervisor -->
                <div class="task-row supervisor">
                  <Badge variant="muted" size="sm">{job.supervisor?.agent_name ?? 'supervisor'}</Badge>
                  <Badge variant={job.supervisor?.status === 'running' ? 'success' : 'muted'} size="sm">
                    {job.supervisor?.status ?? ''}
                  </Badge>
                  <span class="session-name">{job.supervisor?.session_name ?? ''}</span>
                </div>
                <!-- Workers -->
                {#each job.workers as worker (worker.id)}
                  <div class="task-row">
                    <Badge variant="muted" size="sm">{worker.agent_name}</Badge>
                    <Badge
                      variant={worker.status === 'running' ? 'success' : worker.status === 'failed' ? 'danger' : worker.status === 'completed' ? 'info' : 'muted'}
                      size="sm"
                    >{worker.status}</Badge>
                    <span class="session-name">{worker.session_name ?? worker.id.slice(0, 8)}</span>
                  </div>
                {/each}
              </div>
            </div>
          {/each}
        </div>
      </section>
    {/if}

    {#if completedJobs.length > 0}
      <section>
        <h2 class="section-label">history</h2>
        <div class="jobs-list">
          {#each completedJobs as job (job.id)}
            <div class="job-card">
              <div class="job-header">
                <div class="job-id-row">
                  <Badge variant={job.status === 'completed' ? 'info' : job.status === 'failed' ? 'danger' : 'muted'}>
                    {job.status}
                  </Badge>
                  <span class="job-id">{job.id.slice(0, 8)}</span>
                  {#if job.repoUrl}
                    <span class="repo-name">{repoName(job.repoUrl)}</span>
                  {/if}
                  <span class="time-ago">{timeAgo(job.createdAt)}</span>
                </div>
                <span class="worker-count">{job.workers.length} worker{job.workers.length !== 1 ? 's' : ''}</span>
              </div>
            </div>
          {/each}
        </div>
      </section>
    {/if}
  {/if}
</div>

<style>
  .panel {
    padding: var(--panel-padding, 24px);
    display: flex;
    flex-direction: column;
    gap: 24px;
  }

  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  h1 {
    font-size: 22px;
    font-weight: 600;
    letter-spacing: -0.3px;
  }

  .accent {
    color: var(--color-accent);
  }

  .btn-refresh {
    background: none;
    border: none;
    cursor: pointer;
    color: var(--color-text-secondary);
    padding: 4px;
    border-radius: 4px;
    display: flex;
    align-items: center;
  }

  .btn-refresh:hover {
    color: var(--color-text-primary);
    background: var(--color-bg-hover);
  }

  .loading {
    color: var(--color-text-secondary);
    font-size: 13px;
  }

  .section-label {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--color-text-secondary);
    margin-bottom: 10px;
  }

  .jobs-list {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .job-card {
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-lg, 8px);
    padding: 14px 16px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .job-card.running {
    border-color: var(--color-accent-subtle, color-mix(in srgb, var(--color-accent) 25%, transparent));
  }

  .job-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }

  .job-id-row {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }

  .job-id {
    font-family: var(--font-mono, monospace);
    font-size: 12px;
    color: var(--color-text-secondary);
  }

  .repo-name {
    font-size: 12px;
    color: var(--color-text-primary);
    font-weight: 500;
  }

  .time-ago {
    font-size: 11px;
    color: var(--color-text-secondary);
  }

  .worker-count {
    font-size: 12px;
    color: var(--color-text-secondary);
  }

  .btn-stop {
    font-size: 12px;
    padding: 4px 10px;
    border-radius: 4px;
    border: 1px solid var(--color-border-primary);
    background: none;
    color: var(--color-text-secondary);
    cursor: pointer;
    white-space: nowrap;
  }

  .btn-stop:hover:not(:disabled) {
    border-color: var(--color-danger, #e55);
    color: var(--color-danger, #e55);
  }

  .btn-stop:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .task-rows {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding-top: 6px;
    border-top: 1px solid var(--color-border-primary);
  }

  .task-row {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .task-row.supervisor {
    opacity: 0.85;
  }

  .session-name {
    font-family: var(--font-mono, monospace);
    font-size: 11px;
    color: var(--color-text-secondary);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
</style>

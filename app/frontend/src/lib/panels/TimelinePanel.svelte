<script lang="ts">
  import { getApi } from '../utils/api';
  import { onMount } from 'svelte';
  import { brainboxEvents } from '../events.svelte';
  import { profileState } from '../stores.svelte';

  interface TaskNode {
    id: string;
    description: string;
    agent_name: string;
    status: string;
    created_at: number;
    updated_at: number;
    error: string | null;
    session_name: string;
    workspace_profile: string;
    job_id: string;
    spawned_by: string;
    child_task_ids: string[];
  }

  interface JobTree {
    job_id: string;
    root: TaskNode | null;
    tasks: TaskNode[];
    latest_at: number;
  }

  let allTasks   = $state<TaskNode[]>([]);
  let loading    = $state(true);
  let refreshing = $state(false);
  let expanded   = $state<Set<string>>(new Set());
  let selected   = $state<string | null>(null);

  let activeProfile = $derived(profileState.active);

  let filteredTasks = $derived.by(() => {
    if (!activeProfile) return allTasks;
    return allTasks.filter(t =>
      (t.workspace_profile ?? '').toLowerCase() === activeProfile!.name.toLowerCase()
    );
  });

  // Group by job_id into trees, most-recently-active first.
  let jobTrees = $derived.by((): JobTree[] => {
    const byJob = new Map<string, TaskNode[]>();
    for (const t of filteredTasks) {
      const jid = t.job_id || t.id;
      const bucket = byJob.get(jid) ?? [];
      bucket.push(t);
      byJob.set(jid, bucket);
    }
    const trees: JobTree[] = [];
    for (const [job_id, tasks] of byJob) {
      const root = tasks.find(t => t.id === job_id) ?? null;
      const latest_at = Math.max(...tasks.map(t => t.updated_at ?? t.created_at));
      trees.push({ job_id, root, tasks, latest_at });
    }
    trees.sort((a, b) => b.latest_at - a.latest_at);
    return trees;
  });

  let selectedTask = $derived(allTasks.find(t => t.id === selected) ?? null);

  async function load(silent = false) {
    const a = await getApi();
    if (!a) return;
    if (!silent) loading = true; else refreshing = true;
    try {
      const tasks = (await (a.ListHubTasks('', profileState.active?.name ?? '') as Promise<any>).catch(() => [])) ?? [];
      allTasks = tasks;
    } finally {
      loading    = false;
      refreshing = false;
    }
  }

  onMount(() => { void load(); });

  let _lastEv = $derived(brainboxEvents.last);
  $effect(() => {
    if (_lastEv) void load(true);
  });

  function toggleJob(jobID: string) {
    const next = new Set(expanded);
    if (next.has(jobID)) next.delete(jobID); else next.add(jobID);
    expanded = next;
  }

  function selectTask(id: string) {
    selected = selected === id ? null : id;
  }

  function children(tree: JobTree, parentID: string): TaskNode[] {
    return tree.tasks
      .filter(t => t.spawned_by === parentID)
      .sort((a, b) => a.created_at - b.created_at);
  }

  function statusClass(s: string): string {
    switch (s) {
      case 'running':   return 'running';
      case 'completed': return 'done';
      case 'failed':    return 'failed';
      case 'cancelled': return 'cancelled';
      default:          return 'pending';
    }
  }

  function statusGlyph(s: string): string {
    switch (s) {
      case 'running':   return '●';
      case 'completed': return '✓';
      case 'failed':    return '✗';
      case 'cancelled': return '○';
      default:          return '·';
    }
  }

  function elapsed(t: TaskNode): string {
    const end = (t.status === 'running') ? Date.now() : (t.updated_at ?? Date.now());
    const ms = end - t.created_at;
    if (ms < 1000) return '<1s';
    if (ms < 60_000) return `${Math.floor(ms / 1000)}s`;
    const m = Math.floor(ms / 60_000);
    const s = Math.floor((ms % 60_000) / 1000);
    return s > 0 ? `${m}m ${s}s` : `${m}m`;
  }

  function jobStatus(tree: JobTree): string {
    if (tree.tasks.some(t => t.status === 'failed')) return 'failed';
    if (tree.tasks.some(t => t.status === 'running')) return 'running';
    if (tree.tasks.every(t => t.status === 'completed')) return 'completed';
    if (tree.tasks.every(t => t.status === 'cancelled')) return 'cancelled';
    return 'pending';
  }
</script>

<div class="timeline">
  <div class="panel-header">
    <h2 class="panel-title">
      timeline
      {#if refreshing}<span class="blink">·</span>{/if}
    </h2>
    <span class="job-count">{jobTrees.length} job{jobTrees.length !== 1 ? 's' : ''}</span>
  </div>

  {#if loading}
    <div class="empty">loading task history…</div>
  {:else if jobTrees.length === 0}
    <div class="empty">no tasks yet — run a session with a task to see the lineage tree here</div>
  {:else}
    <div class="tree-list">
      {#each jobTrees as tree (tree.job_id)}
        {@const isOpen = expanded.has(tree.job_id)}
        {@const jStatus = jobStatus(tree)}
        {@const rootTask = tree.root}

        <!-- Job root row -->
        <div class="job-root">
          <button
            class="job-header"
            class:open={isOpen}
            onclick={() => toggleJob(tree.job_id)}
          >
            <span class="chevron">{isOpen ? '▾' : '▸'}</span>
            <span class="status-glyph st-{jStatus}">{statusGlyph(jStatus)}</span>
            <span class="job-name">
              {rootTask ? rootTask.description : tree.job_id.slice(0, 12)}
            </span>
            <span class="job-meta">
              {rootTask?.agent_name ?? ''}
              · {tree.tasks.length} task{tree.tasks.length !== 1 ? 's' : ''}
              {#if rootTask}· {elapsed(rootTask)}{/if}
            </span>
            <span class="job-id mono">{tree.job_id.slice(0, 8)}</span>
          </button>

          {#if isOpen}
            <div class="task-tree">
              {#each tree.tasks.sort((a, b) => a.created_at - b.created_at) as task (task.id)}
                {@const isChild = !!task.spawned_by}
                {@const isSelected = selected === task.id}
                <div class="task-row-wrap" class:child={isChild}>
                  <button
                    class="task-row"
                    class:selected={isSelected}
                    onclick={() => selectTask(task.id)}
                  >
                    {#if isChild}
                      <span class="tree-edge">└─</span>
                    {:else}
                      <span class="tree-root-marker"></span>
                    {/if}
                    <span class="status-glyph st-{statusClass(task.status)}">
                      {statusGlyph(task.status)}
                    </span>
                    <span class="task-name">{task.description}</span>
                    <span class="task-meta">
                      {task.agent_name} · {elapsed(task)}
                    </span>
                    {#if task.status === 'failed'}
                      <span class="err-badge">failed</span>
                    {/if}
                  </button>

                  {#if isSelected}
                    <div class="task-detail">
                      <div class="detail-row">
                        <span class="dl">id</span>
                        <span class="dv mono">{task.id}</span>
                      </div>
                      <div class="detail-row">
                        <span class="dl">session</span>
                        <span class="dv mono">{task.session_name || '—'}</span>
                      </div>
                      <div class="detail-row">
                        <span class="dl">status</span>
                        <span class="dv st-{statusClass(task.status)}">{task.status}</span>
                      </div>
                      <div class="detail-row">
                        <span class="dl">agent</span>
                        <span class="dv">{task.agent_name}</span>
                      </div>
                      {#if task.spawned_by}
                        <div class="detail-row">
                          <span class="dl">spawned by</span>
                          <span class="dv mono">{task.spawned_by.slice(0, 8)}</span>
                        </div>
                      {/if}
                      {#if task.child_task_ids?.length}
                        <div class="detail-row">
                          <span class="dl">children</span>
                          <span class="dv">{task.child_task_ids.length}</span>
                        </div>
                      {/if}
                      {#if task.error}
                        <div class="detail-row error-row">
                          <span class="dl">error</span>
                          <span class="dv error-text">{task.error}</span>
                        </div>
                      {/if}
                      <div class="detail-row">
                        <span class="dl">elapsed</span>
                        <span class="dv">{elapsed(task)}</span>
                      </div>
                    </div>
                  {/if}
                </div>
              {/each}
            </div>
          {/if}
        </div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .timeline {
    padding: var(--panel-padding);
    display: flex;
    flex-direction: column;
    gap: var(--spacing-xl);
    min-height: 100%;
  }

  .panel-header {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    padding-bottom: var(--spacing-md);
    border-bottom: 1px solid var(--color-border-primary);
  }

  .panel-title {
    font-size: 14px;
    font-weight: 600;
    color: var(--color-text-primary);
    margin: 0;
    display: flex;
    align-items: center;
    gap: var(--spacing-xs);
  }

  .blink {
    color: var(--color-accent);
    animation: blink 1s step-end infinite;
  }
  @keyframes blink { 50% { opacity: 0; } }

  .job-count {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--color-text-muted);
  }

  .empty {
    font-size: 13px;
    color: var(--color-text-tertiary);
    padding: var(--spacing-3xl) 0;
    line-height: 1.5;
  }

  /* --- Job trees --- */
  .tree-list {
    display: flex;
    flex-direction: column;
    gap: var(--spacing-sm);
  }

  .job-root {
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-md);
    overflow: hidden;
    background: var(--color-bg-secondary);
  }

  .job-header {
    width: 100%;
    display: grid;
    grid-template-columns: 18px 18px 1fr auto auto;
    align-items: center;
    gap: var(--spacing-sm);
    padding: var(--spacing-md) var(--spacing-lg);
    background: transparent;
    border: none;
    cursor: pointer;
    text-align: left;
    color: inherit;
    transition: background 100ms ease;
  }
  .job-header:hover { background: var(--color-surface-hover); }
  .job-header.open  { background: var(--color-surface-subtle); }

  .chevron {
    font-size: 10px;
    color: var(--color-text-muted);
  }

  .job-name {
    font-size: 13px;
    font-weight: 500;
    color: var(--color-text-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    min-width: 0;
  }

  .job-meta {
    font-size: 11px;
    color: var(--color-text-tertiary);
    white-space: nowrap;
    padding-right: var(--spacing-sm);
  }

  .job-id {
    font-size: 10px;
    color: var(--color-text-muted);
  }

  /* --- Task tree inside a job --- */
  .task-tree {
    border-top: 1px solid var(--color-border-primary);
    padding: var(--spacing-xs) 0;
  }

  .task-row-wrap { display: flex; flex-direction: column; }

  .task-row {
    width: 100%;
    display: grid;
    grid-template-columns: 28px 18px 1fr auto auto;
    align-items: center;
    gap: var(--spacing-sm);
    padding: var(--spacing-xs) var(--spacing-lg);
    background: transparent;
    border: none;
    cursor: pointer;
    text-align: left;
    color: inherit;
    transition: background 100ms ease;
  }
  .task-row:hover   { background: var(--color-surface-hover); }
  .task-row.selected { background: var(--color-surface-active); }

  .task-row-wrap.child .task-row { padding-left: calc(var(--spacing-lg) + 16px); }

  .tree-edge {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--color-text-muted);
  }

  .tree-root-marker { display: block; width: 18px; }

  .task-name {
    font-size: 12px;
    color: var(--color-text-secondary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    min-width: 0;
  }

  .task-meta {
    font-size: 11px;
    color: var(--color-text-muted);
    white-space: nowrap;
  }

  .err-badge {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-error);
    background: rgba(239, 68, 68, 0.08);
    border: 1px solid rgba(239, 68, 68, 0.2);
    border-radius: var(--radius-sm);
    padding: 1px 6px;
  }

  /* --- Task detail drawer --- */
  .task-detail {
    margin: 0 var(--spacing-lg) var(--spacing-sm) calc(var(--spacing-lg) + 46px);
    background: var(--color-bg-tertiary);
    border-radius: var(--radius-sm);
    padding: var(--spacing-md);
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .detail-row {
    display: grid;
    grid-template-columns: 90px 1fr;
    gap: var(--spacing-sm);
    font-size: 11px;
  }

  .dl {
    color: var(--color-text-muted);
    font-family: var(--font-mono);
  }

  .dv {
    color: var(--color-text-secondary);
    word-break: break-all;
  }

  .error-row .dv { color: var(--color-error); }

  /* --- Status glyphs --- */
  .status-glyph {
    font-size: 13px;
    font-family: var(--font-mono);
    text-align: center;
  }
  .st-running   { color: var(--color-info); }
  .st-done      { color: var(--color-success); }
  .st-failed    { color: var(--color-error); }
  .st-cancelled { color: var(--color-text-muted); }
  .st-pending   { color: var(--color-text-tertiary); }

  .mono { font-family: var(--font-mono); }
</style>

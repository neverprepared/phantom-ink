<script lang="ts">
  import { getApi } from '../utils/api';
  import { onMount } from 'svelte';
  import { brainboxEvents } from '../events.svelte';
  import { profileState, dashboardState, currentPanel } from '../stores.svelte';

  // ── Types ──────────────────────────────────────────────────────────────

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

  interface EntryAction {
    label: string;
    kind: 'open_url' | 'copy' | 'dispatch' | 'prompt';
    url?: string;
    value?: string;
    agent?: string;
    prompt?: string;
    template?: string;
  }

  interface CollectedEntry {
    job_id: string;
    entry_id: string;
    profile: string;
    kind: string;
    title: string;
    description: string;
    value: string;
    url: string;
    start_at?: number;
    end_at?: number;
    status: string;
    tags: string[];
    metadata: any;
    actions: EntryAction[];
    collected_at: number;
  }

  // Unified stream item
  interface StreamItem {
    id: string;
    source: 'task' | 'event';
    time: number;         // for sorting (start_at or created_at)
    title: string;
    subtitle: string;
    status: string;
    url?: string;
    actions: EntryAction[];
    raw: TaskNode | CollectedEntry;
  }

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
  }

  // ── State ──────────────────────────────────────────────────────────────

  let tab        = $state<'stream' | 'tasks' | 'jobs'>('stream');
  let allTasks   = $state<TaskNode[]>([]);
  let collectEntries = $state<CollectedEntry[]>([]);
  let collectJobs    = $state<CollectJob[]>([]);
  let loading    = $state(true);
  let refreshing = $state(false);
  let expanded   = $state<Set<string>>(new Set());
  let selected   = $state<string | null>(null);
  let tagFilter  = $state('');
  let dispatchMsg = $state('');

  // Jobs tab state
  let jobsLoading  = $state(false);
  let editingJobId = $state<string | null>(null);  // null = no edit, 'new' = create form
  let runningJobId = $state<string | null>(null);
  let editDraft    = $state({ name: '', command: '', interval_s: 300, enabled: true });
  let jobsProfile  = $state('');
  $effect(() => { jobsProfile = profile; });

  const profile = $derived(profileState.active?.name ?? '');

  // streamProfile: '' = all profiles, otherwise scoped to a specific one.
  // Initialised to the active profile; syncs when the active profile changes.
  let streamProfile = $state('');
  $effect(() => { streamProfile = profile; });

  // ── Stream view ────────────────────────────────────────────────────────

  let streamItems = $derived.by((): StreamItem[] => {
    const items: StreamItem[] = [];

    // Hub tasks — filter by streamProfile when set
    const myTasks = allTasks.filter(t =>
      !t.spawned_by &&
      (!streamProfile || (t.workspace_profile ?? '').toLowerCase() === streamProfile.toLowerCase())
    );
    for (const t of myTasks) {
      items.push({
        id: `task:${t.id}`,
        source: 'task',
        time: t.created_at,
        title: t.description || t.id.slice(0, 12),
        subtitle: `${t.agent_name} · ${elapsed(t)}`,
        status: taskStatus(t.status),
        actions: [],
        raw: t,
      });
    }

    // Collected events — filter by streamProfile and tag client-side
    const profileFiltered = streamProfile
      ? collectEntries.filter(e => (e.profile ?? '').toLowerCase() === streamProfile.toLowerCase())
      : collectEntries;
    const tagFiltered = tagFilter
      ? profileFiltered.filter(e => e.tags?.includes(tagFilter))
      : profileFiltered;
    for (const e of tagFiltered) {
      items.push({
        id: `entry:${e.job_id}:${e.entry_id}`,
        source: 'event',
        time: e.start_at ?? e.collected_at,
        title: e.title,
        subtitle: buildEntrySubtitle(e),
        status: entryStatus(e.status),
        url: e.url || undefined,
        actions: Array.isArray(e.actions) ? e.actions : [],
        raw: e,
      });
    }

    items.sort((a, b) => b.time - a.time);
    return items;
  });

  let nowIndex = $derived.by(() => {
    const now = Date.now();
    return streamItems.findIndex(i => i.time <= now);
  });

  // ── Task tree view ─────────────────────────────────────────────────────

  let filteredTasks = $derived.by(() => {
    if (!profile) return allTasks;
    return allTasks.filter(t =>
      (t.workspace_profile ?? '').toLowerCase() === profile.toLowerCase()
    );
  });

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

  // ── Data loading ───────────────────────────────────────────────────────

  async function load(silent = false) {
    const a = await getApi();
    if (!a) return;
    if (!silent) loading = true; else refreshing = true;
    try {
      // Load all profiles' data — stream filters client-side via streamProfile
      const [tasks, entries] = await Promise.all([
        (a.ListHubTasks('', '') as Promise<any>).catch(() => []),
        (a.ListCollectedEntries('', 'event', '') as Promise<any>).catch(() => []),
      ]);
      allTasks = tasks ?? [];
      collectEntries = entries ?? [];
    } finally {
      loading = false;
      refreshing = false;
    }
  }

  async function loadJobs() {
    const a = await getApi();
    if (!a) return;
    jobsLoading = true;
    try {
      collectJobs = ((await (a.ListCollectJobs as any)('')) ?? []) as CollectJob[];
    } finally {
      jobsLoading = false;
    }
  }

  async function saveJob() {
    const a = await getApi();
    if (!a) return;
    const isNew = editingJobId === 'new';
    const payload = {
      id: isNew ? '' : (editingJobId ?? ''),
      profile: isNew ? (jobsProfile || profile) : (collectJobs.find(j => j.id === editingJobId)?.profile ?? profile),
      name: editDraft.name.trim(),
      command: editDraft.command.trim(),
      interval_s: editDraft.interval_s,
      enabled: editDraft.enabled,
      default_actions: '[]',
      last_error: '',
      created_at: 0,
    };
    if (!payload.name || !payload.command) return;
    try {
      await (a.SaveCollectJob as any)(payload);
      editingJobId = null;
      await loadJobs();
    } catch (e: any) {
      dispatchMsg = `Error: ${e?.message ?? 'save failed'}`;
      setTimeout(() => { dispatchMsg = ''; }, 4000);
    }
  }

  async function deleteJob(id: string) {
    const a = await getApi();
    if (!a) return;
    try {
      await (a.DeleteCollectJob as any)(id);
      await loadJobs();
    } catch {}
  }

  async function runJobNow(id: string) {
    const a = await getApi();
    if (!a) return;
    runningJobId = id;
    try {
      await (a.RunCollectJobNow as any)(id);
      await loadJobs();
    } finally {
      runningJobId = null;
    }
  }

  async function toggleJobEnabled(job: CollectJob) {
    const a = await getApi();
    if (!a) return;
    try {
      await (a.SaveCollectJob as any)({ ...job, enabled: !job.enabled });
      await loadJobs();
    } catch {}
  }

  function startEdit(job: CollectJob) {
    editingJobId = job.id;
    editDraft = { name: job.name, command: job.command, interval_s: job.interval_s, enabled: job.enabled };
  }

  function startNew() {
    editingJobId = 'new';
    editDraft = { name: '', command: '', interval_s: 300, enabled: true };
  }

  function cancelEdit() { editingJobId = null; }

  let addedJobId = $state<string | null>(null);

  async function addJobToWidget(job: CollectJob) {
    const a = await getApi();
    if (!a) return;
    const id = `w-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    const widget = {
      id,
      kind: 'script-metric' as const,
      config: {
        label: job.name,
        command: job.command,
        interval: job.interval_s,
        jobId: job.id,
        valueType: 'number' as const,
      },
      x: 0, y: 0, w: 3, h: 2, minW: 2, minH: 2,
    };
    const updated = [...dashboardState.widgets, widget];
    dashboardState.updateWidgets(updated);
    try {
      await a.SaveDashboardLayout(
        profile,
        JSON.stringify({ version: 1, widgets: updated }),
      );
      addedJobId = job.id;
      setTimeout(() => { addedJobId = null; }, 2500);
    } catch {}
  }

  function fmtLastRun(job: CollectJob): string {
    if (!job.last_run_at) return 'never';
    const ms = job.last_run_at;
    const diff = Date.now() - ms;
    if (diff < 60_000) return 'just now';
    if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
    if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
    return new Date(ms).toLocaleDateString([], { month: 'short', day: 'numeric' });
  }

  onMount(() => {
    void load();
    void loadJobs();
    const handler = () => { void load(true); void loadJobs(); };
    window.runtime?.EventsOn('collect:update', handler);
    return () => window.runtime?.EventsOff('collect:update');
  });

  let _lastEv = $derived(brainboxEvents.last);
  $effect(() => { if (_lastEv) void load(true); });

  // ── Actions ────────────────────────────────────────────────────────────

  function openURL(url: string) {
    window.runtime?.BrowserOpenURL(url);
  }

  function copyValue(value: string) {
    navigator.clipboard.writeText(value).catch(() => {});
  }

  async function dispatchAction(action: EntryAction, item: StreamItem) {
    const a = await getApi();
    if (!a) return;
    const rendered = renderTemplate(action.prompt ?? '', item);
    try {
      await (a.EnqueueTask as any)({
        description: item.title,
        input: rendered,
        agent: action.agent ?? 'developer',
        workspace_profile: profile,
      });
      dispatchMsg = `Dispatched to ${action.agent ?? 'developer'}`;
      setTimeout(() => { dispatchMsg = ''; }, 3000);
    } catch (e: any) {
      dispatchMsg = `Error: ${e?.message ?? 'dispatch failed'}`;
      setTimeout(() => { dispatchMsg = ''; }, 4000);
    }
  }

  function promptAction(action: EntryAction, item: StreamItem) {
    // Pre-fill the system clipboard with the rendered template so the user
    // can paste into the dispatch form. A full dispatch-form integration
    // is tracked as a follow-up.
    const rendered = renderTemplate(action.template ?? '', item);
    navigator.clipboard.writeText(rendered).catch(() => {});
    dispatchMsg = 'Prompt copied to clipboard';
    setTimeout(() => { dispatchMsg = ''; }, 3000);
  }

  function handleAction(action: EntryAction, item: StreamItem) {
    switch (action.kind) {
      case 'open_url':  if (action.url)   openURL(action.url); break;
      case 'copy':      if (action.value) copyValue(action.value); break;
      case 'dispatch':  void dispatchAction(action, item); break;
      case 'prompt':    promptAction(action, item); break;
    }
  }

  function renderTemplate(tmpl: string, item: StreamItem): string {
    const e = item.source === 'event' ? item.raw as CollectedEntry : null;
    return tmpl
      .replace(/\{title\}/g, item.title)
      .replace(/\{url\}/g, item.url ?? '')
      .replace(/\{status\}/g, item.status)
      .replace(/\{metadata\.([^}]+)\}/g, (_, key) => {
        if (!e?.metadata) return '';
        return String(e.metadata[key] ?? '');
      });
  }

  // ── Helpers ────────────────────────────────────────────────────────────

  function taskStatus(s: string): string {
    switch (s) {
      case 'running':   return 'active';
      case 'completed': return 'done';
      case 'failed':    return 'failed';
      case 'cancelled': return 'cancelled';
      default:          return 'pending';
    }
  }

  function entryStatus(s: string): string {
    return s || 'active';
  }

  function statusGlyph(s: string): string {
    switch (s) {
      case 'active':   return '●';
      case 'done':     return '✓';
      case 'failed':   return '✗';
      case 'cancelled':return '○';
      case 'upcoming': return '◷';
      default:         return '·';
    }
  }

  function fmtTime(ms: number): string {
    return new Date(ms).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  function fmtDate(ms: number): string {
    const d = new Date(ms);
    const today = new Date();
    if (d.toDateString() === today.toDateString()) return 'today';
    const tom = new Date(today); tom.setDate(tom.getDate() + 1);
    if (d.toDateString() === tom.toDateString()) return 'tomorrow';
    return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
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

  function buildEntrySubtitle(e: CollectedEntry): string {
    const parts: string[] = [];
    if (e.tags?.length) parts.push(e.tags[0]);
    if (e.end_at && e.start_at) {
      const durMin = Math.round((e.end_at - e.start_at) / 60_000);
      if (durMin > 0) parts.push(`${durMin}m`);
    }
    if (e.value) parts.push(e.value);
    return parts.join(' · ');
  }

  function detectURLs(text: string): Array<{ type: 'text' | 'url'; content: string }> {
    const urlRe = /https?:\/\/[^\s)>\]"']+/g;
    const parts: Array<{ type: 'text' | 'url'; content: string }> = [];
    let last = 0, m: RegExpExecArray | null;
    while ((m = urlRe.exec(text)) !== null) {
      if (m.index > last) parts.push({ type: 'text', content: text.slice(last, m.index) });
      parts.push({ type: 'url', content: m[0] });
      last = m.index + m[0].length;
    }
    if (last < text.length) parts.push({ type: 'text', content: text.slice(last) });
    return parts;
  }

  // Tree helpers
  function toggleJob(jobID: string) {
    const next = new Set(expanded);
    if (next.has(jobID)) next.delete(jobID); else next.add(jobID);
    expanded = next;
  }
  function selectTask(id: string) { selected = selected === id ? null : id; }
  function treeStatusClass(s: string): string {
    switch (s) {
      case 'running': return 'active'; case 'completed': return 'done';
      case 'failed': return 'failed'; case 'cancelled': return 'cancelled';
      default: return 'pending';
    }
  }
  function jobStatus(tree: JobTree): string {
    if (tree.tasks.some(t => t.status === 'failed')) return 'failed';
    if (tree.tasks.some(t => t.status === 'running')) return 'active';
    if (tree.tasks.every(t => t.status === 'completed')) return 'done';
    if (tree.tasks.every(t => t.status === 'cancelled')) return 'cancelled';
    return 'pending';
  }

  // All tags for filter bar
  let allTags = $derived.by(() => {
    const set = new Set<string>();
    for (const e of collectEntries) e.tags?.forEach(t => set.add(t));
    return [...set].sort();
  });

  // All profiles present in stream data
  let streamProfiles = $derived.by(() => {
    const set = new Set<string>();
    for (const t of allTasks) if (t.workspace_profile) set.add(t.workspace_profile);
    for (const e of collectEntries) if (e.profile) set.add(e.profile);
    return [...set].sort();
  });

  // Jobs tab: profiles + filtered view
  let jobProfiles = $derived.by(() => {
    const set = new Set<string>();
    for (const j of collectJobs) if (j.profile) set.add(j.profile);
    return [...set].sort();
  });

  let visibleJobs = $derived(
    jobsProfile ? collectJobs.filter(j => j.profile === jobsProfile) : collectJobs
  );
</script>

<div class="timeline">
  <div class="panel-header">
    <h2 class="panel-title">
      timeline
      {#if refreshing}<span class="blink">·</span>{/if}
    </h2>
    <div class="header-right">
      {#if dispatchMsg}
        <span class="dispatch-msg">{dispatchMsg}</span>
      {/if}
      <div class="tabs">
        <button class="tab" class:active={tab === 'stream'} onclick={() => tab = 'stream'}>Stream</button>
        <button class="tab" class:active={tab === 'tasks'}  onclick={() => tab = 'tasks'}>Tasks</button>
        <button class="tab" class:active={tab === 'jobs'}   onclick={() => { tab = 'jobs'; void loadJobs(); }}>Jobs</button>
      </div>
    </div>
  </div>

  {#if loading}
    <div class="empty">loading…</div>

  {:else if tab === 'stream'}
    <!-- Profile filter -->
    {#if streamProfiles.length > 1}
      <div class="filter-row">
        <span class="filter-label">profile</span>
        <div class="tag-bar inline">
          <button class="tag" class:active={streamProfile === ''} onclick={() => streamProfile = ''}>all</button>
          {#each streamProfiles as p (p)}
            <button class="tag" class:active={streamProfile === p} onclick={() => streamProfile = streamProfile === p ? '' : p}>{p}</button>
          {/each}
        </div>
      </div>
    {/if}

    <!-- Tag filter -->
    {#if allTags.length > 0}
      <div class="filter-row">
        <span class="filter-label">tag</span>
        <div class="tag-bar inline">
          <button class="tag" class:active={tagFilter === ''} onclick={() => tagFilter = ''}>all</button>
          {#each allTags as t (t)}
            <button class="tag" class:active={tagFilter === t} onclick={() => tagFilter = tagFilter === t ? '' : t}>{t}</button>
          {/each}
        </div>
      </div>
    {/if}

    {#if streamItems.length === 0}
      <div class="empty">no events yet</div>
    {:else}
      <div class="stream">
        {#each streamItems as item, i (item.id)}
          <!-- "now" divider between upcoming and past -->
          {#if i === nowIndex}
            <div class="now-divider">
              <span class="now-label">now</span>
            </div>
          {/if}

          <div class="stream-item src-{item.source} st-{item.status}">
            <div class="item-time">{fmtTime(item.time)}<br/><span class="item-date">{fmtDate(item.time)}</span></div>
            <span class="item-glyph st-{item.status}">{statusGlyph(item.status)}</span>
            <div class="item-body">
              <div class="item-title-row">
                {#if item.url}
                  <button class="item-title link" onclick={() => openURL(item.url!)}>{item.title}</button>
                {:else}
                  <span class="item-title">{item.title}</span>
                {/if}
              </div>
              {#if item.subtitle}
                <span class="item-sub">{item.subtitle}</span>
              {/if}
              <!-- Description with auto-linked URLs -->
              {#if item.source === 'event'}
                {@const e = item.raw as CollectedEntry}
                {#if e.description}
                  <span class="item-desc">
                    {#each detectURLs(e.description) as part}
                      {#if part.type === 'url'}
                        <button class="inline-link" onclick={() => openURL(part.content)}>{part.content}</button>
                      {:else}
                        {part.content}
                      {/if}
                    {/each}
                  </span>
                {/if}
                {#if e.tags?.length}
                  <div class="item-tags">
                    {#each e.tags as tag (tag)}
                      <span class="item-tag">{tag}</span>
                    {/each}
                  </div>
                {/if}
              {/if}
              <!-- Actions -->
              {#if item.actions.length > 0}
                <div class="item-actions">
                  {#each item.actions as action (action.label)}
                    <button class="action-btn" onclick={() => handleAction(action, item)}>
                      {action.label}
                    </button>
                  {/each}
                </div>
              {/if}
            </div>
          </div>
        {/each}
      </div>
    {/if}

  {:else}
    <!-- Tasks tree view (original) -->
    {#if jobTrees.length === 0}
      <div class="empty">no tasks yet</div>
    {:else}
      <div class="tree-list">
        {#each jobTrees as tree (tree.job_id)}
          {@const isOpen = expanded.has(tree.job_id)}
          {@const jStatus = jobStatus(tree)}
          {@const rootTask = tree.root}
          <div class="job-root">
            <button class="job-header" class:open={isOpen} onclick={() => toggleJob(tree.job_id)}>
              <span class="chevron">{isOpen ? '▾' : '▸'}</span>
              <span class="item-glyph st-{jStatus}">{statusGlyph(jStatus)}</span>
              <span class="job-name">{rootTask ? rootTask.description : tree.job_id.slice(0, 12)}</span>
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
                  {@const isSel = selected === task.id}
                  <div class="task-row-wrap" class:child={isChild}>
                    <button class="task-row" class:selected={isSel} onclick={() => selectTask(task.id)}>
                      {#if isChild}<span class="tree-edge">└─</span>{:else}<span class="tree-root-marker"></span>{/if}
                      <span class="item-glyph st-{treeStatusClass(task.status)}">{statusGlyph(treeStatusClass(task.status))}</span>
                      <span class="task-name">{task.description}</span>
                      <span class="task-meta">{task.agent_name} · {elapsed(task)}</span>
                      {#if task.status === 'failed'}<span class="err-badge">failed</span>{/if}
                    </button>
                    {#if isSel}
                      <div class="task-detail">
                        <div class="detail-row"><span class="dl">id</span><span class="dv mono">{task.id}</span></div>
                        <div class="detail-row"><span class="dl">session</span><span class="dv mono">{task.session_name || '—'}</span></div>
                        <div class="detail-row"><span class="dl">status</span><span class="dv st-{treeStatusClass(task.status)}">{task.status}</span></div>
                        <div class="detail-row"><span class="dl">agent</span><span class="dv">{task.agent_name}</span></div>
                        {#if task.spawned_by}<div class="detail-row"><span class="dl">spawned by</span><span class="dv mono">{task.spawned_by.slice(0, 8)}</span></div>{/if}
                        {#if task.child_task_ids?.length}<div class="detail-row"><span class="dl">children</span><span class="dv">{task.child_task_ids.length}</span></div>{/if}
                        {#if task.error}<div class="detail-row error-row"><span class="dl">error</span><span class="dv error-text">{task.error}</span></div>{/if}
                        <div class="detail-row"><span class="dl">elapsed</span><span class="dv">{elapsed(task)}</span></div>
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

  {:else if tab === 'jobs'}
    <!-- Collect jobs management -->
    <!-- Profile filter -->
    {#if jobProfiles.length > 1}
      <div class="filter-row">
        <span class="filter-label">profile</span>
        <div class="tag-bar inline">
          <button class="tag" class:active={jobsProfile === ''} onclick={() => jobsProfile = ''}>all</button>
          {#each jobProfiles as p (p)}
            <button class="tag" class:active={jobsProfile === p} onclick={() => jobsProfile = jobsProfile === p ? '' : p}>{p}</button>
          {/each}
        </div>
      </div>
    {/if}

    <div class="jobs-header">
      <span class="jobs-count">{visibleJobs.length} job{visibleJobs.length !== 1 ? 's' : ''}</span>
      {#if editingJobId !== 'new'}
        <button class="jobs-add-btn" onclick={startNew}>+ new job</button>
      {/if}
    </div>

    {#if editingJobId === 'new'}
      <div class="job-form">
        <div class="form-title">new collect job</div>
        <label class="form-row">
          <span class="form-label">name</span>
          <input class="form-input" bind:value={editDraft.name} placeholder="my calendar" />
        </label>
        <label class="form-row">
          <span class="form-label">command</span>
          <textarea class="form-textarea" bind:value={editDraft.command} placeholder="script that outputs JSON array" rows="4"></textarea>
        </label>
        <label class="form-row">
          <span class="form-label">interval</span>
          <div class="interval-row">
            <input class="form-input short" type="number" min="30" bind:value={editDraft.interval_s} />
            <span class="form-unit">seconds</span>
          </div>
        </label>
        <div class="form-actions">
          <button class="form-btn primary" onclick={saveJob} disabled={!editDraft.name || !editDraft.command}>save</button>
          <button class="form-btn" onclick={cancelEdit}>cancel</button>
        </div>
      </div>
    {/if}

    {#if jobsLoading && collectJobs.length === 0}
      <div class="empty">loading…</div>
    {:else if collectJobs.length === 0 && editingJobId !== 'new'}
      <div class="empty">no collect jobs yet — create one to schedule data collection</div>
    {:else if visibleJobs.length === 0 && editingJobId !== 'new'}
      <div class="empty">no jobs for this profile</div>
    {:else}
      <div class="job-list">
        {#each visibleJobs as job (job.id)}
          <div class="job-card" class:editing={editingJobId === job.id}>
            {#if editingJobId === job.id}
              <div class="job-form inline">
                <label class="form-row">
                  <span class="form-label">name</span>
                  <input class="form-input" bind:value={editDraft.name} />
                </label>
                <label class="form-row">
                  <span class="form-label">command</span>
                  <textarea class="form-textarea" bind:value={editDraft.command} rows="4"></textarea>
                </label>
                <label class="form-row">
                  <span class="form-label">interval</span>
                  <div class="interval-row">
                    <input class="form-input short" type="number" min="30" bind:value={editDraft.interval_s} />
                    <span class="form-unit">seconds</span>
                  </div>
                </label>
                <div class="form-actions">
                  <button class="form-btn primary" onclick={saveJob} disabled={!editDraft.name || !editDraft.command}>save</button>
                  <button class="form-btn" onclick={cancelEdit}>cancel</button>
                </div>
              </div>
            {:else}
              <div class="job-row">
                <button
                  class="job-toggle"
                  class:on={job.enabled}
                  onclick={() => toggleJobEnabled(job)}
                  title={job.enabled ? 'disable' : 'enable'}
                >
                  {job.enabled ? '●' : '○'}
                </button>
                <div class="job-info" role="button" tabindex="0"
                  onclick={() => startEdit(job)}
                  onkeydown={(e) => e.key === 'Enter' && startEdit(job)}>
                  <span class="job-name">{job.name}</span>
                  <span class="job-cmd">{job.command.split('\n')[0].slice(0, 60)}{job.command.length > 60 ? '…' : ''}</span>
                  <div class="job-meta-row">
                    <span class="job-profile">{job.profile}</span>
                    <span class="job-meta">every {job.interval_s >= 3600 ? `${job.interval_s / 3600}h` : job.interval_s >= 60 ? `${Math.floor(job.interval_s / 60)}m` : `${job.interval_s}s`}</span>
                    <span class="job-meta">last run: {fmtLastRun(job)}</span>
                    {#if job.last_error}
                      <span class="job-err" title={job.last_error}>✗ error</span>
                    {/if}
                  </div>
                </div>
                <div class="job-btns">
                  <button
                    class="job-btn"
                    onclick={() => addJobToWidget(job)}
                    title="add to dashboard"
                    class:added={addedJobId === job.id}
                  >
                    {addedJobId === job.id ? '✓' : '+'}
                  </button>
                  <button
                    class="job-btn"
                    onclick={() => runJobNow(job.id)}
                    disabled={runningJobId === job.id}
                    title="run now"
                  >
                    {runningJobId === job.id ? '…' : '▶'}
                  </button>
                  <button class="job-btn danger" onclick={() => deleteJob(job.id)} title="delete">✕</button>
                </div>
              </div>
            {/if}
          </div>
        {/each}
      </div>
    {/if}
  {/if}
</div>

<style>
  .timeline {
    padding: var(--panel-padding);
    display: flex;
    flex-direction: column;
    gap: var(--spacing-md);
    min-height: 100%;
  }

  .panel-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding-bottom: var(--spacing-md);
    border-bottom: 1px solid var(--color-border-primary);
    flex-shrink: 0;
  }
  .panel-title {
    font-size: 14px; font-weight: 600;
    color: var(--color-text-primary); margin: 0;
    display: flex; align-items: center; gap: var(--spacing-xs);
  }
  .blink { color: var(--color-accent); animation: blink 1s step-end infinite; }
  @keyframes blink { 50% { opacity: 0; } }

  .header-right { display: flex; align-items: center; gap: var(--spacing-md); }

  .dispatch-msg {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--color-success);
  }

  .tabs { display: flex; gap: 2px; }
  .tab {
    padding: 4px 10px; font-size: 11px; font-weight: 500;
    background: none; border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm); cursor: pointer;
    color: var(--color-text-tertiary); font-family: inherit;
  }
  .tab:hover { color: var(--color-text-secondary); }
  .tab.active { color: var(--color-accent); border-color: var(--color-accent); background: rgba(234,179,8,0.06); }

  .empty {
    font-size: 13px; color: var(--color-text-tertiary);
    padding: var(--spacing-3xl) 0; line-height: 1.5;
  }

  /* ── Filter bars ── */
  .filter-row {
    display: flex; align-items: center; gap: var(--spacing-sm);
    padding-bottom: var(--spacing-xs);
  }
  .filter-label {
    font-family: var(--font-mono); font-size: 10px;
    color: var(--color-text-muted); width: 38px; flex-shrink: 0;
  }
  .tag-bar {
    display: flex; flex-wrap: wrap; gap: 6px;
    padding-bottom: var(--spacing-sm);
  }
  .tag-bar.inline { padding-bottom: 0; }
  .tag {
    font-family: var(--font-mono); font-size: 10px;
    padding: 2px 8px; border-radius: 999px;
    border: 1px solid var(--color-border-primary);
    background: none; cursor: pointer;
    color: var(--color-text-muted); font-family: inherit;
  }
  .tag:hover { border-color: var(--color-border-secondary); color: var(--color-text-secondary); }
  .tag.active { border-color: var(--color-accent); color: var(--color-accent); background: rgba(234,179,8,0.06); }

  /* ── Stream ── */
  .stream { display: flex; flex-direction: column; gap: 2px; }

  .now-divider {
    display: flex; align-items: center; gap: var(--spacing-sm);
    padding: var(--spacing-xs) 0; margin: var(--spacing-xs) 0;
  }
  .now-divider::before, .now-divider::after {
    content: ''; flex: 1; height: 1px;
    background: var(--color-accent); opacity: 0.4;
  }
  .now-label {
    font-family: var(--font-mono); font-size: 10px;
    color: var(--color-accent); letter-spacing: 0.1em;
  }

  .stream-item {
    display: grid;
    grid-template-columns: 52px 18px 1fr;
    gap: var(--spacing-sm);
    padding: var(--spacing-sm) var(--spacing-md);
    border-radius: var(--radius-sm);
    transition: background 100ms;
  }
  .stream-item:hover { background: var(--color-surface-hover); }
  .stream-item.st-upcoming { opacity: 0.85; }
  .stream-item.src-event { border-left: 2px solid var(--color-info); padding-left: calc(var(--spacing-md) - 2px); }
  .stream-item.src-task  { border-left: 2px solid var(--color-border-primary); padding-left: calc(var(--spacing-md) - 2px); }

  .item-time {
    font-family: var(--font-mono); font-size: 11px;
    color: var(--color-text-muted); text-align: right;
    line-height: 1.3; flex-shrink: 0;
    padding-top: 1px;
  }
  .item-date { font-size: 9px; opacity: 0.7; }

  .item-glyph { font-size: 13px; font-family: var(--font-mono); text-align: center; padding-top: 1px; }

  .item-body { display: flex; flex-direction: column; gap: 3px; min-width: 0; }

  .item-title-row { display: flex; align-items: baseline; gap: var(--spacing-xs); }

  .item-title {
    font-size: 13px; font-weight: 500;
    color: var(--color-text-primary);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    background: none; border: none; padding: 0; text-align: left;
  }
  .item-title.link {
    cursor: pointer; color: var(--color-text-primary);
    text-decoration: none;
  }
  .item-title.link:hover { color: var(--color-accent); text-decoration: underline; }

  .item-sub {
    font-family: var(--font-mono); font-size: 10px;
    color: var(--color-text-muted);
  }

  .item-desc {
    font-size: 11px; color: var(--color-text-tertiary);
    line-height: 1.4; word-break: break-word;
  }

  .inline-link {
    background: none; border: none; padding: 0;
    color: var(--color-info); font-size: inherit;
    cursor: pointer; text-decoration: underline;
    font-family: var(--font-mono); font-size: 10px;
  }
  .inline-link:hover { opacity: 0.8; }

  .item-tags { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 2px; }
  .item-tag {
    font-family: var(--font-mono); font-size: 9px;
    color: var(--color-text-muted);
    background: var(--color-surface-subtle);
    border: 1px solid var(--color-border-primary);
    border-radius: 999px; padding: 1px 6px;
  }

  .item-actions { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 4px; }
  .action-btn {
    font-family: var(--font-mono); font-size: 10px;
    padding: 2px 8px; border-radius: var(--radius-sm);
    border: 1px solid var(--color-border-primary);
    background: var(--color-bg-secondary); cursor: pointer;
    color: var(--color-text-secondary); font-family: inherit;
    transition: all 100ms;
  }
  .action-btn:hover { border-color: var(--color-accent); color: var(--color-accent); background: rgba(234,179,8,0.06); }

  /* ── Status glyphs ── */
  .st-active    { color: var(--color-info); }
  .st-done      { color: var(--color-success); }
  .st-failed    { color: var(--color-error); }
  .st-cancelled { color: var(--color-text-muted); }
  .st-upcoming  { color: var(--color-text-tertiary); }
  .st-pending   { color: var(--color-text-tertiary); }

  /* ── Task tree (Tasks tab) ── */
  .tree-list { display: flex; flex-direction: column; gap: var(--spacing-sm); }
  .job-root { border: 1px solid var(--color-border-primary); border-radius: var(--radius-md); overflow: hidden; background: var(--color-bg-secondary); }
  .job-header {
    width: 100%; display: grid; grid-template-columns: 18px 18px 1fr auto auto;
    align-items: center; gap: var(--spacing-sm);
    padding: var(--spacing-md) var(--spacing-lg);
    background: transparent; border: none; cursor: pointer;
    text-align: left; color: inherit; transition: background 100ms ease;
  }
  .job-header:hover { background: var(--color-surface-hover); }
  .job-header.open  { background: var(--color-surface-subtle); }
  .chevron { font-size: 10px; color: var(--color-text-muted); }
  .job-name { font-size: 13px; font-weight: 500; color: var(--color-text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
  .job-meta { font-size: 11px; color: var(--color-text-tertiary); white-space: nowrap; padding-right: var(--spacing-sm); }
  .job-id { font-size: 10px; color: var(--color-text-muted); }
  .task-tree { border-top: 1px solid var(--color-border-primary); padding: var(--spacing-xs) 0; }
  .task-row-wrap { display: flex; flex-direction: column; }
  .task-row {
    width: 100%; display: grid; grid-template-columns: 28px 18px 1fr auto auto;
    align-items: center; gap: var(--spacing-sm);
    padding: var(--spacing-xs) var(--spacing-lg);
    background: transparent; border: none; cursor: pointer;
    text-align: left; color: inherit; transition: background 100ms ease;
  }
  .task-row:hover   { background: var(--color-surface-hover); }
  .task-row.selected { background: var(--color-surface-active); }
  .task-row-wrap.child .task-row { padding-left: calc(var(--spacing-lg) + 16px); }
  .tree-edge { font-family: var(--font-mono); font-size: 11px; color: var(--color-text-muted); }
  .tree-root-marker { display: block; width: 18px; }
  .task-name { font-size: 12px; color: var(--color-text-secondary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
  .task-meta { font-size: 11px; color: var(--color-text-muted); white-space: nowrap; }
  .err-badge { font-family: var(--font-mono); font-size: 10px; color: var(--color-error); background: rgba(239,68,68,0.08); border: 1px solid rgba(239,68,68,0.2); border-radius: var(--radius-sm); padding: 1px 6px; }
  .task-detail { margin: 0 var(--spacing-lg) var(--spacing-sm) calc(var(--spacing-lg) + 46px); background: var(--color-bg-tertiary); border-radius: var(--radius-sm); padding: var(--spacing-md); display: flex; flex-direction: column; gap: 4px; }
  .detail-row { display: grid; grid-template-columns: 90px 1fr; gap: var(--spacing-sm); font-size: 11px; }
  .dl { color: var(--color-text-muted); font-family: var(--font-mono); }
  .dv { color: var(--color-text-secondary); word-break: break-all; }
  .error-row .dv { color: var(--color-error); }

  .mono { font-family: var(--font-mono); }

  /* ── Jobs tab ── */
  .jobs-header {
    display: flex; align-items: center; justify-content: space-between;
    padding-bottom: var(--spacing-sm);
  }
  .jobs-count { font-family: var(--font-mono); font-size: 11px; color: var(--color-text-muted); }
  .jobs-add-btn {
    font-family: var(--font-mono); font-size: 11px;
    padding: 3px 10px; border-radius: var(--radius-sm);
    border: 1px solid var(--color-accent); background: rgba(234,179,8,0.06);
    color: var(--color-accent); cursor: pointer;
  }
  .jobs-add-btn:hover { background: rgba(234,179,8,0.12); }

  .job-list { display: flex; flex-direction: column; gap: var(--spacing-sm); }

  .job-card {
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-md);
    background: var(--color-bg-secondary);
    overflow: hidden;
    transition: border-color 100ms;
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
    color: var(--color-text-muted); padding: 0;
    line-height: 1; transition: color 100ms;
  }
  .job-toggle.on { color: var(--color-success); }
  .job-toggle:hover { opacity: 0.7; }

  .job-info {
    display: flex; flex-direction: column; gap: 3px;
    min-width: 0; cursor: pointer;
  }
  .job-name { font-size: 13px; font-weight: 500; color: var(--color-text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .job-cmd { font-family: var(--font-mono); font-size: 10px; color: var(--color-text-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .job-meta-row { display: flex; gap: var(--spacing-md); align-items: center; }
  .job-meta { font-family: var(--font-mono); font-size: 10px; color: var(--color-text-tertiary); }
  .job-profile { font-family: var(--font-mono); font-size: 10px; color: var(--color-accent); background: rgba(234,179,8,0.08); border: 1px solid rgba(234,179,8,0.2); border-radius: 999px; padding: 1px 6px; }
  .job-err { font-family: var(--font-mono); font-size: 10px; color: var(--color-error); }

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

  /* Job form */
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
    font-size: 12px; color: var(--color-text-primary); font-family: inherit;
    width: 100%;
  }
  .form-input:focus { outline: none; border-color: var(--color-accent); }
  .form-input.short { width: 80px; }

  .form-textarea {
    background: var(--color-bg-primary); border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm); padding: 6px 8px;
    font-size: 11px; font-family: var(--font-mono);
    color: var(--color-text-primary); width: 100%; resize: vertical;
  }
  .form-textarea:focus { outline: none; border-color: var(--color-accent); }

  .interval-row { display: flex; align-items: center; gap: var(--spacing-sm); }
  .form-unit { font-family: var(--font-mono); font-size: 11px; color: var(--color-text-muted); }

  .form-actions { display: flex; gap: var(--spacing-sm); }
  .form-btn {
    font-family: var(--font-mono); font-size: 11px;
    padding: 4px 12px; border-radius: var(--radius-sm);
    border: 1px solid var(--color-border-primary);
    background: none; cursor: pointer; color: var(--color-text-secondary);
    transition: all 100ms;
  }
  .form-btn:hover:not(:disabled) { border-color: var(--color-border-secondary); color: var(--color-text-primary); }
  .form-btn:disabled { opacity: 0.4; cursor: not-allowed; }
  .form-btn.primary {
    background: var(--color-accent); border-color: var(--color-accent); color: #000; font-weight: 600;
  }
  .form-btn.primary:hover:not(:disabled) { opacity: 0.85; }
</style>

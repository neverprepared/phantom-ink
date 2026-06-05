<script lang="ts">
  import { getApi } from '../utils/api';
  import { onMount } from 'svelte';
  import { brainboxEvents } from '../events.svelte';
  import { profileState } from '../stores.svelte';
  import Spinner from '../components/Spinner.svelte';
  import EmptyState from '../components/EmptyState.svelte';

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

  interface EntryAction {
    label: string;
    kind: 'open_url' | 'copy' | 'dispatch' | 'prompt';
    url?: string;
    value?: string;
    agent?: string;
    prompt?: string;
    template?: string;
  }

  interface AutomationRule {
    id: string;
    name: string;
    trigger_type: string;
    action_type: string;
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

  // ── State ──────────────────────────────────────────────────────────────

  let allTasks   = $state<TaskNode[]>([]);
  let collectEntries = $state<CollectedEntry[]>([]);
  let loading    = $state(true);
  let refreshing = $state(false);
  let expanded   = $state<Set<string>>(new Set());
  let tagFilter  = $state('');
  let dispatchMsg = $state('');
  let menuItem    = $state<StreamItem | null>(null);
  let menuRules   = $state<AutomationRule[]>([]);
  let menuLoading = $state(false);

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

  // Spawned children grouped by root task id — for inline subtask expansion.
  let jobChildren = $derived.by(() => {
    const map = new Map<string, TaskNode[]>();
    for (const t of allTasks) {
      if (t.spawned_by) {
        const key = t.job_id ?? t.spawned_by;
        const arr = map.get(key) ?? [];
        arr.push(t);
        map.set(key, arr);
      }
    }
    return map;
  });

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

  onMount(() => {
    void load();
    const handler = () => void load(true);
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

  async function openActionMenu(item: StreamItem) {
    if (item.source !== 'event') return;
    menuItem = item;
    menuRules = [];
    menuLoading = true;
    const e = item.raw as CollectedEntry;
    try {
      const a = await getApi();
      if (a) {
        const rules = await (a.GetMatchingRules as any)(e.job_id, e.entry_id).catch(() => []);
        menuRules = (rules ?? []) as AutomationRule[];
      }
    } finally {
      menuLoading = false;
    }
  }

  function closeMenu() { menuItem = null; menuRules = []; }

  async function triggerRule(ruleID: string) {
    if (!menuItem || menuItem.source !== 'event') return;
    const a = await getApi();
    if (!a) return;
    const e = menuItem.raw as CollectedEntry;
    try {
      await (a.TriggerRule as any)(ruleID, e.job_id, e.entry_id);
      dispatchMsg = 'Rule triggered';
      setTimeout(() => { dispatchMsg = ''; }, 3000);
    } catch (err: any) {
      dispatchMsg = `Error: ${err?.message ?? 'trigger failed'}`;
      setTimeout(() => { dispatchMsg = ''; }, 4000);
    }
    closeMenu();
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

  function toggleExpand(jobId: string) {
    const next = new Set(expanded);
    if (next.has(jobId)) next.delete(jobId); else next.add(jobId);
    expanded = next;
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

</script>

<div class="stream-panel">
  <div class="panel-header" style="margin-bottom:var(--spacing-sm);">
    <h1 class="page-title">stream</h1>
    <div style="display:flex;align-items:center;gap:var(--spacing-md);">
      {#if loading || refreshing}<Spinner />{/if}
      {#if dispatchMsg}
        <span class="dispatch-msg">{dispatchMsg}</span>
      {/if}
    </div>
  </div>

  {#if loading}
    <div class="empty">loading…</div>

  {:else}
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
      <EmptyState title="No events yet" />
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
                {#if item.source === 'task'}
                  {@const rootTask = item.raw as TaskNode}
                  {@const children = jobChildren.get(rootTask.id) ?? []}
                  {#if children.length > 0}
                    <button class="expand-btn" onclick={() => toggleExpand(rootTask.id)}
                      title="{expanded.has(rootTask.id) ? 'collapse' : 'expand'} subtasks">
                      {expanded.has(rootTask.id) ? '▼' : '▶'}
                    </button>
                  {/if}
                {/if}
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
              <!-- Actions: open_url and copy inline; dispatch via automation menu -->
              {#if item.actions.filter(a => a.kind === 'open_url' || a.kind === 'copy').length > 0 || item.source === 'event'}
                {@const inlineActions = item.actions.filter(a => a.kind === 'open_url' || a.kind === 'copy')}
                <div class="item-actions">
                  {#each inlineActions as action (action.label)}
                    <button class="action-btn" onclick={() => handleAction(action, item)}>
                      {action.label}
                    </button>
                  {/each}
                  {#if item.source === 'event'}
                    <div class="action-menu-wrap">
                      <button class="action-btn menu-btn"
                        class:active={menuItem?.id === item.id}
                        onclick={() => menuItem?.id === item.id ? closeMenu() : openActionMenu(item)}
                        title="automations">⋯</button>
                      {#if menuItem?.id === item.id}
                        <div class="action-menu">
                          {#if menuLoading}
                            <span class="menu-empty">loading…</span>
                          {:else if menuRules.length === 0}
                            <span class="menu-empty">no matching rules</span>
                          {:else}
                            {#each menuRules as rule (rule.id)}
                              <button class="menu-rule" onclick={() => triggerRule(rule.id)}>
                                ▶ {rule.name}
                              </button>
                            {/each}
                          {/if}
                        </div>
                      {/if}
                    </div>
                  {/if}
                </div>
              {/if}
              <!-- Inline subtask expansion for task items -->
              {#if item.source === 'task'}
                {@const rootTask = item.raw as TaskNode}
                {@const children = jobChildren.get(rootTask.id) ?? []}
                {#if expanded.has(rootTask.id) && children.length > 0}
                  <div class="subtask-list">
                    {#each children.sort((a, b) => a.created_at - b.created_at) as child (child.id)}
                      {@const cs = taskStatus(child.status)}
                      <div class="subtask-row st-{cs}">
                        <span class="subtask-edge">└─</span>
                        <span class="item-glyph st-{cs}">{statusGlyph(cs)}</span>
                        <span class="subtask-name">{child.description || child.id.slice(0, 12)}</span>
                        <span class="subtask-meta">{child.agent_name} · {elapsed(child)}</span>
                        {#if child.status === 'failed' && child.error}
                          <span class="subtask-error" title={child.error}>!</span>
                        {/if}
                      </div>
                    {/each}
                  </div>
                {/if}
              {/if}
            </div>
          </div>
        {/each}
      </div>
    {/if}

  {/if}
</div>

<style>
  .stream-panel {
    padding: var(--panel-padding);
    display: flex;
    flex-direction: column;
    gap: var(--spacing-md);
    min-height: 100%;
  }

  .dispatch-msg {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--color-success);
  }

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
  .action-btn.active { border-color: var(--color-accent); color: var(--color-accent); }

  .action-menu-wrap { position: relative; }
  .action-menu {
    position: absolute; top: calc(100% + 4px); left: 0; z-index: 100;
    background: var(--color-bg-primary);
    border: 1px solid var(--color-border-secondary);
    border-radius: var(--radius-md);
    padding: 4px; min-width: 160px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.2);
    display: flex; flex-direction: column; gap: 2px;
  }
  .menu-rule {
    font-family: var(--font-mono); font-size: 11px;
    padding: 4px 8px; border-radius: var(--radius-sm);
    border: none; background: none; cursor: pointer;
    color: var(--color-text-secondary); text-align: left;
    transition: all 80ms;
  }
  .menu-rule:hover { background: rgba(234,179,8,0.08); color: var(--color-accent); }
  .menu-empty { font-family: var(--font-mono); font-size: 10px; color: var(--color-text-tertiary); padding: 4px 8px; }

  /* ── Status glyphs ── */
  .st-active    { color: var(--color-info); }
  .st-done      { color: var(--color-success); }
  .st-failed    { color: var(--color-error); }
  .st-cancelled { color: var(--color-text-muted); }
  .st-upcoming  { color: var(--color-text-tertiary); }
  .st-pending   { color: var(--color-text-tertiary); }

  /* ── Subtask expansion ── */
  .expand-btn {
    background: none; border: none; padding: 0 4px 0 0;
    font-size: 9px; color: var(--color-text-muted);
    cursor: pointer; line-height: 1; flex-shrink: 0;
    font-family: var(--font-mono);
  }
  .expand-btn:hover { color: var(--color-text-secondary); }

  .subtask-list { display: flex; flex-direction: column; gap: 2px; margin-top: 4px; }
  .subtask-row {
    display: flex; align-items: center; gap: 6px;
    padding: 1px 0;
  }
  .subtask-edge { font-family: var(--font-mono); font-size: 10px; color: var(--color-text-muted); flex-shrink: 0; }
  .subtask-name { font-size: 11px; color: var(--color-text-secondary); min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
  .subtask-meta { font-family: var(--font-mono); font-size: 10px; color: var(--color-text-muted); white-space: nowrap; flex-shrink: 0; }
  .subtask-error { font-family: var(--font-mono); font-size: 10px; color: var(--color-error); cursor: default; }

  .mono { font-family: var(--font-mono); }
</style>

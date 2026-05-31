<script lang="ts">
  import { dashboardDataStore, profileState } from '../stores.svelte';

  type FilterMode = 'open' | 'done' | 'all';
  let filter = $state<FilterMode>('open');

  let tasks = $derived.by(() => {
    const data = dashboardDataStore.value;
    if (!data) return [];
    const ap = profileState.active;
    let list = data.hubTasks ?? [];
    if (ap) list = list.filter((t: any) =>
      (t.workspace_profile ?? '').toLowerCase() === ap.name.toLowerCase()
    );
    return list;
  });

  let shown = $derived.by(() => {
    if (filter === 'all') return tasks;
    if (filter === 'done') return tasks.filter((t: any) => t.status === 'done');
    return tasks.filter((t: any) => t.status !== 'done' && t.status !== 'cancelled');
  });

  function stateDotClass(status: string): string {
    if (status === 'done') return 'done';
    if (status === 'running') return 'running';
    if (status === 'failed' || status === 'cancelled') return 'failed';
    if (status === 'snoozed') return 'snoozed';
    return 'pending';
  }

  function fmtTime(ts: any): string {
    if (!ts) return '';
    const ms = typeof ts === 'number' ? ts : parseFloat(ts) * 1000;
    if (!ms) return '';
    const d = new Date(ms);
    const today = new Date(); today.setHours(0,0,0,0);
    if (d >= today) return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
  }
</script>

<div class="tasks-widget">
  <div class="widget-header">
    <span class="widget-title">» TASKS</span>
  </div>

  <div class="task-list">
    {#each shown as task}
      <div class="task-row">
        <span class="ds-state-dot {stateDotClass(task.status ?? '')}"></span>
        <div class="task-body">
          <span
            class="task-title"
            class:done={task.status === 'done' || task.status === 'cancelled'}
          >{task.description ?? task.id?.slice(0, 12) ?? '—'}</span>
          <span class="task-meta">{task.session_name ?? 'hub'} · {task.status ?? ''}</span>
        </div>
        <span class="task-time">{fmtTime(task.created_at)}</span>
      </div>
    {/each}
    {#if !shown.length}
      <div class="empty-msg">no tasks</div>
    {/if}
  </div>

  <div class="footer">
    <div class="filter-tabs">
      {#each (['open', 'done', 'all'] as FilterMode[]) as f}
        <button
          class="filter-btn"
          class:active={filter === f}
          onclick={() => filter = f}
        >{f}</button>
      {/each}
    </div>
    <span class="count">{shown.length} shown</span>
  </div>
</div>

<style>
  .tasks-widget {
    display: flex;
    flex-direction: column;
    height: 100%;
  }

  .widget-header {
    display: flex;
    align-items: center;
    padding: 12px 14px 10px;
    border-bottom: 1px solid var(--border, var(--color-border-primary));
    flex-shrink: 0;
  }

  .widget-title {
    font-family: var(--font-mono);
    font-size: 11px;
    letter-spacing: .1em;
    text-transform: uppercase;
    color: var(--text-muted, var(--color-text-secondary));
  }

  .task-list {
    flex: 1;
    overflow-y: auto;
    min-height: 0;
  }

  .task-row {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 9px 14px;
    border-bottom: 1px solid var(--border, var(--color-border-primary));
  }

  .task-body {
    flex: 1;
    min-width: 0;
  }

  .task-title {
    display: block;
    font-size: 13.5px;
    font-weight: 600;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    color: var(--text, var(--color-text-primary));
  }
  .task-title.done {
    text-decoration: line-through;
    color: var(--text-faint, var(--color-text-tertiary));
  }

  .task-meta {
    display: block;
    font-family: var(--font-mono);
    font-size: 10.5px;
    color: var(--text-faint, var(--color-text-tertiary));
    margin-top: 2px;
  }

  .task-time {
    font-family: var(--font-mono);
    font-size: 10.5px;
    color: var(--text-faint, var(--color-text-tertiary));
    flex-shrink: 0;
  }

  .empty-msg {
    color: var(--text-faint, var(--color-text-tertiary));
    font-size: 13px;
    padding: 20px;
    text-align: center;
  }

  .footer {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 14px;
    border-top: 1px solid var(--border, var(--color-border-primary));
    flex-shrink: 0;
  }

  .filter-tabs {
    display: flex;
    gap: 4px;
  }

  .filter-btn {
    font-family: var(--font-mono);
    font-size: 11px;
    padding: 3px 9px;
    border-radius: var(--r-sm, var(--radius-sm));
    border: 1px solid var(--border, var(--color-border-primary));
    background: transparent;
    color: var(--text-muted, var(--color-text-secondary));
    cursor: pointer;
    transition: background var(--dur, .2s), color var(--dur, .2s);
  }
  .filter-btn:hover { background: var(--bg-hover, var(--color-surface-hover)); color: var(--text, var(--color-text-primary)); }
  .filter-btn.active { background: var(--accent-soft, var(--color-accent-soft)); border-color: transparent; color: var(--accent, var(--color-accent)); }

  .count {
    margin-left: auto;
    font-family: var(--font-mono);
    font-size: 10.5px;
    color: var(--text-faint, var(--color-text-tertiary));
  }
</style>

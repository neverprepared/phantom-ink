<script lang="ts">
  import { dashboardDataStore, profileState } from '../stores.svelte';
  import Icon from '../components/Icon.svelte';

  const BUCKETS = [
    { id: 'today',   label: 'Today',     color: 'var(--task)' },
    { id: 'week',    label: 'This Week',  color: 'var(--accent)' },
    { id: 'overdue', label: 'Overdue',    color: 'var(--fail)' },
  ] as const;
  type BucketId = typeof BUCKETS[number]['id'];

  let openBucket = $state<BucketId | null>(null);

  function startOfDay(): Date {
    const d = new Date(); d.setHours(0,0,0,0); return d;
  }
  function startOfWeek(): Date {
    const d = startOfDay();
    d.setDate(d.getDate() - d.getDay());
    return d;
  }

  function taskBucket(task: any): BucketId | null {
    const s = task.status ?? task.state ?? '';
    if (s === 'failed') return 'overdue';
    const created = typeof task.created_at === 'number'
      ? task.created_at
      : parseFloat(task.created_at ?? '0') * 1000;
    if (!created) return null;
    const d = new Date(created);
    const tod = startOfDay();
    const sow = startOfWeek();
    if (d >= tod) return 'today';
    if (d >= sow) return 'week';
    const ageMs = Date.now() - created;
    if (ageMs > 3 * 24 * 3600_000 && (s === 'running' || s === 'pending')) return 'overdue';
    return null;
  }

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

  function count(id: BucketId): number {
    return tasks.filter(t => taskBucket(t) === id && t.status !== 'cancelled').length;
  }

  function bucketTasks(id: BucketId): any[] {
    return tasks.filter(t => taskBucket(t) === id);
  }

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
    return new Date(ms).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }
</script>

<div class="cal-widget">
  <div class="widget-header">
    <Icon name="calendar" size={15} style="color: var(--text-muted); flex-shrink: 0;" />
    <span class="widget-title">» CALENDAR</span>
  </div>

  <div class="buckets">
    {#each BUCKETS as b}
      {@const n = count(b.id)}
      {@const active = openBucket === b.id}
      <button
        class="bucket-tile"
        class:active
        style="--bcolor: {b.color}"
        onclick={() => openBucket = active ? null : b.id}
      >
        <span class="bucket-label">{b.label}</span>
        <span class="bucket-count" style="color: {b.color}">{n}</span>
      </button>
    {/each}
  </div>

  {#if openBucket}
    {@const list = bucketTasks(openBucket)}
    <div class="drilldown ds-fade-up">
      <div class="drilldown-header">
        <span class="drilldown-meta">» {openBucket} · {list.length} items</span>
        <button class="btn ghost sm" onclick={() => openBucket = null}>collapse</button>
      </div>
      {#if list.length}
        {#each list as task}
          <div class="task-row">
            <span class="ds-state-dot {stateDotClass(task.status ?? '')}"></span>
            <div class="task-body">
              <span class="task-title" class:done={task.status === 'done' || task.status === 'cancelled'}>
                {task.description ?? task.id?.slice(0, 12) ?? '—'}
              </span>
              <span class="task-meta">
                {task.session_name ?? 'hub'} · {task.status ?? ''}
              </span>
            </div>
            <span class="task-time">{fmtTime(task.created_at)}</span>
          </div>
        {/each}
      {:else}
        <div class="empty-msg">nothing here — clear</div>
      {/if}
    </div>
  {/if}
</div>

<style>
  .cal-widget {
    display: flex;
    flex-direction: column;
    height: 100%;
    font-family: var(--font-ui, var(--font-sans));
  }

  .widget-header {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 12px 14px 10px;
    border-bottom: 1px solid var(--border, var(--color-border-primary));
  }

  .widget-title {
    font-family: var(--font-mono);
    font-size: 11px;
    letter-spacing: .1em;
    text-transform: uppercase;
    color: var(--text-muted, var(--color-text-secondary));
  }

  .buckets {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 10px;
    padding: 14px;
  }

  .bucket-tile {
    text-align: left;
    border: 1px solid var(--border, var(--color-border-primary));
    background: var(--bg, var(--color-bg-primary));
    cursor: pointer;
    border-radius: var(--r-md, var(--radius-md));
    padding: 11px 13px;
    transition: border-color var(--dur, .2s), background var(--dur, .2s);
  }
  .bucket-tile:hover {
    border-color: var(--bcolor);
    background: color-mix(in srgb, var(--bcolor) 6%, var(--bg, var(--color-bg-primary)));
  }
  .bucket-tile.active {
    border-color: var(--bcolor);
    background: color-mix(in srgb, var(--bcolor) 10%, var(--bg, var(--color-bg-primary)));
  }

  .bucket-label {
    display: block;
    font-family: var(--font-mono);
    font-size: 10px;
    letter-spacing: .1em;
    text-transform: uppercase;
    color: var(--text-faint, var(--color-text-tertiary));
  }

  .bucket-count {
    display: block;
    font-family: var(--font-mono);
    font-size: 32px;
    font-weight: 700;
    line-height: 1.1;
    margin-top: 3px;
  }

  .drilldown {
    margin: 0 14px 14px;
    border-top: 1px dashed var(--border, var(--color-border-primary));
    padding-top: 8px;
  }

  .drilldown-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 2px 0 6px;
  }

  .drilldown-meta {
    font-family: var(--font-mono);
    font-size: 10.5px;
    color: var(--text-faint, var(--color-text-tertiary));
  }

  .task-row {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 4px;
    border-bottom: 1px solid var(--border, var(--color-border-primary));
  }

  .task-body {
    flex: 1;
    min-width: 0;
  }

  .task-title {
    display: block;
    font-size: 13px;
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
    padding: 14px;
    text-align: center;
  }
</style>

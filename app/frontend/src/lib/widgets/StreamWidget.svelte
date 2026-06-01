<script lang="ts">
  import { onMount } from 'svelte';
  import { getApi } from '../utils/api';
  import { profileState } from '../stores.svelte';
  import { brainboxEvents } from '../events.svelte';
  import Icon from '../components/Icon.svelte';
  import type { StreamWidgetConfig } from './types';

  let { config }: { config: StreamWidgetConfig } = $props();

  interface StreamItem {
    id: string;
    source: 'task' | 'event';
    time: number;
    title: string;
    subtitle: string;
    status: string;
    url?: string;
  }

  let items  = $state<StreamItem[]>([]);
  let loading = $state(true);

  const profile = $derived(profileState.active?.name ?? '');
  const sources = $derived(config.sources ?? ['task', 'event']);
  const limit   = $derived(config.limit ?? 20);

  // Use config.profile if set, otherwise fall back to active profile
  const filterProfile = $derived(config.profile !== undefined ? config.profile : profile);

  function taskStatus(s: string): string {
    switch (s) {
      case 'running':   return 'active';
      case 'completed': return 'done';
      case 'failed':    return 'failed';
      case 'cancelled': return 'cancelled';
      default:          return 'pending';
    }
  }

  function statusGlyph(s: string): string {
    switch (s) {
      case 'active':    return '●';
      case 'done':      return '✓';
      case 'failed':    return '✗';
      case 'cancelled': return '○';
      case 'upcoming':  return '◷';
      default:          return '·';
    }
  }

  function fmtTime(ms: number): string {
    const d = new Date(ms);
    const today = new Date();
    if (d.toDateString() === today.toDateString()) {
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }
    return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
  }

  function elapsed(t: any): string {
    const end = t.status === 'running' ? Date.now() : (t.updated_at ?? Date.now());
    const ms = end - t.created_at;
    if (ms < 60_000) return `${Math.floor(ms / 1000)}s`;
    const m = Math.floor(ms / 60_000);
    return `${m}m`;
  }

  async function load() {
    const a = await getApi();
    if (!a) { loading = false; return; }
    try {
      const merged: StreamItem[] = [];

      if (sources.includes('task')) {
        const tasks: any[] = ((await (a.ListHubTasks as any)('', '')) ?? [])
          .filter((t: any) => !t.spawned_by)
          .filter((t: any) => !filterProfile || (t.workspace_profile ?? '').toLowerCase() === filterProfile.toLowerCase());
        for (const t of tasks) {
          merged.push({
            id: `task:${t.id}`,
            source: 'task',
            time: t.created_at,
            title: t.description || t.id.slice(0, 12),
            subtitle: `${t.agent_name} · ${elapsed(t)}`,
            status: taskStatus(t.status),
          });
        }
      }

      if (sources.includes('event')) {
        const entries: any[] = ((await (a.ListCollectedEntries as any)('', 'event', config.tag ?? '')) ?? [])
          .filter((e: any) => !filterProfile || (e.profile ?? '').toLowerCase() === filterProfile.toLowerCase())
          .filter((e: any) => !config.tag || e.tags?.includes(config.tag));
        for (const e of entries) {
          merged.push({
            id: `entry:${e.job_id}:${e.entry_id}`,
            source: 'event',
            time: e.start_at ?? e.collected_at,
            title: e.title,
            subtitle: e.tags?.slice(0, 2).join(' · ') ?? '',
            status: e.status || 'active',
            url: e.url || undefined,
          });
        }
      }

      merged.sort((a, b) => b.time - a.time);
      items = merged.slice(0, limit);
    } finally {
      loading = false;
    }
  }

  onMount(() => {
    void load();
    const handler = () => void load();
    window.runtime?.EventsOn('collect:update', handler);
    return () => window.runtime?.EventsOff('collect:update');
  });

  let _lastEv = $derived(brainboxEvents.last);
  $effect(() => { if (_lastEv) void load(); });

  function openURL(url: string) {
    window.runtime?.BrowserOpenURL(url);
  }
</script>

<div class="drag-strip widget-drag-handle" aria-hidden="true"></div>

<div class="stream-widget">
  <div class="widget-header">
    <Icon name="spark" size={15} style="color: var(--text-muted); flex-shrink: 0;" />
    <span class="widget-label">» {config.label ?? 'stream'}</span>
    {#if config.profile}
      <span class="profile-badge">{config.profile}</span>
    {/if}
    {#if config.tag}
      <span class="tag-badge">{config.tag}</span>
    {/if}
  </div>

  {#if loading}
    <div class="empty">…</div>
  {:else if items.length === 0}
    <div class="empty">no events</div>
  {:else}
    <div class="item-list">
      {#each items as item (item.id)}
        <div class="item src-{item.source}">
          <span class="glyph st-{item.status}">{statusGlyph(item.status)}</span>
          <div class="item-body">
            {#if item.url}
              <button class="item-title link" onclick={() => openURL(item.url!)}>{item.title}</button>
            {:else}
              <span class="item-title">{item.title}</span>
            {/if}
            {#if item.subtitle}
              <span class="item-sub">{item.subtitle}</span>
            {/if}
          </div>
          <span class="item-time">{fmtTime(item.time)}</span>
        </div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .drag-strip {
    height: 6px;
    cursor: grab;
    flex-shrink: 0;
  }

  .stream-widget {
    width: 100%;
    height: calc(100% - 6px);
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  .widget-header {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: var(--spacing-sm) var(--spacing-lg);
    padding-bottom: var(--spacing-xs);
    flex-shrink: 0;
    border-bottom: 1px solid var(--color-border-primary);
  }

  .widget-label {
    font-family: var(--font-mono);
    font-size: 10px;
    letter-spacing: 0.08em;
    color: var(--color-text-tertiary);
    flex: 1;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .profile-badge, .tag-badge {
    font-family: var(--font-mono);
    font-size: 9px;
    padding: 1px 5px;
    border-radius: 999px;
    border: 1px solid var(--color-border-primary);
    color: var(--color-text-muted);
    white-space: nowrap;
  }
  .profile-badge {
    border-color: rgba(234,179,8,0.3);
    color: var(--color-accent);
    background: rgba(234,179,8,0.06);
  }

  .empty {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--color-text-muted);
    padding: var(--spacing-md) var(--spacing-lg);
  }

  .item-list {
    flex: 1;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
  }

  .item {
    display: grid;
    grid-template-columns: 16px 1fr auto;
    align-items: start;
    gap: 6px;
    padding: 5px var(--spacing-lg);
    border-bottom: 1px solid var(--color-border-primary);
    transition: background 80ms;
  }
  .item:last-child { border-bottom: none; }
  .item:hover { background: var(--color-surface-hover); }
  .item.src-event { border-left: 2px solid var(--color-info); padding-left: calc(var(--spacing-lg) - 2px); }
  .item.src-task  { border-left: 2px solid var(--color-border-primary); padding-left: calc(var(--spacing-lg) - 2px); }

  .glyph {
    font-family: var(--font-mono);
    font-size: 11px;
    padding-top: 1px;
    text-align: center;
  }

  .item-body {
    display: flex;
    flex-direction: column;
    gap: 1px;
    min-width: 0;
  }

  .item-title {
    font-size: 12px;
    font-weight: 500;
    color: var(--color-text-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    background: none;
    border: none;
    padding: 0;
    text-align: left;
    cursor: default;
  }
  .item-title.link { cursor: pointer; }
  .item-title.link:hover { color: var(--color-accent); text-decoration: underline; }

  .item-sub {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-muted);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .item-time {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-muted);
    white-space: nowrap;
    padding-top: 1px;
    flex-shrink: 0;
  }

  .st-active    { color: var(--color-info); }
  .st-done      { color: var(--color-success); }
  .st-failed    { color: var(--color-error); }
  .st-cancelled { color: var(--color-text-muted); }
  .st-upcoming  { color: var(--color-text-tertiary); }
  .st-pending   { color: var(--color-text-tertiary); }
</style>

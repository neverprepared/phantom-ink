<script lang="ts">
  import { dashboardDataStore, profileState } from '../stores.svelte';

  let sessions = $derived.by(() => {
    const data = dashboardDataStore.value;
    if (!data) return [];
    const ap = profileState.active;
    let list = (data.sessions ?? []) as any[];
    if (ap) list = list.filter((s: any) =>
      (s.workspace_profile ?? '').toLowerCase() === ap.name.toLowerCase()
    );
    return list;
  });

  function statusColor(s: any): string {
    if (s.active) return 'var(--run, var(--color-success))';
    return 'var(--text-faint, var(--color-text-tertiary))';
  }

  function typeBadge(s: any): string {
    const b = s.backend ?? 'docker';
    if (b === 'utm') return 'vm';
    return 'container';
  }
</script>

<div class="sessions-mini-widget">
  <div class="widget-header">
    <span class="widget-title">» LIVE SESSIONS</span>
    <span class="session-count">{sessions.filter((s: any) => s.active).length} running</span>
  </div>

  <div class="session-list">
    {#each sessions as s}
      <div class="session-row">
        <span
          class="status-dot"
          style="background: {statusColor(s)}"
          class:pulse={s.active}
        ></span>
        <div class="session-info">
          <span class="session-name">{s.session_name ?? s.name}</span>
          <span class="session-meta">{s.active ? 'running' : 'stopped'}</span>
        </div>
        <span class="ds-badge-type {typeBadge(s)}">{typeBadge(s)}</span>
      </div>
    {/each}
    {#if !sessions.length}
      <div class="empty-msg">no sessions on this profile</div>
    {/if}
  </div>
</div>

<style>
  .sessions-mini-widget {
    display: flex;
    flex-direction: column;
    height: 100%;
  }

  .widget-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
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

  .session-count {
    font-family: var(--font-mono);
    font-size: 10.5px;
    color: var(--run, var(--color-success));
  }

  .session-list {
    flex: 1;
    overflow-y: auto;
    padding: 10px 12px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .session-row {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 10px;
    border: 1px solid var(--border, var(--color-border-primary));
    border-radius: var(--r-md, var(--radius-md));
    background: var(--bg, var(--color-bg-primary));
  }

  .status-dot {
    width: 8px; height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
    transition: background .3s;
  }
  .status-dot.pulse {
    animation: ds-pulse 2.4s infinite;
  }

  .session-info {
    flex: 1;
    min-width: 0;
  }

  .session-name {
    display: block;
    font-size: 13px;
    font-weight: 600;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    color: var(--text, var(--color-text-primary));
  }

  .session-meta {
    display: block;
    font-family: var(--font-mono);
    font-size: 10.5px;
    color: var(--text-faint, var(--color-text-tertiary));
    margin-top: 1px;
  }

  .empty-msg {
    color: var(--text-faint, var(--color-text-tertiary));
    font-size: 13px;
    padding: 16px;
    text-align: center;
  }
</style>

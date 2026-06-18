<script lang="ts">
  import { dashboardDataStore, currentPanel } from '../stores.svelte';
  import { getApi } from '../utils/api';
  import Icon from '../components/Icon.svelte';
  import type { ActionItem } from './types';

  let data = $derived(dashboardDataStore.value);
  let items = $derived(data?.actionItems ?? []);

  async function activate(item: ActionItem) {
    if (item.openAttentionId) {
      const a = await getApi();
      if (a) {
        try {
          const target: any = await a.AttentionOpenTarget(item.openAttentionId);
          if (target?.panel) {
            currentPanel.value = target.panel;
            return;
          }
        } catch {
          // fall through to the navTarget below
        }
      }
    }
    currentPanel.value = item.navTarget ?? 'stream';
  }
</script>

<div class="widget">
  <div class="widget-header widget-drag-handle">
    <Icon name="check" size={15} style="color: var(--text-muted); flex-shrink: 0;" />
    <span class="widget-title">» ACTION ITEMS</span>
    {#if items.length > 0}
      <span class="badge warn">{items.length}</span>
    {/if}
  </div>
  <div class="widget-body">
    {#if data?.loading}
      <div class="empty">loading…</div>
    {:else if items.length === 0}
      <div class="nominal">
        <span class="dot-green"></span>
        all systems nominal
      </div>
    {:else}
      {#each items as item}
        <button
          type="button"
          class="action-row sev-{item.severity}"
          onclick={() => activate(item)}
          title="open"
        >
          <span class="check">[ ]</span>
          <div class="action-body">
            <span class="action-title">{item.title}</span>
            {#if item.desc}<span class="action-desc">{item.desc}</span>{/if}
          </div>
          {#if item.ref}
            <span class="action-ref mono">{item.ref.slice(0, 8)}</span>
          {/if}
        </button>
      {/each}
    {/if}
  </div>
</div>

<style>
  .widget { display: flex; flex-direction: column; height: 100%; }

  .widget-header {
    display: flex;
    align-items: center;
    gap: var(--spacing-sm);
    padding: var(--spacing-sm) var(--spacing-md);
    border-bottom: 1px solid var(--color-border-primary);
    cursor: grab;
    flex-shrink: 0;
  }

  .widget-title {
    font-family: var(--font-mono);
    font-size: 11px;
    letter-spacing: 0.08em;
    color: var(--color-text-secondary);
    flex: 1;
  }

  .badge {
    font-size: 10px;
    padding: 1px 6px;
    border-radius: var(--radius-sm);
    background: var(--color-surface-subtle);
    border: 1px solid var(--color-border-primary);
    color: var(--color-text-tertiary);
  }
  .badge.warn {
    background: rgba(234, 179, 8, 0.08);
    border-color: rgba(234, 179, 8, 0.2);
    color: var(--color-warning);
  }

  .widget-body {
    flex: 1;
    overflow-y: auto;
    padding: var(--spacing-xs) 0;
  }

  .empty {
    font-size: 12px;
    color: var(--color-text-tertiary);
    padding: var(--spacing-xl) var(--spacing-md);
  }

  .nominal {
    font-family: var(--font-mono);
    font-size: 12px;
    color: var(--color-text-muted);
    display: flex;
    align-items: center;
    gap: var(--spacing-sm);
    padding: var(--spacing-xl) var(--spacing-md);
  }

  .dot-green {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--color-success);
    flex-shrink: 0;
  }

  .action-row {
    display: flex;
    align-items: flex-start;
    gap: var(--spacing-sm);
    padding: var(--spacing-xs) var(--spacing-md);
    border-left: 2px solid transparent;
    background: transparent;
    border-top: none;
    border-right: none;
    border-bottom: none;
    width: 100%;
    text-align: left;
    cursor: pointer;
    font: inherit;
    color: inherit;
    transition: background 0.12s;
  }
  .action-row:hover { background: var(--color-surface-hover, rgba(0,0,0,0.04)); }
  .action-row.sev-urgent  { border-left-color: var(--color-error); background: rgba(239, 68, 68, 0.04); }
  .action-row.sev-warning { border-left-color: var(--color-warning); background: rgba(234, 179, 8, 0.04); }
  .action-row.sev-info    { border-left-color: var(--color-info); background: rgba(14, 165, 233, 0.04); }
  .action-row.sev-urgent:hover  { background: rgba(239, 68, 68, 0.08); }
  .action-row.sev-warning:hover { background: rgba(234, 179, 8, 0.08); }
  .action-row.sev-info:hover    { background: rgba(14, 165, 233, 0.08); }

  .check {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--color-text-muted);
    flex-shrink: 0;
    padding-top: 1px;
  }

  .action-body {
    display: flex;
    flex-direction: column;
    gap: 2px;
    flex: 1;
    min-width: 0;
  }

  .action-title { font-size: 12px; color: var(--color-text-primary); }

  .action-desc {
    font-size: 11px;
    color: var(--color-text-tertiary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .action-ref {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-muted);
    flex-shrink: 0;
    padding: 0;
  }

  .mono { font-family: var(--font-mono); }
</style>

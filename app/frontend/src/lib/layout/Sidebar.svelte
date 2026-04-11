<script lang="ts">
  import { panels } from '../panels';
  import { currentPanel, sidebarCollapsed } from '../stores.svelte';
</script>

<nav class="sidebar" class:collapsed={sidebarCollapsed.value}>
  <ul class="nav-items">
    {#each panels as panel (panel.id)}
      <li>
        <button
          class="nav-btn"
          class:active={currentPanel.value === panel.id}
          onclick={() => currentPanel.value = panel.id}
          title={sidebarCollapsed.value ? `${panel.label} ${panel.shortcut ?? ''}` : panel.label}
          aria-label={panel.label}
          aria-current={currentPanel.value === panel.id ? 'page' : undefined}
        >
          <span class="nav-icon" aria-hidden="true">{@html panel.icon}</span>
          {#if !sidebarCollapsed.value}
            <span class="nav-label">{panel.label}</span>
            {#if panel.shortcut}
              <span class="nav-shortcut">{panel.shortcut}</span>
            {/if}
          {/if}
        </button>
      </li>
    {/each}
  </ul>

  <div class="sidebar-footer">
    <button
      class="collapse-btn"
      onclick={() => sidebarCollapsed.toggle()}
      aria-label={sidebarCollapsed.value ? 'Expand sidebar' : 'Collapse sidebar'}
    >
      {#if sidebarCollapsed.value}
        <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg>
      {:else}
        <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="15 18 9 12 15 6"/></svg>
      {/if}
    </button>
  </div>
</nav>

<style>
  .sidebar {
    display: flex;
    flex-direction: column;
    width: var(--sidebar-width);
    background: var(--color-bg-sidebar);
    border-right: 1px solid var(--color-border-primary);
    transition: width 0.2s ease;
    overflow: hidden;
    flex-shrink: 0;
  }

  .sidebar.collapsed {
    width: var(--sidebar-collapsed-width);
  }

  .nav-items {
    list-style: none;
    flex: 1;
    padding: 8px;
    overflow-y: auto;
    overflow-x: hidden;
  }

  .nav-items li {
    margin-bottom: 2px;
  }

  .nav-btn {
    display: flex;
    align-items: center;
    gap: 10px;
    width: 100%;
    padding: 9px 10px;
    background: none;
    border: none;
    border-radius: var(--radius-lg);
    color: var(--color-text-secondary);
    cursor: pointer;
    font-family: inherit;
    font-size: 13px;
    white-space: nowrap;
    transition: all 0.15s;
    min-width: 0;
  }

  .nav-btn:hover {
    background: rgba(255, 255, 255, 0.05);
    color: var(--color-text-primary);
  }

  .nav-btn.active {
    background: rgba(59, 130, 246, 0.1);
    color: var(--color-info);
  }

  .nav-icon {
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    width: 20px;
    height: 20px;
  }

  .nav-label {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .nav-shortcut {
    font-size: 10px;
    color: var(--color-text-tertiary);
    flex-shrink: 0;
  }

  .sidebar-footer {
    padding: 10px 8px;
    border-top: 1px solid var(--color-border-primary);
    flex-shrink: 0;
  }

  .collapse-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 100%;
    padding: 7px;
    background: none;
    border: none;
    border-radius: var(--radius-md);
    color: var(--color-text-tertiary);
    cursor: pointer;
    transition: all 0.15s;
  }

  .collapse-btn:hover {
    background: rgba(255, 255, 255, 0.05);
    color: var(--color-text-secondary);
  }
</style>

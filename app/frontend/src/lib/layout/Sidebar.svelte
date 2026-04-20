<script lang="ts">
  import { panels } from '../panels';
  import { currentPanel, sidebarCollapsed } from '../stores.svelte';

  let search = $state('');

  let filteredPanels = $derived(
    search.trim()
      ? panels.filter(p => p.label.toLowerCase().includes(search.trim().toLowerCase()))
      : panels
  );
</script>

<nav class="sidebar" class:collapsed={sidebarCollapsed.value}>
  {#if !sidebarCollapsed.value}
    <div class="search-box">
      <svg class="search-icon" xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
      <input
        type="text"
        class="search-input"
        placeholder="filter…"
        bind:value={search}
        aria-label="Filter panels"
      />
      {#if search}
        <button class="search-clear" onclick={() => search = ''} aria-label="Clear filter">
          <svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
      {/if}
    </div>
  {/if}

  <ul class="nav-items">
    {#each filteredPanels as panel (panel.id)}
      <li>
        <button
          class="nav-btn"
          class:active={currentPanel.value === panel.id}
          onclick={() => { currentPanel.value = panel.id; search = ''; }}
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

  :global([data-theme="light"]) .sidebar {
    border-right: none;
    box-shadow: 2px 0 12px rgba(0, 0, 0, 0.08);
  }

  :global([data-theme="light"]) .nav-btn {
    color: var(--color-sidebar-text, rgba(255, 255, 255, 0.8));
  }

  :global([data-theme="light"]) .nav-btn:hover {
    background: var(--color-sidebar-hover, rgba(255, 255, 255, 0.08));
    color: var(--color-sidebar-text-active, #ffffff);
  }

  :global([data-theme="light"]) .nav-btn.active {
    background: var(--color-nav-active-bg);
    color: var(--color-sidebar-text-active, #ffffff);
    box-shadow: inset 3px 0 0 var(--color-nav-active-border);
  }

  :global([data-theme="light"]) .nav-shortcut {
    color: rgba(255, 255, 255, 0.4);
  }

  :global([data-theme="light"]) .search-box {
    background: rgba(255, 255, 255, 0.08);
    border-color: rgba(255, 255, 255, 0.1);
  }

  :global([data-theme="light"]) .search-input {
    color: #ffffff;
  }

  :global([data-theme="light"]) .search-input::placeholder {
    color: rgba(255, 255, 255, 0.4);
  }

  :global([data-theme="light"]) .search-icon,
  :global([data-theme="light"]) .search-clear {
    color: rgba(255, 255, 255, 0.4);
  }

  :global([data-theme="light"]) .sidebar-footer {
    border-top-color: rgba(255, 255, 255, 0.1);
  }

  :global([data-theme="light"]) .collapse-btn {
    color: rgba(255, 255, 255, 0.5);
  }

  :global([data-theme="light"]) .collapse-btn:hover {
    background: rgba(255, 255, 255, 0.08);
    color: #ffffff;
  }

  /* Muse theme — white sidebar with colored active state */
  :global([data-theme="muse"]) .sidebar {
    border-right: 1px solid rgba(0, 0, 0, 0.04);
    box-shadow: 2px 0 8px rgba(0, 0, 0, 0.03);
  }

  :global([data-theme="muse"]) .nav-btn {
    color: var(--color-sidebar-text, #8c8c8c);
    border-radius: 0.75rem;
  }

  :global([data-theme="muse"]) .nav-btn:hover {
    background: var(--color-sidebar-hover, rgba(0, 0, 0, 0.03));
    color: var(--color-text-primary);
  }

  :global([data-theme="muse"]) .nav-btn.active {
    background: var(--color-nav-active-bg);
    color: var(--color-accent);
    box-shadow: none;
  }

  :global([data-theme="muse"]) .nav-shortcut {
    color: var(--color-text-muted);
  }

  :global([data-theme="muse"]) .sidebar-footer {
    border-top-color: rgba(0, 0, 0, 0.04);
  }

  /* Vision theme — deep space with blue glow */
  :global([data-theme="vision"]) .sidebar {
    border-right: none;
    box-shadow: 2px 0 16px rgba(0, 0, 0, 0.4);
  }

  :global([data-theme="vision"]) .nav-btn {
    color: var(--color-sidebar-text, rgba(255, 255, 255, 0.6));
  }

  :global([data-theme="vision"]) .nav-btn:hover {
    background: var(--color-sidebar-hover, rgba(255, 255, 255, 0.06));
    color: var(--color-sidebar-text-active, #ffffff);
  }

  :global([data-theme="vision"]) .nav-btn.active {
    background: var(--color-nav-active-bg);
    color: #ffffff;
    box-shadow: inset 3px 0 0 var(--color-nav-active-border), 0 0 12px rgba(0, 117, 255, 0.2);
  }

  :global([data-theme="vision"]) .nav-shortcut {
    color: rgba(255, 255, 255, 0.3);
  }

  :global([data-theme="vision"]) .search-box {
    background: rgba(255, 255, 255, 0.04);
    border-color: rgba(255, 255, 255, 0.08);
  }

  :global([data-theme="vision"]) .search-input {
    color: #ffffff;
  }

  :global([data-theme="vision"]) .search-input::placeholder {
    color: rgba(255, 255, 255, 0.3);
  }

  :global([data-theme="vision"]) .search-icon,
  :global([data-theme="vision"]) .search-clear {
    color: rgba(255, 255, 255, 0.3);
  }

  :global([data-theme="vision"]) .sidebar-footer {
    border-top-color: rgba(255, 255, 255, 0.06);
  }

  :global([data-theme="vision"]) .collapse-btn {
    color: rgba(255, 255, 255, 0.4);
  }

  :global([data-theme="vision"]) .collapse-btn:hover {
    background: rgba(255, 255, 255, 0.06);
    color: #ffffff;
  }

  /* Paper theme — white sidebar, thin borders, left-accent active */
  :global([data-theme="paper"]) .sidebar {
    background: #ffffff;
    border-right: 1px solid #ddd;
    box-shadow: none;
  }

  :global([data-theme="paper"]) .nav-btn {
    color: #9a9a9a;
    border-radius: 0;
    padding: 12px 15px;
    font-size: 14px;
    font-weight: 400;
    text-transform: uppercase;
    letter-spacing: 0.02em;
  }

  :global([data-theme="paper"]) .nav-btn:hover {
    background: #f6f6f6;
    color: #333333;
  }

  :global([data-theme="paper"]) .nav-btn.active {
    background: transparent;
    color: #333333;
    box-shadow: inset 4px 0 0 #f5593d;
    border-radius: 0;
    font-weight: 500;
  }

  :global([data-theme="paper"]) .nav-label {
    font-size: 12px;
  }

  :global([data-theme="paper"]) .nav-shortcut {
    display: none;
  }

  :global([data-theme="paper"]) .search-box {
    background: #f6f6f6;
    border-color: #ddd;
    border-radius: 4px;
  }

  :global([data-theme="paper"]) .sidebar-footer {
    border-top: 1px solid #ddd;
  }

  .sidebar.collapsed {
    width: var(--sidebar-collapsed-width);
  }

  /* Search */
  .search-box {
    display: flex;
    align-items: center;
    gap: 6px;
    margin: 8px 8px 0;
    padding: 5px 8px;
    background: var(--color-bg-tertiary, rgba(255,255,255,0.04));
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-md);
  }

  .search-icon {
    color: var(--color-text-tertiary);
    flex-shrink: 0;
  }

  .search-input {
    flex: 1;
    background: none;
    border: none;
    outline: none;
    color: var(--color-text-primary);
    font-size: 12px;
    font-family: inherit;
    padding: 0;
    min-width: 0;
  }

  .search-input::placeholder {
    color: var(--color-text-tertiary);
  }

  .search-clear {
    background: none;
    border: none;
    color: var(--color-text-tertiary);
    padding: 2px;
    display: flex;
    border-radius: var(--radius-sm);
    cursor: pointer;
    transition: color 0.15s;
  }
  .search-clear:hover { color: var(--color-text-secondary); }

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
    text-align: left;
    justify-content: flex-start;
  }

  .nav-btn:hover {
    background: var(--color-surface-hover);
    color: var(--color-text-primary);
  }

  .nav-btn.active {
    background: var(--color-nav-active-bg);
    color: var(--color-accent);
    box-shadow: inset 3px 0 0 var(--color-nav-active-border);
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
    background: var(--color-surface-hover);
    color: var(--color-text-secondary);
  }
</style>

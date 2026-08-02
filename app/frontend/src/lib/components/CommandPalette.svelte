<script lang="ts">
  import { commandPalette, currentPanel, integrationState, settingsState, type SettingsTab } from '../stores.svelte';
  import { panels } from '../panels';

  // Settings tabs that used to be top-level panels — kept in the palette so
  // they stay directly jumpable even though they no longer have a sidebar entry.
  const settingsTabCommands: { tab: SettingsTab; label: string }[] = [
    { tab: 'runners', label: 'Runners' },
    { tab: 'profiles', label: 'Profiles' },
    { tab: 'gateway', label: 'Gateway' },
    { tab: 'tokens', label: 'API tokens' },
  ];

  interface Command {
    id: string;
    label: string;
    description?: string;
    action: () => void;
  }

  let query = $state('');
  let selectedIndex = $state(0);
  let inputEl = $state<HTMLInputElement | null>(null);

  // Derived so integration-gated panels (Files requires MinIO) drop out
  // of the palette the same way they drop out of the sidebar.
  const baseCommands: Command[] = $derived([
    ...panels
      .filter(p => !(p.requires === 'minio' && !integrationState.minioEnabled))
      .map(p => ({
        id: `nav:${p.id}`,
        label: `Go to ${p.label}`,
        description: p.shortcut,
        action: () => { currentPanel.value = p.id; commandPalette.close(); },
      })),
    ...settingsTabCommands.map(t => ({
      id: `nav:settings:${t.tab}`,
      label: `Go to ${t.label}`,
      description: 'Settings',
      action: () => { settingsState.open(t.tab); commandPalette.close(); },
    })),
    {
      id: 'reload',
      label: 'Reload app',
      action: () => { commandPalette.close(); window.location.reload(); },
    },
  ]);

  function score(cmd: Command, q: string): number {
    const label = cmd.label.toLowerCase();
    const qLow = q.toLowerCase();
    if (label.startsWith(qLow)) return 2;
    if (label.includes(qLow)) return 1;
    return 0;
  }

  let filtered = $derived(
    query.trim() === ''
      ? baseCommands
      : baseCommands.filter(c => score(c, query) > 0)
          .sort((a, b) => score(b, query) - score(a, query))
  );

  $effect(() => {
    // Reset selection when query changes
    void query;
    selectedIndex = 0;
  });

  $effect(() => {
    if (commandPalette.open) {
      setTimeout(() => inputEl?.focus(), 0);
      query = '';
      selectedIndex = 0;
    }
  });

  function handleKeydown(e: KeyboardEvent) {
    if (!commandPalette.open) return;
    if (e.key === 'Escape') {
      e.preventDefault();
      commandPalette.close();
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      selectedIndex = Math.min(selectedIndex + 1, filtered.length - 1);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      selectedIndex = Math.max(selectedIndex - 1, 0);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      filtered[selectedIndex]?.action();
    }
  }

  function handleBackdrop(e: MouseEvent) {
    if (e.target === e.currentTarget) commandPalette.close();
  }
</script>

<svelte:window onkeydown={handleKeydown} />

{#if commandPalette.open}
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="backdrop" onclick={handleBackdrop}>
    <div class="palette" role="dialog" aria-label="Command palette">
      <div class="search">
        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
        <input
          bind:this={inputEl}
          bind:value={query}
          type="text"
          placeholder="Type a command..."
          autocomplete="off"
          spellcheck="false"
        />
        <kbd>esc</kbd>
      </div>

      {#if filtered.length > 0}
        <ul class="results" role="listbox">
          {#each filtered as cmd, i (cmd.id)}
            <!-- svelte-ignore a11y_click_events_have_key_events -->
            <li
              class="result"
              class:selected={i === selectedIndex}
              role="option"
              aria-selected={i === selectedIndex}
              onclick={() => cmd.action()}
              onmouseenter={() => selectedIndex = i}
            >
              <span class="result-label">{cmd.label}</span>
              {#if cmd.description}
                <span class="result-desc">{cmd.description}</span>
              {/if}
            </li>
          {/each}
        </ul>
      {:else}
        <div class="no-results">No commands found</div>
      {/if}
    </div>
  </div>
{/if}

<style>
  .backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.5);
    display: flex;
    align-items: flex-start;
    justify-content: center;
    padding-top: 120px;
    z-index: 900;
  }

  .palette {
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-secondary);
    border-radius: var(--radius-xl);
    width: 480px;
    max-width: calc(100vw - 48px);
    overflow: hidden;
    box-shadow: 0 20px 60px rgba(0, 0, 0, 0.6);
    animation: pop 0.12s ease;
  }

  @keyframes pop {
    from { opacity: 0; transform: scale(0.96) translateY(-8px); }
    to   { opacity: 1; transform: scale(1) translateY(0); }
  }

  .search {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 12px 16px;
    border-bottom: 1px solid var(--color-border-primary);
    color: var(--color-text-tertiary);
  }

  .search input {
    flex: 1;
    background: none;
    border: none;
    outline: none;
    font-size: 15px;
    color: var(--color-text-primary);
    font-family: inherit;
    padding: 0;
    width: 0; /* let flex grow */
  }

  .search input::placeholder {
    color: var(--color-text-tertiary);
  }

  kbd {
    font-size: 11px;
    color: var(--color-text-tertiary);
    background: var(--color-bg-tertiary);
    border: 1px solid var(--color-border-secondary);
    border-radius: 3px;
    padding: 2px 5px;
    font-family: inherit;
    flex-shrink: 0;
  }

  .results {
    list-style: none;
    padding: 6px;
    max-height: 320px;
    overflow-y: auto;
  }

  .result {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 12px;
    border-radius: var(--radius-md);
    cursor: pointer;
    transition: background 0.1s;
  }

  .result.selected {
    background: var(--color-nav-active-bg);
    color: var(--color-accent);
  }
  .result.selected .result-label {
    color: var(--color-accent);
  }

  .result:hover {
    background: var(--color-surface-hover);
  }

  .result.selected:hover {
    background: var(--color-nav-active-bg);
  }

  .result-label {
    font-size: 13px;
    color: var(--color-text-primary);
  }

  .result-desc {
    font-size: 11px;
    color: var(--color-text-tertiary);
  }

  .no-results {
    padding: 24px;
    text-align: center;
    font-size: 13px;
    color: var(--color-text-tertiary);
  }
</style>

<script lang="ts">
  import { jobsState, type JobsTab } from '../stores.svelte';
  import JobsPanel from './JobsPanel.svelte';
  import LoopsPanel from './LoopsPanel.svelte';
  import CollectorsPanel from './CollectorsPanel.svelte';
  import AutomationsPanel from './AutomationsPanel.svelte';

  // The "Automations" hub — every way to run agent work unattended. Tabs:
  //   jobs    — fleet fan-out (dispatch once across N machines)
  //   loops   — iterate a step with an LLM judge until a condition
  //   collectors — scheduled command/prompt ingest
  //   rules   — reactive event→action rules (the server rules engine)
  // Jobs, Loops, Collectors, and the rules panel each used to be top-level
  // sidebar entries. Each brings its own header/padding, like the Settings tabs.
  // NOTE: the panel id is still 'jobs' internally (jobsState, redirects) — this
  // is a label-only rename; a full id migration is a separate change.
  const TABS: { id: JobsTab; label: string }[] = [
    { id: 'jobs', label: 'jobs' },
    { id: 'loops', label: 'loops' },
    { id: 'collectors', label: 'collectors' },
    { id: 'rules', label: 'rules' },
  ];

  let activeTab = $derived(jobsState.tab);
</script>

<div class="jobs-hub">
  <div class="jobs-tabs" role="tablist" aria-label="Automations sections">
    {#each TABS as t (t.id)}
      <button
        class="jobs-tab"
        class:active={activeTab === t.id}
        role="tab"
        aria-selected={activeTab === t.id}
        onclick={() => (jobsState.tab = t.id)}
      >{t.label}</button>
    {/each}
  </div>

  <div class="jobs-content">
    {#if activeTab === 'jobs'}
      <JobsPanel />
    {:else if activeTab === 'loops'}
      <LoopsPanel />
    {:else if activeTab === 'collectors'}
      <CollectorsPanel />
    {:else if activeTab === 'rules'}
      <AutomationsPanel />
    {/if}
  </div>
</div>

<style>
  .jobs-hub {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
  }

  /* Tab strip pins to the top; the active tab's content scrolls below it. */
  .jobs-tabs {
    display: flex;
    gap: 2px;
    flex-shrink: 0;
    padding: 0 var(--panel-padding);
    border-bottom: 1px solid var(--color-border-primary);
    overflow-x: auto;
  }

  .jobs-tab {
    background: none;
    border: none;
    border-bottom: 2px solid transparent;
    color: var(--color-text-tertiary);
    font-family: inherit;
    font-size: 13px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    padding: 12px 14px;
    margin-bottom: -1px;
    cursor: pointer;
    white-space: nowrap;
    transition: color 0.15s, border-color 0.15s;
  }
  .jobs-tab:hover { color: var(--color-text-secondary); }
  .jobs-tab.active {
    color: var(--color-text-primary);
    border-bottom-color: var(--color-accent);
  }

  .jobs-content {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
  }
</style>

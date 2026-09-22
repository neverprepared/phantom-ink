<script lang="ts">
  import { workState, profileState, type WorkTab } from '../stores.svelte';
  import CodePanel from './CodePanel.svelte';
  import JiraTab from '../components/JiraTab.svelte';
  import { getApi } from '../utils/api';

  // The "Work" hub — the things assigned to you. Tabs:
  //   code — pull requests, issues and notifications across git providers
  //   jira — tickets from this profile's JQL, with their code links
  // The panel id stays 'code' internally (workState, deep links): this is a
  // label-only rename, the same shape JobsHubPanel uses for Automations.
  //
  // The Jira tab appears only when the ACTIVE profile has Jira on. A profile
  // without it sees no tab strip at all, so nothing changes for them.
  let jiraAvailable = $state(false);
  let activeName = $derived(profileState.active?.name ?? '');

  $effect(() => {
    const profile = activeName;
    if (!profile) { jiraAvailable = false; return; }
    void (async () => {
      const a = await getApi();
      if (!a) return;
      try {
        const st = await a.JiraStatus(profile);
        const on = Boolean(st?.configured && st?.opted_in);
        jiraAvailable = on;
        // Switching to a profile without Jira must not strand you on a tab
        // that no longer exists.
        if (!on && workState.tab === 'jira') workState.tab = 'code';
      } catch {
        jiraAvailable = false;
        if (workState.tab === 'jira') workState.tab = 'code';
      }
    })();
  });

  let tabs = $derived<{ id: WorkTab; label: string }[]>(
    jiraAvailable
      ? [{ id: 'code', label: 'code' }, { id: 'jira', label: 'jira' }]
      : [{ id: 'code', label: 'code' }],
  );
  let activeTab = $derived(workState.tab);
</script>

<div class="work-hub">
  {#if tabs.length > 1}
    <div class="work-tabs" role="tablist" aria-label="Work sections">
      {#each tabs as t (t.id)}
        <button
          class="work-tab"
          class:active={activeTab === t.id}
          role="tab"
          aria-selected={activeTab === t.id}
          onclick={() => (workState.tab = t.id)}
        >{t.label}</button>
      {/each}
    </div>
  {/if}

  <div class="work-content">
    {#if activeTab === 'jira' && jiraAvailable}
      <JiraTab />
    {:else}
      <CodePanel />
    {/if}
  </div>
</div>

<style>
  .work-hub { display: flex; flex-direction: column; height: 100%; min-height: 0; }
  .work-tabs {
    display: flex;
    gap: 2px;
    padding: 0 var(--panel-padding);
    border-bottom: 1px solid var(--color-border-primary);
    flex: none;
  }
  .work-tab {
    background: none;
    border: none;
    border-bottom: 2px solid transparent;
    padding: 10px 14px;
    font-family: inherit;
    font-size: 12px;
    color: var(--color-text-tertiary);
    cursor: pointer;
  }
  .work-tab:hover { color: var(--color-text-secondary); }
  .work-tab.active { color: var(--color-text-primary); border-bottom-color: var(--color-accent, var(--color-info)); }
  .work-content { flex: 1; min-height: 0; overflow: auto; }
</style>

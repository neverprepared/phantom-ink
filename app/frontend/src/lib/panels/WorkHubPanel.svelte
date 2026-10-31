<script lang="ts">
  import { workState, profileState, type WorkTab } from '../stores.svelte';
  import { workTabsState } from '../stores/workTabs.svelte';
  import CodePanel from './CodePanel.svelte';
  import JiraTab from '../components/JiraTab.svelte';
  import DoraTab from '../components/DoraTab.svelte';

  // The "Work" hub — the things assigned to you. Tabs:
  //   code     — pull requests, issues and notifications across git providers
  //   jira     — tickets from this profile's JQL, with their code links
  //   delivery — DORA delivery metrics and fleet runner health
  // The panel id stays 'code' internally (workState, deep links): this is a
  // label-only rename, the same shape JobsHubPanel uses for Automations.
  //
  // The Jira tab appears only when the ACTIVE profile has Jira on. A profile
  // without it sees no tab strip at all, so nothing changes for them.
  // The Delivery tab appears whenever the profile has a git provider, since
  // merges are the whole deploy signal — no provider, no metrics to show.
  let activeName = $derived(profileState.active?.name ?? '');

  // Read through the store, so a remount renders the strip on the first frame.
  // See stores/workTabs.svelte.ts for why the probe is split in two.
  let jiraAvailable = $derived(workTabsState.jira(activeName));
  let doraAvailable = $derived(workTabsState.dora(activeName));

  $effect(() => {
    const profile = activeName;
    if (!profile) return;
    void (async () => {
      // The local reads first and on their own: they decide whether the tab is
      // offered, and they cost nothing. Only then the slow confirmation, which
      // resolves the profile's gateway env (~1.6s of server-side decryption)
      // and must never be what the tab strip waits on.
      await workTabsState.probeLocal(profile);
      await workTabsState.confirm(profile, activeName);
    })();
  });

  let tabs = $derived<{ id: WorkTab; label: string }[]>([
    { id: 'code' as WorkTab, label: 'code' },
    ...(jiraAvailable ? [{ id: 'jira' as WorkTab, label: 'jira' }] : []),
    ...(doraAvailable ? [{ id: 'dora' as WorkTab, label: 'delivery' }] : []),
  ]);
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
    {:else if activeTab === 'dora' && doraAvailable}
      <DoraTab />
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

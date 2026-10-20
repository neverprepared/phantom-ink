<script module lang="ts">
  /**
   * Which optional tabs a profile has, remembered across mounts.
   *
   * AppShell renders panels under {#if}, so this component is destroyed on
   * every navigation away. When availability lived in instance $state the tab
   * STRIP itself waited on JiraStatus + DORAOverview before it could render,
   * so the tabs visibly popped in a moment after the panel. Keyed by profile
   * and module-scoped, the strip paints immediately from the last known answer
   * and corrects itself if the probe disagrees.
   *
   * Cached availability is a UI affordance, not an authorization: each tab's
   * own data is fetched per profile, so a wrong guess here costs one frame of
   * a tab, never another profile's rows.
   */
  type Availability = { jira: boolean; dora: boolean };
  const availability = $state<Record<string, Availability>>({});
</script>

<script lang="ts">
  import { workState, profileState, type WorkTab } from '../stores.svelte';
  import CodePanel from './CodePanel.svelte';
  import JiraTab from '../components/JiraTab.svelte';
  import DoraTab from '../components/DoraTab.svelte';
  import { getApi } from '../utils/api';

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

  // Read through the cache, so a remount renders the strip on the first frame.
  let jiraAvailable = $derived(Boolean(activeName && availability[activeName]?.jira));
  let doraAvailable = $derived(Boolean(activeName && availability[activeName]?.dora));

  /** Record one probe result and un-strand the user if their tab just vanished. */
  function setAvailable(profile: string, tab: 'jira' | 'dora', on: boolean): void {
    const cur = availability[profile] ?? { jira: false, dora: false };
    availability[profile] = { ...cur, [tab]: on };
    // Switching to a profile without this tab must not strand you on a tab
    // that no longer exists.
    if (!on && activeName === profile && workState.tab === tab) workState.tab = 'code';
  }

  $effect(() => {
    const profile = activeName;
    if (!profile) return;
    void (async () => {
      const a = await getApi();
      if (!a) return;
      try {
        const st = await a.JiraStatus(profile);
        setAvailable(profile, 'jira', Boolean(st?.configured && st?.opted_in));
      } catch {
        setAvailable(profile, 'jira', false);
      }
      try {
        // DORAOverview reads local SQLite only, so asking it whether a
        // provider is configured costs no network round-trip. The window
        // passed here is irrelevant to that answer.
        const ov = await a.DORAOverview(profile, 30);
        setAvailable(profile, 'dora', !ov?.token_missing);
      } catch {
        setAvailable(profile, 'dora', false);
      }
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

<script lang="ts">
  import TitleBar from './TitleBar.svelte';
  import Sidebar from './Sidebar.svelte';
  import StatusBar from './StatusBar.svelte';
  import { currentPanel, attentionStore, profileState } from '../stores.svelte';
  import { onMount } from 'svelte';

  // Panels (lazy imports)
  import SessionsPanel from '../panels/SessionsPanel.svelte';
  import SequencesPanel from '../panels/SequencesPanel.svelte';
  import LoopsPanel from '../panels/LoopsPanel.svelte';
  import ConversationsPanel from '../panels/ConversationsPanel.svelte';
  import ServicesPanel from '../panels/ServicesPanel.svelte';
  import PlaybooksPanel from '../panels/PlaybooksPanel.svelte';
  import JobsPanel from '../panels/JobsPanel.svelte';
  import AutomationsPanel from '../panels/AutomationsPanel.svelte';
  import ProfilesPanel from '../panels/ProfilesPanel.svelte';
  import SettingsPanel from '../panels/SettingsPanel.svelte';
  import DashboardPanel from '../panels/DashboardPanel.svelte';
  import StreamPanel from '../panels/StreamPanel.svelte';
  import RunnersPanel from '../panels/RunnersPanel.svelte';

  // Attention store powers the sidebar badge + Dashboard ActionItems fold-in.
  // Bootstrapping it here means the count is fresh on every panel, not only
  // while StreamPanel is mounted.
  onMount(() => {
    attentionStore.setWorkspace(profileState.active?.name ?? '');
    attentionStore.start();
  });

  $effect(() => {
    attentionStore.setWorkspace(profileState.active?.name ?? '');
  });
</script>

<div class="shell">
  <TitleBar />
  <div class="body">
    <Sidebar />
    <main class="content">
      {#if currentPanel.value === 'stream'}
        <StreamPanel />
      {:else if currentPanel.value === 'sessions'}
        <SessionsPanel />
      {:else if currentPanel.value === 'runners'}
        <RunnersPanel />
      {:else if currentPanel.value === 'dashboard'}
        <DashboardPanel />
      {:else if currentPanel.value === 'integrations'}
        <ServicesPanel />
      {:else if currentPanel.value === 'sequences'}
        <SequencesPanel />
      {:else if currentPanel.value === 'loops'}
        <LoopsPanel />
      {:else if currentPanel.value === 'conversations'}
        <ConversationsPanel />
      {:else if currentPanel.value === 'playbooks'}
        <PlaybooksPanel />
      {:else if currentPanel.value === 'automations'}
        <AutomationsPanel />
      {:else if currentPanel.value === 'jobs'}
        <JobsPanel />
      {:else if currentPanel.value === 'profiles'}
        <ProfilesPanel />
      {:else if currentPanel.value === 'settings'}
        <SettingsPanel />
      {/if}
    </main>
  </div>
  <StatusBar />
</div>

<style>
  .shell {
    display: flex;
    flex-direction: column;
    height: 100vh;
    overflow: hidden;
  }

  .body {
    display: grid;
    grid-template-columns: auto 1fr;
    flex: 1;
    min-height: 0;
    overflow: hidden;
  }

  .content {
    overflow-y: auto;
    min-width: 0;
  }
</style>

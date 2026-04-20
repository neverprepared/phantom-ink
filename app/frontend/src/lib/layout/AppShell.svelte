<script lang="ts">
  import TitleBar from './TitleBar.svelte';
  import Sidebar from './Sidebar.svelte';
  import StatusBar from './StatusBar.svelte';
  import { currentPanel } from '../stores.svelte';

  // Panels (lazy imports)
  import SessionsPanel from '../panels/SessionsPanel.svelte';
  import AgentsPanel from '../panels/AgentsPanel.svelte';
  import HubPanel from '../panels/HubPanel.svelte';
  import ReposPanel from '../panels/ReposPanel.svelte';
  import ServicesPanel from '../panels/ServicesPanel.svelte';
  import ObservabilityPanel from '../panels/ObservabilityPanel.svelte';
  import ChannelsPanel from '../panels/ChannelsPanel.svelte';
  import PlaybooksPanel from '../panels/PlaybooksPanel.svelte';
  import EventFeedPanel from '../panels/EventFeedPanel.svelte';
  import ProfilesPanel from '../panels/ProfilesPanel.svelte';
  import SettingsPanel from '../panels/SettingsPanel.svelte';
</script>

<div class="shell">
  <TitleBar />
  <div class="body">
    <Sidebar />
    <main class="content">
      {#if currentPanel.value === 'sessions'}
        <SessionsPanel />
      {:else if currentPanel.value === 'agents'}
        <AgentsPanel />
      {:else if currentPanel.value === 'dashboard'}
        <HubPanel />
      {:else if currentPanel.value === 'repos'}
        <ReposPanel />
      {:else if currentPanel.value === 'integrations'}
        <ServicesPanel />
      {:else if currentPanel.value === 'observability'}
        <ObservabilityPanel />
      {:else if currentPanel.value === 'channels'}
        <ChannelsPanel />
      {:else if currentPanel.value === 'playbooks'}
        <PlaybooksPanel />
      {:else if currentPanel.value === 'events'}
        <EventFeedPanel />
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

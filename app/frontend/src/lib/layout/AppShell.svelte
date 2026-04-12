<script lang="ts">
  import TitleBar from './TitleBar.svelte';
  import Sidebar from './Sidebar.svelte';
  import StatusBar from './StatusBar.svelte';
  import { currentPanel } from '../stores.svelte';

  // Panels (lazy imports)
  import SessionsPanel from '../panels/SessionsPanel.svelte';
  import HubPanel from '../panels/HubPanel.svelte';
  import ReposPanel from '../panels/ReposPanel.svelte';
  import PipelinesPanel from '../panels/PipelinesPanel.svelte';
  import ServicesPanel from '../panels/ServicesPanel.svelte';
  import ObservabilityPanel from '../panels/ObservabilityPanel.svelte';
  import ChannelsPanel from '../panels/ChannelsPanel.svelte';
  import EventFeedPanel from '../panels/EventFeedPanel.svelte';
  import SettingsPanel from '../panels/SettingsPanel.svelte';
</script>

<div class="shell">
  <TitleBar />
  <div class="body">
    <Sidebar />
    <main class="content">
      {#if currentPanel.value === 'sessions'}
        <SessionsPanel />
      {:else if currentPanel.value === 'dashboard'}
        <HubPanel />
      {:else if currentPanel.value === 'repos'}
        <ReposPanel />
      {:else if currentPanel.value === 'pipelines'}
        <PipelinesPanel />
      {:else if currentPanel.value === 'integrations'}
        <ServicesPanel />
      {:else if currentPanel.value === 'observability'}
        <ObservabilityPanel />
      {:else if currentPanel.value === 'channels'}
        <ChannelsPanel />
      {:else if currentPanel.value === 'events'}
        <EventFeedPanel />
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
    padding: 24px clamp(16px, 3vw, 32px);
    overflow-y: auto;
    min-width: 0;
  }
</style>

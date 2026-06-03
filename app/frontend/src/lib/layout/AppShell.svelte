<script lang="ts">
  import TitleBar from './TitleBar.svelte';
  import Sidebar from './Sidebar.svelte';
  import StatusBar from './StatusBar.svelte';
  import { currentPanel } from '../stores.svelte';

  // Panels (lazy imports)
  import SessionsPanel from '../panels/SessionsPanel.svelte';
  import ChainsPanel from '../panels/ChainsPanel.svelte';
  import ConversationsPanel from '../panels/ConversationsPanel.svelte';
  import ServicesPanel from '../panels/ServicesPanel.svelte';
  import PlaybooksPanel from '../panels/PlaybooksPanel.svelte';
  import ProfilesPanel from '../panels/ProfilesPanel.svelte';
  import SettingsPanel from '../panels/SettingsPanel.svelte';
  import DashboardPanel from '../panels/DashboardPanel.svelte';
  import TimelinePanel from '../panels/TimelinePanel.svelte';
  import RunnersPanel from '../panels/RunnersPanel.svelte';
</script>

<div class="shell">
  <TitleBar />
  <div class="body">
    <Sidebar />
    <main class="content">
      {#if currentPanel.value === 'timeline'}
        <TimelinePanel />
      {:else if currentPanel.value === 'sessions'}
        <SessionsPanel />
      {:else if currentPanel.value === 'runners'}
        <RunnersPanel />
      {:else if currentPanel.value === 'dashboard'}
        <DashboardPanel />
      {:else if currentPanel.value === 'integrations'}
        <ServicesPanel />
      {:else if currentPanel.value === 'chains'}
        <ChainsPanel />
      {:else if currentPanel.value === 'conversations'}
        <ConversationsPanel />
      {:else if currentPanel.value === 'playbooks'}
        <PlaybooksPanel />
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

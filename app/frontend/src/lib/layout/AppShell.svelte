<script lang="ts">
  import TitleBar from './TitleBar.svelte';
  import Sidebar from './Sidebar.svelte';
  import StatusBar from './StatusBar.svelte';
  import { currentPanel, attentionStore, profileState, integrationState, settingsState, SETTINGS_TAB_PANELS } from '../stores.svelte';
  import { onMount } from 'svelte';
  import { getApi } from '../utils/api';

  // Panels (lazy imports). Runners, Profiles, Gateway, and API tokens live
  // inside SettingsPanel as tabs, so they are imported there, not here.
  import SessionsPanel from '../panels/SessionsPanel.svelte';
  import LoopsPanel from '../panels/LoopsPanel.svelte';
  import ConversationsPanel from '../panels/ConversationsPanel.svelte';
  import ServicesPanel from '../panels/ServicesPanel.svelte';
  import PlaybooksPanel from '../panels/PlaybooksPanel.svelte';
  import JobsPanel from '../panels/JobsPanel.svelte';
  import AutomationsPanel from '../panels/AutomationsPanel.svelte';
  import SettingsPanel from '../panels/SettingsPanel.svelte';
  import DashboardPanel from '../panels/DashboardPanel.svelte';
  import StreamPanel from '../panels/StreamPanel.svelte';
  import EventLogPanel from '../panels/EventLogPanel.svelte';
  import FilesPanel from '../panels/FilesPanel.svelte';

  // Refresh integration flags so the sidebar can hide panels that
  // require unconfigured integrations (e.g. Files hidden until MinIO
  // is wired up). The operator's MinIO integration toggle gates first —
  // toggled off means hidden, no daemon probe; toggled on additionally
  // requires the daemon's /api/artifacts/health to report ok.
  async function refreshIntegrations() {
    try {
      const api = await getApi();
      if (!api) return;
      if (!(await api.MinioIntegrationEnabled())) {
        integrationState.minioEnabled = false;
        return;
      }
      const h = await api.GetArtifactsHealth();
      integrationState.minioEnabled = !!h?.ok;
    } catch {
      integrationState.minioEnabled = false;
    }
  }

  // If the Files panel is open when the integration flips off, bounce
  // to the dashboard rather than rendering a hidden panel.
  $effect(() => {
    if (currentPanel.value === 'files' && !integrationState.minioEnabled) {
      currentPanel.value = 'dashboard';
    }
  });

  // Runners/Profiles/Gateway/API tokens are now Settings tabs. Redirect any
  // stale navigation or legacy #hash deep-links to Settings with the matching
  // tab active. Converges immediately (once on 'settings' the map misses).
  $effect(() => {
    const tab = SETTINGS_TAB_PANELS[currentPanel.value];
    if (tab) {
      settingsState.tab = tab;
      currentPanel.value = 'settings';
    }
  });

  // Attention store powers the sidebar badge + Dashboard ActionItems fold-in.
  // Bootstrapping it here means the count is fresh on every panel, not only
  // while StreamPanel is mounted.
  onMount(() => {
    attentionStore.setWorkspace(profileState.active?.name ?? '');
    attentionStore.start();
    void refreshIntegrations();
    const t = window.setInterval(refreshIntegrations, 30_000);
    return () => window.clearInterval(t);
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
      {:else if currentPanel.value === 'dashboard'}
        <DashboardPanel />
      {:else if currentPanel.value === 'integrations'}
        <ServicesPanel />
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
      {:else if currentPanel.value === 'settings'}
        <SettingsPanel />
      {:else if currentPanel.value === 'event-log'}
        <EventLogPanel />
      {:else if currentPanel.value === 'files'}
        <FilesPanel />
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

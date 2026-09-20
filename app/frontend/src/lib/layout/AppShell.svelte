<script lang="ts">
  import TitleBar from './TitleBar.svelte';
  import Sidebar from './Sidebar.svelte';
  import StatusBar from './StatusBar.svelte';
  import { currentPanel, attentionStore, profileState, integrationState, settingsState, SETTINGS_TAB_PANELS, jobsState, JOBS_TAB_PANELS, streamFocus } from '../stores.svelte';
  import { onMount } from 'svelte';
  import { getApi } from '../utils/api';

  // Panels (lazy imports). Runners, Profiles, Gateway, and API tokens live
  // inside SettingsPanel as tabs, so they are imported there, not here.
  import SessionsPanel from '../panels/SessionsPanel.svelte';
  import ConversationsPanel from '../panels/ConversationsPanel.svelte';
  import ServicesPanel from '../panels/ServicesPanel.svelte';
  import JobsHubPanel from '../panels/JobsHubPanel.svelte';
  import CodePanel from '../panels/CodePanel.svelte';
  import MeshPanel from '../panels/MeshPanel.svelte';
  import MemoryGraphPanel from '../panels/MemoryGraphPanel.svelte';
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

  // Loops, Collectors, and the rules panel are now tabs under the Automations
  // hub (panel id still 'jobs'). Redirect any stale navigation or legacy
  // deep-link to the hub with the matching tab active.
  $effect(() => {
    const jt = JOBS_TAB_PANELS[currentPanel.value];
    if (jt) {
      jobsState.tab = jt;
      currentPanel.value = 'jobs';
    }
  });

  // Timeline is now a tab under the Stream panel. Redirect stale navigation to
  // Stream with the Timeline tab active (via the existing streamFocus signal,
  // which StreamPanel consumes on mount/effect).
  $effect(() => {
    if (currentPanel.value === 'timeline') {
      streamFocus.focus({ tab: 'timeline' });
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
      {:else if currentPanel.value === 'conversations'}
        <ConversationsPanel />
      {:else if currentPanel.value === 'jobs'}
        <JobsHubPanel />
      {:else if currentPanel.value === 'code'}
        <CodePanel />
      {:else if currentPanel.value === 'mesh'}
        <MeshPanel />
      {:else if currentPanel.value === 'memory-graph'}
        <MemoryGraphPanel />
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

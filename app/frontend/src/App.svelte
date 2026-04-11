<script lang="ts">
  import { onMount } from 'svelte';
  import AppShell from './lib/layout/AppShell.svelte';
  import CommandPalette from './lib/components/CommandPalette.svelte';
  import Notifications from './lib/components/Notifications.svelte';
  import { commandPalette, currentPanel, profileState, featureFlags } from './lib/stores.svelte';
  import { startEventListener } from './lib/events.svelte';

  import './styles/tokens.css';
  import './styles/base.css';
  import './styles/buttons.css';
  import './styles/components.css';

  onMount(async () => {
    // Detect platform and load profiles at startup.
    try {
      const mod = await import('../wailsjs/go/main/App');
      const [platform, cfg, scanned, active, services] = await Promise.all([
        mod.GetPlatform(),
        mod.GetConfig(),
        mod.ScanProfiles(),
        mod.GetActiveProfile(),
        mod.ListServices(),
      ]);
      document.documentElement.dataset.platform = platform;
      document.documentElement.dataset.theme = cfg.theme || 'dark';
      profileState.profiles = scanned ?? [];
      profileState.active = active?.name ? active : null;
      featureFlags.services = (services ?? []).map((s: any) => ({
        name: s.name,
        enabled: s.enabled,
        running: s.running,
      }));
    } catch {
      document.documentElement.dataset.platform = 'darwin';
    }

    const cleanup = startEventListener();

    function handleKeydown(e: KeyboardEvent) {
      const meta = e.metaKey || e.ctrlKey;
      if (!meta) return;

      if (e.key === 'k') {
        e.preventDefault();
        commandPalette.toggle();
        return;
      }

      const panelMap: Record<string, string> = {
        '1': 'dashboard',
        '2': 'sessions',
        '3': 'repos',
        '4': 'pipelines',
        '5': 'integrations',
        '6': 'observability',
        '7': 'events',
        ',': 'settings',
      };

      if (panelMap[e.key]) {
        e.preventDefault();
        currentPanel.value = panelMap[e.key];
      }
    }

    window.addEventListener('keydown', handleKeydown);

    return () => {
      cleanup();
      window.removeEventListener('keydown', handleKeydown);
    };
  });
</script>

<AppShell />
<CommandPalette />
<Notifications />

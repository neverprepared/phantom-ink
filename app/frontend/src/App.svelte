<script lang="ts">
  import { onMount } from 'svelte';
  import AppShell from './lib/layout/AppShell.svelte';
  import CommandPalette from './lib/components/CommandPalette.svelte';
  import Notifications from './lib/components/Notifications.svelte';
  import { commandPalette, currentPanel, profileState, profileColorStore, featureFlags } from './lib/stores.svelte';
  import { notifications } from './lib/notifications.svelte';
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

      // Load profile color overrides
      try {
        const colors = await mod.GetProfileColors();
        profileColorStore.overrides = colors ?? {};
      } catch {}

      // Run preflight checks and notify on issues
      try {
        const checks = await mod.RunPreflightChecks();
        for (const c of checks ?? []) {
          if (c.status === 'error') {
            notifications.error(c.message, 10000);
          } else if (c.status === 'warning') {
            notifications.warning(c.message, 8000);
          }
        }
      } catch {}

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
        '4': 'integrations',
        '5': 'observability',
        '6': 'channels',
        '7': 'playbooks',
        '8': 'events',
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

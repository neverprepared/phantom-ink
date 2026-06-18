<script lang="ts">
  import { onMount } from 'svelte';
  import AppShell from './lib/layout/AppShell.svelte';
  import CommandPalette from './lib/components/CommandPalette.svelte';
  import Notifications from './lib/components/Notifications.svelte';
  import { commandPalette, currentPanel, profileState, profileColorStore, featureFlags, refreshTick } from './lib/stores.svelte';
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
      document.documentElement.dataset.theme = (cfg.theme === 'muse' ? 'brew' : cfg.theme) || 'dark';
      profileState.profiles = scanned ?? [];
      profileState.active = active?.name ? active : null;
      // Load hidden-profile preferences (UI-only visibility).
      try {
        const hidden = await (mod as any).GetHiddenProfiles?.();
        profileState.hidden = new Set<string>(hidden ?? []);
      } catch {}
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

      // Vim-style panel cycling: ⌘[ jumps to the most-recent panel, ⌘] to
      // the oldest entry in the recency stack. Lets keyboard users avoid
      // memorizing numeric mappings.
      if (e.key === '[') {
        e.preventDefault();
        currentPanel.cyclePrev();
        return;
      }
      if (e.key === ']') {
        e.preventDefault();
        currentPanel.cycleNext();
        return;
      }

      const panelMap: Record<string, string> = {
        '1': 'dashboard',
        '2': 'sessions',
        '3': 'integrations',
        '4': 'sequences',
        '5': 'playbooks',
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

  $effect(() => {
    refreshTick.count;
    void (async () => {
      try {
        const mod = await import('../wailsjs/go/main/App');
        const services = await mod.ListServices();
        featureFlags.services = (services ?? []).map((s: any) => ({
          name: s.name,
          enabled: s.enabled,
          running: s.running,
        }));
      } catch {}
    })();
  });
</script>

<AppShell />
<CommandPalette />
<Notifications />

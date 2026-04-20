<script>
  import AppShell from './lib/AppShell.svelte';
  import Notifications from './lib/Notifications.svelte';
  import './styles/tokens.css';
</script>

<AppShell />
<Notifications />

<style>
  :global(*) { box-sizing: border-box; margin: 0; padding: 0; }
  :global(body) {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Inter, sans-serif;
    background: var(--color-bg-primary);
    color: var(--color-text-primary);
  }

  /* Focus indicators for keyboard navigation */
  :global(*:focus-visible) {
    outline: 2px solid var(--color-focus);
    outline-offset: 2px;
  }

  :global(button:focus-visible) {
    outline: 2px solid var(--color-focus);
    outline-offset: 2px;
  }

  :global(a:focus-visible) {
    outline: 2px solid var(--color-focus);
    outline-offset: 2px;
  }

  :global(input:focus-visible),
  :global(select:focus-visible) {
    outline: 2px solid var(--color-focus);
    outline-offset: 0;
  }
</style>

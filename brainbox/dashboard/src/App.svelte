<script>
  import AppShell from './lib/AppShell.svelte';
  import Notifications from './lib/Notifications.svelte';
</script>

<AppShell />
<Notifications />

<style>
  :global(:root) {
    /* Colors */
    --color-bg-primary: #0a0e1a;
    --color-bg-secondary: #111827;
    --color-bg-tertiary: #1e293b;
    --color-border-primary: #1e293b;
    --color-border-secondary: #334155;
    --color-text-primary: #e2e8f0;
    --color-text-secondary: #94a3b8;
    --color-text-tertiary: #64748b;
    --color-text-muted: #64748b;
    --color-accent: #f59e0b;
    --color-focus: #3b82f6;

    /* Status colors */
    --color-success: #10b981;
    --color-error: #ef4444;
    --color-warning: #f59e0b;
    --color-info: #3b82f6;

    /* Role colors */
    --color-role-developer: #3b82f6;
    --color-role-researcher: #a855f7;
    --color-role-performer: #f97316;

    /* LLM visibility colors */
    --color-llm-public: #ec4899;
    --color-llm-private: #22c55e;

    /* Spacing scale */
    --spacing-xs: 4px;
    --spacing-sm: 8px;
    --spacing-md: 12px;
    --spacing-lg: 16px;
    --spacing-xl: 20px;
    --spacing-2xl: 24px;

    /* Border radius */
    --radius-sm: 3px;
    --radius-md: 4px;
    --radius-lg: 6px;
    --radius-xl: 8px;
  }

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

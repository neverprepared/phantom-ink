<script lang="ts">
  import ServerRulesTab from '../components/ServerRulesTab.svelte';

  // Server-side rules (brainbox /api/rules — run 24/7 on the daemon). The
  // app-local automation engine (sequences/jobs/webhooks) has been removed;
  // this panel now just hosts the server rules surface. ServerRulesTab reads
  // the ruleSeed store directly (e.g. Stream's "create rule" hand-off).
</script>

<div class="automations">
  <div class="panel-header" style="margin-bottom:var(--spacing-sm);">
    <h1 class="page-title">automations</h1>
  </div>

  <ServerRulesTab />
</div>

<style>
  .automations {
    padding: var(--panel-padding);
    display: flex;
    flex-direction: column;
    gap: var(--spacing-md);
    min-height: 100%;
  }
</style>

<script lang="ts">
  import ServerRulesTab from '../components/ServerRulesTab.svelte';

  // Server-side rules (brainbox /api/rules — run 24/7 on the daemon). The
  // app-local automation engine (sequences/jobs/webhooks) has been removed;
  // this panel now just hosts the server rules surface. ServerRulesTab reads
  // the ruleSeed store directly (e.g. Stream's "create rule" hand-off).
</script>

<div class="automations">
  <div class="panel-header" style="margin-bottom:var(--spacing-sm);">
    <h1 class="page-title">rules</h1>
  </div>

  <ServerRulesTab />
</div>

<style>
  .automations {
    padding: var(--panel-padding);
    display: flex;
    flex-direction: column;
    gap: var(--spacing-md);

    /* This IS the scroll region. StreamPanel embeds this panel in a
       `.tab-embed` cell inside a `.panel { overflow: hidden }`, and its
       comment there says this panel "brings its own header and scroll
       region" -- but it never did: it was `min-height: 100%` with no
       `overflow`, so a tall RuleForm grew past the clipped cell and the
       bottom of the form (its save/cancel buttons) was unreachable.
       `flex: 1; min-height: 0` is the same idiom EventLogPanel's `.log`
       uses for the same reason. */
    flex: 1;
    min-height: 0;
    overflow-y: auto;
  }
</style>

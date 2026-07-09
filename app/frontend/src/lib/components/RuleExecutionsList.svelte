<script lang="ts">
  import { getApi } from '../utils/api';
  import { notifications } from '../notifications.svelte';
  import { executionStatusClass, relativeMs, type RuleExecution } from '../rules';

  // Execution history for one rule. refreshTick lets the parent's poll
  // re-trigger loads while the expander is open.
  let { ruleId, refreshTick = 0 }: { ruleId: string; refreshTick?: number } = $props();

  let executions = $state<RuleExecution[]>([]);
  let statusFilter = $state('');
  let loading = $state(false);

  async function load() {
    const api = await getApi();
    if (!api) return;
    loading = true;
    try {
      const res = await api.ListRuleExecutions(ruleId, statusFilter, 50, 0);
      executions = (res ?? []) as RuleExecution[];
    } catch (e: any) {
      notifications.error(`Failed to load executions: ${e?.message ?? e}`);
    } finally {
      loading = false;
    }
  }

  async function retry(ex: RuleExecution) {
    const api = await getApi();
    if (!api) return;
    try {
      await api.RetryRuleExecution(ex.id);
      await load();
    } catch (e: any) {
      notifications.error(`Retry failed: ${e?.message ?? e}`);
    }
  }

  const RETRYABLE = new Set(['failed', 'throttled', 'dead']);

  $effect(() => {
    refreshTick;
    statusFilter;
    void load();
  });
</script>

<div class="exec-list">
  <div class="exec-toolbar">
    <select class="exec-filter" bind:value={statusFilter}>
      <option value="">all statuses</option>
      {#each ['queued', 'running', 'ok', 'failed', 'throttled', 'dead'] as s (s)}
        <option value={s}>{s}</option>
      {/each}
    </select>
    {#if loading}<span class="exec-loading">…</span>{/if}
  </div>

  {#if executions.length === 0}
    <div class="exec-empty">{loading ? 'loading…' : 'no executions yet'}</div>
  {:else}
    {#each executions as ex (ex.id)}
      <div class="exec-row">
        <span class="exec-seq" title="event seq #{ex.event_seq} · {ex.event_id}">#{ex.event_seq}</span>
        <span class="exec-type">{ex.action_type}</span>
        <span class={executionStatusClass(ex.status)}>{ex.status}</span>
        <span class="exec-attempts" title="attempts">{ex.attempts}×</span>
        <span class="exec-when">{relativeMs(ex.updated_at)}</span>
        {#if ex.error}
          <span class="exec-error" title={ex.error}>{ex.error.length > 60 ? ex.error.slice(0, 60) + '…' : ex.error}</span>
        {/if}
        {#if RETRYABLE.has(ex.status)}
          <button class="exec-retry" onclick={() => retry(ex)}>retry</button>
        {/if}
      </div>
    {/each}
  {/if}
</div>

<style>
  .exec-list { display: flex; flex-direction: column; gap: 4px; }
  .exec-toolbar { display: flex; align-items: center; gap: var(--spacing-sm); }
  .exec-filter {
    background: var(--color-bg-primary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm);
    padding: 2px 6px;
    font-size: 10px;
    font-family: var(--font-mono);
    color: var(--color-text-secondary);
    cursor: pointer;
  }
  .exec-loading { font-size: 10px; color: var(--color-text-tertiary); }
  .exec-empty { font-size: 11px; color: var(--color-text-tertiary); padding: 4px 0; }

  .exec-row {
    display: flex;
    align-items: center;
    gap: var(--spacing-sm);
    font-family: var(--font-mono);
    font-size: 10px;
    padding: 3px 0;
    border-bottom: 1px solid var(--color-border-primary);
    flex-wrap: wrap;
  }
  .exec-row:last-child { border-bottom: none; }
  .exec-seq { color: var(--color-text-muted); }
  .exec-type { color: var(--color-text-secondary); }
  .exec-attempts { color: var(--color-text-tertiary); }
  .exec-when { color: var(--color-text-tertiary); }
  .exec-error { color: var(--color-error); min-width: 0; overflow: hidden; text-overflow: ellipsis; }
  .exec-retry {
    background: none;
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm);
    padding: 1px 6px;
    font-size: 10px;
    cursor: pointer;
    color: var(--color-text-muted);
    font-family: var(--font-mono);
    margin-left: auto;
  }
  .exec-retry:hover { border-color: var(--color-accent); color: var(--color-accent); }

  .pill {
    display: inline-block;
    padding: 1px 7px;
    font-size: 10px;
    font-weight: 600;
    border-radius: 10px;
    background: var(--color-bg-tertiary);
    color: var(--color-text-secondary);
  }
  .pill-good { background: rgba(16, 185, 129, 0.12); color: #10b981; }
  .pill-running { background: rgba(96, 165, 250, 0.12); color: #60a5fa; }
  .pill-bad { background: rgba(239, 68, 68, 0.12); color: #ef4444; }
</style>

<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { getApi } from '../utils/api';
  import { profileState, ruleSeed, type RuleSeed } from '../stores.svelte';
  import { notifications } from '../notifications.svelte';
  import Spinner from './Spinner.svelte';
  import EmptyState from './EmptyState.svelte';
  import CardExpander from './CardExpander.svelte';
  import RuleForm from './RuleForm.svelte';
  import RuleExecutionsList from './RuleExecutionsList.svelte';
  import {
    executionStatusClass,
    patternSummary,
    relativeMs,
    type Rule,
    type RuleExecution,
    type RulesStatus,
  } from '../rules';

  // Server-side event rules (brainbox /api/rules): rules run on the daemon
  // 24/7 — this tab is just the editor + audit view.

  const profile = $derived(profileState.active?.name ?? '');
  const allProfiles = $derived(profileState.profiles.map((p) => p.name));

  let rules = $state<Rule[]>([]);
  let loading = $state(true);
  let loadError = $state('');
  let editing = $state<Rule | 'new' | null>(null);
  let seed = $state<RuleSeed | null>(null); // pre-fill for a Stream-initiated new rule
  let rulesStatus = $state<RulesStatus | null>(null);
  let statusView = $state<string | null>(null); // open execution-filter panel
  let statusExecs = $state<RuleExecution[]>([]);
  let execTick = $state(0); // bumped by the poll to refresh open executions lists

  const POLL_MS = 5000;
  let pollHandle: ReturnType<typeof setInterval> | null = null;

  function normalize(r: any): Rule {
    return {
      ...r,
      description: r.description ?? '',
      pattern: r.pattern ?? {},
      actions: r.actions ?? [],
      last_triggered_at: r.last_triggered_at ?? null,
    };
  }

  async function load(silent = false) {
    const api = await getApi();
    if (!api) return;
    if (!silent) loading = true;
    try {
      const res = await api.ListRules(profile);
      rules = ((res ?? []) as any[]).map(normalize);
      loadError = '';
    } catch (e: any) {
      loadError = String(e?.message ?? e);
    } finally {
      loading = false;
    }
  }

  async function refreshStatus() {
    const api = await getApi();
    if (!api) return;
    try {
      rulesStatus = (await api.GetRulesStatus()) as RulesStatus;
      if (statusView !== null) {
        const res = await api.ListAllRuleExecutions(statusView, 2000, 0);
        statusExecs = (res ?? []) as RuleExecution[];
      }
    } catch {
      /* transient — next tick retries */
    }
  }

  function toggleStatusView(status: string) {
    statusView = statusView === status ? null : status;
    statusExecs = [];
    if (statusView !== null) void refreshStatus();
  }

  async function toggle(rule: Rule) {
    const api = await getApi();
    if (!api) return;
    try {
      await api.SetRuleEnabled(rule.id, !rule.enabled);
      await load(true);
    } catch (e: any) {
      notifications.error(`Failed to toggle rule: ${e?.message ?? e}`);
    }
  }

  async function remove(rule: Rule) {
    if (!confirm(`Delete rule "${rule.name}"? Its execution history is kept.`)) return;
    const api = await getApi();
    if (!api) return;
    try {
      await api.DeleteRule(rule.id);
      await load(true);
    } catch (e: any) {
      notifications.error(`Failed to delete rule: ${e?.message ?? e}`);
    }
  }

  const RETRYABLE = new Set(['failed', 'throttled', 'dead']);

  async function retryFromPanel(ex: RuleExecution) {
    const api = await getApi();
    if (!api) return;
    try {
      await api.RetryRuleExecution(ex.id);
      await refreshStatus();
    } catch (e: any) {
      notifications.error(`Retry failed: ${e?.message ?? e}`);
    }
  }

  function ruleName(ruleId: string): string {
    return rules.find((r) => r.id === ruleId)?.name ?? ruleId;
  }

  function actionTypes(rule: Rule): string[] {
    return [...new Set(rule.actions.map((a) => String(a.type ?? '?')))];
  }

  function onSaved() {
    editing = null;
    seed = null;
    void load(true);
  }

  function cancelEdit() {
    editing = null;
    seed = null;
  }

  // Consume a rule seed handed off from Stream's "create rule" — open the
  // new-rule form pre-filled. Reads ruleSeed.value reactively so it fires
  // whether this tab is freshly mounted or already alive when the seed lands.
  $effect(() => {
    if (ruleSeed.value) {
      seed = ruleSeed.consume();
      editing = 'new';
    }
  });

  onMount(() => {
    void load();
    void refreshStatus();
    pollHandle = setInterval(() => {
      void load(true);
      void refreshStatus();
      execTick += 1;
    }, POLL_MS);
  });

  onDestroy(() => {
    if (pollHandle !== null) clearInterval(pollHandle);
  });

  $effect(() => {
    profile;
    void load(true);
  });
</script>

<div class="server-rules">
  <div class="sr-toolbar">
    <span class="sr-note">rules run on the brainbox daemon — active even when this app is closed</span>
    <div class="sr-toolbar-right">
      {#if loading}<Spinner />{/if}
      {#if rulesStatus}
        <div class="status-strip">
          {#each (['queued', 'running', 'throttled', 'dead'] as const) as s (s)}
            <button
              class="status-chip"
              class:alert={s === 'dead' && rulesStatus.counts.dead > 0}
              class:active={statusView === s}
              onclick={() => toggleStatusView(s)}
              title="{s} executions across all rules">
              {s} {rulesStatus.counts[s]}
            </button>
          {/each}
          <span class="status-lag" class:alert={rulesStatus.lag > 50}
            title="events waiting for the rules consumer (cursor {rulesStatus.cursor} / head {rulesStatus.head_seq})">
            lag {rulesStatus.lag}
          </span>
          {#if rulesStatus.sink.enabled}
            <span class="status-lag" class:alert={!!rulesStatus.sink.last_error}
              title={rulesStatus.sink.last_error || `events not yet indexed to OpenSearch (cursor ${rulesStatus.sink.cursor})`}>
              sink {rulesStatus.sink.lag}
            </span>
          {/if}
        </div>
      {/if}
      {#if editing === null}
        <button class="btn primary" onclick={() => (editing = 'new')}>+ new rule</button>
      {/if}
    </div>
  </div>

  {#if statusView !== null}
    <div class="status-panel" class:alert={statusView === 'dead'}>
      {#if statusExecs.length === 0}
        <div class="sr-empty">no {statusView} executions</div>
      {:else}
        {#each statusExecs as ex (ex.id)}
          <div class="status-row">
            <span class="srow-rule">{ruleName(ex.rule_id)}</span>
            <span class="srow-type">{ex.action_type}</span>
            <span class={executionStatusClass(ex.status)}>{ex.status}</span>
            <span class="srow-when">{relativeMs(ex.updated_at)}</span>
            {#if ex.error}
              <span class="srow-error" title={ex.error}>{ex.error.length > 70 ? ex.error.slice(0, 70) + '…' : ex.error}</span>
            {/if}
            {#if RETRYABLE.has(ex.status)}
              <button class="srow-retry" onclick={() => retryFromPanel(ex)}>retry</button>
            {/if}
          </div>
        {/each}
      {/if}
    </div>
  {/if}

  {#if editing === 'new'}
    <RuleForm rule={null} {seed} activeProfile={profile} {allProfiles}
      onSaved={onSaved} onCancel={cancelEdit} />
  {/if}

  {#if loadError}
    <div class="sr-error">cannot reach the rules API: {loadError}</div>
  {:else if loading && rules.length === 0}
    <div class="sr-empty">loading…</div>
  {:else if rules.length === 0 && editing === null}
    <EmptyState title="No server rules"
      message="Rules match events on the brainbox bus and run actions — even while this app is closed." />
  {:else}
    <div class="sr-list">
      {#each rules as rule (rule.id)}
        <div class="sr-card" class:editing={editing !== 'new' && editing !== null && editing.id === rule.id}>
          {#if editing !== null && editing !== 'new' && editing.id === rule.id}
            <RuleForm rule={editing} activeProfile={profile} {allProfiles}
              onSaved={onSaved} onCancel={() => (editing = null)} />
          {:else}
            <div class="sr-row">
              <button class="sr-toggle" class:on={rule.enabled} onclick={() => toggle(rule)}
                title={rule.enabled ? 'disable' : 'enable'}>
                {rule.enabled ? '●' : '○'}
              </button>
              <div class="sr-info" role="button" tabindex="0"
                onclick={() => (editing = structuredClone($state.snapshot(rule)))}
                onkeydown={(e) => e.key === 'Enter' && (editing = structuredClone($state.snapshot(rule)))}>
                <span class="sr-name">{rule.name}</span>
                <div class="sr-meta-row">
                  <span class="sr-profile">{rule.profile || 'global'}</span>
                  <span class="sr-pattern">{patternSummary(rule.pattern)}</span>
                </div>
                <div class="sr-meta-row">
                  {#each actionTypes(rule) as t (t)}
                    <span class="sr-badge type-{t}">{t.replace('_', ' ')}</span>
                  {/each}
                  {#if rule.description}
                    <span class="sr-desc">{rule.description}</span>
                  {/if}
                </div>
              </div>
              <div class="sr-stats">
                {#if rule.trigger_count > 0}
                  <span class="sr-count" title="times triggered">{rule.trigger_count}×</span>
                {/if}
                <span class="sr-last">{relativeMs(rule.last_triggered_at)}</span>
                <button class="sr-btn danger" onclick={() => remove(rule)} title="delete">✕</button>
              </div>
            </div>
            <div class="sr-expander">
              <CardExpander label="executions">
                <RuleExecutionsList ruleId={rule.id} refreshTick={execTick} />
              </CardExpander>
            </div>
          {/if}
        </div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .server-rules { display: flex; flex-direction: column; gap: var(--spacing-md); }

  .sr-toolbar { display: flex; align-items: center; justify-content: space-between; gap: var(--spacing-md); }
  .sr-note { font-family: var(--font-mono); font-size: 10px; color: var(--color-text-tertiary); }
  .sr-toolbar-right { display: flex; align-items: center; gap: var(--spacing-md); }

  .status-strip { display: flex; align-items: center; gap: 4px; flex-wrap: wrap; }
  .status-chip {
    font-family: var(--font-mono); font-size: 10px;
    padding: 2px 8px; border-radius: 999px;
    border: 1px solid var(--color-border-primary);
    background: none; color: var(--color-text-muted); cursor: pointer;
  }
  .status-chip:hover { color: var(--color-text-secondary); }
  .status-chip.alert { color: var(--color-error); border-color: rgba(239,68,68,0.4); background: rgba(239,68,68,0.06); }
  .status-chip.active { border-color: var(--color-accent); color: var(--color-accent); }
  .status-lag {
    font-family: var(--font-mono); font-size: 10px;
    padding: 2px 8px; color: var(--color-text-tertiary);
  }
  .status-lag.alert { color: var(--color-error); }

  .status-panel {
    display: flex; flex-direction: column; gap: 4px;
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-md);
    padding: var(--spacing-md);
    background: var(--color-bg-secondary);
  }
  .status-panel.alert { border-color: rgba(239,68,68,0.25); }
  .status-row {
    display: flex; align-items: center; gap: var(--spacing-sm);
    font-family: var(--font-mono); font-size: 10px;
    padding: 3px 0; border-bottom: 1px solid var(--color-border-primary);
    flex-wrap: wrap;
  }
  .status-row:last-child { border-bottom: none; }
  .srow-rule { color: var(--color-text-primary); }
  .srow-type { color: var(--color-text-secondary); }
  .srow-when { color: var(--color-text-tertiary); }
  .srow-error { color: var(--color-error); min-width: 0; overflow: hidden; text-overflow: ellipsis; }
  .srow-retry {
    background: none; border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm); padding: 1px 6px;
    font-size: 10px; cursor: pointer; color: var(--color-text-muted);
    font-family: var(--font-mono); margin-left: auto;
  }
  .srow-retry:hover { border-color: var(--color-accent); color: var(--color-accent); }

  .sr-empty { font-size: 13px; color: var(--color-text-tertiary); padding: var(--spacing-lg) 0; }
  .sr-error { font-family: var(--font-mono); font-size: 11px; color: var(--color-error); }

  .sr-list { display: flex; flex-direction: column; gap: var(--spacing-sm); }
  .sr-card {
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-md);
    background: var(--color-bg-secondary);
    overflow: hidden; transition: border-color 100ms;
  }
  .sr-card:hover { border-color: var(--color-border-secondary); }
  .sr-card.editing { border-color: var(--color-accent); }

  .sr-row {
    display: grid; grid-template-columns: 24px 1fr auto;
    align-items: start; gap: var(--spacing-md);
    padding: var(--spacing-md) var(--spacing-lg) 4px;
  }
  .sr-expander { padding: 0 var(--spacing-lg) var(--spacing-sm) calc(var(--spacing-lg) + 24px + var(--spacing-md)); }

  .sr-toggle {
    background: none; border: none; cursor: pointer;
    font-size: 14px; font-family: var(--font-mono);
    color: var(--color-text-muted); padding: 0; line-height: 1.6;
  }
  .sr-toggle.on { color: var(--color-success); }
  .sr-toggle:hover { opacity: 0.7; }

  .sr-info { display: flex; flex-direction: column; gap: 4px; min-width: 0; cursor: pointer; }
  .sr-name { font-size: 13px; font-weight: 500; color: var(--color-text-primary); }
  .sr-meta-row { display: flex; gap: var(--spacing-sm); align-items: center; flex-wrap: wrap; }
  .sr-pattern { font-family: var(--font-mono); font-size: 10px; color: var(--color-text-tertiary); }
  .sr-desc { font-size: 10px; color: var(--color-text-tertiary); }
  .sr-profile {
    font-family: var(--font-mono); font-size: 10px;
    color: var(--color-accent); background: rgba(234,179,8,0.08);
    border: 1px solid rgba(234,179,8,0.2); border-radius: 999px; padding: 1px 6px;
  }

  .sr-badge {
    font-family: var(--font-mono); font-size: 10px;
    padding: 1px 6px; border-radius: 999px;
    border: 1px solid var(--color-border-primary);
    color: var(--color-text-muted);
  }
  .sr-badge.type-submit_task  { color: var(--color-accent); border-color: rgba(234,179,8,0.3); background: rgba(234,179,8,0.06); }
  .sr-badge.type-start_loop   { color: #f59e0b; border-color: rgba(245,158,11,0.3); background: rgba(245,158,11,0.06); }
  .sr-badge.type-webhook      { color: #60a5fa; border-color: rgba(96,165,250,0.3); background: rgba(96,165,250,0.06); }
  .sr-badge.type-run_script   { color: #f472b6; border-color: rgba(244,114,182,0.3); background: rgba(244,114,182,0.06); }

  .sr-stats { display: flex; flex-direction: column; align-items: flex-end; gap: 4px; flex-shrink: 0; }
  .sr-count { font-family: var(--font-mono); font-size: 10px; color: var(--color-text-secondary); }
  .sr-last { font-family: var(--font-mono); font-size: 10px; color: var(--color-text-tertiary); }
  .sr-btn {
    background: none; border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm); padding: 2px 6px;
    font-size: 10px; cursor: pointer; color: var(--color-text-muted);
    font-family: var(--font-mono);
  }
  .sr-btn.danger:hover { border-color: var(--color-error); color: var(--color-error); }

  .pill {
    display: inline-block; padding: 1px 7px;
    font-size: 10px; font-weight: 600; border-radius: 10px;
    background: var(--color-bg-tertiary); color: var(--color-text-secondary);
  }
  .pill-good { background: rgba(16,185,129,0.12); color: #10b981; }
  .pill-running { background: rgba(96,165,250,0.12); color: #60a5fa; }
  .pill-bad { background: rgba(239,68,68,0.12); color: #ef4444; }
</style>

<script lang="ts">
  import { getApi } from '../utils/api';
  import { profileState } from '../stores.svelte';
  import { notifications } from '../notifications.svelte';
  import Icon from '../components/Icon.svelte';

  const AGENTS = ['supervisor','worker','reviewer','linter','qa','python','golang','typescript','assistant'];

  let dispatchAgent = $state('supervisor');
  let dispatchDesc  = $state('');
  let dispatchRepo  = $state('');
  let dispatching   = $state(false);

  async function handleDispatch() {
    if (!dispatchDesc.trim()) return;
    dispatching = true;
    try {
      const a = await getApi();
      if (!a) return;
      await a.SubmitTask({
        description: dispatchDesc.trim(),
        agent_name: dispatchAgent,
        repo_url: dispatchRepo.trim() || undefined,
        workspace_profile: profileState.active?.name ?? '',
      });
      dispatchDesc = '';
      dispatchRepo = '';
      notifications.success('Task dispatched');
    } catch (err: any) {
      notifications.error(`Dispatch failed: ${err?.message ?? err}`);
    } finally {
      dispatching = false;
    }
  }
</script>

<div class="widget">
  <div class="widget-header widget-drag-handle">
    <Icon name="bolt" size={15} style="color: var(--text-muted); flex-shrink: 0;" />
    <span class="widget-title">» DISPATCH AGENT</span>
  </div>
  <div class="widget-body">
    <div class="dispatch-row">
      <select bind:value={dispatchAgent} class="dispatch-select">
        {#each AGENTS as a (a)}
          <option value={a}>{a}</option>
        {/each}
      </select>
      <input
        class="dispatch-repo"
        bind:value={dispatchRepo}
        placeholder="repo url (optional)"
      />
      <button
        class="dispatch-btn"
        onclick={handleDispatch}
        disabled={dispatching || !dispatchDesc.trim()}
      >{dispatching ? '…' : '[ run ]'}</button>
    </div>
    <textarea
      class="dispatch-desc"
      bind:value={dispatchDesc}
      rows="3"
      placeholder="describe the task…"
      onkeydown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) handleDispatch(); }}
    ></textarea>
  </div>
</div>

<style>
  .widget {
    display: flex;
    flex-direction: column;
    height: 100%;
  }

  .widget-header {
    display: flex;
    align-items: center;
    gap: var(--spacing-sm);
    padding: var(--spacing-sm) var(--spacing-md);
    border-bottom: 1px solid var(--color-border-primary);
    cursor: grab;
    flex-shrink: 0;
  }

  .widget-title {
    font-family: var(--font-mono);
    font-size: 11px;
    letter-spacing: 0.08em;
    color: var(--color-text-secondary);
  }

  .widget-body {
    flex: 1;
    overflow-y: auto;
    padding: var(--spacing-md);
    display: flex;
    flex-direction: column;
    gap: var(--spacing-sm);
  }

  .dispatch-row {
    display: flex;
    gap: var(--spacing-sm);
    align-items: center;
  }

  .dispatch-select,
  .dispatch-repo {
    font-family: var(--font-mono);
    font-size: 12px;
    background: var(--color-bg-primary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm);
    color: var(--color-text-primary);
    padding: 5px 9px;
  }
  .dispatch-select { flex-shrink: 0; cursor: pointer; }
  .dispatch-repo { flex: 1; min-width: 0; }
  .dispatch-repo::placeholder { color: var(--color-text-muted); }
  .dispatch-select:focus,
  .dispatch-repo:focus { outline: 1px solid var(--color-accent); border-color: var(--color-accent); }

  .dispatch-desc {
    width: 100%;
    flex: 1;
    font-family: var(--font-mono);
    font-size: 12px;
    background: var(--color-bg-primary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm);
    color: var(--color-text-primary);
    padding: 6px 9px;
    resize: none;
    box-sizing: border-box;
    min-height: 60px;
  }
  .dispatch-desc::placeholder { color: var(--color-text-muted); }
  .dispatch-desc:focus { outline: 1px solid var(--color-accent); border-color: var(--color-accent); }

  .dispatch-btn {
    font-family: var(--font-mono);
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.04em;
    color: var(--color-accent);
    background: transparent;
    border: 1px solid var(--color-accent);
    border-radius: var(--radius-sm);
    padding: 5px 14px;
    flex-shrink: 0;
    transition: background 120ms ease;
    cursor: pointer;
  }
  .dispatch-btn:hover:not(:disabled) { background: rgba(234, 179, 8, 0.08); }
  .dispatch-btn:disabled { opacity: 0.35; cursor: not-allowed; }
</style>

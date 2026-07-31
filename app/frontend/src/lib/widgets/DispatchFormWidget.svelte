<script lang="ts">
  import { onMount } from 'svelte';
  import { getApi } from '../utils/api';
  import { profileState } from '../stores.svelte';
  import { notifications } from '../notifications.svelte';
  import Icon from '../components/Icon.svelte';

  const AGENTS = ['supervisor','worker','reviewer','linter','qa','python','golang','typescript','assistant'];

  // Dispatch history is stored per profile in localStorage. Click a chip to
  // dispatch the same task again; shift-click to edit before sending. Cap
  // intentionally small — older entries fade off the chip strip.
  const HISTORY_KEY = 'pi-dispatch-history-v1';
  const HISTORY_MAX = 5;
  interface DispatchEntry {
    agent: string;
    description: string;
    repo: string;
    profile: string;
    ts: number;
  }

  let dispatchAgent = $state('supervisor');
  let dispatchDesc  = $state('');
  let dispatchRepo  = $state('');
  let dispatchPool  = $state('');   // '' = any (global fleet)
  let dispatching   = $state(false);
  let history       = $state<DispatchEntry[]>(loadHistory());
  let pools         = $state<{ name: string }[]>([]);

  onMount(async () => {
    const a = await getApi();
    if (!a) return;
    try { pools = ((await a.ListPools()) ?? []) as { name: string }[]; } catch { /* no fleet */ }
  });

  let activeProfile = $derived(profileState.active?.name ?? '');
  let profileHistory = $derived(
    history.filter(h => h.profile === activeProfile).slice(0, HISTORY_MAX)
  );

  function loadHistory(): DispatchEntry[] {
    if (typeof localStorage === 'undefined') return [];
    try {
      const raw = localStorage.getItem(HISTORY_KEY);
      return raw ? (JSON.parse(raw) as DispatchEntry[]) : [];
    } catch {
      return [];
    }
  }

  function saveHistory(next: DispatchEntry[]): void {
    history = next;
    try { localStorage.setItem(HISTORY_KEY, JSON.stringify(next)); } catch {}
  }

  function recordDispatch(entry: DispatchEntry): void {
    // Drop any prior copy with the same agent + description for this profile
    // to keep the chip strip dense and avoid stuttering "did I just send that?"
    const filtered = history.filter(
      h => !(h.profile === entry.profile && h.agent === entry.agent && h.description === entry.description)
    );
    saveHistory([entry, ...filtered].slice(0, HISTORY_MAX * 4));
  }

  function summarize(d: string): string {
    const s = d.trim().split('\n')[0];
    return s.length > 36 ? s.slice(0, 35) + '…' : s;
  }

  function loadIntoForm(entry: DispatchEntry): void {
    dispatchAgent = entry.agent;
    dispatchDesc  = entry.description;
    dispatchRepo  = entry.repo;
  }

  async function replay(entry: DispatchEntry, evt: MouseEvent): Promise<void> {
    if (evt.shiftKey) {
      // Edit-then-send: just hydrate the form and let the user tweak before
      // hitting [run].
      loadIntoForm(entry);
      return;
    }
    if (dispatching) return;
    dispatching = true;
    try {
      const a = await getApi();
      if (!a) return;
      await a.SubmitTask({
        description: entry.description,
        agent_name: entry.agent,
        repo_url: entry.repo || undefined,
        workspace_profile: entry.profile || profileState.active?.name || '',
      });
      recordDispatch({ ...entry, ts: Date.now() });
      notifications.success('Task re-dispatched');
    } catch (err: any) {
      notifications.error(`Dispatch failed: ${err?.message ?? err}`);
    } finally {
      dispatching = false;
    }
  }

  async function handleDispatch() {
    if (!dispatchDesc.trim()) return;
    dispatching = true;
    try {
      const a = await getApi();
      if (!a) return;
      const entry: DispatchEntry = {
        agent: dispatchAgent,
        description: dispatchDesc.trim(),
        repo: dispatchRepo.trim(),
        profile: profileState.active?.name ?? '',
        ts: Date.now(),
      };
      await a.SubmitTask({
        description: entry.description,
        agent_name: entry.agent,
        repo_url: entry.repo || undefined,
        workspace_profile: entry.profile,
        pool: dispatchPool || undefined,
      });
      recordDispatch(entry);
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
    {#if profileHistory.length > 0}
      <div class="recent-row" title="Click to re-dispatch · Shift-click to edit">
        {#each profileHistory as entry (entry.ts)}
          <button
            class="recent-chip"
            onclick={(e) => replay(entry, e)}
            disabled={dispatching}
            title={`${entry.agent} · ${entry.description}`}>
            <span class="recent-agent">{entry.agent}</span>
            <span class="recent-desc">{summarize(entry.description)}</span>
          </button>
        {/each}
      </div>
    {/if}
    <div class="dispatch-row">
      <select bind:value={dispatchAgent} class="dispatch-select">
        {#each AGENTS as a (a)}
          <option value={a}>{a}</option>
        {/each}
      </select>
      {#if pools.length}
        <select bind:value={dispatchPool} class="dispatch-select" title="route to a machine-class pool (blank = whole fleet)">
          <option value="">any pool</option>
          {#each pools as p (p.name)}
            <option value={p.name}>{p.name}</option>
          {/each}
        </select>
      {/if}
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

  .recent-row {
    display: flex;
    gap: 6px;
    overflow-x: auto;
    padding-bottom: 4px;
    border-bottom: 1px dashed var(--color-border-primary);
    margin-bottom: 2px;
  }
  .recent-chip {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    background: var(--color-bg-primary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm);
    color: var(--color-text-secondary);
    font-family: var(--font-mono);
    font-size: 10.5px;
    padding: 2px 8px;
    cursor: pointer;
    flex-shrink: 0;
    transition: background 120ms ease;
  }
  .recent-chip:hover:not(:disabled) {
    background: var(--color-surface-hover);
    color: var(--color-text-primary);
  }
  .recent-chip:disabled { opacity: 0.4; cursor: not-allowed; }
  .recent-agent {
    color: var(--color-accent);
    font-weight: 600;
  }
  .recent-desc {
    max-width: 220px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
</style>

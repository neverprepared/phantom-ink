<script lang="ts">
  import { getApi } from '../utils/api';
  import { onMount } from 'svelte';
  import { brainboxEvents } from '../events.svelte';
  import { notifications } from '../notifications.svelte';
  import { profileState } from '../stores.svelte';
  import Modal from '../components/Modal.svelte';
  import EmptyState from '../components/EmptyState.svelte';

  interface PlaybookTask {
    id: string;
    index: number;
    content: string;
    status: string; // "pending", "running", "completed", "failed"
    session_name?: string;
    output?: string;
    error?: string;
    started_at?: number;
    finished_at?: number;
  }

  interface Playbook {
    id: string;
    name: string;
    markdown: string;
    tasks: PlaybookTask[];
    status: string; // "idle", "running", "completed", "failed", "cancelled"
    workspace_profile: string;
    created_at: number;
    started_at?: number;
    finished_at?: number;
  }

  // --- State ---
  let playbooks = $state<Playbook[]>([]);
  let selected = $state<Playbook | null>(null);
  let loading = $state(true);
  let expandedTasks = $state<Set<string>>(new Set());
  let confirmingDelete = $state(false);

  // Create modal
  let showCreateModal = $state(false);
  let newName = $state('');
  let markdownDraft = $state(`- [ ] Step one: describe what to do\n- [ ] Step two: next action\n- [ ] Step three: final check`);
  let newScope = $state<'profile' | 'global'>('profile');
  let isCreating = $state(false);

  const activeProfileName = $derived(profileState.active?.name ?? '');

  // --- SSE ---
  $effect(() => {
    const lastEvent = brainboxEvents.last;
    if (!lastEvent) return;
    try {
      const parsed = typeof lastEvent === 'string' ? JSON.parse(lastEvent) : lastEvent;
      const action = parsed?.action ?? parsed?.event;
      if (
        action === 'playbook.created' ||
        action === 'playbook.deleted' ||
        action === 'playbook.started' ||
        action === 'playbook.completed' ||
        action === 'playbook.failed' ||
        action === 'playbook.cancelled'
      ) {
        loadPlaybooks();
      } else if (action === 'playbook.task_started' || action === 'playbook.task_done') {
        const pid = parsed?.data?.playbook_id;
        if (pid && selected?.id === pid) {
          refreshSelected();
        }
      }
    } catch {}
  });

  // --- API ---
  async function loadPlaybooks() {
    try {
      const api = await getApi();
      // Pass active profile so API returns profile's playbooks + global ones
      const result = await api.ListPlaybooks(activeProfileName);
      playbooks = result ?? [];
      // Refresh selected if it's in the list
      if (selected) {
        const updated = playbooks.find(p => p.id === selected!.id);
        if (updated) selected = updated;
      }
    } catch (e: any) {
      notifications.add('error', `Failed to load playbooks: ${e.message ?? e}`);
    } finally {
      loading = false;
    }
  }

  async function refreshSelected() {
    if (!selected) return;
    try {
      const api = await getApi();
      selected = await api.GetPlaybook(selected.id);
      // Sync into list
      const idx = playbooks.findIndex(p => p.id === selected!.id);
      if (idx >= 0) playbooks[idx] = selected;
    } catch {}
  }

  async function createPlaybook() {
    if (!newName.trim() || !markdownDraft.trim()) return;
    isCreating = true;
    try {
      const api = await getApi();
      const profile = newScope === 'global' ? 'global' : (activeProfileName || 'global');
      const pb = await api.CreatePlaybook({
        name: newName.trim(),
        markdown: markdownDraft,
        workspace_profile: profile,
      });
      playbooks = [pb, ...playbooks];
      selected = pb;
      showCreateModal = false;
      newName = '';
      markdownDraft = `- [ ] Step one: describe what to do\n- [ ] Step two: next action\n- [ ] Step three: final check`;
    } catch (e: any) {
      notifications.add('error', `Failed to create playbook: ${e.message ?? e}`);
    } finally {
      isCreating = false;
    }
  }

  async function runPlaybook() {
    if (!selected) return;
    try {
      const api = await getApi();
      selected = await api.RunPlaybook(selected.id);
      const idx = playbooks.findIndex(p => p.id === selected!.id);
      if (idx >= 0) playbooks[idx] = selected;
    } catch (e: any) {
      notifications.add('error', `Failed to run playbook: ${e.message ?? e}`);
    }
  }

  async function cancelPlaybook() {
    if (!selected) return;
    try {
      const api = await getApi();
      await api.CancelPlaybook(selected.id);
      await refreshSelected();
    } catch (e: any) {
      notifications.add('error', `Failed to cancel playbook: ${e.message ?? e}`);
    }
  }

  async function deleteSelected() {
    if (!selected) return;
    const id = selected.id;
    confirmingDelete = false;
    try {
      const api = await getApi();
      await api.DeletePlaybook(id);
      playbooks = playbooks.filter(p => p.id !== id);
      selected = null;
    } catch (e: any) {
      notifications.add('error', `Failed to delete playbook: ${e.message ?? e}`);
    }
  }

  function toggleTask(taskId: string) {
    const next = new Set(expandedTasks);
    if (next.has(taskId)) next.delete(taskId);
    else next.add(taskId);
    expandedTasks = next;
  }

  function statusIcon(status: string): string {
    switch (status) {
      case 'completed': return '✓';
      case 'running': return '▶';
      case 'failed': return '✗';
      case 'cancelled': return '⊘';
      default: return '○';
    }
  }

  function statusClass(status: string): string {
    switch (status) {
      case 'completed': return 'status-completed';
      case 'running': return 'status-running';
      case 'failed': return 'status-failed';
      case 'cancelled': return 'status-cancelled';
      default: return 'status-pending';
    }
  }

  function formatDuration(started?: number, finished?: number): string {
    if (!started) return '';
    const end = finished ?? Date.now();
    const secs = Math.round((end - started) / 1000);
    if (secs < 60) return `${secs}s`;
    return `${Math.floor(secs / 60)}m ${secs % 60}s`;
  }

  // Reload when the active profile changes
  $effect(() => {
    const _ = activeProfileName; // track
    loadPlaybooks();
  });

  onMount(() => {
    // initial load handled by $effect above
  });
</script>

<div class="panel">
  <!-- Left sidebar: playbook list -->
  <div class="sidebar">
    <div class="sidebar-header">
      <span class="sidebar-title">Playbooks</span>
      <button class="icon-btn" onclick={() => { showCreateModal = true; newScope = activeProfileName ? 'profile' : 'global'; newName = ''; markdownDraft = `- [ ] Step one: describe what to do\n- [ ] Step two: next action\n- [ ] Step three: final check`; }} title="New playbook">+</button>
    </div>

    {#if loading}
      <div class="sidebar-empty">Loading…</div>
    {:else if playbooks.length === 0}
      <div class="sidebar-empty">No playbooks yet</div>
    {:else}
      <ul class="playbook-list">
        {#each playbooks as pb (pb.id)}
          <li
            class="playbook-item {selected?.id === pb.id ? 'selected' : ''}"
            onclick={() => { selected = pb; expandedTasks = new Set(); confirmingDelete = false; }}
          >
            <div class="playbook-item-row">
              <span class="playbook-name">{pb.name}</span>
              <span class="playbook-status-badge {statusClass(pb.status)}">{pb.status}</span>
            </div>
            <div class="playbook-meta">
              {pb.tasks.length} task{pb.tasks.length !== 1 ? 's' : ''}
              · {pb.workspace_profile === 'global' ? 'global' : pb.workspace_profile}
            </div>
          </li>
        {/each}
      </ul>
    {/if}
  </div>

  <!-- Right detail pane -->
  <div class="detail">
    {#if !selected}
      <EmptyState message="Select a playbook or create a new one" />
    {:else}
      <div class="detail-header">
        <div class="detail-title-row">
          <h2 class="detail-title">{selected.name}</h2>
          <span class="detail-status {statusClass(selected.status)}">{selected.status}</span>
          {#if selected.workspace_profile !== 'global'}
            <span class="detail-profile">{selected.workspace_profile}</span>
          {/if}
        </div>
        <div class="detail-meta">
          {selected.tasks.length} task{selected.tasks.length !== 1 ? 's' : ''}
          {#if selected.started_at}
            · {formatDuration(selected.started_at, selected.finished_at)}
          {/if}
        </div>
      </div>

      <div class="task-list">
        {#each selected.tasks as task (task.id)}
          <div class="task-row {statusClass(task.status)}">
            <div class="task-header" onclick={() => task.output || task.error ? toggleTask(task.id) : null}>
              <span class="task-icon">{statusIcon(task.status)}</span>
              <span class="task-content">{task.content}</span>
              {#if task.started_at}
                <span class="task-duration">{formatDuration(task.started_at, task.finished_at)}</span>
              {/if}
              {#if (task.output || task.error) && task.status !== 'running'}
                <span class="task-expand">{expandedTasks.has(task.id) ? '▲' : '▼'}</span>
              {/if}
            </div>
            {#if expandedTasks.has(task.id)}
              <div class="task-output">
                {#if task.error}
                  <pre class="task-error">{task.error}</pre>
                {:else if task.output}
                  <pre class="task-text">{task.output}</pre>
                {/if}
              </div>
            {/if}
          </div>
        {/each}
      </div>

      <div class="detail-actions">
        {#if selected.status === 'running'}
          <button class="btn btn-cancel" onclick={cancelPlaybook}>Cancel</button>
        {:else if confirmingDelete}
          <span class="confirm-label">Delete this playbook?</span>
          <button class="btn btn-danger" onclick={deleteSelected}>Yes, delete</button>
          <button class="btn" onclick={() => (confirmingDelete = false)}>No</button>
        {:else}
          <button
            class="btn btn-run"
            onclick={runPlaybook}
          >
            {selected.status === 'idle' ? 'Run' : 'Run Again'}
          </button>
          <button class="btn btn-danger" onclick={() => (confirmingDelete = true)}>Delete</button>
        {/if}
      </div>
    {/if}
  </div>
</div>

{#if showCreateModal}
  <Modal onClose={() => (showCreateModal = false)}>
    <div class="modal-body">
      <h3 class="modal-title">New Playbook</h3>
      <label class="form-label">
        Name
        <input
          class="form-input"
          type="text"
          bind:value={newName}
          placeholder="e.g. refactor-auth"
        />
      </label>
      <label class="form-label">
        Tasks (markdown checklist)
        <textarea
          class="form-textarea"
          bind:value={markdownDraft}
          rows="8"
          placeholder="- [ ] First task&#10;- [ ] Second task"
        ></textarea>
      </label>
      <label class="form-label">
        Scope
        <div class="scope-toggle">
          <button
            class="scope-btn {newScope === 'profile' ? 'active' : ''}"
            onclick={() => (newScope = 'profile')}
            disabled={!activeProfileName}
          >
            {activeProfileName || 'No profile active'}
          </button>
          <button
            class="scope-btn {newScope === 'global' ? 'active' : ''}"
            onclick={() => (newScope = 'global')}
          >
            Global (all profiles)
          </button>
        </div>
      </label>
      <div class="modal-footer">
        <button class="btn" onclick={() => (showCreateModal = false)}>Cancel</button>
        <button
          class="btn btn-primary"
          onclick={createPlaybook}
          disabled={isCreating || !newName.trim() || !markdownDraft.trim()}
        >
          {isCreating ? 'Creating…' : 'Create'}
        </button>
      </div>
    </div>
  </Modal>
{/if}

<style>
  .panel {
    display: flex;
    height: 100%;
    overflow: hidden;
  }

  /* Sidebar */
  .sidebar {
    width: 220px;
    flex-shrink: 0;
    border-right: 1px solid var(--color-border-primary);
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  .sidebar-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 14px;
    border-bottom: 1px solid var(--color-border-primary);
    flex-shrink: 0;
  }

  .sidebar-title {
    font-size: 12px;
    font-weight: 600;
    color: var(--color-text-tertiary);
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }

  .icon-btn {
    background: none;
    border: none;
    cursor: pointer;
    color: var(--color-text-secondary);
    font-size: 18px;
    line-height: 1;
    padding: 2px 4px;
    border-radius: var(--radius-sm);
  }
  .icon-btn:hover { background: var(--color-surface-hover); color: var(--color-text-primary); }

  .sidebar-empty {
    padding: 24px 14px;
    color: var(--color-text-muted);
    font-size: 13px;
  }

  .playbook-list {
    list-style: none;
    margin: 0;
    padding: 0;
    overflow-y: auto;
    flex: 1;
  }

  .playbook-item {
    padding: 10px 14px;
    cursor: pointer;
    border-bottom: 1px solid var(--color-border-primary);
  }
  .playbook-item:hover { background: var(--color-surface-hover); }
  .playbook-item.selected { background: var(--color-nav-active-bg); }

  .playbook-item-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 6px;
  }

  .playbook-name {
    font-size: 13px;
    font-weight: 500;
    color: var(--color-text-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .playbook-status-badge {
    font-size: 10px;
    padding: 1px 5px;
    border-radius: var(--radius-sm);
    flex-shrink: 0;
  }

  .playbook-meta {
    font-size: 11px;
    color: var(--color-text-muted);
    margin-top: 2px;
  }

  .confirm-label {
    font-size: 13px;
    color: var(--color-text-secondary);
    flex: 1;
  }

  /* Detail pane */
  .detail {
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  .detail-header {
    padding: 16px 20px 12px;
    border-bottom: 1px solid var(--color-border-primary);
    flex-shrink: 0;
  }

  .detail-title-row {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .detail-title {
    font-size: 16px;
    font-weight: 600;
    color: var(--color-text-primary);
    margin: 0;
  }

  .detail-status {
    font-size: 11px;
    padding: 2px 7px;
    border-radius: var(--radius-sm);
  }

  .detail-profile {
    font-size: 11px;
    color: var(--color-text-muted);
    background: var(--color-surface-hover);
    padding: 2px 7px;
    border-radius: var(--radius-sm);
  }

  .detail-meta {
    font-size: 12px;
    color: var(--color-text-muted);
    margin-top: 4px;
  }

  /* Task list */
  .task-list {
    flex: 1;
    overflow-y: auto;
    padding: 12px 20px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .task-row {
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-md);
    overflow: hidden;
  }

  .task-header {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 12px;
    cursor: default;
  }
  .task-header:has(.task-expand) { cursor: pointer; }

  .task-icon {
    font-size: 14px;
    flex-shrink: 0;
    width: 16px;
    text-align: center;
  }

  .task-content {
    flex: 1;
    font-size: 13px;
    color: var(--color-text-primary);
  }

  .task-duration {
    font-size: 11px;
    color: var(--color-text-muted);
    flex-shrink: 0;
  }

  .task-expand {
    font-size: 10px;
    color: var(--color-text-muted);
    flex-shrink: 0;
  }

  .task-output {
    border-top: 1px solid var(--color-border-primary);
    padding: 10px 12px;
    background: var(--color-bg-tertiary);
    max-height: 200px;
    overflow-y: auto;
  }

  .task-text, .task-error {
    margin: 0;
    font-size: 12px;
    white-space: pre-wrap;
    word-break: break-word;
    font-family: var(--font-mono, monospace);
  }

  .task-error { color: var(--color-error); }

  /* Status colours */
  .status-completed { background: color-mix(in srgb, var(--color-success) 15%, transparent); border-color: color-mix(in srgb, var(--color-success) 40%, transparent); color: var(--color-success); }
  .status-running   { background: color-mix(in srgb, var(--color-info) 15%, transparent); border-color: color-mix(in srgb, var(--color-info) 40%, transparent); color: var(--color-info); }
  .status-failed    { background: color-mix(in srgb, var(--color-error) 15%, transparent); border-color: color-mix(in srgb, var(--color-error) 40%, transparent); color: var(--color-error); }
  .status-cancelled { background: color-mix(in srgb, var(--color-warning) 15%, transparent); border-color: color-mix(in srgb, var(--color-warning) 40%, transparent); color: var(--color-warning); }
  .status-pending   { color: var(--color-text-muted); }

  /* Detail pane status badge (inline, not as border) */
  .detail-status.status-completed { background: color-mix(in srgb, var(--color-success) 20%, transparent); color: var(--color-success); }
  .detail-status.status-running   { background: color-mix(in srgb, var(--color-info) 20%, transparent); color: var(--color-info); }
  .detail-status.status-failed    { background: color-mix(in srgb, var(--color-error) 20%, transparent); color: var(--color-error); }
  .detail-status.status-cancelled { background: color-mix(in srgb, var(--color-warning) 20%, transparent); color: var(--color-warning); }
  .detail-status.status-idle      { background: var(--color-surface-hover); color: var(--color-text-muted); }

  /* Actions */
  .detail-actions {
    padding: 12px 20px;
    border-top: 1px solid var(--color-border-primary);
    display: flex;
    gap: 8px;
    flex-shrink: 0;
  }

  .btn {
    padding: 6px 14px;
    border-radius: var(--radius-md);
    font-size: 13px;
    cursor: pointer;
    border: 1px solid var(--color-border-secondary);
    background: var(--color-bg-tertiary);
    color: var(--color-text-primary);
    font-family: inherit;
  }
  .btn:hover { background: var(--color-surface-active); }
  .btn:disabled { opacity: 0.5; cursor: not-allowed; }

  .btn-primary { background: var(--color-accent); color: #18181b; border-color: var(--color-accent); }
  .btn-primary:hover { opacity: 0.9; }

  .btn-run { background: rgba(245, 158, 11, 0.12); color: var(--color-accent); border-color: rgba(245, 158, 11, 0.3); }
  .btn-run:hover { background: rgba(245, 158, 11, 0.2); }

  .btn-cancel { border-color: rgba(234, 179, 8, 0.3); color: var(--color-warning); background: rgba(234, 179, 8, 0.08); }
  .btn-cancel:hover { background: rgba(234, 179, 8, 0.15); }
  .btn-danger { border-color: rgba(239, 68, 68, 0.3); color: var(--color-error); background: rgba(239, 68, 68, 0.08); }
  .btn-danger:hover { background: rgba(239, 68, 68, 0.15); }

  /* Modal */
  .modal-title {
    font-size: 15px;
    font-weight: 600;
    color: var(--color-text-primary);
    margin: 0 0 4px;
  }

  .modal-body {
    display: flex;
    flex-direction: column;
    gap: 14px;
  }

  .form-label {
    display: flex;
    flex-direction: column;
    gap: 6px;
    font-size: 13px;
    color: var(--color-text-secondary);
  }

  .form-input {
    padding: 7px 10px;
    border: 1px solid var(--color-border-secondary);
    border-radius: var(--radius-md);
    background: var(--color-bg-tertiary);
    color: var(--color-text-primary);
    font-size: 13px;
    font-family: inherit;
  }

  .form-textarea {
    padding: 7px 10px;
    border: 1px solid var(--color-border-secondary);
    border-radius: var(--radius-md);
    background: var(--color-bg-tertiary);
    color: var(--color-text-primary);
    font-size: 12px;
    font-family: var(--font-mono, monospace);
    resize: vertical;
  }

  .modal-footer {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    padding-top: 8px;
  }

  .scope-toggle {
    display: flex;
    gap: 6px;
  }

  .scope-btn {
    flex: 1;
    padding: 6px 10px;
    border-radius: var(--radius-md);
    font-size: 12px;
    cursor: pointer;
    border: 1px solid var(--color-border-secondary);
    background: var(--color-bg-tertiary);
    color: var(--color-text-secondary);
    text-align: center;
    font-family: inherit;
  }
  .scope-btn.active {
    border-color: rgba(245, 158, 11, 0.4);
    color: var(--color-accent);
    background: rgba(245, 158, 11, 0.1);
  }
  .scope-btn:disabled { opacity: 0.4; cursor: not-allowed; }
</style>

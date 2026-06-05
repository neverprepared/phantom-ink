<script lang="ts">
  import { getApi } from '../utils/api';
  import { onMount } from 'svelte';
  import { brainboxEvents } from '../events.svelte';
  import { notifications } from '../notifications.svelte';
  import { profileState, panelFocus } from '../stores.svelte';
  import Modal from '../components/Modal.svelte';
  import EmptyState from '../components/EmptyState.svelte';
  import Spinner from '../components/Spinner.svelte';

  interface Participant {
    name: string;
    type: string;
    session_name?: string;
    ollama_model?: string;
    system_prompt?: string;
    joined_at: number;
  }

  interface Channel {
    id: string;
    name: string;
    participants: Participant[];
    status: string;
    created_at: number;
    completed_at?: number;
    completed_by?: string;
    parent_task_id?: string;
    workspace_profile?: string;
  }

  interface ChannelMessage {
    id: string;
    channel_id: string;
    from_participant: string;
    content: string;
    summary?: string;
    addressed_to?: string;
    type: string;
    timestamp: number;
  }

  // --- State ---
  let channels = $state<Channel[]>([]);
  let selected = $state<Channel | null>(null);
  let messages = $state<ChannelMessage[]>([]);
  let lastMessageId = $state<string | null>(null);
  let loading = $state(true);
  let messagesLoading = $state(false);
  let draft = $state('');
  let myName = $state('user');
  let isSending = $state(false);
  let isCompleting = $state(false);
  let confirmDeleteId = $state<string | null>(null);
  let selectedIds = $state<Set<string>>(new Set());
  let isBatchDeleting = $state(false);

  let filteredChannels = $derived.by(() => {
    if (!profileState.active) return channels;
    const target = profileState.active.name.toLowerCase();
    return channels.filter(c => (c.workspace_profile ?? '').toLowerCase() === target);
  });

  const allSelected = $derived(filteredChannels.length > 0 && filteredChannels.every(c => selectedIds.has(c.id)));
  const someSelected = $derived(selectedIds.size > 0);

  // Create channel modal
  let showCreateModal = $state(false);
  // Add-participant modal — opened from a live conversation's header to
  // attach another running session post-creation.
  let showAddParticipantModal = $state(false);
  let addParticipantCandidates = $state<any[]>([]);
  let addParticipantSelection = $state<string>(''); // session name to add
  let newChannelName = $state('');
  let availableSessions = $state<any[]>([]);
  let participantRows = $state<Array<{
    name: string;
    type: 'session' | 'ollama';
    session_name: string;
    ollama_model: string;
    system_prompt: string;
  }>>([]);
  let isCreating = $state(false);

  // --- SSE subscription ---
  $effect(() => {
    const lastEvent = brainboxEvents.last;
    if (!lastEvent) return;
    try {
      const parsed = typeof lastEvent === 'string' ? JSON.parse(lastEvent) : lastEvent;
      const action = parsed?.action ?? parsed?.event;
      if (action === 'channel.message' && parsed.channel_id === selected?.id) {
        loadMessages();
      } else if (action === 'channel.created' || action === 'channel.completed') {
        loadChannels();
      }
    } catch { /* ignore */ }
  });

  // --- Data loading ---
  async function loadChannels() {
    const a = await getApi();
    if (!a) { loading = false; return; }
    try {
      channels = (await a.ListChannels(profileState.active?.name ?? '')) ?? [];
    } catch (err: any) {
      notifications.error(`Failed to load conversations: ${err?.message ?? err}`);
    } finally {
      loading = false;
    }
  }

  async function loadMessages() {
    if (!selected) return;
    messagesLoading = true;
    const a = await getApi();
    if (!a) { messagesLoading = false; return; }
    try {
      const newMsgs = (await a.GetChannelMessages(selected.id, lastMessageId ?? '')) ?? [];
      if (newMsgs.length > 0) {
        messages = [...messages, ...newMsgs];
        lastMessageId = newMsgs[newMsgs.length - 1].id;
      }
    } catch (err) {
      notifications.error('Failed to fetch messages: ' + err.message);
    } finally {
      messagesLoading = false;
    }
  }

  async function selectChannel(channel: Channel) {
    selected = channel;
    messages = [];
    lastMessageId = null;
    // Load all messages
    const a = await getApi();
    if (!a) return;
    try {
      messages = (await a.GetChannelMessages(channel.id, '')) ?? [];
      if (messages.length > 0) {
        lastMessageId = messages[messages.length - 1].id;
      }
    } catch (err) {
      notifications.error('Failed to load messages: ' + err.message);
    }
  }

  // Polling fallback: refresh every 5s when a channel is selected
  $effect(() => {
    if (!selected || selected.status !== 'active') return;
    const interval = setInterval(loadMessages, 5000);
    return () => clearInterval(interval);
  });

  $effect(() => {
    profileState.active; // re-run when active profile changes
    selected = null;
    messages = [];
    lastMessageId = null;
    void loadChannels();
  });

  onMount(() => {
    // If we got here via panelFocus.startConversationWith(...), open the
    // create modal pre-seeded with those sessions.
    const seed = panelFocus.consumeConversationSeed();
    if (seed.length > 0) {
      openCreateModal(seed);
    }
  });

  // --- Create channel ---
  // When opening the modal we pre-populate one participant row per currently-
  // running session in the active profile, so the user can uncheck/remove
  // rather than build the list from scratch. Falls back to a single empty row
  // when there are no running sessions to seed from.
  function openCreateModal(seedSessions: string[] = []) {
    newChannelName = '';
    participantRows = [];
    showCreateModal = true;
    loadSessionsForModal(seedSessions);
  }

  async function loadSessionsForModal(seedSessions: string[] = []) {
    const a = await getApi();
    if (!a) return;
    try {
      const all = ((await a.GetSessions()) ?? []) as any[];
      const activeProfile = profileState.active?.name?.toLowerCase() ?? '';
      availableSessions = all.filter((s: any) => {
        if (!s.active) return false;
        if (!activeProfile) return true;
        return (s.workspace_profile ?? '').toLowerCase() === activeProfile;
      });
      // Seed participantRows. Explicit seedSessions (from "start conversation
      // from Sessions panel") wins; otherwise every running session is included.
      const namesToSeed = seedSessions.length > 0
        ? availableSessions.filter(s => seedSessions.includes(s.name))
        : availableSessions;
      participantRows = namesToSeed.length > 0
        ? namesToSeed.map((s: any) => ({
            name: s.name,
            type: 'session' as const,
            session_name: s.name,
            ollama_model: '',
            system_prompt: '',
          }))
        : [{ name: '', type: 'session' as const, session_name: '', ollama_model: '', system_prompt: '' }];
    } catch { /* ignore */ }
  }

  function addParticipantRow() {
    participantRows = [...participantRows, { name: '', type: 'session', session_name: '', ollama_model: '', system_prompt: '' }];
  }

  function removeParticipantRow(i: number) {
    participantRows = participantRows.filter((_, idx) => idx !== i);
  }

  // --- Live participant management (post-creation) ---

  async function openAddParticipant() {
    if (!selected) return;
    const a = await getApi();
    if (!a) return;
    try {
      const all = ((await a.GetSessions()) ?? []) as any[];
      const activeProfile = profileState.active?.name?.toLowerCase() ?? '';
      const inChannel = new Set(selected.participants.map(p => p.name));
      addParticipantCandidates = all
        .filter((s: any) => s.active)
        .filter((s: any) => !activeProfile || (s.workspace_profile ?? '').toLowerCase() === activeProfile)
        .filter((s: any) => !inChannel.has(s.name));
      addParticipantSelection = addParticipantCandidates[0]?.name ?? '';
      showAddParticipantModal = true;
    } catch (err: any) {
      notifications.error(`Failed to load sessions: ${err?.message ?? err}`);
    }
  }

  async function handleAddParticipant() {
    if (!selected || !addParticipantSelection) return;
    const a = await getApi();
    if (!a) return;
    try {
      const updated = await a.AddChannelParticipant(selected.id, {
        name: addParticipantSelection,
        type: 'session',
        session_name: addParticipantSelection,
        ollama_model: '',
        system_prompt: '',
      });
      selected = updated;
      // Refresh the list so the participant-count badge updates.
      channels = channels.map(c => c.id === updated.id ? updated : c);
      showAddParticipantModal = false;
      notifications.success(`${addParticipantSelection} joined the conversation`);
    } catch (err: any) {
      notifications.error(`Failed to add: ${err?.message ?? err}`);
    }
  }

  async function handleRemoveParticipant(name: string) {
    if (!selected) return;
    if (!confirm(`Remove ${name} from this conversation?`)) return;
    const a = await getApi();
    if (!a) return;
    try {
      const updated = await a.RemoveChannelParticipant(selected.id, name);
      selected = updated;
      channels = channels.map(c => c.id === updated.id ? updated : c);
      notifications.success(`${name} removed`);
    } catch (err: any) {
      notifications.error(`Failed to remove: ${err?.message ?? err}`);
    }
  }

  async function handleCreate() {
    if (!newChannelName.trim() || participantRows.length === 0) return;
    const valid = participantRows.filter(p => p.name.trim() && (
      (p.type === 'session' && p.session_name) ||
      (p.type === 'ollama' && p.ollama_model)
    ));
    if (valid.length === 0) return;

    isCreating = true;
    const a = await getApi();
    if (!a) { isCreating = false; return; }
    try {
      const channel = await a.CreateChannel({
        name: newChannelName,
        participants: valid.map(p => ({
          name: p.name,
          type: p.type,
          session_name: p.type === 'session' ? p.session_name : undefined,
          ollama_model: p.type === 'ollama' ? p.ollama_model : undefined,
          system_prompt: p.system_prompt || undefined,
        })),
        workspace_profile: profileState.active?.name ?? undefined,
      });
      notifications.success(`Conversation "${newChannelName}" created`);
      showCreateModal = false;
      await loadChannels();
      await selectChannel(channel);
    } catch (err: any) {
      notifications.error(`Failed to create channel: ${err}`);
    } finally {
      isCreating = false;
    }
  }

  // --- Send message ---
  async function handleSend() {
    if (!draft.trim() || !selected || isSending) return;
    isSending = true;
    const a = await getApi();
    if (!a) { isSending = false; return; }
    try {
      // Parse @mention from draft
      const addressed = draft.match(/^@(\S+)/)?.[1] ?? undefined;
      const content = draft.trim();
      await a.PostChannelMessage(selected.id, {
        from_participant: myName,
        content,
        addressed_to: addressed,
      });
      draft = '';
      await loadMessages();
    } catch (err: any) {
      notifications.error(`Failed to send: ${err}`);
    } finally {
      isSending = false;
    }
  }

  async function handleComplete() {
    if (!selected || isCompleting) return;
    isCompleting = true;
    const a = await getApi();
    if (!a) { isCompleting = false; return; }
    try {
      await a.CompleteChannel(selected.id, { by: myName, reason: 'Conversation ended by user' });
      notifications.success('Conversation ended');
      await loadChannels();
      // Refresh selected to show updated status
      const updated = channels.find(c => c.id === selected!.id);
      if (updated) selected = updated;
    } catch (err: any) {
      notifications.error(`Failed to end channel: ${err}`);
    } finally {
      isCompleting = false;
    }
  }

  function requestDelete(ch: Channel, e: MouseEvent) {
    e.stopPropagation();
    confirmDeleteId = ch.id;
  }

  function cancelDelete(e: MouseEvent) {
    e.stopPropagation();
    confirmDeleteId = null;
  }

  async function confirmDelete(ch: Channel, e: MouseEvent) {
    e.stopPropagation();
    confirmDeleteId = null;
    const a = await getApi();
    if (!a) return;
    try {
      await a.DeleteChannel(ch.id);
      notifications.success(`Conversation "${ch.name}" deleted`);
      if (selected?.id === ch.id) { selected = null; messages = []; lastMessageId = null; }
      await loadChannels();
    } catch (err: any) {
      notifications.error(`Failed to delete channel: ${err}`);
    }
  }

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  function toggleSelect(id: string) {
    const next = new Set(selectedIds);
    if (next.has(id)) next.delete(id); else next.add(id);
    selectedIds = next;
  }

  function toggleSelectAll() {
    selectedIds = allSelected ? new Set() : new Set(filteredChannels.map(c => c.id));
  }

  async function handleBatchDelete() {
    if (selectedIds.size === 0 || isBatchDeleting) return;
    isBatchDeleting = true;
    const a = await getApi();
    if (!a) { isBatchDeleting = false; return; }
    const ids = [...selectedIds];
    let failed = 0;
    for (const id of ids) {
      try {
        await a.DeleteChannel(id);
        if (selected?.id === id) { selected = null; messages = []; lastMessageId = null; }
      } catch {
        failed++;
      }
    }
    selectedIds = new Set();
    isBatchDeleting = false;
    if (failed > 0) notifications.error(`${failed} channel(s) failed to delete`);
    else notifications.success(`${ids.length} channel(s) deleted`);
    await loadChannels();
  }

  function formatTime(ts: number) {
    return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }
</script>

<div class="channels-layout">
  <!-- Left: channel list -->
  <div class="channel-list">
    <div class="list-header">
      {#if someSelected}
        <input type="checkbox" class="select-all-cb" checked={allSelected} onclick={toggleSelectAll} title="Select all" />
        <span class="list-title">{selectedIds.size} selected</span>
        <button class="btn-batch-delete" onclick={handleBatchDelete} disabled={isBatchDeleting} title="Delete selected">
          <svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>
          {isBatchDeleting ? 'Deleting…' : 'Delete'}
        </button>
      {:else}
        <span class="list-title">Conversations</span>
        {#if loading}<Spinner size={12} />{/if}
        <button class="btn-icon" onclick={() => openCreateModal()} title="New conversation">
          <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14"/><path d="M12 5v14"/></svg>
        </button>
      {/if}
    </div>

    {#if loading}
      <div class="list-empty">Loading…</div>
    {:else if filteredChannels.length === 0}
      <div class="list-empty">No conversations yet</div>
    {:else}
      <ul class="channel-items">
        {#each filteredChannels as ch (ch.id)}
          <li class="channel-item-row" class:row-selecting={someSelected}>
            <input
              type="checkbox"
              class="row-cb"
              checked={selectedIds.has(ch.id)}
              onclick={(e) => { e.stopPropagation(); toggleSelect(ch.id); }}
            />
            {#if confirmDeleteId === ch.id}
              <div class="delete-confirm">
                <span>Delete?</span>
                <button class="btn-confirm-yes" onclick={(e) => confirmDelete(ch, e)}>Yes</button>
                <button class="btn-confirm-no" onclick={cancelDelete}>No</button>
              </div>
            {:else}
              <button
                class="channel-item"
                class:active={selected?.id === ch.id}
                onclick={() => selectChannel(ch)}
              >
                <span class="status-dot" class:completed={ch.status === 'completed'}></span>
                <span class="channel-name">{ch.name}</span>
                <span class="participant-count">{ch.participants.length}</span>
              </button>
              <button class="btn-delete-channel" onclick={(e) => requestDelete(ch, e)} title="Delete conversation">
                <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>
              </button>
            {/if}
          </li>
        {/each}
      </ul>
    {/if}
  </div>

  <!-- Right: channel view -->
  <div class="channel-view">
    {#if !selected}
      <EmptyState
        title="No conversation selected"
        message="Select a conversation from the list or create a new one."
      />
    {:else}
      <div class="channel-header">
        <div class="channel-meta">
          <span class="channel-title">#{selected.name}</span>
          <span class="channel-status" class:completed={selected.status === 'completed'}>
            {selected.status}
          </span>
        </div>
        <div class="participant-list">
          {#each selected.participants as p}
            <span class="participant-chip" title={p.type}>
              {p.type === 'session' ? '💻' : p.type === 'ollama' ? '🤖' : '👤'}
              {p.name}
              {#if selected.status !== 'completed' && p.name !== myName}
                <button
                  class="participant-remove"
                  onclick={() => handleRemoveParticipant(p.name)}
                  title="Remove from conversation"
                  aria-label="Remove {p.name}"
                >×</button>
              {/if}
            </span>
          {/each}
          {#if selected.status !== 'completed'}
            <button class="add-participant-btn" onclick={openAddParticipant} title="Add a running session">+ add session</button>
          {/if}
        </div>
      </div>

      <div class="messages">
        {#if messages.length === 0}
          <div class="no-messages">No messages yet. Start the conversation.</div>
        {:else}
          {#each messages as msg (msg.id)}
            <div class="message" class:completion={msg.type === 'completion'} class:mine={msg.from_participant === myName}>
              <div class="message-header">
                <span class="msg-from">{msg.from_participant}</span>
                {#if msg.addressed_to}
                  <span class="msg-addressed">→ @{msg.addressed_to}</span>
                {/if}
                <span class="msg-time">{formatTime(msg.timestamp)}</span>
              </div>
              <div class="message-content">{msg.content}</div>
              {#if msg.summary && msg.from_participant !== myName}
                <div class="message-summary">Summary: {msg.summary}</div>
              {/if}
            </div>
          {/each}
        {/if}
      </div>

      {#if selected.status === 'active'}
        <div class="composer">
          <div class="composer-name">
            <label class="my-name-label" for="my-name">As:</label>
            <input id="my-name" class="my-name-input" bind:value={myName} placeholder="your name" />
          </div>
          <div class="composer-row">
            <textarea
              class="draft-input"
              bind:value={draft}
              placeholder="Type a message… use @name to address someone"
              rows="2"
              onkeydown={handleKeydown}
              disabled={isSending}
            ></textarea>
            <div class="composer-actions">
              <button class="btn-send" onclick={handleSend} disabled={isSending || !draft.trim()}>
                Send
              </button>
              <button class="btn-end" onclick={handleComplete} disabled={isCompleting}>
                End
              </button>
            </div>
          </div>
        </div>
      {:else}
        <div class="channel-ended">
          Conversation ended{selected.completed_by ? ` by ${selected.completed_by}` : ''}.
        </div>
      {/if}
    {/if}
  </div>
</div>

<!-- Create channel modal -->
{#if showCreateModal}
  <Modal onClose={() => showCreateModal = false}>
    <div class="modal-body">
      <label class="field-label" for="ch-name">Conversation name</label>
      <input id="ch-name" class="field-input" bind:value={newChannelName} placeholder="e.g. architecture-debate" />

      <div class="participants-section">
        <div class="section-label">Participants</div>
        {#each participantRows as row, i}
          <div class="participant-row">
            <input class="p-name" bind:value={row.name} placeholder="Display name" />
            <select class="p-type" bind:value={row.type}>
              <option value="session">Session</option>
              <option value="ollama">Ollama</option>
            </select>
            {#if row.type === 'session'}
              <select class="p-detail" bind:value={row.session_name}>
                <option value="">— session —</option>
                {#each availableSessions as s}
                  <option value={s.name}>{s.name}</option>
                {/each}
              </select>
            {:else}
              <input class="p-detail" bind:value={row.ollama_model} placeholder="Model (e.g. llama3)" />
            {/if}
            <input class="p-prompt" bind:value={row.system_prompt} placeholder="Role / system prompt (optional)" />
            <button class="btn-remove" onclick={() => removeParticipantRow(i)} disabled={participantRows.length <= 1}>✕</button>
          </div>
        {/each}
        <button class="btn-add-participant" onclick={addParticipantRow}>+ Add participant</button>
      </div>

      <div class="modal-footer">
        <button class="btn-cancel" onclick={() => showCreateModal = false}>Cancel</button>
        <button class="btn-primary" onclick={handleCreate} disabled={isCreating || !newChannelName.trim()}>
          {isCreating ? 'Creating…' : 'Create Channel'}
        </button>
      </div>
    </div>
  </Modal>
{/if}

{#if showAddParticipantModal}
  <Modal onClose={() => showAddParticipantModal = false}>
    <div class="modal-body">
      <h2>Add session to conversation</h2>
      {#if addParticipantCandidates.length === 0}
        <p class="hint">No other running sessions in the active profile.</p>
      {:else}
        <label class="field-label" for="ap-select">Session</label>
        <select id="ap-select" class="field-input" bind:value={addParticipantSelection}>
          {#each addParticipantCandidates as s (s.name)}
            <option value={s.name}>{s.session_name ?? s.name}</option>
          {/each}
        </select>
      {/if}
      <div class="modal-actions">
        <button class="btn-cancel" onclick={() => showAddParticipantModal = false}>Cancel</button>
        <button class="btn-primary" onclick={handleAddParticipant} disabled={!addParticipantSelection}>Add</button>
      </div>
    </div>
  </Modal>
{/if}

<style>
  .channels-layout {
    display: grid;
    grid-template-columns: 220px 1fr;
    height: 100%;
    gap: 0;
    overflow: hidden;
  }

  /* Channel list */
  .channel-list {
    border-right: 1px solid var(--color-border-primary);
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  .list-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 14px;
    border-bottom: 1px solid var(--color-border-primary);
    flex-shrink: 0;
  }

  .list-title {
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--color-text-tertiary);
  }

  .btn-icon {
    background: none;
    border: none;
    cursor: pointer;
    color: var(--color-text-secondary);
    display: flex;
    align-items: center;
    padding: 4px;
    border-radius: var(--radius-sm);
  }

  .btn-icon:hover {
    background: rgba(255,255,255,0.07);
    color: var(--color-text-primary);
  }

  .list-empty {
    padding: 16px;
    font-size: 12px;
    color: var(--color-text-tertiary);
    text-align: center;
  }

  .channel-items {
    list-style: none;
    overflow-y: auto;
    flex: 1;
    padding: 8px;
  }

  .channel-item-row {
    display: flex;
    align-items: center;
    gap: 2px;
  }

  .row-cb {
    flex-shrink: 0;
    margin: 0 2px 0 6px;
    cursor: pointer;
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.1s;
  }

  .channel-item-row:hover .row-cb,
  .channel-item-row.row-selecting .row-cb {
    opacity: 1;
    pointer-events: auto;
  }

  .select-all-cb {
    flex-shrink: 0;
    margin: 0 2px 0 6px;
    cursor: pointer;
  }

  .btn-batch-delete {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 3px 8px;
    border: none;
    border-radius: var(--radius-sm);
    background: var(--color-error, #ef4444);
    color: #fff;
    font-size: 11px;
    cursor: pointer;
    white-space: nowrap;
  }

  .btn-batch-delete:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .channel-item-row:not(:hover):not(.row-selecting) .btn-delete-channel {
    opacity: 0;
    pointer-events: none;
  }

  .delete-confirm {
    display: flex;
    align-items: center;
    gap: 4px;
    width: 100%;
    padding: 6px 10px;
    font-size: 12px;
    color: var(--color-text-secondary);
  }

  .delete-confirm span {
    flex: 1;
  }

  .btn-confirm-yes {
    padding: 2px 8px;
    border: none;
    border-radius: var(--radius-sm);
    background: var(--color-error, #ef4444);
    color: #fff;
    font-size: 11px;
    cursor: pointer;
  }

  .btn-confirm-no {
    padding: 2px 8px;
    border: none;
    border-radius: var(--radius-sm);
    background: var(--color-bg-tertiary, rgba(255,255,255,0.08));
    color: var(--color-text-secondary);
    font-size: 11px;
    cursor: pointer;
  }

  .btn-delete-channel {
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    width: 24px;
    height: 24px;
    border: none;
    background: none;
    border-radius: var(--radius-sm);
    cursor: pointer;
    color: var(--color-text-tertiary);
    transition: opacity 0.1s, color 0.1s, background 0.1s;
  }

  .btn-delete-channel:hover {
    color: var(--color-error, #ef4444);
    background: rgba(239, 68, 68, 0.1);
  }

  .channel-item {
    display: flex;
    align-items: center;
    gap: 8px;
    flex: 1;
    min-width: 0;
    padding: 8px 10px;
    border: none;
    background: none;
    border-radius: var(--radius-md);
    cursor: pointer;
    color: var(--color-text-secondary);
    font-size: 13px;
    text-align: left;
  }

  .channel-item:hover {
    background: rgba(255,255,255,0.05);
    color: var(--color-text-primary);
  }

  .channel-item.active {
    background: rgba(59,130,246,0.1);
    color: var(--color-info);
  }

  .status-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--color-success);
    flex-shrink: 0;
  }

  .status-dot.completed {
    background: var(--color-text-tertiary);
  }

  .channel-name {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .participant-count {
    font-size: 11px;
    color: var(--color-text-tertiary);
  }

  /* Channel view */
  .channel-view {
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  .channel-header {
    padding: 12px 20px;
    border-bottom: 1px solid var(--color-border-primary);
    flex-shrink: 0;
  }

  .channel-meta {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 6px;
  }

  .channel-title {
    font-size: 15px;
    font-weight: 600;
    color: var(--color-text-primary);
  }

  .channel-status {
    font-size: 11px;
    padding: 2px 7px;
    border-radius: 99px;
    background: rgba(34,197,94,0.1);
    color: var(--color-success);
  }

  .channel-status.completed {
    background: rgba(156,163,175,0.1);
    color: var(--color-text-tertiary);
  }

  .participant-list {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }

  .participant-chip {
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 99px;
    background: var(--color-bg-tertiary);
    color: var(--color-text-secondary);
    border: 1px solid var(--color-border-primary);
    display: inline-flex;
    align-items: center;
    gap: 4px;
  }
  .participant-remove {
    background: none; border: none; padding: 0 0 0 2px;
    color: var(--color-text-tertiary); cursor: pointer;
    font-size: 14px; line-height: 1;
  }
  .participant-remove:hover { color: var(--color-error); }
  .add-participant-btn {
    background: none;
    border: 1px dashed var(--color-border-secondary);
    border-radius: 99px;
    padding: 2px 8px; font-size: 11px;
    color: var(--color-text-tertiary); cursor: pointer;
  }
  .add-participant-btn:hover { color: var(--color-accent); border-color: var(--color-accent); }

  /* Messages */
  .messages {
    flex: 1;
    overflow-y: auto;
    padding: 16px 20px;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .no-messages {
    color: var(--color-text-tertiary);
    font-size: 13px;
    text-align: center;
    margin-top: 32px;
  }

  .message {
    background: var(--color-bg-tertiary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-lg);
    padding: 10px 14px;
  }

  .message.mine {
    border-color: rgba(59,130,246,0.3);
    background: rgba(59,130,246,0.05);
  }

  .message.completion {
    border-style: dashed;
    opacity: 0.7;
    font-style: italic;
  }

  .message-header {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 4px;
  }

  .msg-from {
    font-size: 12px;
    font-weight: 600;
    color: var(--color-info);
  }

  .msg-addressed {
    font-size: 11px;
    color: var(--color-text-tertiary);
  }

  .msg-time {
    font-size: 11px;
    color: var(--color-text-tertiary);
    margin-left: auto;
  }

  .message-content {
    font-size: 13px;
    color: var(--color-text-primary);
    white-space: pre-wrap;
    word-break: break-word;
  }

  .message-summary {
    margin-top: 6px;
    font-size: 11px;
    color: var(--color-text-tertiary);
    font-style: italic;
    border-top: 1px solid var(--color-border-primary);
    padding-top: 4px;
  }

  /* Composer */
  .composer {
    border-top: 1px solid var(--color-border-primary);
    padding: 12px 20px;
    flex-shrink: 0;
  }

  .composer-name {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 8px;
  }

  .my-name-label {
    font-size: 11px;
    color: var(--color-text-tertiary);
  }

  .my-name-input {
    width: 140px;
    padding: 3px 8px;
    font-size: 12px;
    background: var(--color-bg-tertiary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm);
    color: var(--color-text-primary);
  }

  .composer-row {
    display: flex;
    gap: 10px;
    align-items: flex-end;
  }

  .draft-input {
    flex: 1;
    padding: 8px 12px;
    font-size: 13px;
    font-family: inherit;
    background: var(--color-bg-tertiary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-md);
    color: var(--color-text-primary);
    resize: none;
    line-height: 1.4;
  }

  .draft-input:focus {
    outline: none;
    border-color: var(--color-info);
  }

  .composer-actions {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .btn-send {
    padding: 7px 16px;
    background: var(--color-info);
    color: white;
    border: none;
    border-radius: var(--radius-md);
    font-size: 13px;
    cursor: pointer;
    font-family: inherit;
  }

  .btn-send:hover:not(:disabled) {
    opacity: 0.85;
  }

  .btn-send:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  .btn-end {
    padding: 7px 16px;
    background: none;
    color: var(--color-text-tertiary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-md);
    font-size: 12px;
    cursor: pointer;
    font-family: inherit;
  }

  .btn-end:hover:not(:disabled) {
    background: rgba(239,68,68,0.1);
    border-color: rgba(239,68,68,0.4);
    color: var(--fail, #ef4444);
  }

  .channel-ended {
    padding: 16px 20px;
    text-align: center;
    font-size: 12px;
    color: var(--color-text-tertiary);
    font-style: italic;
    border-top: 1px solid var(--color-border-primary);
    flex-shrink: 0;
  }

  /* Modal */
  .modal-body {
    display: flex;
    flex-direction: column;
    gap: 12px;
    min-width: 560px;
  }

  .field-label {
    font-size: 12px;
    color: var(--color-text-secondary);
    display: block;
    margin-bottom: 4px;
  }

  .field-input {
    width: 100%;
    padding: 8px 12px;
    font-size: 13px;
    background: var(--color-bg-tertiary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-md);
    color: var(--color-text-primary);
    box-sizing: border-box;
  }

  .participants-section {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .section-label {
    font-size: 12px;
    font-weight: 600;
    color: var(--color-text-secondary);
  }

  .participant-row {
    display: grid;
    grid-template-columns: 120px 90px 1fr 1fr auto;
    gap: 6px;
    align-items: center;
  }

  .p-name, .p-type, .p-detail, .p-prompt {
    padding: 6px 8px;
    font-size: 12px;
    background: var(--color-bg-tertiary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm);
    color: var(--color-text-primary);
    font-family: inherit;
  }

  .btn-remove {
    background: none;
    border: none;
    cursor: pointer;
    color: var(--color-text-tertiary);
    font-size: 12px;
    padding: 4px 6px;
  }

  .btn-remove:hover:not(:disabled) {
    color: var(--fail, #ef4444);
  }

  .btn-add-participant {
    background: none;
    border: 1px dashed var(--color-border-primary);
    border-radius: var(--radius-sm);
    color: var(--color-text-tertiary);
    font-size: 12px;
    padding: 6px 12px;
    cursor: pointer;
    font-family: inherit;
    align-self: flex-start;
  }

  .btn-add-participant:hover {
    border-color: var(--color-info);
    color: var(--color-info);
  }

  .modal-footer {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    margin-top: 4px;
  }

  .btn-cancel {
    padding: 7px 16px;
    background: none;
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-md);
    color: var(--color-text-secondary);
    font-size: 13px;
    cursor: pointer;
    font-family: inherit;
  }

  .btn-primary {
    padding: 7px 16px;
    background: var(--color-info);
    border: none;
    border-radius: var(--radius-md);
    color: white;
    font-size: 13px;
    cursor: pointer;
    font-family: inherit;
  }

  .btn-primary:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
</style>

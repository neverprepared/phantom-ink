<script lang="ts">
  /**
   * Conversations panel — the multi-agent Chat surface.
   *
   * Rewired onto the conversation engine: records live in the
   * local-first store (profile-scoped, ULID-keyed) and updates arrive on a
   * per-conversation SSE stream bridged through the Go layer as the
   * `conversation:event` Wails event. The 5-second poll this panel used to run
   * is gone — a persona's reply renders token by token as it is generated.
   *
   * PR2 adds the multi-persona surface on top: a persona-management modal
   * (add / edit / remove personas on a live room) and an @address affordance in
   * the composer, plus the `quiet` frame the turn orchestrator emits when the
   * room has stopped talking and is waiting for a human.
   *
   * PR3 adds the per-message promote menu: a turn worth keeping becomes a
   * memory (brain `learn`), a todo (phantom-todo vault), or a hub task —
   * server-side, profile-scoped, through existing platform surfaces. The menu
   * is deliberately per-MESSAGE: what gets promoted is one turn, not the room.
   */
  import { getApi } from '../utils/api';
  import { onMount, untrack } from 'svelte';
  import { notifications } from '../notifications.svelte';
  import { profileState, panelFocus } from '../stores.svelte';
  import EmptyState from '../components/EmptyState.svelte';
  import Spinner from '../components/Spinner.svelte';
  import NewConversationModal from '../components/NewConversationModal.svelte';
  import PersonaManager from '../components/PersonaManager.svelte';

  interface Participant {
    name: string;
    kind: string;
    model_target?: Record<string, any>;
    role_prompt?: string;
    cooldown_s?: number;
    joined_at: number;
  }

  interface Conversation {
    id: string;
    profile: string;
    title: string;
    status: string;
    participants: Participant[];
    created_at: number;
    updated_at: number;
  }

  interface ConversationMessage {
    id: string;
    conversation_id: string;
    author: string;
    kind: string;
    content: string;
    addressed_to?: string;
    in_reply_to?: string;
    created_at: number;
    /** True between message.created and message.done — renders a live caret. */
    streaming?: boolean;
  }

  // --- State ---
  let conversations = $state<Conversation[]>([]);
  let selected = $state<Conversation | null>(null);
  let messages = $state<ConversationMessage[]>([]);
  let loading = $state(true);
  let draft = $state('');
  let myName = $state('user');
  let isSending = $state(false);
  let isArchiving = $state(false);
  let thinkingAuthor = $state<string | null>(null);
  let confirmArchiveId = $state<string | null>(null);
  let selectedIds = $state<Set<string>>(new Set());
  let isBatchArchiving = $state(false);
  let showCreateModal = $state(false);
  let showPersonaManager = $state(false);
  /**
   * Why the room stopped talking, from the orchestrator's `quiet` frame. Held
   * per room and cleared on the next message so it always describes the tail of
   * the conversation, never a stale earlier lull.
   */
  let quietReason = $state<string | null>(null);

  const activeProfile = $derived(profileState.active?.name ?? '');

  const allSelected = $derived(
    conversations.length > 0 && conversations.every(c => selectedIds.has(c.id)),
  );
  const someSelected = $derived(selectedIds.size > 0);

  let activeCollapsed = $state(false);
  let archivedCollapsed = $state(false);
  const activeConversations = $derived(conversations.filter(c => c.status !== 'archived'));
  const archivedConversations = $derived(conversations.filter(c => c.status === 'archived'));

  // --- Per-conversation SSE ---
  // Frames arrive as {event, conversation_id, data}. The stream is opened by
  // the Go layer on select and closed on deselect, so a frame for another room
  // should never arrive — the id is still checked, because a stale frame from a
  // just-closed room must not scribble into the newly opened one.
  function handleFrame(frame: any) {
    if (!frame || frame.conversation_id !== selected?.id) return;
    const data = frame.data ?? {};
    switch (frame.event) {
      case 'thinking':
        thinkingAuthor = data.author ?? null;
        break;
      case 'message.created': {
        const msg = data.message as ConversationMessage | undefined;
        if (!msg) break;
        quietReason = null;
        if (messages.some(m => m.id === msg.id)) break;
        messages = [...messages, { ...msg, streaming: msg.content === '' }];
        break;
      }
      case 'message.delta': {
        const { id, delta } = data as { id: string; delta: string };
        messages = messages.map(m =>
          m.id === id ? { ...m, content: m.content + (delta ?? ''), streaming: true } : m,
        );
        break;
      }
      case 'message.done': {
        const msg = data.message as ConversationMessage | undefined;
        if (!msg) break;
        thinkingAuthor = null;
        const known = messages.some(m => m.id === msg.id);
        messages = known
          ? messages.map(m => (m.id === msg.id ? { ...msg, streaming: false } : m))
          : [...messages, { ...msg, streaming: false }];
        break;
      }
      case 'quiet': {
        // The turn orchestrator has nothing more to add — the room is waiting
        // for a human. Shown so a silent room reads as "your move", not "broken".
        thinkingAuthor = null;
        quietReason = (data as { reason?: string }).reason ?? 'quiet';
        break;
      }
      case 'error': {
        // Drop the half-written bubble rather than leaving it spinning forever.
        thinkingAuthor = null;
        const id = (data as { id?: string }).id;
        if (id) messages = messages.filter(m => m.id !== id);
        notifications.error(`${data.author ?? 'persona'} failed to reply: ${data.reason ?? 'unknown error'}`);
        break;
      }
      default:
        break; // 'connected' and anything the backend adds later
    }
  }

  onMount(() => {
    // Arriving via the Sessions panel's "talk" button: open the create modal.
    // The seeded session names are consumed but not used as participants —
    // session participants are the PR4 promotion path, not a PR1 roster entry.
    if (panelFocus.consumeConversationSeed().length > 0) showCreateModal = true;

    const rt = (window as any).runtime;
    rt?.EventsOn?.('conversation:event', handleFrame);
    return () => {
      rt?.EventsOff?.('conversation:event');
      void unsubscribeCurrent();
    };
  });

  async function subscribe(conversationId: string) {
    const a = await getApi();
    try {
      await a?.SubscribeConversation(conversationId, activeProfile);
    } catch (err: any) {
      notifications.error(`Live updates unavailable: ${err?.message ?? err}`);
    }
  }

  async function unsubscribeCurrent() {
    const current = selected?.id;
    if (!current) return;
    const a = await getApi();
    try {
      await a?.UnsubscribeConversation(current);
    } catch { /* closing a stream must never surface an error */ }
  }

  // --- Data loading ---
  async function loadConversations() {
    const a = await getApi();
    if (!a || !activeProfile) { loading = false; return; }
    try {
      conversations = (await a.ListConversations(activeProfile, true)) ?? [];
    } catch (err: any) {
      notifications.error(`Failed to load conversations: ${err?.message ?? err}`);
    } finally {
      loading = false;
    }
  }

  async function loadMessages(conversationId: string) {
    const a = await getApi();
    if (!a) return;
    try {
      messages = ((await a.ListConversationMessages(conversationId, activeProfile, '')) ?? [])
        .map(m => ({ ...m, streaming: false }));
    } catch (err: any) {
      notifications.error(`Failed to load messages: ${err?.message ?? err}`);
    }
  }

  async function selectConversation(conv: Conversation) {
    if (selected?.id === conv.id) return;
    await unsubscribeCurrent();
    selected = conv;
    messages = [];
    thinkingAuthor = null;
    quietReason = null;
    promoteMenuFor = null;
    await loadMessages(conv.id);
    await subscribe(conv.id);
  }

  $effect(() => {
    profileState.active; // re-run when the active profile changes
    // untrack: the reset below reads `selected` and then writes it. Left
    // tracked, that write would re-dirty the effect and spin it forever.
    untrack(() => { void resetForProfile(); });
  });

  async function resetForProfile() {
    await unsubscribeCurrent();
    selected = null;
    messages = [];
    thinkingAuthor = null;
    quietReason = null;
    selectedIds = new Set();
    await loadConversations();
  }

  // --- Create ---
  async function handleCreated(conv: Conversation) {
    showCreateModal = false;
    await loadConversations();
    await selectConversation(conv);
  }

  // --- @address ---
  // A leading @mention is the wire-level `addressed_to`: the orchestrator lets
  // that persona bypass its relevance gate and keeps everyone else silent for
  // the message. The chips below the composer are just a way to type it.
  const addressedName = $derived(draft.match(/^@(\S+)/)?.[1] ?? null);

  const addressablePersonas = $derived(
    (selected?.participants ?? []).filter(p => p.kind === 'persona'),
  );

  function toggleAddress(name: string) {
    const rest = draft.replace(/^@\S+\s*/, '');
    draft = addressedName?.toLowerCase() === name.toLowerCase() ? rest : `@${name} ${rest}`;
  }

  // --- Send ---
  async function handleSend() {
    if (!draft.trim() || !selected || isSending) return;
    isSending = true;
    const a = await getApi();
    if (!a) { isSending = false; return; }
    try {
      const addressed = addressedName ?? undefined;
      await a.PostConversationMessage(selected.id, activeProfile, {
        author: myName,
        content: draft.trim(),
        addressed_to: addressed,
      } as any);
      draft = '';
      quietReason = null;
      // The posted message arrives on the stream — no refetch needed.
    } catch (err: any) {
      notifications.error(`Failed to send: ${err?.message ?? err}`);
    } finally {
      isSending = false;
    }
  }

  // --- Promote a message (PR3) ---
  // Targets are the platform's own surfaces; the Go/brainbox side resolves the
  // profile's vault credentials, so nothing secret passes through here.
  type PromoteTarget = 'memory' | 'todo' | 'task';
  const PROMOTE_LABELS: Record<PromoteTarget, string> = {
    memory: 'to memory',
    todo: 'to todo',
    task: 'to task',
  };
  /** Which message's promote menu is open (only ever one). */
  let promoteMenuFor = $state<string | null>(null);
  /** `<messageId>:<target>` while that promotion is in flight. */
  let promoting = $state<string | null>(null);

  function togglePromoteMenu(id: string) {
    promoteMenuFor = promoteMenuFor === id ? null : id;
  }

  async function promoteMessage(msg: ConversationMessage, target: PromoteTarget) {
    if (!selected || promoting) return;
    promoteMenuFor = null;
    promoting = `${msg.id}:${target}`;
    const a = await getApi();
    if (!a) { promoting = null; return; }
    try {
      const res: any = await (a as any).PromoteConversationMessage(
        selected.id,
        msg.id,
        activeProfile,
        { target },
      );
      // The server's own detail is the honest report — it names the vault or
      // the agent the promotion actually reached.
      notifications.success(
        res?.detail ? `Promoted ${PROMOTE_LABELS[target]}: ${res.detail}` : `Promoted ${PROMOTE_LABELS[target]}`,
      );
    } catch (err: any) {
      notifications.error(`Promote ${PROMOTE_LABELS[target]} failed: ${err?.message ?? err}`);
    } finally {
      promoting = null;
    }
  }

  async function handleArchive() {
    if (!selected || isArchiving) return;
    isArchiving = true;
    const a = await getApi();
    if (!a) { isArchiving = false; return; }
    try {
      const updated = await a.ArchiveConversation(selected.id, activeProfile);
      selected = updated as unknown as Conversation;
      conversations = conversations.map(c => (c.id === updated.id ? (updated as unknown as Conversation) : c));
      notifications.success('Conversation archived');
    } catch (err: any) {
      notifications.error(`Failed to archive: ${err?.message ?? err}`);
    } finally {
      isArchiving = false;
    }
  }

  function requestArchive(conv: Conversation, e: MouseEvent) {
    e.stopPropagation();
    confirmArchiveId = conv.id;
  }

  function cancelArchive(e: MouseEvent) {
    e.stopPropagation();
    confirmArchiveId = null;
  }

  async function confirmArchive(conv: Conversation, e: MouseEvent) {
    e.stopPropagation();
    confirmArchiveId = null;
    const a = await getApi();
    if (!a) return;
    try {
      await a.ArchiveConversation(conv.id, activeProfile);
      notifications.success(`Conversation "${conv.title}" archived`);
      await loadConversations();
      if (selected?.id === conv.id) {
        selected = conversations.find(c => c.id === conv.id) ?? null;
      }
    } catch (err: any) {
      notifications.error(`Failed to archive: ${err?.message ?? err}`);
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
    selectedIds = allSelected ? new Set() : new Set(conversations.map(c => c.id));
  }

  async function handleBatchArchive() {
    if (selectedIds.size === 0 || isBatchArchiving) return;
    isBatchArchiving = true;
    const a = await getApi();
    if (!a) { isBatchArchiving = false; return; }
    const ids = [...selectedIds];
    let failed = 0;
    for (const id of ids) {
      try {
        await a.ArchiveConversation(id, activeProfile);
      } catch {
        failed++;
      }
    }
    selectedIds = new Set();
    isBatchArchiving = false;
    if (failed > 0) notifications.error(`${failed} conversation(s) failed to archive`);
    else notifications.success(`${ids.length} conversation(s) archived`);
    await loadConversations();
  }

  function handleRosterUpdated(conv: Conversation) {
    selected = conv;
    conversations = conversations.map(c => (c.id === conv.id ? conv : c));
  }

  function participantIcon(kind: string) {
    if (kind === 'session') return '💻';
    if (kind === 'persona') return '🤖';
    return '👤';
  }

  function formatTime(ts: number) {
    return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }
</script>

<div class="conversations-root">
  <header class="panel-header conversations-header">
    <h1 class="page-title">conversations</h1>
    <div class="header-actions">
      {#if loading}<Spinner />{/if}
      <button class="btn primary" onclick={() => showCreateModal = true}>+ new conversation</button>
    </div>
  </header>
  <div class="channels-layout">
  <!-- Left: conversation list -->
  <div class="channel-list">
    <div class="list-header">
      {#if someSelected}
        <input type="checkbox" class="select-all-cb" checked={allSelected} onclick={toggleSelectAll} title="Select all" />
        <span class="list-title">{selectedIds.size} selected</span>
        <button class="btn-batch-delete" onclick={handleBatchArchive} disabled={isBatchArchiving} title="Archive selected">
          {isBatchArchiving ? 'Archiving…' : 'Archive'}
        </button>
      {:else}
        <span class="list-title">rooms</span>
        {#if loading}<Spinner size={12} />{/if}
      {/if}
    </div>

    {#if loading}
      <div class="list-empty">Loading…</div>
    {:else if !activeProfile}
      <div class="list-empty">Select a workspace profile</div>
    {:else if conversations.length === 0}
      <div class="list-empty">No conversations yet</div>
    {:else}
      <div class="channel-sections">
        {#each [{ label: 'active', rooms: activeConversations, collapsed: activeCollapsed }, { label: 'archived', rooms: archivedConversations, collapsed: archivedCollapsed }] as section (section.label)}
          {#if section.rooms.length > 0}
            <button
              class="section-toggle"
              onclick={() => section.label === 'active' ? (activeCollapsed = !activeCollapsed) : (archivedCollapsed = !archivedCollapsed)}
            >
              <svg class="section-chevron" class:collapsed={section.collapsed} xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg>
              {section.label}
              <span class="section-count">{section.rooms.length}</span>
            </button>
            {#if !section.collapsed}
              <ul class="channel-items">
                {#each section.rooms as conv (conv.id)}
                  <li class="channel-item-row" class:row-selecting={someSelected}>
                    <input
                      type="checkbox"
                      class="row-cb"
                      checked={selectedIds.has(conv.id)}
                      onclick={(e) => { e.stopPropagation(); toggleSelect(conv.id); }}
                    />
                    {#if confirmArchiveId === conv.id}
                      <div class="delete-confirm">
                        <span>Archive?</span>
                        <button class="btn-confirm-yes" onclick={(e) => confirmArchive(conv, e)}>Yes</button>
                        <button class="btn-confirm-no" onclick={cancelArchive}>No</button>
                      </div>
                    {:else}
                      <button
                        class="channel-item"
                        class:active={selected?.id === conv.id}
                        onclick={() => selectConversation(conv)}
                      >
                        <span class="status-dot" class:completed={conv.status === 'archived'}></span>
                        <span class="channel-name">{conv.title}</span>
                        <span class="participant-count">{conv.participants.length}</span>
                      </button>
                      {#if conv.status !== 'archived'}
                        <button class="btn-delete-channel" onclick={(e) => requestArchive(conv, e)} title="Archive conversation" aria-label="Archive {conv.title}">
                          <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="4" rx="1"/><path d="M5 8v11a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8"/><path d="M10 12h4"/></svg>
                        </button>
                      {/if}
                    {/if}
                  </li>
                {/each}
              </ul>
            {/if}
          {/if}
        {/each}
      </div>
    {/if}
  </div>

  <!-- Right: conversation view -->
  <div class="channel-view">
    {#if !selected}
      <EmptyState
        title="No conversation selected"
        message="Select a conversation from the list or create a new one."
      />
    {:else}
      <div class="channel-header">
        <div class="channel-meta">
          <span class="channel-title">#{selected.title}</span>
          <span class="channel-status" class:completed={selected.status === 'archived'}>
            {selected.status}
          </span>
        </div>
        <div class="participant-list">
          {#each selected.participants as p (p.name)}
            <span class="participant-chip" title={p.kind}>
              {participantIcon(p.kind)}
              {p.name}
            </span>
          {/each}
          {#if selected.status === 'active'}
            <button class="btn-personas" onclick={() => showPersonaManager = true}>
              manage personas
            </button>
          {/if}
        </div>
      </div>

      <div class="messages">
        {#if messages.length === 0}
          <div class="no-messages">No messages yet. Start the conversation.</div>
        {:else}
          {#each messages as msg (msg.id)}
            <div class="message" class:mine={msg.author === myName}>
              <div class="message-header">
                <span class="msg-from">{msg.author}</span>
                {#if msg.addressed_to}
                  <span class="msg-addressed">→ @{msg.addressed_to}</span>
                {/if}
                <span class="msg-time">{formatTime(msg.created_at)}</span>
                {#if !msg.streaming && msg.content.trim()}
                  <div class="promote">
                    <button
                      class="promote-trigger"
                      title="Promote this message: to memory, a todo, or a task"
                      aria-label="Promote this message"
                      aria-expanded={promoteMenuFor === msg.id}
                      onclick={() => togglePromoteMenu(msg.id)}
                    >⋯</button>
                    {#if promoteMenuFor === msg.id}
                      <div class="promote-menu" role="menu">
                        {#each Object.entries(PROMOTE_LABELS) as [target, label] (target)}
                          <button
                            class="promote-item"
                            role="menuitem"
                            disabled={promoting !== null}
                            onclick={() => promoteMessage(msg, target as PromoteTarget)}
                          >
                            {promoting === `${msg.id}:${target}` ? `${label}…` : label}
                          </button>
                        {/each}
                      </div>
                    {/if}
                  </div>
                {/if}
              </div>
              <div class="message-content">{msg.content}{#if msg.streaming}<span class="stream-caret"></span>{/if}</div>
            </div>
          {/each}
        {/if}
        {#if thinkingAuthor}
          <div class="thinking-row">{thinkingAuthor} is thinking…</div>
        {:else if quietReason}
          <div class="quiet-row">
            {quietReason === 'max_consecutive_agent_turns'
              ? 'The agents have been talking to each other — your turn.'
              : 'No one has more to add — waiting for you.'}
          </div>
        {/if}
      </div>

      {#if selected.status === 'active'}
        <div class="composer">
          <div class="composer-name">
            <label class="my-name-label" for="my-name">As:</label>
            <input id="my-name" class="my-name-input" bind:value={myName} placeholder="your name" />
          </div>
          {#if addressablePersonas.length > 0}
            <div class="address-row">
              <span class="address-label">Address:</span>
              {#each addressablePersonas as p (p.name)}
                <button
                  class="address-chip"
                  class:active={addressedName?.toLowerCase() === p.name.toLowerCase()}
                  onclick={() => toggleAddress(p.name)}
                  title="Address @{p.name} — they answer, everyone else stays quiet"
                >
                  @{p.name}
                </button>
              {/each}
              {#if addressedName && !addressablePersonas.some(p => p.name.toLowerCase() === addressedName?.toLowerCase())}
                <span class="address-unknown">@{addressedName} is not in this room</span>
              {/if}
            </div>
          {/if}
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
              <button class="btn-end" onclick={handleArchive} disabled={isArchiving}>
                Archive
              </button>
            </div>
          </div>
        </div>
      {:else}
        <div class="channel-ended">
          Conversation archived.
        </div>
      {/if}
    {/if}
  </div>
  </div>
</div>

{#if showPersonaManager && selected}
  <PersonaManager
    conversationId={selected.id}
    profile={activeProfile}
    participants={selected.participants}
    onClose={() => showPersonaManager = false}
    onUpdated={handleRosterUpdated}
  />
{/if}

{#if showCreateModal}
  <NewConversationModal
    myName={myName}
    onClose={() => showCreateModal = false}
    onCreated={handleCreated}
  />
{/if}

<style>
  /* Live-token affordances: a caret on the message being streamed and a
     one-line "…is thinking" row while a persona composes. */
  .stream-caret {
    display: inline-block;
    width: 0.5em;
    height: 1em;
    margin-left: 2px;
    vertical-align: text-bottom;
    background: currentColor;
    opacity: 0.7;
    animation: stream-blink 1s steps(2, start) infinite;
  }

  @keyframes stream-blink {
    to { visibility: hidden; }
  }

  .quiet-row {
    padding: 0.25rem 0.5rem;
    font-size: 0.75rem;
    font-style: italic;
    opacity: 0.45;
  }

  .address-row {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.25rem;
    padding: 0 0 0.3rem;
  }

  .address-label {
    font-size: 0.68rem;
    opacity: 0.5;
    margin-right: 0.15rem;
  }

  .address-chip {
    font: inherit;
    font-size: 0.68rem;
    padding: 0.1rem 0.35rem;
    border-radius: 10px;
    border: 1px solid var(--color-border-primary, #333);
    background: transparent;
    color: inherit;
    cursor: pointer;
    opacity: 0.7;
  }

  .address-chip:hover { opacity: 1; }

  .address-chip.active {
    opacity: 1;
    border-color: var(--accent, #3b82f6);
    color: var(--accent, #3b82f6);
  }

  .address-unknown {
    font-size: 0.68rem;
    opacity: 0.5;
  }

  .btn-personas {
    font: inherit;
    font-size: 0.68rem;
    padding: 0.1rem 0.4rem;
    border-radius: 10px;
    border: 1px dashed var(--color-border-primary, #333);
    background: transparent;
    color: inherit;
    cursor: pointer;
    opacity: 0.7;
  }

  .btn-personas:hover { opacity: 1; }

  .thinking-row {
    padding: 0.25rem 0.5rem;
    font-size: 0.75rem;
    font-style: italic;
    opacity: 0.6;
  }

  .channels-layout {
    display: grid;
    grid-template-columns: 220px 1fr;
    flex: 1;
    min-height: 0;
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



  .list-empty {
    padding: 16px;
    font-size: 12px;
    color: var(--color-text-tertiary);
    text-align: center;
  }

  .channel-items {
    list-style: none;
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

  /* Per-message promote menu (PR3). Anchored to the message header and
     revealed on hover/focus so a wall of turns stays quiet. */
  .promote {
    position: relative;
    margin-left: 6px;
  }

  .promote-trigger {
    font: inherit;
    line-height: 1;
    padding: 0 4px;
    border: none;
    background: transparent;
    color: var(--color-text-tertiary);
    cursor: pointer;
    opacity: 0;
    transition: opacity 0.12s;
  }

  .message:hover .promote-trigger,
  .promote-trigger:focus-visible,
  .promote-trigger[aria-expanded='true'] {
    opacity: 1;
  }

  .promote-menu {
    position: absolute;
    right: 0;
    top: 100%;
    z-index: 20;
    display: flex;
    flex-direction: column;
    min-width: 110px;
    padding: 4px;
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-md, 6px);
    background: var(--color-bg-secondary, #1a1a1a);
    box-shadow: 0 4px 14px rgba(0,0,0,0.35);
  }

  .promote-item {
    font: inherit;
    font-size: 11px;
    text-align: left;
    padding: 4px 8px;
    border: none;
    border-radius: 4px;
    background: transparent;
    color: var(--color-text-primary);
    cursor: pointer;
  }

  .promote-item:hover:not(:disabled) {
    background: rgba(255,255,255,0.06);
  }

  .promote-item:disabled {
    opacity: 0.5;
    cursor: default;
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






  .conversations-root {
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow: hidden;
  }

  .conversations-header {
    padding: var(--panel-padding);
    padding-bottom: var(--spacing-xl);
    border-bottom: 1px solid var(--color-border-primary);
    flex-shrink: 0;
  }

  .header-actions {
    display: flex;
    align-items: center;
    gap: var(--spacing-sm);
  }

  .channel-sections {
    overflow-y: auto;
    flex: 1;
  }

  .section-toggle {
    display: flex;
    align-items: center;
    gap: 6px;
    width: 100%;
    background: none;
    border: none;
    padding: 6px 14px;
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--color-text-tertiary);
    cursor: pointer;
  }

  .section-toggle:hover {
    color: var(--color-text-secondary);
    background: rgba(255,255,255,0.03);
  }

  .section-chevron {
    transition: transform 0.15s;
    flex-shrink: 0;
  }

  .section-chevron.collapsed {
    transform: rotate(-90deg);
  }

  .section-count {
    margin-left: auto;
    font-family: var(--font-mono);
    font-size: 10px;
  }
</style>

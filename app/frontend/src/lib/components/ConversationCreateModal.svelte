<script lang="ts">
  /**
   * New-conversation modal — names a channel and assembles its participant
   * roster (running sessions and/or Ollama models), then creates it.
   *
   * Extracted from ConversationsPanel. On open it pre-populates one participant
   * row per running session in the active profile (or just the `seed` sessions
   * when the modal was launched from "start conversation with…"), so the user
   * unchecks/removes rather than building the list from scratch. Owns all of its
   * own state; signals the parent via `onCreated` (with the new channel) and
   * `onClose`.
   */
  import { onMount } from 'svelte';
  import { getApi } from '../utils/api';
  import { notifications } from '../notifications.svelte';
  import { profileState } from '../stores.svelte';
  import Modal from './Modal.svelte';

  let {
    seed = [],
    onClose,
    onCreated,
  }: {
    seed?: string[];
    onClose: () => void;
    onCreated: (channel: any) => void;
  } = $props();

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

  onMount(() => {
    void loadSessions();
  });

  async function loadSessions() {
    const a = await getApi();
    if (!a) return;
    try {
      const all = ((await a.GetSessions(profileState.active?.name ?? '')) ?? []) as any[];
      const activeProfile = profileState.active?.name?.toLowerCase() ?? '';
      availableSessions = all.filter((s: any) => {
        if (!s.active) return false;
        if (!activeProfile) return true;
        return (s.workspace_profile ?? '').toLowerCase() === activeProfile;
      });
      // Seed participantRows. Explicit `seed` (from "start conversation from
      // Sessions panel") wins; otherwise every running session is included.
      const namesToSeed = seed.length > 0
        ? availableSessions.filter(s => seed.includes(s.name))
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
      onCreated(channel);
    } catch (err: any) {
      notifications.error(`Failed to create channel: ${err}`);
    } finally {
      isCreating = false;
    }
  }
</script>

<Modal onClose={onClose} maxWidth="680px">
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
      <button class="btn-cancel" onclick={onClose}>Cancel</button>
      <button class="btn-primary" onclick={handleCreate} disabled={isCreating || !newChannelName.trim()}>
        {isCreating ? 'Creating…' : 'Create Channel'}
      </button>
    </div>
  </div>
</Modal>

<style>
  .modal-body {
    display: flex;
    flex-direction: column;
    gap: 12px;
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
    grid-template-columns: minmax(100px,140px) minmax(80px,100px) minmax(120px,1fr) minmax(120px,1fr) auto;
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

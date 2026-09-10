<script lang="ts">
  /**
   * New-conversation modal for the multi-agent Chat engine.
   *
   * A conversation is a title plus a roster: you (a `human` participant) and a
   * persona — a lightweight LLM participant defined by a model and a role
   * prompt, driven server-side through the complete() seam. PR1 supports one
   * persona per room; the roster field stays a list so PR2 can add more without
   * reshaping the record.
   *
   * The old channel modal (ConversationCreateModal.svelte) went out with the
   * channels engine in PR4; this is the only create surface.
   */
  import { onMount } from 'svelte';
  import { getApi } from '../utils/api';
  import { notifications } from '../notifications.svelte';
  import { profileState } from '../stores.svelte';
  import Modal from './Modal.svelte';

  let {
    myName = 'user',
    onClose,
    onCreated,
  }: {
    myName?: string;
    onClose: () => void;
    onCreated: (conversation: any) => void;
  } = $props();

  let title = $state('');
  let personaName = $state('sage');
  let personaModel = $state('');
  let rolePrompt = $state('');
  let models = $state<string[]>([]);
  let isCreating = $state(false);

  const canCreate = $derived(title.trim().length > 0 && personaName.trim().length > 0);

  onMount(() => {
    void loadModels();
  });

  async function loadModels() {
    const a = await getApi();
    if (!a) return;
    try {
      const list = (await a.ListOllamaModels()) ?? [];
      models = list.map((m: any) => m.name).filter(Boolean);
      if (!personaModel && models.length > 0) personaModel = models[0];
    } catch {
      // No Ollama reachable — leaving the model blank lets the seam pick its
      // own default rather than blocking room creation.
    }
  }

  async function handleCreate() {
    if (!canCreate || isCreating) return;
    const profile = profileState.active?.name ?? '';
    if (!profile) {
      notifications.error('Select a workspace profile first — conversations are profile-scoped.');
      return;
    }
    isCreating = true;
    const a = await getApi();
    if (!a) { isCreating = false; return; }
    try {
      const conversation = await a.CreateConversation({
        title: title.trim(),
        profile,
        participants: [
          { name: myName, kind: 'human' },
          {
            name: personaName.trim(),
            kind: 'persona',
            model_target: personaModel ? { provider: 'ollama', model: personaModel } : undefined,
            role_prompt: rolePrompt.trim() || undefined,
          },
        ],
      } as any);
      notifications.success(`Conversation "${title}" created`);
      onCreated(conversation);
    } catch (err: any) {
      notifications.error(`Failed to create conversation: ${err?.message ?? err}`);
    } finally {
      isCreating = false;
    }
  }
</script>

<Modal onClose={onClose} maxWidth="560px">
  <div class="modal-body">
    <h2>New conversation</h2>

    <label class="field-label" for="conv-title">Title</label>
    <input id="conv-title" class="field-input" bind:value={title} placeholder="e.g. architecture-debate" />

    <div class="section-label">Persona</div>
    <div class="persona-grid">
      <div>
        <label class="field-label" for="persona-name">Name</label>
        <input id="persona-name" class="field-input" bind:value={personaName} placeholder="sage" />
      </div>
      <div>
        <label class="field-label" for="persona-model">Model</label>
        {#if models.length > 0}
          <select id="persona-model" class="field-input" bind:value={personaModel}>
            {#each models as m (m)}<option value={m}>{m}</option>{/each}
          </select>
        {:else}
          <input id="persona-model" class="field-input" bind:value={personaModel} placeholder="default" />
        {/if}
      </div>
    </div>

    <label class="field-label" for="persona-prompt">Role prompt</label>
    <textarea
      id="persona-prompt"
      class="field-input prompt-input"
      bind:value={rolePrompt}
      rows="3"
      placeholder="How should this persona behave? Leave blank for a sensible default."
    ></textarea>

    <div class="modal-actions">
      <button class="btn-cancel" onclick={onClose}>Cancel</button>
      <button class="btn-primary" onclick={handleCreate} disabled={!canCreate || isCreating}>
        {isCreating ? 'Creating…' : 'Create'}
      </button>
    </div>
  </div>
</Modal>

<style>
  .modal-body { display: flex; flex-direction: column; gap: 0.6rem; }
  h2 { margin: 0 0 0.4rem; font-size: 0.95rem; font-weight: 600; }
  .field-label {
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    opacity: 0.7;
  }
  .field-input {
    width: 100%;
    padding: 0.4rem 0.5rem;
    border: 1px solid var(--border, #333);
    border-radius: 4px;
    background: var(--input-bg, #1a1a1a);
    color: inherit;
    font: inherit;
  }
  .prompt-input { resize: vertical; }
  .section-label {
    margin-top: 0.4rem;
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    opacity: 0.7;
  }
  .persona-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0.5rem; }
  .modal-actions {
    display: flex;
    justify-content: flex-end;
    gap: 0.5rem;
    margin-top: 0.8rem;
  }
  .btn-cancel, .btn-primary {
    padding: 0.35rem 0.8rem;
    border-radius: 4px;
    border: 1px solid var(--border, #333);
    background: transparent;
    color: inherit;
    cursor: pointer;
    font: inherit;
  }
  .btn-primary { background: var(--accent, #3b82f6); border-color: transparent; color: #fff; }
  .btn-primary:disabled { opacity: 0.5; cursor: default; }
</style>

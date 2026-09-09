<script lang="ts">
  /**
   * Persona management for one conversation (PR2).
   *
   * A room's roster is not fixed at creation any more: the turn orchestrator
   * evaluates every persona on every message, so adding one changes who can
   * speak from the next turn onward. This panel is the write path for that
   * roster — name, model, role prompt, cooldown — over
   * Add/RemoveConversationParticipant.
   *
   * Editing is the same call as adding: the server replaces a participant with
   * a matching name, so clicking a persona loads it into the form and saving
   * updates it in place.
   */
  import { onMount } from 'svelte';
  import { getApi } from '../utils/api';
  import { notifications } from '../notifications.svelte';
  import Modal from './Modal.svelte';

  interface Participant {
    name: string;
    kind: string;
    model_target?: Record<string, any>;
    role_prompt?: string;
    cooldown_s?: number;
    joined_at?: number;
  }

  let {
    conversationId,
    profile,
    participants = [],
    onClose,
    onUpdated,
  }: {
    conversationId: string;
    profile: string;
    participants?: Participant[];
    onClose: () => void;
    onUpdated: (conversation: any) => void;
  } = $props();

  // The roster comes from the panel until a write returns a fresher one; the
  // override is what the server last handed back, so an add/remove renders
  // immediately without waiting for the parent to re-fetch.
  let rosterOverride = $state<Participant[] | null>(null);
  const roster = $derived(rosterOverride ?? participants);
  let models = $state<string[]>([]);
  let busy = $state(false);
  let removing = $state<string | null>(null);

  // Form state. `editing` holds the original name while updating an existing
  // persona, so a rename removes the old entry instead of leaving a ghost.
  let editing = $state<string | null>(null);
  let name = $state('');
  let model = $state('');
  let rolePrompt = $state('');
  /** Blank means "use the server default" — distinct from an explicit 0. */
  let cooldown = $state('');

  const personas = $derived(roster.filter(p => p.kind === 'persona'));
  const humans = $derived(roster.filter(p => p.kind !== 'persona'));
  const canSave = $derived(name.trim().length > 0 && !busy);

  onMount(() => { void loadModels(); });

  async function loadModels() {
    const a = await getApi();
    if (!a) return;
    try {
      models = ((await a.ListOllamaModels()) ?? []).map((m: any) => m.name).filter(Boolean);
    } catch {
      // No Ollama reachable — a free-text model field still works.
    }
  }

  function resetForm() {
    editing = null;
    name = '';
    model = '';
    rolePrompt = '';
    cooldown = '';
  }

  function loadIntoForm(p: Participant) {
    editing = p.name;
    name = p.name;
    model = (p.model_target?.model as string) ?? '';
    rolePrompt = p.role_prompt ?? '';
    cooldown = p.cooldown_s === undefined || p.cooldown_s === null ? '' : String(p.cooldown_s);
  }

  function parsedCooldown(): number | undefined {
    const raw = cooldown.trim();
    if (raw === '') return undefined;
    const n = Number(raw);
    return Number.isFinite(n) && n >= 0 ? n : undefined;
  }

  async function handleSave() {
    if (!canSave) return;
    busy = true;
    const a = await getApi();
    if (!a) { busy = false; return; }
    const trimmed = name.trim();
    try {
      // A rename is a remove + add: the name is the addressing key, so a stale
      // entry would keep answering to @old-name.
      if (editing && editing !== trimmed) {
        await a.RemoveConversationParticipant(conversationId, profile, editing);
      }
      const conv = await a.AddConversationParticipant(conversationId, profile, {
        name: trimmed,
        kind: 'persona',
        model_target: model.trim() ? { provider: 'ollama', model: model.trim() } : undefined,
        role_prompt: rolePrompt.trim() || undefined,
        cooldown_s: parsedCooldown(),
      } as any);
      rosterOverride = (conv as any).participants ?? roster;
      onUpdated(conv);
      notifications.success(editing ? `Persona "${trimmed}" updated` : `Persona "${trimmed}" added`);
      resetForm();
    } catch (err: any) {
      notifications.error(`Failed to save persona: ${err?.message ?? err}`);
    } finally {
      busy = false;
    }
  }

  async function handleRemove(p: Participant) {
    removing = null;
    busy = true;
    const a = await getApi();
    if (!a) { busy = false; return; }
    try {
      const conv = await a.RemoveConversationParticipant(conversationId, profile, p.name);
      rosterOverride = (conv as any).participants ?? roster;
      onUpdated(conv);
      if (editing === p.name) resetForm();
      notifications.success(`Persona "${p.name}" removed`);
    } catch (err: any) {
      notifications.error(`Failed to remove persona: ${err?.message ?? err}`);
    } finally {
      busy = false;
    }
  }
</script>

<Modal onClose={onClose} maxWidth="600px">
  <div class="modal-body">
    <h2>Personas</h2>
    <p class="hint">
      Every persona is evaluated on each new message; the most relevant one or two
      reply, then cool down. Leave the cooldown blank to use the server default.
    </p>

    {#if personas.length === 0}
      <div class="roster-empty">No personas yet — this room is humans only.</div>
    {:else}
      <ul class="roster">
        {#each personas as p (p.name)}
          <li class="roster-row" class:editing={editing === p.name}>
            <button class="roster-main" onclick={() => loadIntoForm(p)} title="Edit {p.name}">
              <span class="roster-name">🤖 {p.name}</span>
              <span class="roster-meta">
                {p.model_target?.model ?? 'default model'}
                · cooldown {p.cooldown_s ?? '—'}s
              </span>
              {#if p.role_prompt}<span class="roster-prompt">{p.role_prompt}</span>{/if}
            </button>
            {#if removing === p.name}
              <span class="confirm">
                <button class="btn-yes" onclick={() => handleRemove(p)} disabled={busy}>Remove</button>
                <button class="btn-no" onclick={() => removing = null}>Cancel</button>
              </span>
            {:else}
              <button class="btn-remove" onclick={() => removing = p.name} disabled={busy} aria-label="Remove {p.name}">
                ×
              </button>
            {/if}
          </li>
        {/each}
      </ul>
    {/if}

    {#if humans.length > 0}
      <div class="humans">
        {#each humans as h (h.name)}<span class="human-chip">👤 {h.name}</span>{/each}
      </div>
    {/if}

    <div class="section-label">{editing ? `Edit "${editing}"` : 'Add a persona'}</div>
    <div class="grid">
      <div>
        <label class="field-label" for="pm-name">Name</label>
        <input id="pm-name" class="field-input" bind:value={name} placeholder="sage" />
      </div>
      <div>
        <label class="field-label" for="pm-model">Model</label>
        {#if models.length > 0}
          <input id="pm-model" class="field-input" list="pm-models" bind:value={model} placeholder="default" />
          <datalist id="pm-models">
            {#each models as m (m)}<option value={m}></option>{/each}
          </datalist>
        {:else}
          <input id="pm-model" class="field-input" bind:value={model} placeholder="default" />
        {/if}
      </div>
      <div>
        <label class="field-label" for="pm-cooldown">Cooldown (s)</label>
        <input id="pm-cooldown" class="field-input" bind:value={cooldown} inputmode="decimal" placeholder="default" />
      </div>
    </div>

    <label class="field-label" for="pm-prompt">Role prompt</label>
    <textarea
      id="pm-prompt"
      class="field-input prompt-input"
      bind:value={rolePrompt}
      rows="3"
      placeholder="How should this persona behave? Blank uses a sensible default."
    ></textarea>

    <div class="modal-actions">
      {#if editing}
        <button class="btn-cancel" onclick={resetForm}>New persona</button>
      {/if}
      <button class="btn-cancel" onclick={onClose}>Close</button>
      <button class="btn-primary" onclick={handleSave} disabled={!canSave}>
        {busy ? 'Saving…' : editing ? 'Save' : 'Add'}
      </button>
    </div>
  </div>
</Modal>

<style>
  .modal-body { display: flex; flex-direction: column; gap: 0.6rem; }
  h2 { margin: 0; font-size: 0.95rem; font-weight: 600; }
  .hint { margin: 0; font-size: 0.72rem; opacity: 0.65; line-height: 1.4; }
  .roster { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.3rem; }
  .roster-row {
    display: flex;
    align-items: flex-start;
    gap: 0.4rem;
    border: 1px solid var(--border, #333);
    border-radius: 4px;
    padding: 0.35rem 0.5rem;
  }
  .roster-row.editing { border-color: var(--accent, #3b82f6); }
  .roster-main {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
    background: none;
    border: none;
    color: inherit;
    font: inherit;
    text-align: left;
    cursor: pointer;
    padding: 0;
  }
  .roster-name { font-size: 0.8rem; font-weight: 600; }
  .roster-meta { font-size: 0.68rem; opacity: 0.6; }
  .roster-prompt {
    font-size: 0.68rem;
    opacity: 0.5;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 100%;
  }
  .roster-empty { font-size: 0.75rem; opacity: 0.6; }
  .humans { display: flex; flex-wrap: wrap; gap: 0.3rem; }
  .human-chip { font-size: 0.68rem; opacity: 0.6; }
  .btn-remove {
    background: none;
    border: none;
    color: inherit;
    opacity: 0.5;
    cursor: pointer;
    font-size: 1rem;
    line-height: 1;
    padding: 0 0.25rem;
  }
  .btn-remove:hover { opacity: 1; }
  .confirm { display: flex; gap: 0.25rem; align-items: center; }
  .btn-yes, .btn-no {
    font: inherit;
    font-size: 0.68rem;
    padding: 0.15rem 0.4rem;
    border-radius: 3px;
    border: 1px solid var(--border, #333);
    background: transparent;
    color: inherit;
    cursor: pointer;
  }
  .section-label {
    margin-top: 0.4rem;
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    opacity: 0.7;
  }
  .field-label { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.04em; opacity: 0.7; }
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
  .grid { display: grid; grid-template-columns: 1fr 1fr 0.6fr; gap: 0.5rem; }
  .modal-actions { display: flex; justify-content: flex-end; gap: 0.5rem; margin-top: 0.8rem; }
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

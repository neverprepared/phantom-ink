<script lang="ts">
  // "Begin work" — the Code panel's hand-off from a GitHub row into one of two
  // lanes. The template picker SEEDS the textarea rather than replacing the
  // form: templates get you to a sensible brief in one click, and the operator
  // still edits before sending.
  //
  //   autonomous task     → the existing hub task path (agent picker applies)
  //   interactive session → a container session the operator attaches to; the
  //                         session clones the repo itself from the prompt, so
  //                         no agent role is chosen here
  //
  // The third lane — clone to this host and open a terminal — skips the modal
  // entirely; it's the repo row's direct button.
  import Modal from './Modal.svelte';
  import { codeState } from '../stores/code.svelte';

  let { profile }: { profile: string } = $props();

  const target = $derived(codeState.dispatchTarget);
  const kindLabel = $derived(
    target?.kind === 'pr' ? 'pull request' : target?.kind === 'issue' ? 'issue' : 'repository'
  );
  const session = $derived(codeState.dispatchLane === 'session');
  const canSend = $derived(codeState.dispatchPrompt.trim().length > 0 && !codeState.dispatching);
</script>

{#if target}
  <Modal onClose={() => codeState.closeDispatch()} maxWidth="620px">
    <header class="dm-head">
      <h2>Begin work</h2>
      <p class="dm-target">
        on {kindLabel}
        <strong>{target.repoFullName}{target.number ? `#${target.number}` : ''}</strong>
      </p>
      {#if target.title}<p class="dm-title">{target.title}</p>{/if}
    </header>

    <div class="dm-field">
      <span class="form-label">launch as</span>
      <div class="dm-lanes" role="group" aria-label="Launch as">
        <button
          type="button"
          class="dm-lane"
          class:active={!session}
          onclick={() => codeState.setLane('task')}
        >
          ⚡ Autonomous task
        </button>
        <button
          type="button"
          class="dm-lane"
          class:active={session}
          onclick={() => codeState.setLane('session')}
        >
          ▶ Interactive session
        </button>
      </div>
    </div>

    <div class="dm-field">
      <span class="form-label">template</span>
      <div class="dm-templates">
        {#each codeState.templates as tpl}
          <button type="button" class="dm-chip" onclick={() => codeState.applyTemplate(tpl)}>
            {tpl.label}
          </button>
        {/each}
      </div>
    </div>

    <label class="dm-field">
      <span class="form-label">prompt <span class="dm-opt">(editable)</span></span>
      <textarea bind:value={codeState.dispatchPrompt} rows="9"></textarea>
    </label>

    <div class="dm-row">
      {#if !session}
        <!-- A session has no agent role to pick — you are the agent. -->
        <label class="dm-field">
          <span class="form-label">agent</span>
          <select bind:value={codeState.dispatchAgent}>
            {#each codeState.agents as name}<option value={name}>{name}</option>{/each}
          </select>
        </label>
      {/if}
      <label class="dm-field">
        <span class="form-label">profile</span>
        <input value={profile} readonly />
      </label>
    </div>
    <p class="dm-hint">
      {#if session}
        Runs as <code>{profile}</code> in a container that clones
        <code>{target.repoURL}</code> itself. Attach when it comes up.
      {:else}
        Runs as <code>{profile}</code> against <code>{target.repoURL}</code>. Fire-and-forget —
        watch it in Jobs.
      {/if}
    </p>

    <footer class="dm-actions">
      <button type="button" class="dm-secondary" onclick={() => codeState.closeDispatch()}>
        Cancel
      </button>
      <button type="button" class="dm-primary" disabled={!canSend} onclick={() => codeState.submit(profile)}>
        {#if codeState.dispatching}
          {session ? 'Launching…' : 'Dispatching…'}
        {:else}
          {session ? 'Launch session' : 'Dispatch'}
        {/if}
      </button>
    </footer>
  </Modal>
{/if}

<style>
  .dm-head { margin-bottom: var(--spacing-lg); }
  .dm-head h2 { font-size: 1.05rem; margin: 0; color: var(--color-text-primary); }
  .dm-target { margin: 4px 0 0; font-size: 0.85rem; color: var(--color-text-muted); }
  .dm-target strong { color: var(--color-text-secondary); font-family: var(--font-mono); }
  .dm-title { margin: 4px 0 0; font-size: 0.85rem; color: var(--color-text-secondary); }

  .dm-field { display: flex; flex-direction: column; gap: 4px; margin-bottom: var(--spacing-md); }
  .dm-row { display: flex; gap: var(--spacing-md); }
  .dm-row .dm-field { flex: 1; }
  .form-label {
    font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.04em;
    color: var(--color-text-tertiary);
  }
  .dm-opt { text-transform: none; letter-spacing: 0; color: var(--color-text-muted); }

  textarea, select, input {
    background: var(--color-bg-primary);
    border: 1px solid var(--color-border-secondary);
    border-radius: var(--radius-sm);
    color: var(--color-text-primary);
    padding: 6px 8px;
    font-size: 0.85rem;
    font-family: inherit;
  }
  textarea { resize: vertical; font-family: var(--font-mono); line-height: 1.45; }
  input[readonly] { color: var(--color-text-muted); }

  .dm-templates { display: flex; flex-wrap: wrap; gap: 6px; }
  .dm-chip {
    background: var(--color-bg-primary);
    border: 1px solid var(--color-border-secondary);
    border-radius: 999px;
    color: var(--color-text-secondary);
    padding: 4px 10px;
    font-size: 0.78rem;
    cursor: pointer;
  }
  .dm-chip:hover { border-color: var(--color-accent); color: var(--color-accent); }

  .dm-lanes { display: flex; gap: 0; }
  .dm-lane {
    background: var(--color-bg-primary);
    border: 1px solid var(--color-border-secondary);
    color: var(--color-text-muted);
    padding: 5px 12px;
    font-size: 0.78rem;
    font-family: inherit;
    cursor: pointer;
  }
  .dm-lane:first-child { border-radius: var(--radius-sm) 0 0 var(--radius-sm); }
  .dm-lane:last-child { border-radius: 0 var(--radius-sm) var(--radius-sm) 0; border-left: none; }
  .dm-lane.active {
    border-color: var(--color-accent);
    color: var(--color-accent);
    background: var(--color-bg-secondary);
  }

  .dm-hint { margin: 0 0 var(--spacing-md); font-size: 0.75rem; color: var(--color-text-muted); }
  .dm-hint code { font-family: var(--font-mono); }

  .dm-actions { display: flex; justify-content: flex-end; gap: 8px; }
  .dm-primary {
    background: var(--color-accent);
    color: var(--color-bg-primary);
    border: none; border-radius: var(--radius-sm);
    padding: 7px 16px; font-weight: 600; cursor: pointer;
  }
  .dm-primary:disabled { opacity: 0.5; cursor: not-allowed; }
  .dm-secondary {
    background: transparent; color: var(--color-text-secondary);
    border: 1px solid var(--color-border-secondary); border-radius: var(--radius-sm);
    padding: 7px 14px; cursor: pointer;
  }
</style>

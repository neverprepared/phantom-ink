<script>
  import { onMount } from 'svelte';
  import Modal from './Modal.svelte';

  let { name, onClose } = $props();

  let displayName = $derived.by(() => {
    for (const prefix of ['developer-', 'researcher-', 'performer-']) {
      if (name.startsWith(prefix)) return name.slice(prefix.length);
    }
    return name;
  });
  let execCmd = $derived(`docker exec -it ${name} tmux attach -t main`);
  let copied = $state(false);

  let modalElement;
  let previousActiveElement;

  onMount(() => {
    previousActiveElement = document.activeElement;
    const firstButton = modalElement?.querySelector('button');
    if (firstButton) firstButton.focus();

    return () => {
      if (previousActiveElement) previousActiveElement.focus();
    };
  });


  function copyCmd() {
    navigator.clipboard.writeText(execCmd);
    copied = true;
    setTimeout(() => copied = false, 1500);
  }
</script>

<Modal {onClose}>
  {#snippet children()}
    <div bind:this={modalElement}>
      <h2 id="modal-title">{displayName}</h2>
      <div class="modal-field">
        <label for="exec-cmd">enter container</label>
        <input type="text" id="exec-cmd" readonly value={execCmd} onclick={(e) => e.target.select()} />
      </div>
      <div class="modal-actions">
        <button class="modal-cancel" onclick={onClose}>close</button>
        <button class="modal-submit" onclick={copyCmd}>{copied ? 'copied' : 'copy'}</button>
      </div>
    </div>
  {/snippet}
</Modal>


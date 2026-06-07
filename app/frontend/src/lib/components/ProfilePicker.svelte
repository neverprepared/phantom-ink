<script lang="ts">
  import { profileState } from '../stores.svelte';

  let { selected = $bindable(''), label = 'profile' }: { selected: string; label?: string } = $props();
  let profiles = $derived(profileState.visible);
</script>

<div class="field">
  <label for="pp-{label}">{label}</label>
  {#if profiles.length > 0}
    <div class="profile-picker" id="pp-{label}">
      {#each profiles as p (p.name)}
        <button
          class="profile-opt"
          class:active={selected === p.name}
          onclick={() => selected = p.name}
          type="button"
        >{p.name}</button>
      {/each}
    </div>
  {:else}
    <p class="hint">no profiles found — create one in settings</p>
  {/if}
</div>

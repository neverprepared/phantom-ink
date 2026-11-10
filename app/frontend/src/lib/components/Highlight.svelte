<script lang="ts">
  // Renders `text` with the runs matching `terms` marked. With no terms it is
  // the plain string, so a caller can use it unconditionally wherever a
  // searchable label is drawn.
  import { highlightSegments } from '../utils/search';

  let { text = '', terms = [] }: { text?: string; terms?: string[] } = $props();

  const segments = $derived(highlightSegments(text, terms));
</script>

{#each segments as seg, i (i)}{#if seg.match}<mark>{seg.text}</mark>{:else}{seg.text}{/if}{/each}

<style>
  /* The browser default is a yellow block that fights every theme. Tint the
     accent instead and keep the row's own text color. */
  mark {
    background: color-mix(in srgb, var(--color-accent, var(--color-info)) 28%, transparent);
    color: inherit;
    border-radius: 2px;
    padding: 0 1px;
  }
</style>

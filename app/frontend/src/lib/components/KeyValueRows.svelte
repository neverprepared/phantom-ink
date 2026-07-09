<script lang="ts">
  // Dumb bindable key/value row editor (headers, artifact_refs, …).
  // The GatewayEnvEditor row shape without its secrets machinery.

  export interface KVRow {
    key: string;
    value: string;
  }

  let {
    rows = $bindable([] as KVRow[]),
    keyPlaceholder = 'key',
    valuePlaceholder = 'value',
  }: {
    rows?: KVRow[];
    keyPlaceholder?: string;
    valuePlaceholder?: string;
  } = $props();

  function addRow() {
    rows = [...rows, { key: '', value: '' }];
  }

  function removeRow(i: number) {
    rows = rows.filter((_, idx) => idx !== i);
  }
</script>

<div class="kv-rows">
  {#each rows as row, i (i)}
    <div class="kv-row">
      <input class="kv-input key" bind:value={row.key} placeholder={keyPlaceholder} />
      <input class="kv-input" bind:value={row.value} placeholder={valuePlaceholder} />
      <button class="kv-btn" onclick={() => removeRow(i)} title="remove">✕</button>
    </div>
  {/each}
  <button class="kv-add" onclick={addRow}>+ add</button>
</div>

<style>
  .kv-rows { display: flex; flex-direction: column; gap: 4px; }
  .kv-row { display: flex; gap: 6px; align-items: center; }
  .kv-input {
    background: var(--color-bg-primary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm);
    padding: 4px 8px;
    font-size: 11px;
    font-family: var(--font-mono);
    color: var(--color-text-primary);
    flex: 1;
    min-width: 0;
  }
  .kv-input.key { flex: 0 0 38%; }
  .kv-input:focus { outline: none; border-color: var(--color-accent); }
  .kv-btn {
    background: none;
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm);
    padding: 2px 6px;
    font-size: 10px;
    cursor: pointer;
    color: var(--color-text-muted);
    font-family: var(--font-mono);
  }
  .kv-btn:hover { border-color: var(--color-error); color: var(--color-error); }
  .kv-add {
    align-self: flex-start;
    background: none;
    border: none;
    color: var(--color-text-tertiary);
    font-size: 10px;
    font-family: var(--font-mono);
    cursor: pointer;
    padding: 2px 0;
  }
  .kv-add:hover { color: var(--color-accent); }
</style>

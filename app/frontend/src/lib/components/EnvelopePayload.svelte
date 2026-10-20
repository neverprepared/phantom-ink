<script lang="ts">
  /**
   * Expanded payload view for one event-log row.
   *
   * The bus persists the WHOLE envelope (`agent_store.ingest` writes it to
   * `agent_events.envelope` as JSON) and the typed payload lives in
   * `metadata` -- for `llm.completion` that is the LlmCompletionRecord:
   * backend, model, tokens, cost, attempts[]. The log rows render only
   * title + status, so without this the payload was reachable only as a
   * `title=` hover tooltip full of unformatted JSON.
   *
   * `payload` is whatever the row has: a search result's `envelope`, or a
   * live SSE frame's parsed body. Envelope-shaped input gets the field grid
   * plus a metadata block; anything else falls back to raw pretty JSON.
   */

  let { payload }: { payload: unknown } = $props();

  // Display order for the envelope's own scalar fields. Anything not listed
  // (tags, metadata, actions, outcome) is rendered separately or left to the
  // full-JSON view, so a contract addition shows up there rather than vanishing.
  const FIELD_ORDER = [
    'id',
    'kind',
    'type',
    'status',
    'source',
    'workspace',
    'parent_id',
    'subtitle',
    'description',
  ];

  const obj = $derived(
    payload && typeof payload === 'object' && !Array.isArray(payload)
      ? (payload as Record<string, any>)
      : null
  );

  const fields = $derived(
    obj
      ? FIELD_ORDER.filter((k) => obj[k] !== undefined && obj[k] !== null && obj[k] !== '').map(
          (k) => [k, String(obj[k])] as [string, string]
        )
      : []
  );

  const tags = $derived(Array.isArray(obj?.tags) ? (obj.tags as unknown[]).map(String) : []);

  const metadata = $derived(
    obj?.metadata && typeof obj.metadata === 'object' && Object.keys(obj.metadata).length > 0
      ? (obj.metadata as Record<string, unknown>)
      : null
  );

  const full = $derived(JSON.stringify(payload ?? null, null, 2));

  // A non-envelope payload has nothing else to show, so the raw view is the
  // only view -- open it without a click rather than rendering an empty box.
  const fullOnly = $derived(fields.length === 0 && !metadata);

  let showFull = $state(false);
  let copied = $state(false);

  async function copyJson() {
    try {
      await navigator.clipboard.writeText(full);
      copied = true;
      setTimeout(() => (copied = false), 1500);
    } catch {
      // Clipboard unavailable (no permission / not focused) -- the full JSON
      // view is still there to select by hand.
    }
  }
</script>

<div class="payload">
  {#if fields.length > 0}
    <dl class="fields">
      {#each fields as [key, value] (key)}
        <dt>{key}</dt>
        <dd>{value}</dd>
      {/each}
    </dl>
  {/if}

  {#if tags.length > 0}
    <div class="tags">
      {#each tags as tag (tag)}<span class="tag">{tag}</span>{/each}
    </div>
  {/if}

  {#if metadata}
    <div class="section">
      <span class="section-label">metadata</span>
      <pre class="json">{JSON.stringify(metadata, null, 2)}</pre>
    </div>
  {/if}

  <div class="actions">
    {#if !fullOnly}
      <button class="link-btn" onclick={() => (showFull = !showFull)}>
        {showFull ? '▾' : '▸'} full envelope
      </button>
    {/if}
    <button class="link-btn" onclick={copyJson}>{copied ? 'copied' : 'copy json'}</button>
  </div>

  {#if showFull || fullOnly}
    <pre class="json">{full}</pre>
  {/if}
</div>

<style>
  .payload {
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm);
    padding: 8px 10px;
    display: flex;
    flex-direction: column;
    gap: 8px;
    font-family: var(--font-mono);
    font-size: 11px;
  }

  .fields {
    display: grid;
    grid-template-columns: max-content 1fr;
    gap: 2px 12px;
    margin: 0;
  }

  .fields dt {
    color: var(--color-text-muted);
  }

  .fields dd {
    margin: 0;
    color: var(--color-text-primary);
    overflow-wrap: anywhere;
  }

  .tags {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }

  .tag {
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm);
    padding: 1px 6px;
    font-size: 10px;
    color: var(--color-text-tertiary);
  }

  .section {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .section-label {
    color: var(--color-text-muted);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }

  .json {
    margin: 0;
    padding: 6px 8px;
    background: var(--color-bg-primary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm);
    color: var(--color-text-secondary);
    font-size: 11px;
    line-height: 1.45;
    max-height: 340px;
    overflow: auto;
    white-space: pre;
  }

  .actions {
    display: flex;
    gap: 12px;
  }

  .link-btn {
    background: none;
    border: none;
    padding: 0;
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-muted);
    cursor: pointer;
  }

  .link-btn:hover {
    color: var(--color-accent);
  }
</style>

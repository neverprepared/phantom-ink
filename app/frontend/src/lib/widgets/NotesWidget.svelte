<script lang="ts">
  import { untrack } from 'svelte';
  import Icon from '../components/Icon.svelte';

  let { config }: { config: { widgetId?: string } } = $props();

  // widgetId is stamped at widget creation and never changes
  const STORAGE_KEY = untrack(() => `pi-notes-${config?.widgetId ?? 'default'}`);

  function loadNotes(): string {
    try { return localStorage.getItem(STORAGE_KEY) ?? ''; } catch { return ''; }
  }

  let notes = $state(loadNotes());

  function save(v: string) {
    notes = v;
    try { localStorage.setItem(STORAGE_KEY, v); } catch {}
  }
</script>

<div class="notes-widget">
  <div class="widget-header">
    <Icon name="note" size={15} style="color: var(--text-muted); flex-shrink: 0;" />
    <span class="widget-title">» SCRATCHPAD</span>
  </div>
  <textarea
    class="notes-area"
    value={notes}
    oninput={(e) => save((e.target as HTMLTextAreaElement).value)}
    placeholder="take notes…"
    spellcheck={false}
  ></textarea>
</div>

<style>
  .notes-widget {
    display: flex;
    flex-direction: column;
    height: 100%;
  }

  .widget-header {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 12px 14px 10px;
    border-bottom: 1px solid var(--border, var(--color-border-primary));
    flex-shrink: 0;
  }

  .widget-title {
    font-family: var(--font-mono);
    font-size: 11px;
    letter-spacing: .1em;
    text-transform: uppercase;
    color: var(--text-muted, var(--color-text-secondary));
  }

  .notes-area {
    flex: 1;
    resize: none;
    border: none;
    outline: none;
    background: transparent;
    color: var(--text, var(--color-text-primary));
    font-family: var(--font-mono);
    font-size: 13px;
    line-height: 1.7;
    padding: 14px;
    box-shadow: none;
    width: 100%;
    min-height: 0;
  }

  .notes-area::placeholder {
    color: var(--text-faint, var(--color-text-tertiary));
  }
</style>

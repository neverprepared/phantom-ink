<script lang="ts">
  /**
   * Renders a markdown string as HTML.
   *
   * SECURITY — the whole point of this component. The markdown it is handed is
   * UNTRUSTED third-party content (a repository README written by whoever owns
   * that repo), and `{@html}` in a Wails webview runs with the app's own
   * privileges. So `marked`'s output NEVER reaches the DOM directly: it goes
   * through DOMPurify with an explicit allow-list first. `script`, `iframe`,
   * `style`, `form`, every `on*` handler, and `javascript:` URLs are stripped.
   *
   * Do not "simplify" this by dropping the sanitize step or widening the
   * allow-list to `*` — that is a remote-code-execution hole one `git clone`
   * away from anybody.
   */
  import DOMPurify from 'dompurify';
  import { marked } from 'marked';

  interface Props {
    /** Raw markdown. May be empty — that renders the "no content" note. */
    content: string;
    /** Show a skeleton instead of content while the fetch is in flight. */
    loading?: boolean;
    /** What to say when there is nothing to render. */
    emptyLabel?: string;
  }

  let { content, loading = false, emptyLabel = 'No README.' }: Props = $props();

  /**
   * Tags a README legitimately uses. Anything outside this list is dropped by
   * DOMPurify rather than rendered — an allow-list, not a deny-list, so a tag
   * nobody thought of is refused by default.
   */
  const ALLOWED_TAGS = [
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'p', 'br', 'hr',
    'strong', 'em', 'del', 's', 'b', 'i',
    'code', 'pre', 'kbd', 'samp',
    'ul', 'ol', 'li',
    'blockquote',
    'a', 'img',
    'table', 'thead', 'tbody', 'tr', 'th', 'td',
    'span', 'div', 'details', 'summary',
  ];

  /** No `on*`, no `style`, no `srcdoc` — just what a link/image/cell needs. */
  const ALLOWED_ATTR = ['href', 'src', 'alt', 'title', 'align', 'colspan', 'rowspan', 'open'];

  // Pure derivation, no side effects: parse → sanitize → string. `marked` is
  // called synchronously (async: false is its default) so the result is a
  // string, not a promise.
  const html = $derived.by(() => {
    if (!content) return '';
    const raw = marked.parse(content, { async: false }) as string;
    return DOMPurify.sanitize(raw, {
      ALLOWED_TAGS,
      ALLOWED_ATTR,
      // Belt and braces: even if a handler survived the attribute allow-list,
      // these forbid the shapes that actually execute.
      FORBID_TAGS: ['script', 'style', 'iframe', 'object', 'embed', 'form', 'input'],
      FORBID_ATTR: ['onerror', 'onload', 'onclick', 'style', 'srcdoc', 'formaction'],
    });
  });
</script>

{#if loading}
  <div class="skeleton" aria-busy="true">
    <span class="bar w-60"></span>
    <span class="bar w-90"></span>
    <span class="bar w-75"></span>
  </div>
{:else if !html}
  <p class="muted-note">{emptyLabel}</p>
{:else}
  <!-- Sanitized above. See the security note at the top of this file. -->
  <div class="readme">{@html html}</div>
{/if}

<style>
  .muted-note { color: var(--color-text-muted); font-size: 0.82rem; margin: 0; }

  .skeleton { display: flex; flex-direction: column; gap: 8px; }
  .bar {
    height: 10px; border-radius: var(--radius-sm);
    background: var(--color-bg-tertiary, var(--color-bg-primary));
    opacity: 0.6;
  }
  .w-60 { width: 60%; }
  .w-75 { width: 75%; }
  .w-90 { width: 90%; }

  .readme {
    font-size: 0.85rem;
    line-height: 1.6;
    color: var(--color-text-secondary);
    max-height: 480px;
    overflow: auto;
  }
  /* :global — the markup is injected at runtime, so scoped selectors would
     never match it. Every rule is nested under .readme to stay contained. */
  .readme :global(h1),
  .readme :global(h2),
  .readme :global(h3) {
    color: var(--color-text-primary);
    margin: 1em 0 0.4em;
    line-height: 1.3;
  }
  .readme :global(h1) { font-size: 1.15rem; }
  .readme :global(h2) { font-size: 1.02rem; }
  .readme :global(h3) { font-size: 0.92rem; }
  .readme :global(h1:first-child),
  .readme :global(h2:first-child),
  .readme :global(h3:first-child) { margin-top: 0; }
  .readme :global(p) { margin: 0 0 0.7em; }
  .readme :global(a) { color: var(--color-accent); text-decoration: none; }
  .readme :global(a:hover) { text-decoration: underline; }
  .readme :global(strong) { color: var(--color-text-primary); }
  .readme :global(code) {
    font-family: var(--font-mono);
    font-size: 0.78rem;
    background: var(--color-bg-primary);
    border: 1px solid var(--color-border-secondary);
    border-radius: var(--radius-sm);
    padding: 1px 4px;
  }
  .readme :global(pre) {
    background: var(--color-bg-primary);
    border: 1px solid var(--color-border-secondary);
    border-radius: var(--radius-sm);
    padding: 8px 10px;
    overflow-x: auto;
    margin: 0 0 0.7em;
  }
  .readme :global(pre code) { background: none; border: none; padding: 0; }
  .readme :global(ul),
  .readme :global(ol) { margin: 0 0 0.7em; padding-left: 1.3em; }
  .readme :global(li) { margin: 0.2em 0; }
  .readme :global(blockquote) {
    margin: 0 0 0.7em;
    padding: 2px 0 2px 10px;
    border-left: 2px solid var(--color-border-secondary);
    color: var(--color-text-muted);
  }
  .readme :global(img) { max-width: 100%; height: auto; }
  .readme :global(hr) { border: none; border-top: 1px solid var(--color-border-secondary); margin: 1em 0; }
  .readme :global(table) { border-collapse: collapse; margin: 0 0 0.7em; font-size: 0.8rem; }
  .readme :global(th),
  .readme :global(td) { border: 1px solid var(--color-border-secondary); padding: 4px 8px; text-align: left; }
</style>

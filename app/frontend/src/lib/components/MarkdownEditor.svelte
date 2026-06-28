<script lang="ts">
  /**
   * CodeMirror 6 markdown editor with debounced server-side lint.
   *
   * Used by LoopsPanel's Templates tab to author Loop templates (now
   * markdown with YAML frontmatter). Same props/behavior as YamlEditor;
   * just swaps the language extension for markdown highlighting.
   */
  import { onMount, onDestroy } from 'svelte';
  import { EditorView, lineNumbers, highlightActiveLine, keymap } from '@codemirror/view';
  import { EditorState, Compartment } from '@codemirror/state';
  import { markdown } from '@codemirror/lang-markdown';
  import { defaultKeymap, history, historyKeymap } from '@codemirror/commands';
  import { linter, lintGutter, type Diagnostic } from '@codemirror/lint';
  import { HighlightStyle, syntaxHighlighting } from '@codemirror/language';
  import { tags as t } from '@lezer/highlight';

  interface LintError {
    line: number | null;
    col: number | null;
    field: string | null;
    message: string;
  }

  interface Selection {
    startLine: number; // 1-indexed
    endLine: number;   // 1-indexed inclusive
    isEmpty: boolean;
  }

  interface Props {
    value: string;
    onChange?: (next: string) => void;
    readonly?: boolean;
    lintRequest?: (text: string) => Promise<LintError[]>;
    onSelectionChange?: (sel: Selection) => void;
  }

  let { value, onChange, readonly = false, lintRequest, onSelectionChange }: Props = $props();

  let container: HTMLDivElement;
  let view: EditorView | null = null;
  const readonlyCompartment = new Compartment();
  let suppressOnChange = false;

  function lineColToOffset(state: EditorState, line: number, col: number | null): number {
    const safeLine = Math.min(Math.max(1, line), state.doc.lines);
    const lineInfo = state.doc.line(safeLine);
    const safeCol = col !== null ? Math.min(Math.max(0, col - 1), lineInfo.length) : 0;
    return lineInfo.from + safeCol;
  }

  function errorsToDiagnostics(state: EditorState, errors: LintError[]): Diagnostic[] {
    return errors.map((e): Diagnostic => {
      const targetLine = e.line ?? 1;
      const lineInfo = state.doc.line(Math.min(Math.max(1, targetLine), state.doc.lines));
      const from = lineColToOffset(state, targetLine, e.col);
      const to = lineInfo.to;
      const msg = e.field ? `${e.message} (${e.field})` : e.message;
      return { from, to, severity: 'error', message: msg };
    });
  }

  function makeLinter() {
    if (!lintRequest) return [];
    return linter(async (view: EditorView): Promise<Diagnostic[]> => {
      try {
        const errors = await lintRequest(view.state.doc.toString());
        if (!errors || errors.length === 0) return [];
        return errorsToDiagnostics(view.state, errors);
      } catch (err) {
        console.warn('MarkdownEditor: lint request failed', err);
        return [];
      }
    }, { delay: 500 });
  }

  function makeExtensions() {
    // Theme-aware editor surface. All colors come from the design tokens
    // in styles/tokens.css so the editor follows the active theme
    // (paper / light / dark / brew / vision).
    const editorTheme = EditorView.theme({
      '&': {
        height: '100%',
        fontSize: '13px',
        color: 'var(--text)',
        backgroundColor: 'var(--bg-elev)',
      },
      '.cm-scroller': { fontFamily: 'var(--font-mono)' },
      '.cm-content': { padding: '10px 0', caretColor: 'var(--accent)' },
      '&.cm-focused': { outline: 'none' },
      '.cm-cursor, .cm-dropCursor': { borderLeftColor: 'var(--accent)' },
      '&.cm-focused .cm-selectionBackground, ::selection, .cm-selectionBackground': {
        backgroundColor: 'color-mix(in srgb, var(--accent) 24%, transparent)',
      },
      '.cm-gutters': {
        backgroundColor: 'var(--bg-sunken)',
        color: 'var(--text-faint)',
        border: 'none',
        borderRight: '1px solid var(--border)',
      },
      '.cm-activeLine': {
        backgroundColor: 'color-mix(in srgb, var(--bg-hover) 60%, transparent)',
      },
      '.cm-activeLineGutter': {
        backgroundColor: 'transparent',
        color: 'var(--text-muted)',
      },
      '.cm-lineNumbers .cm-gutterElement': { padding: '0 8px 0 6px' },
      '.cm-foldGutter .cm-gutterElement': { color: 'var(--text-faint)' },
      // Lint underline + gutter marker pulled from the design tokens
      // so red doesn't punch through every theme equally.
      '.cm-diagnostic-error': {
        borderLeft: '3px solid var(--fail)',
        backgroundColor: 'var(--fail-soft)',
        color: 'var(--text)',
      },
      '.cm-tooltip': {
        backgroundColor: 'var(--bg-elev)',
        border: '1px solid var(--border)',
        color: 'var(--text)',
        borderRadius: 'var(--r-sm)',
        boxShadow: 'var(--shadow-md)',
      },
      '.cm-panels': {
        backgroundColor: 'var(--bg-elev)',
        color: 'var(--text)',
      },
    });

    // Markdown syntax highlighting tuned for the phantom-ink ink theme.
    // Tokens (--text, --accent, --text-muted, --run, --task) inherit the
    // current theme so the editor reads the same across paper/dark/brew.
    const editorHighlight = HighlightStyle.define([
      { tag: t.heading,        color: 'var(--text)',        fontWeight: '700' },
      { tag: t.heading1,       color: 'var(--text)',        fontWeight: '700' },
      { tag: t.heading2,       color: 'var(--text)',        fontWeight: '700' },
      { tag: t.heading3,       color: 'var(--text)',        fontWeight: '700' },
      { tag: t.strong,         color: 'var(--text)',        fontWeight: '700' },
      { tag: t.emphasis,       color: 'var(--text)',        fontStyle: 'italic' },
      { tag: t.link,           color: 'var(--accent)',      textDecoration: 'underline' },
      { tag: t.url,            color: 'var(--accent)' },
      { tag: t.monospace,      color: 'var(--accent)' },     // inline code
      { tag: t.literal,        color: 'var(--accent)' },     // literal nodes
      { tag: t.list,           color: 'var(--text-muted)' }, // bullets/numbers
      { tag: t.quote,          color: 'var(--text-muted)',  fontStyle: 'italic' },
      { tag: t.processingInstruction, color: 'var(--text-faint)' },
      { tag: t.contentSeparator,      color: 'var(--text-faint)' },   // --- fences
      // YAML frontmatter (lezer parses YAML inside the fences when we
      // pass it through markdown's default config); fall back to base
      // tokens so values stay legible.
      { tag: t.propertyName,   color: 'var(--task)' },        // YAML keys
      { tag: t.string,         color: 'var(--text)' },
      { tag: t.number,         color: 'var(--run)' },
      { tag: t.bool,           color: 'var(--run)' },
      { tag: t.keyword,        color: 'var(--sched)' },
      { tag: t.comment,        color: 'var(--text-muted)',  fontStyle: 'italic' },
      { tag: t.invalid,        color: 'var(--fail)' },
    ]);

    return [
      lineNumbers(),
      history(),
      highlightActiveLine(),
      markdown(),
      syntaxHighlighting(editorHighlight),
      lintGutter(),
      makeLinter(),
      EditorView.lineWrapping,
      keymap.of([...defaultKeymap, ...historyKeymap]),
      readonlyCompartment.of(EditorState.readOnly.of(readonly)),
      editorTheme,
      EditorView.updateListener.of((update) => {
        if (suppressOnChange) return;
        if (update.docChanged && onChange) {
          onChange(update.state.doc.toString());
        }
        if ((update.docChanged || update.selectionSet) && onSelectionChange) {
          const sel = update.state.selection.main;
          const startLine = update.state.doc.lineAt(sel.from).number;
          const endLine = update.state.doc.lineAt(sel.to).number;
          onSelectionChange({
            startLine,
            endLine,
            isEmpty: sel.from === sel.to,
          });
        }
      }),
    ];
  }

  onMount(() => {
    view = new EditorView({
      state: EditorState.create({
        doc: value,
        extensions: makeExtensions(),
      }),
      parent: container,
    });
  });

  onDestroy(() => {
    view?.destroy();
    view = null;
  });

  $effect(() => {
    if (!view) return;
    const current = view.state.doc.toString();
    if (current === value) return;
    suppressOnChange = true;
    view.dispatch({
      changes: { from: 0, to: current.length, insert: value },
    });
    suppressOnChange = false;
  });

  $effect(() => {
    if (!view) return;
    view.dispatch({
      effects: readonlyCompartment.reconfigure(EditorState.readOnly.of(readonly)),
    });
  });
</script>

<div class="editor" bind:this={container}></div>

<style>
  .editor {
    height: 100%;
    min-height: 0;
    border: 1px solid var(--border);
    border-radius: var(--r-sm);
    background: var(--bg-elev);
    overflow: hidden;
  }
  /* Make sure the inner CodeMirror surface follows the theme even when
     a parent injects a different background. */
  .editor :global(.cm-editor) {
    height: 100%;
    background: var(--bg-elev);
    color: var(--text);
  }
  .editor :global(.cm-editor.cm-focused) {
    outline: none;
  }
</style>

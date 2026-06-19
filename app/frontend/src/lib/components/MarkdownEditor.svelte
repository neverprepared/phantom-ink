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
    return [
      lineNumbers(),
      history(),
      highlightActiveLine(),
      markdown(),
      lintGutter(),
      makeLinter(),
      EditorView.lineWrapping,
      keymap.of([...defaultKeymap, ...historyKeymap]),
      readonlyCompartment.of(EditorState.readOnly.of(readonly)),
      EditorView.theme({
        '&': { height: '100%', fontSize: '13px' },
        '.cm-scroller': { fontFamily: 'var(--font-mono, monospace)' },
        '.cm-content': { padding: '8px 0' },
        '&.cm-focused': { outline: 'none' },
        '.cm-gutters': {
          backgroundColor: 'transparent',
          color: 'var(--color-text-muted, #888)',
          border: 'none',
        },
        '.cm-activeLine': { backgroundColor: 'rgba(255,255,255,0.03)' },
        '.cm-activeLineGutter': { backgroundColor: 'transparent' },
      }),
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
    border: 1px solid var(--color-border, #2a2a2a);
    border-radius: 4px;
    background: var(--color-surface-1, #181818);
    overflow: hidden;
  }
</style>

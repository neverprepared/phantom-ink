<script lang="ts">
  /**
   * CodeMirror 6 YAML editor with debounced server-side lint.
   *
   * Used by LoopsPanel's Templates tab to author Loop templates. The
   * lintRequest prop is the seam — caller decides what "valid" means
   * (today: POST /api/loops/templates/validate). Empty errors array
   * means clean.
   */
  import { onMount, onDestroy } from 'svelte';
  import { EditorView, lineNumbers, highlightActiveLine, keymap } from '@codemirror/view';
  import { EditorState, Compartment } from '@codemirror/state';
  import { yaml } from '@codemirror/lang-yaml';
  import { defaultKeymap, history, historyKeymap } from '@codemirror/commands';
  import { linter, lintGutter, type Diagnostic } from '@codemirror/lint';

  interface LintError {
    line: number | null;
    col: number | null;
    field: string | null;
    message: string;
  }

  interface Props {
    value: string;
    onChange?: (next: string) => void;
    readonly?: boolean;
    lintRequest?: (text: string) => Promise<LintError[]>;
  }

  let { value, onChange, readonly = false, lintRequest }: Props = $props();

  let container: HTMLDivElement;
  let view: EditorView | null = null;
  const readonlyCompartment = new Compartment();
  let suppressOnChange = false;

  // Convert 1-indexed (line, col) → CodeMirror absolute offset.
  function lineColToOffset(state: EditorState, line: number, col: number | null): number {
    const safeLine = Math.min(Math.max(1, line), state.doc.lines);
    const lineInfo = state.doc.line(safeLine);
    const safeCol = col !== null ? Math.min(Math.max(0, col - 1), lineInfo.length) : 0;
    return lineInfo.from + safeCol;
  }

  function errorsToDiagnostics(state: EditorState, errors: LintError[]): Diagnostic[] {
    return errors.map((e): Diagnostic => {
      // No line info → annotate the first line. Field-only errors get
      // the field path appended to the message so the operator can find it.
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
        // Lint endpoint unreachable — don't decorate the editor with a
        // misleading error; just stay quiet. The Save path will surface
        // the failure when the operator tries to persist.
        console.warn('YamlEditor: lint request failed', err);
        return [];
      }
    }, { delay: 500 });
  }

  function makeExtensions() {
    return [
      lineNumbers(),
      history(),
      highlightActiveLine(),
      yaml(),
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

  // React to external value changes (e.g. operator switched templates).
  // Suppress the change callback so we don't loop.
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
  /* The cm-editor lives inside .editor; CodeMirror applies its own
     theme above (height 100%, font from --font-mono). We just need the
     container to fill its grid cell. */
</style>

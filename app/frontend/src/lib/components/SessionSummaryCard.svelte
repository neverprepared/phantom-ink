<script lang="ts">
  // Rich renderer for a `session.summary` envelope: the agent's NARRATIVE
  // (title/description) up top, MACHINE facts in a grid, commits, and the
  // heavy EVIDENCE as artifact links (previewed via the Go presign→fetch
  // binding, keeping the no-fetch-from-JS convention).
  import { getApi } from '../utils/api';

  interface Artifact { handle: string; kind?: string; bytes?: number; url?: string }
  interface Meta {
    repo?: string; branch?: string; files_changed?: number;
    additions?: number; deletions?: number;
    commits?: { sha?: string; subject?: string }[];
    pr_url?: string; tests?: { passed?: number; failed?: number; skipped?: number };
    tools_used?: string[]; model?: string; cost_usd?: number;
    tokens?: number; duration_ms?: number; artifacts?: Artifact[];
  }
  interface Envelope {
    title?: string; description?: string; status?: string;
    workspace?: string; url?: string; metadata?: Meta;
  }

  let { envelope }: { envelope: Envelope } = $props();
  const m = $derived((envelope.metadata ?? {}) as Meta);

  let preview = $state<{ kind: string; text: string } | null>(null);
  let previewErr = $state('');

  function fmtDuration(ms?: number): string {
    if (!ms) return '';
    const s = Math.round(ms / 1000);
    if (s < 60) return `${s}s`;
    const mnt = Math.floor(s / 60);
    return mnt < 60 ? `${mnt}m ${s % 60}s` : `${Math.floor(mnt / 60)}h ${mnt % 60}m`;
  }
  function fmtBytes(b?: number): string {
    if (!b) return '';
    if (b < 1024) return `${b} B`;
    if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
    return `${(b / 1024 / 1024).toFixed(1)} MB`;
  }
  const prUrl = $derived(m.pr_url || envelope.url || '');

  function openUrl(url: string) {
    // Wails: hand external links to the OS browser, never navigate the webview.
    (window as any).runtime?.BrowserOpenURL?.(url);
  }

  async function openArtifact(a: Artifact) {
    previewErr = '';
    const slash = a.handle.indexOf('/');
    if (slash < 0) { previewErr = 'bad handle'; return; }
    const bucket = a.handle.slice(0, slash);
    const key = a.handle.slice(slash + 1);
    try {
      const api = await getApi();
      if (!api) return;
      const res = await api.GetArtifactPreview(bucket, key);
      preview = { kind: a.kind ?? 'artifact', text: res?.text ?? '(no preview)' };
    } catch (e: any) {
      previewErr = `preview failed: ${e?.message ?? e}`;
    }
  }
</script>

<div class="summary-card">
  {#if envelope.title}<div class="headline">{envelope.title}</div>{/if}
  {#if envelope.description}<div class="prose">{envelope.description}</div>{/if}

  <div class="facts">
    {#if m.repo}<span class="fact"><b>repo</b> {m.repo}</span>{/if}
    {#if m.branch}<span class="fact"><b>branch</b> {m.branch}</span>{/if}
    {#if m.files_changed != null}<span class="fact"><b>files</b> {m.files_changed}</span>{/if}
    {#if m.additions != null || m.deletions != null}
      <span class="fact"><b>diff</b> <span class="add">+{m.additions ?? 0}</span> <span class="del">−{m.deletions ?? 0}</span></span>
    {/if}
    {#if m.tests}
      <span class="fact"><b>tests</b>
        <span class="add">{m.tests.passed ?? 0}✓</span>
        {#if m.tests.failed}<span class="del">{m.tests.failed}✗</span>{/if}
      </span>
    {/if}
    {#if m.tokens != null}<span class="fact"><b>tokens</b> {m.tokens.toLocaleString()}</span>{/if}
    {#if m.cost_usd != null}<span class="fact"><b>cost</b> ${m.cost_usd.toFixed(2)}</span>{/if}
    {#if m.duration_ms != null}<span class="fact"><b>took</b> {fmtDuration(m.duration_ms)}</span>{/if}
    {#if m.model}<span class="fact"><b>model</b> {m.model}</span>{/if}
  </div>

  {#if m.tools_used?.length}
    <div class="tools">{#each m.tools_used as t (t)}<span class="tool">{t}</span>{/each}</div>
  {/if}

  {#if m.commits?.length}
    <div class="commits">
      {#each m.commits as c (c.sha)}
        <div class="commit"><code>{c.sha?.slice(0, 8)}</code> {c.subject}</div>
      {/each}
    </div>
  {/if}

  <div class="actions">
    {#if prUrl}<button class="link-btn" onclick={() => openUrl(prUrl)}>View PR ↗</button>{/if}
    {#each m.artifacts ?? [] as a (a.handle)}
      <button class="link-btn" onclick={() => openArtifact(a)}>
        Open {a.kind ?? 'artifact'}{#if a.bytes} ({fmtBytes(a.bytes)}){/if}
      </button>
    {/each}
  </div>

  {#if previewErr}<div class="preview-err">{previewErr}</div>{/if}
  {#if preview}
    <div class="preview">
      <div class="preview-head">{preview.kind}<button class="close" onclick={() => (preview = null)}>×</button></div>
      <pre>{preview.text}</pre>
    </div>
  {/if}
</div>

<style>
  .summary-card { display: flex; flex-direction: column; gap: 8px; padding: 10px 12px;
    background: var(--color-bg-secondary); border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm); font-family: var(--font-mono); }
  .headline { font-size: 13px; font-weight: 600; color: var(--color-text-primary); }
  .prose { font-size: 12px; color: var(--color-text-secondary); white-space: pre-wrap; line-height: 1.45; }
  .facts { display: flex; flex-wrap: wrap; gap: 6px 12px; font-size: 11px; color: var(--color-text-secondary); }
  .fact b { color: var(--color-text-tertiary); font-weight: 600; margin-right: 3px; text-transform: uppercase; letter-spacing: 0.04em; }
  .add { color: var(--color-status-success-text); }
  .del { color: var(--color-status-error-text); }
  .tools { display: flex; flex-wrap: wrap; gap: 4px; }
  .tool { font-size: 10px; padding: 1px 6px; border-radius: 3px; background: var(--color-muted-bg); color: var(--color-text-tertiary); }
  .commits { display: flex; flex-direction: column; gap: 2px; }
  .commit { font-size: 11px; color: var(--color-text-secondary); }
  .commit code { color: var(--color-accent); }
  .actions { display: flex; flex-wrap: wrap; gap: 6px; }
  .link-btn { font-family: var(--font-mono); font-size: 11px; color: var(--color-accent);
    background: transparent; border: 1px solid var(--color-accent); border-radius: var(--radius-sm);
    padding: 3px 10px; cursor: pointer; }
  .link-btn:hover { background: rgba(234, 179, 8, 0.08); }
  .preview { border: 1px solid var(--color-border-primary); border-radius: var(--radius-sm); overflow: hidden; }
  .preview-head { display: flex; justify-content: space-between; align-items: center;
    padding: 4px 8px; font-size: 11px; background: var(--color-muted-bg); color: var(--color-text-tertiary); }
  .close { background: transparent; border: none; color: var(--color-text-secondary); cursor: pointer; font-size: 14px; }
  .preview pre { margin: 0; padding: 8px; max-height: 320px; overflow: auto; font-size: 11px;
    color: var(--color-text-secondary); white-space: pre-wrap; word-break: break-word; }
  .preview-err { font-size: 11px; color: var(--color-status-error-text); }
</style>

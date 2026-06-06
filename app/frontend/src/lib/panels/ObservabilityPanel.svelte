<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { getApi, openInBrowser } from '../utils/api';
  import { featureFlags, profileState } from '../stores.svelte';
  import Spinner from '../components/Spinner.svelte';
  import EmptyState from '../components/EmptyState.svelte';

  interface Overview {
    cost_today_usd: number;
    tokens_today: number;
    api_requests_1h: number;
    avg_latency_ms_1h: number;
    as_of: string;
    workspace: string;
    matched_workspace: boolean;
  }

  let overview = $state<Overview | null>(null);
  let loading = $state(true);
  let loadError = $state<string | null>(null);
  let pollHandle: number | undefined;

  let opensearchEnabled = $derived(featureFlags.isEnabled('opensearch'));
  let opensearchActive  = $derived(featureFlags.isActive('opensearch'));

  // null = "all" — pass empty string to backend so no filter is applied.
  let activeProfile = $derived(profileState.active);
  let workspaceFilter = $derived(activeProfile?.name ?? '');

  // True when a profile is selected but no telemetry exists tagged with that
  // workspace.  In that case we replace the cards with a setup hint.
  let workspaceMissing = $derived(
    overview != null && workspaceFilter !== '' && !overview.matched_workspace
  );

  const POLL_MS = 15_000;
  const DOCS_URL = 'https://code.claude.com/docs/en/monitoring-usage#available-metrics-and-events';

  async function refresh() {
    const a = await getApi();
    if (!a) { loading = false; loadError = 'API bindings unavailable'; return; }
    if (!opensearchEnabled) { loading = false; return; }
    try {
      overview = (await a.GetObservabilityOverview(workspaceFilter)) as Overview;
      loadError = null;
    } catch (err: any) {
      loadError = `${err?.message ?? err}`;
    } finally {
      loading = false;
    }
  }

  onMount(() => {
    void refresh();
    pollHandle = window.setInterval(refresh, POLL_MS);
  });

  onDestroy(() => {
    if (pollHandle !== undefined) window.clearInterval(pollHandle);
  });

  // Re-fetch immediately when the user switches profiles.
  let lastFilter = $state(workspaceFilter);
  $effect(() => {
    if (workspaceFilter !== lastFilter) {
      lastFilter = workspaceFilter;
      void refresh();
    }
  });

  function fmtUSD(v: number): string {
    return `$${(v ?? 0).toFixed(4)}`;
  }
  function fmtInt(v: number): string {
    return (v ?? 0).toLocaleString();
  }
  function fmtMs(v: number): string {
    if (!v) return '—';
    return v < 1000 ? `${Math.round(v)} ms` : `${(v / 1000).toFixed(2)} s`;
  }
  function fmtTime(iso: string): string {
    if (!iso) return '';
    try { return new Date(iso).toLocaleTimeString(); } catch { return ''; }
  }

  async function copyEnvLine() {
    const line = `OTEL_RESOURCE_ATTRIBUTES=workspace=${workspaceFilter}`;
    try { await navigator.clipboard.writeText(line); } catch { /* ignore */ }
  }
</script>

<div class="panel">
  <header class="panel-header">
    <h1 class="page-title">observability</h1>
    <div class="header-actions">
      <div class="scope">
        scope:
        <span class="scope-value">{workspaceFilter || 'all'}</span>
      </div>
      {#if loading && opensearchEnabled}<Spinner />{/if}
      {#if opensearchActive}
        <button class="btn ghost" onclick={() => openInBrowser('http://localhost:5601/app/dashboards#/view/cc-dashboard')}>
          open dashboard ↗
        </button>
      {/if}
    </div>
  </header>

  {#if !opensearchEnabled}
    <EmptyState
      title="OpenSearch is not enabled"
      message="Enable the OpenSearch integration to view Claude Code telemetry metrics here." />
  {:else if loadError}
    <EmptyState title="Failed to load metrics" message={loadError} />
  {:else if loading && !overview}
    <div class="empty">loading…</div>
  {:else if workspaceMissing}
    <div class="setup">
      <h2 class="setup-title">no telemetry tagged with workspace <code>{workspaceFilter}</code></h2>
      <p class="setup-body">
        Telemetry exists in OpenSearch, but none of it is tagged with this workspace.
        Tag your Claude Code sessions by adding a resource attribute to your environment.
      </p>

      <div class="env-block">
        <div class="env-label">Add to <code>~/.env</code> (or your workspace's env file):</div>
        <div class="env-row">
          <pre class="env-line">OTEL_RESOURCE_ATTRIBUTES=workspace={workspaceFilter}</pre>
          <button class="btn ghost small" onclick={copyEnvLine}>copy</button>
        </div>
        <p class="env-note">
          The value becomes <code>resource.attributes.workspace</code> on every metric,
          log, and trace emitted by Claude Code. After updating, start a new Claude Code
          session — telemetry from then on will show up here.
        </p>
      </div>

      <p class="setup-link">
        Reference: <a href="#" onclick={(e) => { e.preventDefault(); openInBrowser(DOCS_URL); }}>
          Claude Code monitoring docs — available metrics and events
        </a>
      </p>
    </div>
  {:else if overview}
    <div class="cards">
      <article class="card">
        <div class="card-label">cost today</div>
        <div class="card-value">{fmtUSD(overview.cost_today_usd)}</div>
        <div class="card-sub">USD · since 00:00</div>
      </article>

      <article class="card">
        <div class="card-label">tokens today</div>
        <div class="card-value">{fmtInt(overview.tokens_today)}</div>
        <div class="card-sub">all types · since 00:00</div>
      </article>

      <article class="card">
        <div class="card-label">api requests</div>
        <div class="card-value">{fmtInt(overview.api_requests_1h)}</div>
        <div class="card-sub">last 1h</div>
      </article>

      <article class="card">
        <div class="card-label">avg api latency</div>
        <div class="card-value">{fmtMs(overview.avg_latency_ms_1h)}</div>
        <div class="card-sub">last 1h</div>
      </article>
    </div>

    {#if overview.as_of}
      <div class="footer">updated {fmtTime(overview.as_of)} · polling every {POLL_MS / 1000}s</div>
    {/if}
  {/if}
</div>

<style>
  .panel {
    display: flex;
    flex-direction: column;
    height: 100%;
    padding: var(--panel-padding);
    overflow-y: auto;
  }

  .panel-header {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    margin-bottom: 24px;
    gap: 16px;
  }

  .page-title {
    font-size: 18px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: lowercase;
    margin: 0;
    color: var(--text, var(--color-text-primary));
  }

  .header-actions {
    display: flex;
    gap: 12px;
    align-items: center;
  }

  .scope {
    font-size: 11px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--text-faint, var(--color-text-tertiary));
  }
  .scope-value {
    color: var(--text, var(--color-text-primary));
    font-weight: 600;
    letter-spacing: 0;
    text-transform: none;
    margin-left: 4px;
    padding: 2px 8px;
    background: var(--bg-elev, var(--color-bg-tertiary));
    border-radius: var(--r-sm, var(--radius-sm));
  }

  .btn.ghost {
    background: none;
    border: 1px solid var(--border, var(--color-border-primary));
    color: var(--text-muted, var(--color-text-secondary));
    padding: 6px 12px;
    border-radius: var(--r-sm, var(--radius-sm));
    font-family: inherit;
    font-size: 12.5px;
    cursor: pointer;
    transition: all 0.15s;
  }
  .btn.ghost:hover {
    color: var(--text, var(--color-text-primary));
    background: var(--bg-hover, var(--color-surface-hover));
  }
  .btn.ghost.small {
    padding: 4px 10px;
    font-size: 11.5px;
  }

  .cards {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 16px;
  }

  .card {
    background: var(--bg-elev, var(--color-bg-secondary));
    border: 1px solid var(--border, var(--color-border-primary));
    border-radius: var(--r-md, var(--radius-md));
    padding: 18px 20px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .card-label {
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--text-faint, var(--color-text-tertiary));
  }

  .card-value {
    font-size: 32px;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
    line-height: 1.1;
    color: var(--text, var(--color-text-primary));
  }

  .card-sub {
    font-size: 12px;
    color: var(--text-muted, var(--color-text-secondary));
  }

  .footer {
    margin-top: 20px;
    font-size: 11.5px;
    color: var(--text-faint, var(--color-text-tertiary));
    text-align: right;
  }

  .empty {
    text-align: center;
    padding: 60px 20px;
    color: var(--text-faint, var(--color-text-tertiary));
    font-size: 13px;
  }

  /* --- Setup hint --- */
  .setup {
    max-width: 720px;
    margin: 20px 0;
    padding: 24px;
    background: var(--bg-elev, var(--color-bg-secondary));
    border: 1px solid var(--border, var(--color-border-primary));
    border-left: 3px solid var(--accent, var(--color-accent));
    border-radius: var(--r-md, var(--radius-md));
  }
  .setup-title {
    margin: 0 0 12px;
    font-size: 15px;
    font-weight: 600;
    color: var(--text, var(--color-text-primary));
  }
  .setup-body {
    margin: 0 0 18px;
    font-size: 13px;
    line-height: 1.6;
    color: var(--text-muted, var(--color-text-secondary));
  }
  .env-block {
    margin: 16px 0;
    padding: 14px;
    background: var(--bg, var(--color-bg-primary));
    border: 1px solid var(--border, var(--color-border-primary));
    border-radius: var(--r-sm, var(--radius-sm));
  }
  .env-label {
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--text-faint, var(--color-text-tertiary));
    margin-bottom: 8px;
  }
  .env-row {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .env-line {
    flex: 1;
    margin: 0;
    padding: 8px 12px;
    background: var(--bg-elev, var(--color-bg-tertiary));
    border-radius: var(--r-sm, var(--radius-sm));
    font-family: var(--font-mono, ui-monospace, monospace);
    font-size: 12.5px;
    color: var(--text, var(--color-text-primary));
    white-space: pre;
    overflow-x: auto;
  }
  .env-note {
    margin: 12px 0 0;
    font-size: 12px;
    line-height: 1.5;
    color: var(--text-muted, var(--color-text-secondary));
  }
  .setup-link {
    margin: 18px 0 0;
    font-size: 12.5px;
    color: var(--text-muted, var(--color-text-secondary));
  }
  .setup-link a {
    color: var(--accent, var(--color-accent));
    text-decoration: none;
  }
  .setup-link a:hover { text-decoration: underline; }

  code {
    font-family: var(--font-mono, ui-monospace, monospace);
    font-size: 0.92em;
    padding: 1px 5px;
    background: var(--bg-elev, var(--color-bg-tertiary));
    border-radius: 3px;
  }
</style>

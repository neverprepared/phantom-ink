<script lang="ts">
  // Doctor: the install's own health report.
  //
  // Two UI rules carry the weight here. Checks run only when asked — opening
  // the panel must not fire a dozen probes at the platform. And `unknown`
  // renders muted grey rather than amber: an absence of evidence must not look
  // like a mild failure, which is the same mistake as a fake green light.
  import { getApi } from '../utils/api';
  import Badge from '../components/Badge.svelte';
  import CardExpander from '../components/CardExpander.svelte';
  import EmptyState from '../components/EmptyState.svelte';
  import Spinner from '../components/Spinner.svelte';

  type State = 'pass' | 'warn' | 'fail' | 'unknown';
  interface Remedy { command: string; auto_safe: boolean; confirm: string }
  interface Result {
    id: string; title: string; scope: 'local' | 'platform';
    state: State; detail: string; remedy?: Remedy; docs?: string;
  }

  let results = $state<Result[]>([]);
  let running = $state(false);
  let ran = $state(false);
  let error = $state('');

  // Deliberately NOT persisted: off on every launch, so it cannot be left on.
  let allowFixes = $state(false);
  let pendingFix = $state<Result | null>(null);
  let fixing = $state('');

  const local = $derived(results.filter((r) => r.scope === 'local'));
  const platform = $derived(results.filter((r) => r.scope === 'platform'));

  // Badge variants are limited to those Badge.svelte defines. 'unknown' maps to
  // no variant on purpose: the muted default is the point.
  function variantFor(s: State): string {
    if (s === 'fail') return 'failed';
    if (s === 'warn') return 'pending';
    if (s === 'pass') return 'running';
    return '';
  }

  function summarize(rs: Result[]): string {
    if (!rs.length) return 'no checks';
    const counts: Record<string, number> = {};
    for (const r of rs) counts[r.state] = (counts[r.state] ?? 0) + 1;
    return (['fail', 'unknown', 'warn', 'pass'] as State[])
      .filter((s) => counts[s])
      .map((s) => `${counts[s]} ${s}`)
      .join(' · ');
  }

  // getApi() is ASYNC — it dynamically imports the generated Wails module and
  // resolves to null when that import fails. Await it and null-check it, the
  // same shape every other panel uses; calling getApi().RunDoctor() directly
  // reads a property off a Promise and is always undefined.
  async function run() {
    running = true;
    error = '';
    const a = await getApi();
    if (!a) {
      error = 'Wails bindings unavailable';
      running = false;
      return;
    }
    try {
      results = ((await (a as any).RunDoctor('')) as Result[]) ?? [];
      ran = true;
    } catch (e) {
      error = String(e);
    } finally {
      running = false;
    }
  }

  async function applyFix(r: Result) {
    pendingFix = null;
    fixing = r.id;
    const a = await getApi();
    if (!a) {
      error = 'Wails bindings unavailable';
      fixing = '';
      return;
    }
    try {
      const out = (await (a as any).ApplyRemedy(r.id)) as {
        result: Result; error?: string;
      };
      if (out.error) error = out.error;
      // Replace the row with the re-run result, never an assumed pass.
      if (out.result?.id) {
        results = results.map((x) => (x.id === out.result.id ? out.result : x));
      }
    } catch (e) {
      error = String(e);
    } finally {
      fixing = '';
    }
  }

  async function copy(text: string) {
    try { await navigator.clipboard.writeText(text); } catch { /* clipboard denied */ }
  }
</script>

<div class="doctor">
  <header>
    <div>
      <h2>Doctor</h2>
      <p class="sub">Checks run only when you ask. Nothing here changes until you say so.</p>
    </div>
    <div class="actions">
      <label class="switch">
        <input type="checkbox" bind:checked={allowFixes} />
        Allow fixes
      </label>
      <button onclick={run} disabled={running}>
        {running ? 'Running…' : 'Run checks'}
      </button>
    </div>
  </header>

  {#if error}
    <div class="error">{error}</div>
  {/if}

  {#if running && !ran}
    <Spinner />
  {:else if !ran}
    <EmptyState message="Run the checks to see this install's health." />
  {:else}
    {#each [{ label: 'Local', rows: local }, { label: 'Platform', rows: platform }] as group (group.label)}
      <!-- CardExpander takes `label` (required, lowercase by convention) plus a
           separate `count`; it has no `title` prop. -->
      <CardExpander label={group.label.toLowerCase()} count={summarize(group.rows)}>
        {#if !group.rows.length}
          <p class="sub">No checks in this scope.</p>
        {:else}
          <ul>
            {#each group.rows as r (r.id)}
              <li>
                <Badge text={r.state} variant={variantFor(r.state)} />
                <div class="body">
                  <div class="title">{r.title}</div>
                  <div class="detail">{r.detail}</div>
                </div>
                {#if r.remedy}
                  {#if r.remedy.auto_safe && allowFixes}
                    <button
                      class="fix"
                      disabled={fixing === r.id}
                      onclick={() => (pendingFix = r)}
                    >{fixing === r.id ? 'Fixing…' : 'Fix'}</button>
                  {:else}
                    <button class="copy" onclick={() => copy(r.remedy!.command)}>Copy fix</button>
                  {/if}
                {/if}
              </li>
            {/each}
          </ul>
        {/if}
      </CardExpander>
    {/each}
  {/if}

  {#if pendingFix?.remedy}
    <div class="confirm">
      <p>{pendingFix.remedy.confirm}</p>
      <code>{pendingFix.remedy.command}</code>
      <div class="confirm-actions">
        <button onclick={() => (pendingFix = null)}>Cancel</button>
        <button class="fix" onclick={() => applyFix(pendingFix!)}>Run it</button>
      </div>
    </div>
  {/if}
</div>

<style>
  .doctor { padding: 16px; display: flex; flex-direction: column; gap: 12px; }
  header { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
  h2 { margin: 0; font-size: 15px; }
  .sub { margin: 2px 0 0; font-size: 11px; color: var(--color-text-tertiary); }
  .actions { display: flex; align-items: center; gap: 10px; }
  .switch { display: inline-flex; align-items: center; gap: 5px; font-size: 11px; color: var(--color-text-secondary); }
  ul { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
  li { display: flex; align-items: flex-start; gap: 8px; }
  .body { flex: 1; min-width: 0; }
  .title { font-size: 12px; font-weight: 600; }
  .detail { font-size: 11px; color: var(--color-text-tertiary); word-break: break-word; }
  .error { font-size: 11px; color: var(--color-status-error-text); }
  .confirm { border: 1px solid var(--color-warning-border); background: var(--color-warning-bg); padding: 10px; border-radius: 6px; font-size: 11px; }
  .confirm code { display: block; margin: 6px 0; font-size: 10px; word-break: break-all; }
  .confirm-actions { display: flex; gap: 6px; justify-content: flex-end; }
</style>

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

  // Monotonic counter bumped at the start of every run(). applyFix captures it
  // before awaiting and refuses to write a result splice once it has moved on
  // — a fresh Run checks must win over a fix that was already in flight when
  // it was clicked, never the other way around.
  let epoch = $state(0);

  // One "busy" notion covers both races at once: while true, Run checks is
  // disabled and no row offers a Fix button (the copy-command fallback stays
  // available), so a second fix or a check-run can never start while one is
  // outstanding. Concurrent remediation isn't a feature worth having here.
  const busy = $derived(running || fixing !== '');

  const local = $derived(results.filter((r) => r.scope === 'local'));
  const platform = $derived(results.filter((r) => r.scope === 'platform'));

  // Kill-switch guard: turning the switch off must also drop any confirm
  // that's already open, so a stale "Run it" click can't execute a remedy
  // the operator just withdrew consent for.
  $effect(() => {
    if (!allowFixes) pendingFix = null;
  });

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
    epoch += 1;
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
    // Kill-switch guard, belt-and-braces: the $effect above already clears
    // pendingFix (and so the confirm) the instant the switch flips off, but
    // re-check here too in case this call was already queued by a click.
    if (!allowFixes) return;
    fixing = r.id;
    // Capture the epoch before awaiting so a Run checks that lands while
    // this fix is in flight can be detected when it comes back.
    const myEpoch = epoch;
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
      // Replace the row with the re-run result, never an assumed pass — but
      // only if no fresh Run checks has superseded this result in the
      // meantime. A superseded write would clobber live data with a stale
      // read; the error above still surfaces either way.
      if (epoch === myEpoch && out.result?.id) {
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
  <header class="panel-header">
    <div>
      <h1>Doctor</h1>
      <p class="sub">Checks run only when you ask. Nothing here changes until you say so.</p>
    </div>
    <div class="head-actions">
      <label class="switch">
        <input type="checkbox" bind:checked={allowFixes} />
        Allow fixes
      </label>
      <button class="btn sm primary" onclick={run} disabled={busy}>
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
          <ul class="rows">
            {#each group.rows as r (r.id)}
              <li class="row">
                <Badge text={r.state} variant={variantFor(r.state)} />
                <div class="body">
                  <div class="title">{r.title}</div>
                  <div class="detail">{r.detail}</div>
                </div>
                {#if r.remedy}
                  {#if fixing === r.id}
                    <!-- The row actually in flight gets its own indicator,
                         not a button — busy below already withholds Fix from
                         every row, this one included, so there is only ever
                         one of these at a time. -->
                    <span class="fixing">Fixing…</span>
                  {:else if r.remedy.auto_safe && allowFixes && !busy}
                    <button class="btn sm soft" onclick={() => (pendingFix = r)}>Fix</button>
                  {:else}
                    <button class="btn ghost sm" onclick={() => copy(r.remedy!.command)}>Copy fix</button>
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
    <div class="confirm card">
      <p>{pendingFix.remedy.confirm}</p>
      <code>{pendingFix.remedy.command}</code>
      <div class="confirm-actions">
        <button class="btn ghost sm" onclick={() => (pendingFix = null)}>Cancel</button>
        <button class="btn sm primary" onclick={() => applyFix(pendingFix!)}>Run it</button>
      </div>
    </div>
  {/if}
</div>

<style>
  /* Only what is genuinely Doctor-specific lives here. The panel header, the
     result rows, the buttons and the confirm surface all come from the shared
     system (styles/tokens.css, buttons.css, components.css) so this panel reads
     like every other one: `.panel-header` + `h1`, `.rows`/`.row`, `.btn` with
     space-separated modifiers, `.card`. Spacing uses the --spacing-* scale
     rather than hand-picked pixels. */
  .doctor { padding: var(--spacing-lg); display: flex; flex-direction: column; gap: var(--spacing-md); }
  .head-actions { display: flex; align-items: center; gap: var(--spacing-md); }

  /* The fixes switch is a label wrapping a checkbox; no shared class covers it. */
  .switch {
    display: inline-flex;
    align-items: center;
    gap: var(--spacing-xs);
    font-size: 12px;
    color: var(--color-text-secondary);
    white-space: nowrap;
  }

  /* `.rows` is a panel-local idiom in this codebase, not a shared class: every
     panel that uses it declares its own list reset. Matching that rule exactly
     rather than relying on a class that styles/ does not define. */
  .rows { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: var(--spacing-xs); }

  /* .row centres its children; a result's badge should sit with the title line
     rather than float in the middle of a two-line body. */
  .row { align-items: flex-start; }
  .body { flex: 1; min-width: 0; }
  .title { font-size: 12px; font-weight: 600; }
  .detail { font-size: 11px; color: var(--color-text-tertiary); word-break: break-word; }

  .error { font-size: 11px; color: var(--color-status-error-text); }
  .fixing { font-size: 11px; color: var(--color-text-tertiary); flex-shrink: 0; }

  /* Warning accent over the shared .card surface: this box is the last thing
     between a click and a command running, so it should not look like chrome. */
  .confirm {
    border-color: var(--color-warning-border);
    background: var(--color-warning-bg);
    padding: var(--spacing-md);
    font-size: 12px;
  }
  .confirm p { margin: 0; }
  .confirm code {
    display: block;
    margin: var(--spacing-sm) 0;
    font-family: var(--font-mono);
    font-size: 11px;
    word-break: break-all;
  }
  .confirm-actions { display: flex; gap: var(--spacing-sm); justify-content: flex-end; }
</style>

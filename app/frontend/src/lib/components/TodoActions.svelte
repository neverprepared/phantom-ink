<script lang="ts">
  // Todo-only actions for the VaultBrowser reader pane (vault === 'todo').
  // Turns an inert todo record into work using the job systems that already
  // exist — nothing new is invented here:
  //   • Run once & remove → hub agent job (SubmitAgentJob), then the todo is
  //     forgotten from the vault.
  //   • Turn into job ▾ → a Collect job (SaveCollectJob, target_type=runner,
  //     prompt = the todo text): either repeatable (interval / time-of-day) or
  //     one-shot (run_once_at_ms). The Collect scheduler runs in-app.
  // Both "turn into job" paths also forget the todo — it has become a job.
  import { onMount } from 'svelte';
  import { getApi } from '../utils/api';
  import { notifications } from '../notifications.svelte';

  let { record, profile, onChanged }: {
    record: { sha: string; title: string; body: string };
    profile: string;
    onChanged: () => void;
  } = $props();

  // The prompt the agent runs = the todo body, falling back to its title.
  const prompt = $derived((record.body?.trim() || record.title || '').trim());
  const jobName = $derived(record.title?.trim() || 'todo');

  let agents = $state<string[]>(['worker']);
  let runners = $state<string[]>([]);

  // Shared inline options for the hub "run once" job (Collect runner jobs run
  // the claude CLI locally, so target/priority don't apply to them).
  let agent = $state('worker');
  let target = $state('local'); // 'local' | <runner name>
  let priority = $state(0);

  let menuOpen = $state(false);
  type FormKind = null | 'repeatable' | 'once';
  let form = $state<FormKind>(null);

  // repeatable
  let repeatMode = $state<'interval' | 'time'>('interval');
  let intervalMin = $state(60);
  let timeOfDay = $state('09:00');
  let days = $state<'daily' | 'weekdays'>('daily');

  // one-shot
  let onceDate = $state('');
  let onceTime = $state('');

  let busy = $state(false);

  onMount(async () => {
    const a = await getApi();
    if (!a) return;
    try {
      const roles = ((await (a as any).ListAgentRoles()) ?? []).map((r: any) => r.name).filter(Boolean);
      if (roles.length) { agents = roles; if (!roles.includes(agent)) agent = roles[0]; }
    } catch { /* fall back to ['worker'] */ }
    try {
      runners = ((await (a as any).ListRunners()) ?? []).map((r: any) => r.name).filter(Boolean);
    } catch { /* runners optional */ }
  });

  function hubTarget(): any {
    return target === 'local' ? { backend: 'docker' } : { runner: target, backend: 'docker' };
  }

  async function forgetTodo(a: any) {
    await a.ForgetVaultRecord('todo', record.sha);
  }

  async function runOnceAndRemove() {
    if (!prompt || busy) return;
    const a = await getApi();
    if (!a) return;
    busy = true;
    try {
      await (a as any).SubmitAgentJob({
        description: prompt,
        agent_name: agent,
        targets: [hubTarget()],
        priority,
        workspace_profile: profile || undefined,
      });
      await forgetTodo(a);
      notifications.success(`Running “${jobName}” — removed from todos`);
      onChanged();
    } catch (e) {
      notifications.error(`Run failed: ${e}`);
    } finally {
      busy = false;
    }
  }

  function collectJobBase(): any {
    return {
      name: jobName,
      profile: profile || '',
      target_type: 'runner',
      target_prompt: prompt,
      enabled: true,
    };
  }

  async function saveRepeatable() {
    if (!prompt || busy) return;
    const a = await getApi();
    if (!a) return;
    busy = true;
    try {
      const job = collectJobBase();
      if (repeatMode === 'interval') {
        job.interval_s = Math.max(60, Math.round(intervalMin * 60));
      } else {
        job.run_at = timeOfDay;
        job.days = days;
      }
      await (a as any).SaveCollectJob(job);
      await forgetTodo(a);
      const when = repeatMode === 'interval' ? `every ${intervalMin}m` : `${timeOfDay} ${days}`;
      notifications.success(`Repeatable job “${jobName}” (${when}) created`);
      reset();
      onChanged();
    } catch (e) {
      notifications.error(`Couldn't create job: ${e}`);
    } finally {
      busy = false;
    }
  }

  async function saveOnce() {
    if (!prompt || busy) return;
    if (!onceDate || !onceTime) { notifications.error('Pick a date and time'); return; }
    const at = new Date(`${onceDate}T${onceTime}`).getTime();
    if (!at || Number.isNaN(at)) { notifications.error('Invalid date/time'); return; }
    if (at <= Date.now()) { notifications.error('Pick a time in the future'); return; }
    const a = await getApi();
    if (!a) return;
    busy = true;
    try {
      const job = collectJobBase();
      job.run_once_at_ms = at;
      await (a as any).SaveCollectJob(job);
      await forgetTodo(a);
      notifications.success(`Scheduled “${jobName}” for ${onceDate} ${onceTime} — removed from todos`);
      reset();
      onChanged();
    } catch (e) {
      notifications.error(`Couldn't schedule: ${e}`);
    } finally {
      busy = false;
    }
  }

  function openForm(kind: FormKind) {
    form = kind;
    menuOpen = false;
  }
  function reset() {
    form = null;
    menuOpen = false;
  }
</script>

<div class="todo-actions">
  <div class="action-row">
    <button class="btn run" onclick={() => void runOnceAndRemove()} disabled={busy || !prompt} title="Run this todo now as an agent job, then remove it">
      ▶ Run once &amp; remove
    </button>

    <div class="menu-wrap">
      <button class="btn" onclick={() => (menuOpen = !menuOpen)} disabled={busy || !prompt}>
        Turn into job ▾
      </button>
      {#if menuOpen}
        <div class="menu" role="menu">
          <button class="menu-item" onclick={() => openForm('repeatable')}>Make repeatable…</button>
          <button class="menu-item" onclick={() => openForm('once')}>Schedule once…</button>
        </div>
      {/if}
    </div>

    <!-- shared options for the run-once hub job -->
    <label class="opt">agent
      <select bind:value={agent} disabled={busy}>
        {#each agents as name}<option value={name}>{name}</option>{/each}
      </select>
    </label>
    <label class="opt">target
      <select bind:value={target} disabled={busy}>
        <option value="local">local</option>
        {#each runners as r}<option value={r}>{r}</option>{/each}
      </select>
    </label>
    <label class="opt narrow">prio
      <input type="number" bind:value={priority} disabled={busy} />
    </label>
  </div>

  {#if form === 'repeatable'}
    <div class="form">
      <div class="form-title">Repeatable job — runs the todo text on a schedule</div>
      <div class="fields">
        <label class="opt">mode
          <select bind:value={repeatMode} disabled={busy}>
            <option value="interval">every N minutes</option>
            <option value="time">time of day</option>
          </select>
        </label>
        {#if repeatMode === 'interval'}
          <label class="opt narrow">minutes
            <input type="number" min="1" bind:value={intervalMin} disabled={busy} />
          </label>
        {:else}
          <label class="opt narrow">at
            <input type="time" bind:value={timeOfDay} disabled={busy} />
          </label>
          <label class="opt">days
            <select bind:value={days} disabled={busy}>
              <option value="daily">daily</option>
              <option value="weekdays">weekdays</option>
            </select>
          </label>
        {/if}
      </div>
      <div class="form-actions">
        <button class="btn primary" onclick={() => void saveRepeatable()} disabled={busy}>Create job</button>
        <button class="btn" onclick={reset} disabled={busy}>Cancel</button>
      </div>
    </div>
  {:else if form === 'once'}
    <div class="form">
      <div class="form-title">Schedule once — runs one time, then clears itself</div>
      <div class="fields">
        <label class="opt">date
          <input type="date" bind:value={onceDate} disabled={busy} />
        </label>
        <label class="opt narrow">time
          <input type="time" bind:value={onceTime} disabled={busy} />
        </label>
      </div>
      <p class="hint">Fires while phantom-ink is running (the Collect scheduler is in-app).</p>
      <div class="form-actions">
        <button class="btn primary" onclick={() => void saveOnce()} disabled={busy}>Schedule</button>
        <button class="btn" onclick={reset} disabled={busy}>Cancel</button>
      </div>
    </div>
  {/if}
</div>

<style>
  .todo-actions { margin-top: 10px; border-top: 1px dashed var(--color-border-secondary); padding-top: 10px; }
  .action-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .menu-wrap { position: relative; }
  .menu {
    position: absolute; top: calc(100% + 4px); left: 0; z-index: 20;
    background: var(--color-bg-secondary); border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm); box-shadow: 0 6px 20px rgba(0,0,0,0.3);
    display: flex; flex-direction: column; min-width: 160px; overflow: hidden;
  }
  .menu-item {
    background: transparent; border: none; text-align: left; cursor: pointer;
    color: var(--color-text-secondary); font-size: 0.78rem; padding: 8px 10px;
  }
  .menu-item:hover { background: var(--color-bg-hover, rgba(255,255,255,0.05)); color: var(--color-text-primary); }

  .btn {
    background: var(--color-bg-secondary); border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm); color: var(--color-text-muted);
    cursor: pointer; font-size: 0.75rem; padding: 0.3rem 0.7rem;
  }
  .btn:hover:not(:disabled) { color: var(--color-text-primary); }
  .btn:disabled { opacity: 0.5; cursor: default; }
  .btn.run { color: var(--color-text-primary); border-color: var(--color-accent, var(--color-border-primary)); }
  .btn.primary { color: var(--color-bg-primary); background: var(--color-accent); border-color: var(--color-accent); }

  .opt { display: inline-flex; align-items: center; gap: 5px; font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.03em; color: var(--color-text-tertiary); }
  .opt select, .opt input {
    background: var(--color-bg-primary); border: 1px solid var(--color-border-secondary);
    border-radius: var(--radius-sm); color: var(--color-text-primary);
    font-size: 0.78rem; text-transform: none; letter-spacing: 0; padding: 3px 6px;
  }
  .opt.narrow input { width: 72px; }

  .form { margin-top: 10px; background: var(--color-bg-primary); border: 1px solid var(--color-border-secondary); border-radius: var(--radius-sm); padding: 10px; }
  .form-title { font-size: 0.78rem; color: var(--color-text-secondary); margin-bottom: 8px; }
  .fields { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  .form-actions { display: flex; gap: 8px; margin-top: 10px; }
  .hint { color: var(--color-text-muted); font-size: 0.7rem; margin: 8px 0 0; }
</style>

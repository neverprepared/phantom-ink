<script lang="ts">
  import { getApi } from '../utils/api';
  import { timeAgoOrDate } from '../utils/format';
  import { onCollectUpdate } from '../utils/collectEvents';
  import { onMount, onDestroy } from 'svelte';
  import { profileState, dashboardState } from '../stores.svelte';
  import { notifications } from '../notifications.svelte';
  import Spinner from '../components/Spinner.svelte';
  import EmptyState from '../components/EmptyState.svelte';
  import { collectJobsRes } from '../stores/collectors.svelte';
  import { schedulesRes, schedulerStatusRes } from '../stores/schedules.svelte';
  import {
    CADENCE_KINDS, cadenceFrom, cadenceToDraft, cadenceValid,
    isLocalRow, isRemoteRow, parseScheduleError, rowFromCollectJob, rowFromSchedule,
    type CadenceKind, type ScheduleRow,
  } from '../schedules';

  // ── Two backends, one list ─────────────────────────────────────────────
  //
  // Scheduled work moved to the server (phantom-events owns the clock) so it
  // fires with this app closed. Dashboard-widget jobs stayed local, because a
  // widget polling a metric while the app is open is not an infrastructure
  // concern. So this panel shows rows from both, and `row.isRemote` routes
  // every mutation — run-now, toggle, delete — to the right backend.
  //
  // The two resources are deliberately separate rather than merged upstream:
  // they have independent loading and error states, and the bus being
  // unreachable must not blank the local widget rows.
  //
  // The local `CollectJob` interface that used to live here was deleted: it had
  // drifted (no source / owner_widget_id / run_once_at_ms) and was already
  // shadowed by the generated main.CollectJob from the store.

  const profile = $derived(profileState.active?.name ?? '');

  const localJobs = $derived(collectJobsRes.value);
  const remoteSchedules = $derived(schedulesRes.value);
  const schedulerStatus = $derived(schedulerStatusRes.value);

  const loading = $derived(collectJobsRes.loading || schedulesRes.loading);
  const refreshing = $derived(collectJobsRes.refreshing || schedulesRes.refreshing);
  // Surface either failure rather than letting one mask the other — "no rows"
  // and "could not reach the bus" must never look the same.
  const loadError = $derived(schedulesRes.error ?? collectJobsRes.error);

  let saving = $state(false); // in-flight guard — a new schedule has id:'' and would insert twice
  let editingId = $state<string | null>(null); // null=none, 'new'=create, id=edit
  let runningId = $state<string | null>(null);
  let importing = $state(false);
  let statusMsg = $state('');
  let statusIsError = $state(false);
  let filterProfile = $state('');
  $effect(() => { filterProfile = profile; });

  // Edit form state. Cadence is now a kind + its own fields, matching the
  // server's discriminated cadence rather than the old inferred interval/time
  // pair — which is also how cron and one-shot become reachable at all.
  let draft = $state({
    name: '',
    command: '',
    kind: 'interval' as CadenceKind,
    intervalS: 300,
    runAt: '08:30',
    days: 'daily' as 'daily' | 'weekdays',
    cronExpr: '0 * * * *',
    fireAtMs: Date.now() + 3_600_000,
    enabled: true,
  });

  // ── Derived ────────────────────────────────────────────────────────────

  const rows = $derived.by<ScheduleRow[]>(() => [
    ...remoteSchedules.map(rowFromSchedule),
    // Only widget-owned local jobs belong here now. A legacy source='' row is
    // inert (the local scheduler skips it) and showing it would imply it still
    // runs — the import banner is how those are surfaced instead.
    ...localJobs.filter((j: any) => j.source === 'widget').map(rowFromCollectJob),
  ]);

  const jobProfiles = $derived.by(() => {
    const s = new Set<string>();
    for (const r of rows) if (r.profile) s.add(r.profile);
    return [...s].sort();
  });

  const visible = $derived(
    filterProfile ? rows.filter(r => r.profile === filterProfile) : rows
  );

  // Legacy local rows that have not been carried to the server yet. These no
  // longer fire, so the panel has to say so rather than hide them.
  const unimported = $derived(
    localJobs.filter((j: any) => j.source !== 'widget' && !j.imported_schedule_id)
  );

  // ── Data loading ───────────────────────────────────────────────────────

  function load() {
    return Promise.all([
      collectJobsRes.refresh(),
      schedulesRes.refresh(),
      schedulerStatusRes.refresh(),
    ]);
  }

  // A server-side run emits no Wails event, so the remote half needs polling.
  // The local `collect:update` subscription stays for widget rows, which do
  // emit it. POLL_MS matches ServerRulesTab.
  const POLL_MS = 5000;
  let poll: ReturnType<typeof setInterval> | null = null;

  // ── CRUD, routed by backend ────────────────────────────────────────────

  async function save() {
    if (saving) return;
    const a = await getApi();
    if (!a || !draft.name.trim()) return;
    const isNew = editingId === 'new';
    const existing = rows.find(r => r.id === editingId);

    // Local widget rows are not editable here — a widget owns its own job and
    // re-registers it, so editing it from this panel would be overwritten.
    if (!isNew && existing && isLocalRow(existing)) {
      flash('Widget-owned jobs are configured on their dashboard widget.', true);
      return;
    }

    const cadence = cadenceFrom(draft.kind, draft);
    if (!cadenceValid(cadence)) {
      flash('That cadence is incomplete.', true);
      return;
    }
    if (!draft.command.trim()) return;

    saving = true;
    try {
      await (a.SaveSchedule as any)({
        id: isNew ? '' : (editingId ?? ''),
        name: draft.name.trim(),
        profile: isNew ? (filterProfile || profile) : (existing?.profile ?? profile),
        enabled: draft.enabled,
        description: '',
        schedule: cadence,
        timezone: '', // Go fills in this machine's zone
        actions: [{
          type: 'run_profile_command',
          command: draft.command.trim(),
          workspace_profile: isNew ? (filterProfile || profile) : (existing?.profile ?? profile),
          // It is a collector: its stdout entries publish onto the bus.
          emit_entries: true,
        }],
        catch_up: 'skip',
        next_run_at: null,
        last_run_at: null,
        last_status: '',
        trigger_count: 0,
        created_at: 0,
        updated_at: 0,
      });
      editingId = null;
      await load();
    } catch (e: any) {
      flash(parseScheduleError(e), true);
    } finally {
      saving = false;
    }
  }

  // Two-step inline delete confirmation. Native window.confirm() is an
  // unreliable no-op in the Wails webview (it returns undefined, so a
  // `!confirm()` guard bails silently and "nothing happens"), so the ✕ arms and
  // a second click commits — the pattern TokensPanel and ConversationsPanel
  // already use.
  //
  // This existed as a one-click delete, and an inbox schedule was lost to a
  // stray click. An ENABLED schedule is the costlier mistake, so its armed
  // label says so.
  let confirmDeleteId = $state<string | null>(null);
  let confirmTimer: ReturnType<typeof setTimeout> | null = null;

  function armDelete(row: ScheduleRow) {
    if (confirmTimer) clearTimeout(confirmTimer);
    confirmDeleteId = row.id;
    // Disarm on its own, so a forgotten armed button cannot be committed by a
    // later unrelated click.
    confirmTimer = setTimeout(() => { confirmDeleteId = null; }, 5000);
  }

  function disarmDelete() {
    if (confirmTimer) clearTimeout(confirmTimer);
    confirmTimer = null;
    confirmDeleteId = null;
  }

  async function confirmRemove(row: ScheduleRow) {
    disarmDelete();
    await remove(row);
  }

  async function remove(row: ScheduleRow) {
    const a = await getApi();
    if (!a) return;
    try {
      if (isRemoteRow(row)) {
        await (a.DeleteSchedule as any)(row.id);
      } else {
        await (a.DeleteCollectJob as any)(row.id);
        // A dashboard widget referencing this job would auto-recreate it on its
        // next mount, so drop it from the layout too. Only meaningful for local
        // rows — a remote schedule has no widget owner.
        const layout = dashboardState.layout;
        if (layout) {
          const filtered = layout.widgets.filter(w => (w.config as any)?.jobId !== row.id);
          if (filtered.length !== layout.widgets.length) {
            dashboardState.updateWidgets(filtered);
            try {
              await (a as any).SaveDashboardLayout(
                profileState.active?.name ?? '',
                JSON.stringify({ version: 1, widgets: filtered }),
              );
            } catch {}
          }
        }
      }
      await load();
    } catch (e: any) {
      notifications.error(`Failed to delete: ${parseScheduleErrorSafe(e)}`);
    }
  }

  function parseScheduleErrorSafe(e: any): string {
    try { return parseScheduleError(e); } catch { return 'unknown error'; }
  }

  async function runNow(row: ScheduleRow) {
    const a = await getApi();
    if (!a) return;
    runningId = row.id;
    try {
      if (isRemoteRow(row)) {
        const res = await (a.RunScheduleNow as any)(row.id);
        flash(`queued ${res?.queued ?? 0} action(s)`);
      } else {
        await (a.RunCollectJobNow as any)(row.id);
      }
      await load();
    } catch (e: any) {
      flash(parseScheduleError(e), true);
    } finally {
      runningId = null;
    }
  }

  async function toggle(row: ScheduleRow) {
    const a = await getApi();
    if (!a) return;
    try {
      if (isLocalRow(row)) {
        await (a.SaveCollectJob as any)({ ...row.job, enabled: !row.enabled });
      } else {
        // The dedicated endpoint, not a full save: a save would round-trip the
        // server-managed run stats and clobber them, and re-enabling re-seeds
        // next_run_at so a long-disabled schedule does not come back "missed".
        await (a.SetScheduleEnabled as any)(row.id, !row.enabled);
      }
      await load();
    } catch (e: any) {
      notifications.error(`Failed to update: ${parseScheduleErrorSafe(e)}`);
    }
  }

  async function importLegacy() {
    const a = await getApi();
    if (!a || importing) return;
    importing = true;
    try {
      const res = await (a.ImportLocalSchedules as any)('');
      const n = res?.imported?.length ?? 0;
      const skipped = res?.skipped ?? [];
      const failed = res?.failed ?? [];
      if (n) notifications.info(`Carried ${n} job(s) over to the server.`);
      for (const s of skipped) {
        notifications.error(`"${s.name}" was not imported: ${s.reason}`);
      }
      for (const f of failed) {
        notifications.error(`"${f.name}" failed to import: ${f.reason}`);
      }
      if (!n && !skipped.length && !failed.length) {
        notifications.info('Nothing left to import.');
      }
      await load();
    } catch (e: any) {
      notifications.error(`Import failed: ${parseScheduleErrorSafe(e)}`);
    } finally {
      importing = false;
    }
  }

  // ── Form helpers ───────────────────────────────────────────────────────

  function startNew() {
    editingId = 'new';
    draft = {
      name: '', command: '', kind: 'interval', intervalS: 300,
      runAt: '08:30', days: 'daily', cronExpr: '0 * * * *',
      fireAtMs: Date.now() + 3_600_000, enabled: true,
    };
  }

  function startEdit(row: ScheduleRow) {
    if (!isRemoteRow(row)) {
      flash('Widget-owned jobs are configured on their dashboard widget.', true);
      return;
    }
    editingId = row.id;
    const c = cadenceToDraft(row.schedule.schedule);
    const action = row.schedule.actions?.[0] ?? {};
    draft = {
      name: row.name,
      command: String(action.command ?? ''),
      kind: c.kind,
      intervalS: c.intervalS,
      runAt: c.runAt,
      days: c.days,
      cronExpr: c.cronExpr,
      fireAtMs: c.fireAtMs,
      enabled: row.enabled,
    };
  }

  function cancelEdit() { editingId = null; }

  // flash used to ignore its error flag and render every message in the
  // success colour, including failures.
  function flash(msg: string, err = false) {
    statusMsg = msg;
    statusIsError = err;
    setTimeout(() => { statusMsg = ''; statusIsError = false; }, 4000);
  }

  function fmtLastRun(row: ScheduleRow): string {
    return timeAgoOrDate(row.lastRunAt ?? undefined);
  }

  function isFormValid(): boolean {
    if (!draft.name.trim() || !draft.command.trim()) return false;
    return cadenceValid(cadenceFrom(draft.kind, draft));
  }

  onMount(() => {
    void load();
    const off = onCollectUpdate(() => void load());
    poll = setInterval(() => void load(), POLL_MS);
    return () => off();
  });

  onDestroy(() => { if (poll) clearInterval(poll); });
</script>


<div class="jobs">
  <div class="panel-header" style="margin-bottom:var(--spacing-sm);">
    <h1 class="page-title">jobs</h1>
    <div style="display:flex;align-items:center;gap:var(--spacing-md);">
      {#if loading}<Spinner />{/if}
      {#if statusMsg}
        <span class="status-msg" class:err={statusIsError}>{statusMsg}</span>
      {/if}
      {#if editingId !== 'new'}
        <button class="btn primary" onclick={startNew}>+ new job</button>
      {/if}
    </div>
  </div>

  <!-- The clock lives on the server now, so say whether it is actually
       running rather than leaving it to be inferred from rows changing. -->
  {#if schedulerStatus && !schedulerStatus.enabled}
    <div class="sched-note warn">
      The server scheduler is disabled (PE_SCHEDULER__ENABLED), so nothing here will fire.
    </div>
  {:else if schedulerStatus?.last_error}
    <div class="sched-note warn">Scheduler error: {schedulerStatus.last_error}</div>
  {/if}

  <!-- Legacy local jobs stopped firing when the local scheduler went
       widget-only. Offer the one-way carry-over rather than letting them go
       quiet unannounced. -->
  {#if unimported.length > 0}
    <div class="sched-note">
      {unimported.length} local job{unimported.length === 1 ? '' : 's'} no longer run here.
      Carry them over to the server to keep them firing.
      <button class="btn sm primary" onclick={importLegacy} disabled={importing}>
        {importing ? 'importing…' : 'import to server'}
      </button>
    </div>
  {/if}

  <!-- Profile filter -->
  {#if jobProfiles.length > 1}
    <div class="filter-row">
      <span class="filter-label">profile</span>
      <div class="tag-bar">
        <button class="tag" class:active={filterProfile === ''} onclick={() => filterProfile = ''}>all</button>
        {#each jobProfiles as p (p)}
          <button class="tag" class:active={filterProfile === p} onclick={() => filterProfile = filterProfile === p ? '' : p}>{p}</button>
        {/each}
      </div>
    </div>
  {/if}

  <!-- Create form -->
  {#if editingId === 'new'}
    <div class="job-form">
      <div class="form-title">new job</div>

      <label class="form-row">
        <span class="form-label">name</span>
        <input class="form-input" bind:value={draft.name} placeholder="Morning standup" />
      </label>

      <label class="form-row">
        <span class="form-label">command</span>
        <textarea class="form-textarea" bind:value={draft.command} placeholder="script that outputs a JSON array of entries" rows="4"></textarea>
      </label>

      <!-- Cadence -->
      <div class="form-row">
        <span class="form-label">cadence</span>
        <div class="seg-ctrl">
          {#each CADENCE_KINDS as k (k)}
            <button class="seg-btn" class:active={draft.kind === k} onclick={() => draft.kind = k}>{k}</button>
          {/each}
        </div>
      </div>

      {#if draft.kind === 'interval'}
        <label class="form-row">
          <span class="form-label">every</span>
          <div class="interval-row">
            <input class="form-input short" type="number" min="30" bind:value={draft.intervalS} />
            <span class="form-unit">seconds</span>
          </div>
        </label>
      {:else if draft.kind === 'daily'}
        <div class="form-row">
          <span class="form-label">time</span>
          <div class="time-row">
            <input class="form-input short" type="time" bind:value={draft.runAt} />
            <select class="form-select narrow" bind:value={draft.days}>
              <option value="daily">daily</option>
              <option value="weekdays">weekdays</option>
            </select>
          </div>
        </div>
      {:else if draft.kind === 'cron'}
        <label class="form-row">
          <span class="form-label">expression</span>
          <div class="interval-row">
            <input class="form-input" bind:value={draft.cronExpr} placeholder="0 */4 * * *" />
            <span class="form-unit">{draft.days === 'weekdays' ? '' : ''}resolved in this machine's timezone</span>
          </div>
        </label>
      {:else}
        <label class="form-row">
          <span class="form-label">fires at</span>
          <div class="interval-row">
            <input class="form-input" type="datetime-local"
              value={new Date(draft.fireAtMs - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16)}
              onchange={(e) => draft.fireAtMs = new Date((e.currentTarget as HTMLInputElement).value).getTime()} />
            <span class="form-unit">once, then disables itself</span>
          </div>
        </label>
      {/if}

      <div class="form-actions">
        <button class="btn sm primary" onclick={save} disabled={saving || !isFormValid()}>{saving ? 'saving…' : 'save'}</button>
        <button class="btn sm ghost" onclick={cancelEdit}>cancel</button>
      </div>
    </div>
  {/if}

  <!-- Job list -->
  {#if loading && rows.length === 0}
    <div class="empty">loading…</div>
  {:else if loadError}
    <EmptyState title="Failed to load schedules" message={loadError} />
  {:else if rows.length === 0 && editingId !== 'new'}
    <EmptyState title="No schedules yet" message="Create one to run work on a schedule — it fires on the platform, with this app closed." />
  {:else if visible.length === 0 && editingId !== 'new'}
    <EmptyState title="No jobs for this profile" />
  {:else}
    <div class="job-list">
      {#each visible as row (row.id)}
        <div class="job-card" class:editing={editingId === row.id}>
          {#if editingId === row.id}
            <!-- Inline edit form -->
            <div class="job-form inline">
              <label class="form-row">
                <span class="form-label">name</span>
                <input class="form-input" bind:value={draft.name} />
              </label>

              <label class="form-row">
                <span class="form-label">command</span>
                <textarea class="form-textarea" bind:value={draft.command} rows="4"></textarea>
              </label>

              <!-- Cadence -->
              <div class="form-row">
                <span class="form-label">cadence</span>
                <div class="seg-ctrl">
                  {#each CADENCE_KINDS as k (k)}
                            <button class="seg-btn" class:active={draft.kind === k} onclick={() => draft.kind = k}>{k}</button>
                  {/each}
                </div>
              </div>

              {#if draft.kind === 'interval'}
                <label class="form-row">
                  <span class="form-label">every</span>
                  <div class="interval-row">
                            <input class="form-input short" type="number" min="30" bind:value={draft.intervalS} />
                            <span class="form-unit">seconds</span>
                  </div>
                </label>
              {:else if draft.kind === 'daily'}
                <div class="form-row">
                  <span class="form-label">time</span>
                  <div class="time-row">
                            <input class="form-input short" type="time" bind:value={draft.runAt} />
                            <select class="form-select narrow" bind:value={draft.days}>
                              <option value="daily">daily</option>
                              <option value="weekdays">weekdays</option>
                            </select>
                  </div>
                </div>
              {:else if draft.kind === 'cron'}
                <label class="form-row">
                  <span class="form-label">expression</span>
                  <div class="interval-row">
                            <input class="form-input" bind:value={draft.cronExpr} placeholder="0 */4 * * *" />
                            <span class="form-unit">{draft.days === 'weekdays' ? '' : ''}resolved in this machine's timezone</span>
                  </div>
                </label>
              {:else}
                <label class="form-row">
                  <span class="form-label">fires at</span>
                  <div class="interval-row">
                            <input class="form-input" type="datetime-local"
                              value={new Date(draft.fireAtMs - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16)}
                              onchange={(e) => draft.fireAtMs = new Date((e.currentTarget as HTMLInputElement).value).getTime()} />
                            <span class="form-unit">once, then disables itself</span>
                  </div>
                </label>
              {/if}

              <div class="form-actions">
                <button class="btn sm primary" onclick={save} disabled={saving || !isFormValid()}>{saving ? 'saving…' : 'save'}</button>
                <button class="btn sm ghost" onclick={cancelEdit}>cancel</button>
              </div>
            </div>
          {:else}
            <div class="job-row">
              <button class="job-toggle" class:on={row.enabled} onclick={() => toggle(row)}
                title={row.enabled ? 'disable' : 'enable'}>
                {row.enabled ? '●' : '○'}
              </button>
              <div class="job-info" role="button" tabindex="0"
                onclick={() => startEdit(row)}
                onkeydown={(e) => e.key === 'Enter' && startEdit(row)}>
                <span class="job-name">{row.name}</span>
                <span class="job-cmd">{row.target}</span>
                <div class="job-meta-row">
                  {#if row.profile}
                    <span class="job-profile">{row.profile}</span>
                  {/if}
                  {#if row.isRemote}
                    <span class="job-type-badge type-shell" title="runs on the platform, with this app closed">server</span>
                  {:else}
                    <span class="job-source-badge" title="created by a dashboard widget; runs locally while the app is open">widget</span>
                  {/if}
                  <span class="job-meta">{row.cadence}</span>
                  <span class="job-meta">last run: {fmtLastRun(row)}</span>
                  {#if row.overdue}
                    <span class="job-err" title="its next fire time has already passed">⚠ overdue</span>
                  {/if}
                  {#if row.lastError}
                    <span class="job-err" title={row.lastError}>✗ error</span>
                  {/if}
                </div>
              </div>
              <div class="job-btns">
                <button class="job-btn" onclick={() => runNow(row)} disabled={runningId === row.id} title="run now">
                  {runningId === row.id ? '…' : '▶'}
                </button>
                {#if confirmDeleteId === row.id}
                  <button
                    class="job-btn danger armed"
                    onclick={() => confirmRemove(row)}
                    title={row.enabled
                      ? 'Delete this ENABLED schedule — click again to confirm'
                      : 'Delete this schedule — click again to confirm'}
                  >{row.enabled ? 'delete enabled?' : 'delete?'}</button>
                  <button class="job-btn" onclick={disarmDelete} title="cancel">↩</button>
                {:else}
                  <button class="job-btn danger" onclick={() => armDelete(row)} title="delete">✕</button>
                {/if}
              </div>
            </div>
          {/if}
        </div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .jobs {
    padding: var(--panel-padding);
    display: flex;
    flex-direction: column;
    gap: var(--spacing-md);
    min-height: 100%;
  }

  .status-msg {
    font-family: var(--font-mono); font-size: 11px;
    color: var(--color-success);
  }

  /* Filter */
  .filter-row { display: flex; align-items: center; gap: var(--spacing-sm); }
  .filter-label { font-family: var(--font-mono); font-size: 10px; color: var(--color-text-muted); width: 38px; flex-shrink: 0; }
  .tag-bar { display: flex; flex-wrap: wrap; gap: 6px; }
  .tag {
    font-family: var(--font-mono); font-size: 10px; padding: 2px 8px;
    border-radius: 999px; border: 1px solid var(--color-border-primary);
    background: none; cursor: pointer; color: var(--color-text-muted);
  }
  .tag:hover { border-color: var(--color-border-secondary); color: var(--color-text-secondary); }
  .tag.active { border-color: var(--color-accent); color: var(--color-accent); background: rgba(234,179,8,0.06); }

  .empty {
    font-size: 13px;
    padding: var(--spacing-3xl) 0;
    line-height: 1.5;
  }

  /* Job list */
  .job-list { display: flex; flex-direction: column; gap: var(--spacing-sm); }

  .job-card {
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-md);
    background: var(--color-bg-secondary);
    overflow: hidden; transition: border-color 100ms;
  }
  .job-card:hover { border-color: var(--color-border-secondary); }
  .job-card.editing { border-color: var(--color-accent); }

  .job-row {
    display: grid; grid-template-columns: 24px 1fr auto;
    align-items: center; gap: var(--spacing-md);
    padding: var(--spacing-md) var(--spacing-lg);
  }

  .job-toggle {
    background: none; border: none; cursor: pointer;
    font-size: 14px; font-family: var(--font-mono);
    color: var(--color-text-muted); padding: 0; line-height: 1; transition: color 100ms;
  }
  .job-toggle.on { color: var(--color-success); }
  .job-toggle:hover { opacity: 0.7; }

  .job-info { display: flex; flex-direction: column; gap: 3px; min-width: 0; cursor: pointer; }
  .job-name { font-size: 13px; font-weight: 500; color: var(--color-text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .job-cmd { font-family: var(--font-mono); font-size: 10px; color: var(--color-text-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .job-meta-row { display: flex; gap: var(--spacing-sm); align-items: center; flex-wrap: wrap; }
  .job-meta { font-family: var(--font-mono); font-size: 10px; color: var(--color-text-tertiary); }
  .job-profile { font-family: var(--font-mono); font-size: 10px; color: var(--color-accent); background: rgba(234,179,8,0.08); border: 1px solid rgba(234,179,8,0.2); border-radius: 999px; padding: 1px 6px; }
  .job-err { font-family: var(--font-mono); font-size: 10px; color: var(--color-error); }

  .job-type-badge {
    font-family: var(--font-mono); font-size: 10px;
    padding: 1px 6px; border-radius: 999px;
    border: 1px solid var(--color-border-primary);
    color: var(--color-text-muted);
  }

  .job-source-badge {
    font-family: var(--font-mono); font-size: 10px;
    padding: 1px 6px; border-radius: 999px;
    color: var(--accent, var(--color-accent));
    border: 1px solid color-mix(in srgb, var(--accent, var(--color-accent)) 30%, transparent);
    background: color-mix(in srgb, var(--accent, var(--color-accent)) 8%, transparent);
  }


  .job-btns { display: flex; gap: 4px; flex-shrink: 0; }
  .job-btn {
    background: none; border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm); padding: 3px 8px;
    font-size: 11px; cursor: pointer; color: var(--color-text-muted);
    transition: all 100ms; font-family: var(--font-mono);
  }
  .job-btn:hover:not(:disabled) { border-color: var(--color-accent); color: var(--color-accent); }
  .job-btn:disabled { opacity: 0.4; cursor: not-allowed; }
  .job-btn.danger:hover:not(:disabled) { border-color: var(--color-error); color: var(--color-error); }
  /* Armed delete: filled rather than outlined, so the committing click never
     looks like the arming one. Width is free-form because the label is words,
     not a glyph. */
  .job-btn.danger.armed {
    border-color: var(--color-error);
    background: var(--color-error);
    color: var(--color-bg);
    width: auto;
    padding: 0 8px;
    font-size: 11px;
    white-space: nowrap;
  }

  /* Form */
  .job-form {
    display: flex; flex-direction: column; gap: var(--spacing-md);
    padding: var(--spacing-lg);
    border: 1px solid var(--color-accent);
    border-radius: var(--radius-md);
    background: var(--color-bg-secondary);
  }
  .job-form.inline { border: none; border-radius: 0; }
  .form-title { font-size: 12px; font-weight: 600; color: var(--color-text-secondary); }
  .form-row { display: flex; flex-direction: column; gap: 4px; }
  .form-label { font-family: var(--font-mono); font-size: 10px; color: var(--color-text-muted); }

  .form-input {
    background: var(--color-bg-primary); border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm); padding: 6px 8px;
    font-size: 12px; color: var(--color-text-primary); font-family: inherit; width: 100%;
  }
  .form-input:focus { outline: none; border-color: var(--color-accent); }
  .form-input.short { width: 100px; }

  .form-textarea {
    background: var(--color-bg-primary); border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm); padding: 6px 8px;
    font-size: 11px; font-family: var(--font-mono);
    color: var(--color-text-primary); width: 100%; resize: vertical;
  }
  .form-textarea:focus { outline: none; border-color: var(--color-accent); }

  .form-select {
    background: var(--color-bg-primary); border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm); padding: 6px 8px;
    font-size: 12px; color: var(--color-text-primary); font-family: inherit;
    width: 100%; cursor: pointer;
  }
  .form-select:focus { outline: none; border-color: var(--color-accent); }
  .form-select.narrow { width: auto; min-width: 100px; }

  /* Segmented control */
  .seg-ctrl { display: flex; gap: 0; }
  .seg-btn {
    font-family: var(--font-mono); font-size: 11px;
    padding: 4px 10px; background: none;
    border: 1px solid var(--color-border-primary);
    cursor: pointer; color: var(--color-text-muted);
    transition: all 100ms; margin-left: -1px;
  }
  .seg-btn:first-child { border-radius: var(--radius-sm) 0 0 var(--radius-sm); margin-left: 0; }
  .seg-btn:last-child  { border-radius: 0 var(--radius-sm) var(--radius-sm) 0; }
  .seg-btn.active { background: rgba(234,179,8,0.08); border-color: var(--color-accent); color: var(--color-accent); z-index: 1; position: relative; }
  .seg-btn:hover:not(.active) { color: var(--color-text-secondary); }

  .interval-row { display: flex; align-items: center; gap: var(--spacing-sm); }
  .time-row { display: flex; align-items: center; gap: var(--spacing-sm); }
  .form-unit { font-family: var(--font-mono); font-size: 11px; color: var(--color-text-muted); }

  .form-actions { display: flex; gap: var(--spacing-sm); }

  /* Added with the schedules cutover. */
  .sched-note {
    display: flex;
    align-items: center;
    gap: var(--spacing-sm);
    flex-wrap: wrap;
    margin-bottom: var(--spacing-sm);
    padding: var(--spacing-sm) var(--spacing-md);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-sm);
    font-size: var(--font-size-sm);
    color: var(--color-text-secondary);
  }
  .sched-note.warn {
    border-color: var(--color-warning, var(--color-border));
    color: var(--color-text);
  }
  .status-msg.err { color: var(--color-danger, crimson); }
</style>

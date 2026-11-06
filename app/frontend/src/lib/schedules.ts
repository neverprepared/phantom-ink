/** Server-side schedules — the time-trigger plane.
 *
 * phantom-events owns the clock and the schedule store; phantom-router proxies
 * /api/schedules; the Go client (app/brainbox/schedules.go) is the only thing
 * that speaks HTTP. This module is pure: types, cadence conversion, display
 * helpers, and error parsing. No IO.
 *
 * The cutover this belongs to: user-created collect jobs moved to the server so
 * they fire with the app closed, while dashboard-widget jobs stay local. The
 * panel therefore shows two kinds of row, and `isRemote` below is the
 * discriminator that routes every mutation to the right backend.
 */

/** Cadence shapes the server accepts, discriminated on `kind`. */
export type Cadence =
  | { kind: 'interval'; every_s: number }
  | { kind: 'daily'; at: string; days: 'daily' | 'weekdays' }
  | { kind: 'cron'; expr: string }
  | { kind: 'at'; fire_at_ms: number };

export type CadenceKind = Cadence['kind'];

export const CADENCE_KINDS: readonly CadenceKind[] = ['interval', 'daily', 'cron', 'at'];

/** What to do with a window missed while nothing was running. */
export type CatchUp = 'skip' | 'run_once';

/** Mirrors brainbox.Schedule (app/brainbox/schedules.go). */
export interface Schedule {
  id: string;
  name: string;
  profile: string;
  enabled: boolean;
  description: string;
  schedule: Record<string, any>;
  timezone: string;
  actions: Record<string, any>[];
  catch_up: string;
  next_run_at: number | null;
  last_run_at: number | null;
  last_status: string;
  trigger_count: number;
  created_at: number;
  updated_at: number;
}

/** A row in the Schedules panel, from either backend.
 *
 * Deliberately a discriminated union rather than a merged shape: run-now,
 * toggle and delete all have to dispatch to a different backend per row, and a
 * single flattened type would let that routing be forgotten silently.
 */
export type ScheduleRow =
  | { isRemote: true; id: string; name: string; profile: string; enabled: boolean;
      cadence: string; lastRunAt: number | null; nextRunAt: number | null;
      lastError: string; target: string; overdue: boolean; schedule: Schedule }
  | { isRemote: false; id: string; name: string; profile: string; enabled: boolean;
      cadence: string; lastRunAt: number | null; nextRunAt: null;
      lastError: string; target: string; overdue: false; job: any };

export type LocalRow = Extract<ScheduleRow, { isRemote: false }>;
export type RemoteRow = Extract<ScheduleRow, { isRemote: true }>;

/** Explicit guards rather than relying on discriminant narrowing, which did not
 * resolve through svelte-check here. Being explicit also makes the routing
 * obvious at each call site: every mutation has to pick a backend. */
export function isLocalRow(r: ScheduleRow): r is LocalRow {
  return r.isRemote === false;
}

export function isRemoteRow(r: ScheduleRow): r is RemoteRow {
  return r.isRemote === true;
}

/** Build a cadence object from the panel's form state. */
export function cadenceFrom(
  kind: CadenceKind,
  draft: { intervalS: number; runAt: string; days: 'daily' | 'weekdays'; cronExpr: string; fireAtMs: number },
): Cadence {
  switch (kind) {
    case 'interval':
      return { kind: 'interval', every_s: Math.max(30, Math.floor(draft.intervalS || 0)) };
    case 'daily':
      return { kind: 'daily', at: draft.runAt || '08:30', days: draft.days || 'daily' };
    case 'cron':
      return { kind: 'cron', expr: draft.cronExpr.trim() };
    case 'at':
      return { kind: 'at', fire_at_ms: draft.fireAtMs };
  }
}

/** Read a server cadence back into form state, for editing. */
export function cadenceToDraft(schedule: Record<string, any> | null | undefined): {
  kind: CadenceKind;
  intervalS: number;
  runAt: string;
  days: 'daily' | 'weekdays';
  cronExpr: string;
  fireAtMs: number;
} {
  const s = schedule ?? {};
  const kind: CadenceKind = CADENCE_KINDS.includes(s.kind) ? s.kind : 'interval';
  return {
    kind,
    intervalS: typeof s.every_s === 'number' ? s.every_s : 300,
    runAt: typeof s.at === 'string' ? s.at : '08:30',
    days: s.days === 'weekdays' ? 'weekdays' : 'daily',
    cronExpr: typeof s.expr === 'string' ? s.expr : '',
    fireAtMs: typeof s.fire_at_ms === 'number' ? s.fire_at_ms : Date.now() + 3_600_000,
  };
}

/** Is this cadence complete enough to save? Mirrors what the server validates,
 * so an obviously-empty cron does not need a round trip to be rejected. */
export function cadenceValid(c: Cadence): boolean {
  switch (c.kind) {
    case 'interval':
      return Number.isFinite(c.every_s) && c.every_s >= 30;
    case 'daily':
      return /^\d{1,2}:\d{2}$/.test(c.at);
    case 'cron':
      // Shape only — the server (croniter) is the real authority on validity.
      return c.expr.trim().split(/\s+/).length >= 5;
    case 'at':
      return Number.isFinite(c.fire_at_ms) && c.fire_at_ms > 0;
  }
}

/** Human cadence, for the row. Covers one-shot, which the old local formatter
 * did not — a run_once job used to render as "every 0s". */
export function fmtCadence(schedule: Record<string, any> | null | undefined): string {
  const s = schedule ?? {};
  switch (s.kind) {
    case 'interval': {
      const n = Number(s.every_s) || 0;
      if (n >= 3600 && n % 3600 === 0) return `every ${n / 3600}h`;
      if (n >= 60 && n % 60 === 0) return `every ${n / 60}m`;
      return `every ${n}s`;
    }
    case 'daily':
      return `${s.at} ${s.days === 'weekdays' ? 'weekdays' : 'daily'}`;
    case 'cron':
      return `cron ${s.expr}`;
    case 'at': {
      const ms = Number(s.fire_at_ms) || 0;
      return ms ? `once at ${new Date(ms).toLocaleString()}` : 'once';
    }
    default:
      return 'no cadence';
  }
}

/** The two action shapes this panel can author.
 *
 * The server accepts ten (phantom-events `RuleAction`); a schedule carries the
 * same union a rule does. This form knows how to edit two of them, and
 * `authoredActionKind` returning null is what puts the rest into pass-through
 * mode — a `run_host_command` rewritten as a container command would lose its
 * runner, argv and timeout.
 */
export type ScheduleActionKind = 'command' | 'task';

const AUTHORED: Record<string, ScheduleActionKind> = {
  run_profile_command: 'command',
  submit_task: 'task',
};

/** Which shape this schedule's action is, or null when the form must pass it
 * through untouched. A multi-action schedule is always pass-through: the form
 * authors exactly one action and saving would drop the rest. */
export function authoredActionKind(
  actions: Record<string, any>[] | null | undefined,
): ScheduleActionKind | null {
  const list = actions ?? [];
  if (list.length !== 1) return null;
  return AUTHORED[String(list[0]?.type ?? '')] ?? null;
}

/** Short label for what a schedule actually does. */
export function fmtTarget(actions: Record<string, any>[] | null | undefined): string {
  const list = actions ?? [];
  if (list.length === 0) return 'no actions';
  if (list.length > 1) return `${list.length} actions`;
  const a = list[0];
  switch (a.type) {
    case 'run_profile_command':
      return truncate(String(a.command ?? ''), 60);
    case 'submit_task':
      return `task: ${truncate(String(a.description ?? ''), 48)}`;
    case 'notify':
      return `notify ${a.channel ?? ''}`.trim();
    case 'webhook':
      return `webhook ${truncate(String(a.url ?? ''), 48)}`;
    default:
      return String(a.type ?? 'action');
  }
}

function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n - 1) + '…' : s;
}

/** A schedule whose next fire is in the past has MISSED its window rather than
 * merely being late — the whole point of the server materializing next_run_at
 * instead of deriving it is that this is visible rather than silent. */
export function isOverdue(s: Schedule, graceMs = 300_000): boolean {
  return s.enabled && s.next_run_at != null && s.next_run_at < Date.now() - graceMs;
}

/** Parse a Wails-relayed error ("HTTP 400: {json}") into a display message.
 * The Go client truncates bodies at 500 bytes, so JSON.parse can fail — fall
 * back to the raw string rather than showing nothing. Mirrors
 * rules.ts::parseSaveError, which is the established idiom for this. */
export function parseScheduleError(e: any): string {
  const raw = String(e?.message ?? e ?? 'save failed');
  const m = raw.match(/HTTP (\d+): (.*)$/s);
  if (m) {
    try {
      const detail = JSON.parse(m[2])?.detail;
      if (typeof detail === 'string') return detail;
      if (detail) return JSON.stringify(detail);
    } catch {
      /* truncated body — fall through to raw */
    }
  }
  return raw;
}

/** Project a server schedule into a panel row. */
export function rowFromSchedule(s: Schedule): ScheduleRow {
  return {
    isRemote: true,
    id: s.id,
    name: s.name,
    profile: s.profile,
    enabled: s.enabled,
    cadence: fmtCadence(s.schedule),
    lastRunAt: s.last_run_at,
    nextRunAt: s.next_run_at,
    lastError: '',
    target: fmtTarget(s.actions),
    overdue: isOverdue(s),
    schedule: s,
  };
}

/** Project a local widget-owned collect job into a panel row. */
export function rowFromCollectJob(job: any): ScheduleRow {
  return {
    isRemote: false,
    id: job.id,
    name: job.name,
    profile: job.profile,
    enabled: job.enabled,
    cadence: fmtLocalCadence(job),
    lastRunAt: job.last_run_at ?? null,
    nextRunAt: null,
    lastError: job.last_error ?? '',
    target: truncate(String(job.command ?? ''), 60),
    overdue: false,
    job,
  };
}

/** Local widget jobs still use the old flat cadence columns. */
function fmtLocalCadence(job: any): string {
  if (job.run_once_at_ms) return `once at ${new Date(job.run_once_at_ms).toLocaleString()}`;
  if (job.run_at) return `${job.run_at} ${job.days === 'weekdays' ? 'weekdays' : 'daily'}`;
  const n = Number(job.interval_s) || 0;
  if (n >= 3600 && n % 3600 === 0) return `every ${n / 3600}h`;
  if (n >= 60 && n % 60 === 0) return `every ${n / 60}m`;
  return `every ${n}s`;
}

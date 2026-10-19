// Turns a log entry into the one line a human reads.
//
// The Logs tab used to render `body` verbatim, so every row said
// "claude_code.hook_execution_complete" and nothing about what the hook did —
// the attributes carrying the meaning were never even fetched. TailLogs now
// projects them into `attrs`; this is where they become readable.
//
// Rules of thumb for anything added here:
//   - lead with the identity of the thing (hook name, tool name, model)
//   - say the outcome, and say it loudly when it is a failure
//   - keep it short enough to scan a thousand rows
//   - never invent: if the attributes are absent, fall back to the event name

export interface LogEntry {
  time: string;
  body: string;
  session?: string;
  workspace?: string;
  model?: string;
  duration_ms?: number;
  severity?: string;
  service?: string;
  event?: string;
  attrs?: Record<string, unknown>;
}

// These indices are dynamically mapped, so the same attribute arrives as a
// different JSON type depending on which document typed it first: duration_ms
// is a string on tool_result but a number on api_request, every num_* on a hook
// event is a string, and `success` is the STRING "false", not a boolean.
// Comparing with === against a real boolean therefore matched nothing and a
// failed tool rendered with no outcome at all. Coerce, do not compare.
const num = (v: unknown): number | undefined => {
  if (typeof v === 'number') return Number.isFinite(v) ? v : undefined;
  if (typeof v === 'string' && v.trim() !== '') {
    const n = Number(v);
    return Number.isFinite(n) ? n : undefined;
  }
  return undefined;
};

/** Tri-state: true / false / undefined when the attribute is absent or junk. */
const bool = (v: unknown): boolean | undefined => {
  if (typeof v === 'boolean') return v;
  if (typeof v === 'string') {
    const t = v.trim().toLowerCase();
    if (t === 'true') return true;
    if (t === 'false') return false;
  }
  return undefined;
};

const str = (v: unknown): string | undefined =>
  typeof v === 'string' && v !== '' ? v : undefined;

/** The event name, preferring the keyword attribute over parsing `body`. */
export function eventName(l: LogEntry): string {
  if (l.event) return l.event;
  // body looks like "claude_code.tool_result"; keep the last segment.
  const b = l.body ?? '';
  const dot = b.lastIndexOf('.');
  return dot === -1 ? b : b.slice(dot + 1);
}

/** True when the row represents something that went wrong. */
export function isFailure(l: LogEntry): boolean {
  const a = l.attrs ?? {};
  if (bool(a.success) === false) return true;
  if (str(a.error_type)) return true;
  if ((num(a.num_non_blocking_error) ?? 0) > 0) return true;
  if ((num(a.num_blocking) ?? 0) > 0) return true;
  const sev = (l.severity ?? '').toUpperCase();
  return sev === 'ERROR' || sev === 'FATAL';
}

function hookSummary(a: Record<string, unknown>): string | undefined {
  const name = str(a.hook_name) ?? str(a.hook_event);
  if (!name) return undefined;
  const parts = [name];
  const total = num(a.num_hooks);
  const ok = num(a.num_success);
  if (total !== undefined && ok !== undefined) parts.push(`${ok}/${total} ok`);
  const blocked = num(a.num_blocking) ?? 0;
  if (blocked > 0) parts.push(`${blocked} blocked`);
  const cancelled = num(a.num_cancelled) ?? 0;
  if (cancelled > 0) parts.push(`${cancelled} cancelled`);
  const errored = num(a.num_non_blocking_error) ?? 0;
  if (errored > 0) parts.push(`${errored} errored`);
  return parts.join(' · ');
}

function toolSummary(a: Record<string, unknown>): string | undefined {
  const tool = str(a.tool_name);
  if (!tool) return undefined;
  const parts = [tool];
  const ok = bool(a.success);
  if (ok === false) {
    // The case that was previously indistinguishable from success.
    parts.push(str(a.error_type) ? `FAILED ${a.error_type}` : 'FAILED');
  } else if (ok === true) {
    parts.push('ok');
  }
  if (str(a.decision_type)) parts.push(String(a.decision_type));
  return parts.join(' · ');
}

function apiSummary(l: LogEntry, a: Record<string, unknown>): string | undefined {
  const model = l.model ?? str(a.model);
  if (!model) return undefined;
  const parts = [model];
  const inTok = num(a.input_tokens);
  const outTok = num(a.output_tokens);
  if (inTok !== undefined || outTok !== undefined) {
    parts.push(`${inTok ?? 0}→${outTok ?? 0} tok`);
  }
  if (str(a.error_type)) parts.push(`FAILED ${a.error_type}`);
  return parts.join(' · ');
}

/**
 * A compact, scannable description of one log row.
 *
 * Returns the event name alone when the record carries nothing more — which is
 * honest, rather than implying detail that is not there.
 */
export function logSummary(l: LogEntry): string {
  const a = l.attrs ?? {};
  const name = eventName(l);

  const specific =
    hookSummary(a) ??
    toolSummary(a) ??
    apiSummary(l, a) ??
    str(a.skill_name) ??
    str(a.agent_type) ??
    str(a.server_name) ??
    str(a.plugin_name);

  return specific ? `${name} · ${specific}` : name;
}

/** Short human duration. 0/absent renders as empty so the caller can omit it. */
export function fmtDuration(ms?: number): string {
  if (!ms || ms <= 0) return '';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)}s`;
  const mins = Math.floor(ms / 60_000);
  const secs = Math.round((ms % 60_000) / 1000);
  return secs ? `${mins}m${secs}s` : `${mins}m`;
}

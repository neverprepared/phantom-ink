/** Shared types + helpers for the server-side event rules editor
 * (ServerRulesTab and friends) over brainbox /api/rules. */
import { timeAgoOrDate } from './utils/format';

export interface Rule {
  id: string;
  name: string;
  profile: string; // '' or 'global' = all workspaces
  enabled: boolean;
  description: string;
  pattern: Record<string, any>;
  actions: Record<string, any>[];
  created_at: number;
  updated_at: number;
  last_triggered_at: number | null;
  trigger_count: number;
}

export interface RuleExecution {
  id: number;
  rule_id: string;
  event_seq: number;
  event_id: string;
  action_index: number;
  action_type: string;
  status: string; // queued|running|ok|failed|throttled|dead
  attempts: number;
  result: Record<string, any> | null;
  error: string;
  created_at: number;
  updated_at: number;
}

export interface RuleTestMatch {
  seq: number;
  id: string;
  type: string;
  status: string;
  ts: number;
}

export interface RulesStatus {
  counts: {
    queued: number;
    running: number;
    throttled: number;
    dead: number;
    ok_24h: number;
  };
  cursor: number;
  head_seq: number;
  lag: number;
  sink: {
    enabled: boolean;
    cursor: number;
    lag: number;
    last_error: string;
  };
}

export const ACTION_TYPES = [
  'submit_task',
  'start_loop',
  'webhook',
  'run_script',
] as const;
export type RuleActionType = (typeof ACTION_TYPES)[number];

// ── Pattern builder ⇄ JSON ─────────────────────────────────────────────────
//
// The builder covers the common case: event type entries (exact or prefix),
// plus single-valued workspace / source / status. Anything beyond that
// (nested fields, suffix/exists/anything-but/numeric, multi-valued facets)
// lives in raw-JSON mode only.

export interface TypeEntry {
  value: string;
  mode: 'exact' | 'prefix';
}

export interface BuilderState {
  types: TypeEntry[];
  workspace: string;
  source: string;
  status: string;
}

export function emptyBuilder(): BuilderState {
  return { types: [{ value: '', mode: 'exact' }], workspace: '', source: '', status: '' };
}

export function builderToPattern(b: BuilderState): Record<string, any> {
  const pattern: Record<string, any> = {};
  const types = b.types
    .filter((t) => t.value.trim() !== '')
    .map((t) => (t.mode === 'prefix' ? { prefix: t.value.trim() } : t.value.trim()));
  if (types.length) pattern.type = types;
  if (b.workspace.trim()) pattern.workspace = [b.workspace.trim()];
  if (b.source.trim()) pattern.source = [b.source.trim()];
  if (b.status.trim()) pattern.status = [b.status.trim()];
  return pattern;
}

const BUILDER_KEYS = new Set(['type', 'workspace', 'source', 'status']);

function normalizeToList(v: any): any[] {
  return Array.isArray(v) ? v : [v];
}

function isPrefixOp(v: any): v is { prefix: string } {
  return (
    v !== null &&
    typeof v === 'object' &&
    !Array.isArray(v) &&
    Object.keys(v).length === 1 &&
    typeof v.prefix === 'string'
  );
}

/** Returns builder state when the pattern is representable, else null. */
export function patternToBuilder(pattern: any): BuilderState | null {
  if (pattern === null || typeof pattern !== 'object' || Array.isArray(pattern)) return null;
  for (const key of Object.keys(pattern)) {
    if (!BUILDER_KEYS.has(key)) return null;
  }

  const b = emptyBuilder();
  b.types = [];

  if ('type' in pattern) {
    for (const el of normalizeToList(pattern.type)) {
      if (typeof el === 'string') b.types.push({ value: el, mode: 'exact' });
      else if (isPrefixOp(el)) b.types.push({ value: el.prefix, mode: 'prefix' });
      else return null;
    }
  }
  for (const key of ['workspace', 'source', 'status'] as const) {
    if (key in pattern) {
      const list = normalizeToList(pattern[key]);
      if (list.length !== 1 || typeof list[0] !== 'string') return null;
      b[key] = list[0];
    }
  }
  if (b.types.length === 0) b.types = [{ value: '', mode: 'exact' }];
  return b;
}

/** One-line human summary for rule cards. */
export function patternSummary(pattern: Record<string, any>): string {
  const b = patternToBuilder(pattern ?? {});
  if (b === null) {
    const raw = JSON.stringify(pattern);
    return raw.length > 80 ? raw.slice(0, 80) + '…' : raw;
  }
  const parts: string[] = [];
  const types = b.types.filter((t) => t.value !== '');
  if (types.length) {
    parts.push(`type: ${types.map((t) => (t.mode === 'prefix' ? `${t.value}*` : t.value)).join(' | ')}`);
  }
  if (b.workspace) parts.push(`ws: ${b.workspace}`);
  if (b.source) parts.push(`src: ${b.source}`);
  if (b.status) parts.push(`status: ${b.status}`);
  return parts.length ? parts.join(' · ') : 'all events';
}

// ── Action drafts (form state ⇄ API action objects) ────────────────────────
//
// Each draft carries an `extra` bag of keys the form doesn't manage
// (runner, model_target, …) so editing an existing rule never drops them.

export interface KVPair {
  key: string;
  value: string;
}

export interface ActionDraft {
  type: RuleActionType;
  // submit_task
  agentName: string;
  description: string;
  priority: string; // text input; parsed on serialize
  backend: string;
  workspaceProfile: string; // '' = inherit event workspace
  repoUrl: string;
  // start_loop
  templateName: string;
  refRows: KVPair[];
  refsRaw: string; // raw-JSON fallback when refs contain non-string values
  refsRawMode: boolean;
  workspaceHome: string;
  // webhook
  url: string;
  headerRows: KVPair[];
  bodyMode: 'envelope' | 'custom';
  bodyRaw: string;
  timeoutS: string;
  // run_script
  argv: string[];
  cwd: string;
  extra: Record<string, any>;
}

export function newActionDraft(type: RuleActionType): ActionDraft {
  return {
    type,
    agentName: '', description: '', priority: '0', backend: 'docker',
    workspaceProfile: '', repoUrl: '',
    templateName: '', refRows: [], refsRaw: '{}', refsRawMode: false, workspaceHome: '',
    url: '', headerRows: [], bodyMode: 'envelope', bodyRaw: '{}', timeoutS: '',
    argv: [''], cwd: '',
    extra: {},
  };
}

const MANAGED_KEYS: Record<RuleActionType, string[]> = {
  submit_task: ['type', 'description', 'agent_name', 'priority', 'backend', 'workspace_profile', 'repo_url'],
  start_loop: ['type', 'template_name', 'artifact_refs', 'workspace_profile', 'workspace_home'],
  webhook: ['type', 'url', 'headers', 'body', 'timeout_s'],
  run_script: ['type', 'argv', 'cwd', 'timeout_s'],
};

export function draftFromAction(action: Record<string, any>): ActionDraft {
  const type = (ACTION_TYPES as readonly string[]).includes(action?.type)
    ? (action.type as RuleActionType)
    : 'submit_task';
  const d = newActionDraft(type);
  const managed = new Set(MANAGED_KEYS[type]);
  for (const [k, v] of Object.entries(action ?? {})) {
    if (!managed.has(k)) d.extra[k] = v;
  }

  if (type === 'submit_task') {
    d.agentName = action.agent_name ?? '';
    d.description = action.description ?? '';
    d.priority = String(action.priority ?? 0);
    d.backend = action.backend ?? 'docker';
    d.workspaceProfile = action.workspace_profile ?? '';
    d.repoUrl = action.repo_url ?? '';
  } else if (type === 'start_loop') {
    d.templateName = action.template_name ?? '';
    d.workspaceProfile = action.workspace_profile ?? '';
    d.workspaceHome = action.workspace_home ?? '';
    const refs = action.artifact_refs ?? {};
    const allStrings = Object.values(refs).every((v) => typeof v === 'string');
    if (allStrings) {
      d.refRows = Object.entries(refs).map(([key, value]) => ({ key, value: value as string }));
    } else {
      d.refsRawMode = true;
      d.refsRaw = JSON.stringify(refs, null, 2);
    }
  } else if (type === 'webhook') {
    d.url = action.url ?? '';
    d.headerRows = Object.entries(action.headers ?? {}).map(([key, value]) => ({ key, value: String(value) }));
    if (action.body != null) {
      d.bodyMode = 'custom';
      d.bodyRaw = JSON.stringify(action.body, null, 2);
    }
    d.timeoutS = action.timeout_s != null ? String(action.timeout_s) : '';
  } else if (type === 'run_script') {
    d.argv = Array.isArray(action.argv) && action.argv.length ? action.argv.map(String) : [''];
    d.cwd = action.cwd ?? '';
    d.timeoutS = action.timeout_s != null ? String(action.timeout_s) : '';
  }
  return d;
}

function kvToObject(rows: KVPair[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const r of rows) {
    if (r.key.trim()) out[r.key.trim()] = r.value;
  }
  return out;
}

/** Serialize a draft back to the API action object. Throws Error with a
 * user-facing message when embedded raw JSON doesn't parse. */
export function draftToAction(d: ActionDraft): Record<string, any> {
  const action: Record<string, any> = { ...d.extra, type: d.type };

  if (d.type === 'submit_task') {
    action.description = d.description;
    action.agent_name = d.agentName;
    const pri = parseInt(d.priority, 10);
    if (!Number.isNaN(pri) && pri !== 0) action.priority = pri;
    if (d.backend && d.backend !== 'docker') action.backend = d.backend;
    if (d.workspaceProfile) action.workspace_profile = d.workspaceProfile;
    if (d.repoUrl.trim()) action.repo_url = d.repoUrl.trim();
  } else if (d.type === 'start_loop') {
    action.template_name = d.templateName;
    if (d.refsRawMode) {
      let parsed: any;
      try {
        parsed = JSON.parse(d.refsRaw);
      } catch (e: any) {
        throw new Error(`artifact_refs: invalid JSON (${e?.message ?? e})`);
      }
      if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error('artifact_refs must be a JSON object');
      }
      action.artifact_refs = parsed;
    } else if (d.refRows.some((r) => r.key.trim())) {
      action.artifact_refs = kvToObject(d.refRows);
    }
    if (d.workspaceProfile) action.workspace_profile = d.workspaceProfile;
    if (d.workspaceHome.trim()) action.workspace_home = d.workspaceHome.trim();
  } else if (d.type === 'webhook') {
    action.url = d.url.trim();
    const headers = kvToObject(d.headerRows);
    if (Object.keys(headers).length) action.headers = headers;
    if (d.bodyMode === 'custom') {
      let parsed: any;
      try {
        parsed = JSON.parse(d.bodyRaw);
      } catch (e: any) {
        throw new Error(`webhook body: invalid JSON (${e?.message ?? e})`);
      }
      if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error('webhook body must be a JSON object');
      }
      action.body = parsed;
    }
    const t = parseFloat(d.timeoutS);
    if (!Number.isNaN(t) && t > 0) action.timeout_s = t;
  } else if (d.type === 'run_script') {
    action.argv = d.argv.map((s) => s).filter((s, i) => s.trim() !== '' || i === 0);
    action.argv = action.argv.filter((s: string) => s.trim() !== '');
    if (d.cwd.trim()) action.cwd = d.cwd.trim();
    const t = parseFloat(d.timeoutS);
    if (!Number.isNaN(t) && t > 0) action.timeout_s = t;
  }
  return action;
}

/** Minimal per-draft validity for the save button. */
export function draftValid(d: ActionDraft): boolean {
  switch (d.type) {
    case 'submit_task': return d.agentName.trim() !== '' && d.description.trim() !== '';
    case 'start_loop': return d.templateName.trim() !== '';
    case 'webhook': return d.url.trim() !== '';
    case 'run_script': return d.argv.some((a) => a.trim() !== '');
  }
}

// ── Misc display / error helpers ───────────────────────────────────────────

export function relativeMs(ts: number | null | undefined): string {
  return timeAgoOrDate(ts);
}

export function executionStatusClass(s: string): string {
  if (s === 'ok') return 'pill pill-good';
  if (s === 'queued' || s === 'running') return 'pill pill-running';
  return 'pill pill-bad'; // failed | throttled | dead
}

/** Parse a Wails-relayed brainbox error ("HTTP 400: {json}") into either
 * pattern errors or a display message. The Go client truncates bodies at
 * 500 bytes, so JSON.parse can fail — fall back to the raw string. */
export function parseSaveError(e: any): { patternErrors: string[]; message: string } {
  const raw = String(e?.message ?? e ?? 'save failed');
  const m = raw.match(/HTTP (\d+): (.*)$/s);
  if (m) {
    try {
      const body = JSON.parse(m[2]);
      const detail = body?.detail;
      if (detail && typeof detail === 'object' && Array.isArray(detail.pattern_errors)) {
        return { patternErrors: detail.pattern_errors, message: '' };
      }
      if (typeof detail === 'string') return { patternErrors: [], message: detail };
    } catch {
      /* truncated body — fall through to raw */
    }
  }
  return { patternErrors: [], message: raw };
}

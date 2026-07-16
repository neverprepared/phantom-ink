/* eslint-disable */
/**
 * GENERATED — do not edit. Source: phantom-contracts schema/timeline-entry.schema.json
 * at pinned tag timeline-entry/v2.1 (0440c1a161f48196054c7ee6e33406f5c5d0c9e3).
 * Regenerate with `npm run gen:types`; commit the result.
 */

export type Actions = {
  [k: string]: unknown;
}[];
/**
 * Display-only supporting detail — longer free text than `subtitle`; URLs found here render as clickable links. Not routed or filtered on.
 */
export type Description = string | null;
export type EndAt = number | null;
export type Id = string;
export type Kind = string;
export type Actor = string;
export type DurationMs = number | null;
export type Error = string | null;
export type Ok = boolean;
export type ParentId = string | null;
export type Source = string | null;
export type StartAt = number | null;
/**
 * The six display/attention statuses an envelope may carry.
 *
 * Distinct from `models.TaskStatus` (the internal task lifecycle, which has
 * additional non-display states like `pending`/`running`/`cancelled`).
 * Producers map their own lifecycle onto these six; consumers render and
 * surface off them. Single source for the enum — no bare status literals.
 *
 * - upcoming     — future / queued.
 * - active       — in progress.
 * - done         — completed (terminal, not attention-worthy).
 * - failed       — terminal error (attention-eligible).
 * - blocked      — waiting on a dependency or an offline runner
 *                  (attention-eligible). Producer-emittable: the hub maps
 *                  `TaskStatus.BLOCKED` here (see `_TASK_STATUS_MAP`).
 * - needs_action — waiting on human input (attention-eligible).
 */
export type EnvelopeStatus = 'upcoming' | 'active' | 'done' | 'failed' | 'blocked' | 'needs_action';
/**
 * Display-only secondary label, one short line rendered under the title (e.g. 'developer · session-3'). Never routed or filtered on.
 */
export type Subtitle = string | null;
export type Tags = string[];
export type Title = string;
export type Type = string | null;
export type Url = string | null;
/**
 * Tenancy / routing key — the workspace profile this envelope belongs to. Routable, not display: it is a column on `agent_state` and a filter in `/api/agent_events/search`. None means unscoped/global.
 */
export type Workspace = string | null;

export interface AgentEnvelope {
  actions?: Actions;
  description?: Description;
  end_at?: EndAt;
  id: Id;
  kind?: Kind;
  metadata?: Metadata;
  outcome?: ActionOutcome | null;
  parent_id?: ParentId;
  source?: Source;
  start_at?: StartAt;
  status?: EnvelopeStatus | null;
  subtitle?: Subtitle;
  tags?: Tags;
  title: Title;
  type?: Type;
  url?: Url;
  workspace?: Workspace;
  [k: string]: unknown;
}
export interface Metadata {
  [k: string]: unknown;
}
export interface ActionOutcome {
  actor: Actor;
  duration_ms?: DurationMs;
  error?: Error;
  ok: Ok;
  [k: string]: unknown;
}

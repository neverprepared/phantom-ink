/**
 * Resolve (and if necessary register) the collect job a metric widget owns.
 *
 * This block was character-for-character identical in HttpMetricWidget and
 * ScriptMetricWidget. That matters more than the line count: it encodes the
 * widget→job ownership invariant, and duplicate jobs accumulating from exactly
 * this path is a known failure in this codebase — there is a Go-side
 * reconciler and an orphan prune that exist to clean up after it. Two copies
 * of the rule that decides which job a widget owns is the last place drift
 * should be allowed.
 *
 * The lookup order is deliberate:
 *   1. owner_widget_id — the authoritative link.
 *   2. (label, command) — the legacy identity, for jobs predating that column.
 * A match on either is BACKFILLED with the owner id and source, so the next
 * resolution takes the fast path and the reconciler can see the link.
 */

import { getApi } from '../utils/api';

export interface ResolveCollectJobArgs {
  profile: string;
  /** Job name; the widget's label. */
  label: string;
  /** The shell command the scheduler will run. */
  command: string;
  /** Owning widget instance id, when the widget has one. */
  widgetId?: string;
  intervalS?: number;
}

/**
 * Returns the id of the job this widget owns, registering one if none exists.
 * Returns null when the inputs are insufficient (no profile/label/command) or
 * the bindings are unavailable — callers treat that as "try again next tick"
 * rather than as an error, because registering with a partial identity is how
 * duplicate jobs get created.
 */
export async function resolveCollectJob(args: ResolveCollectJobArgs): Promise<string | null> {
  const { profile, label, command, widgetId, intervalS } = args;
  if (!profile || !label || !command) return null;

  const a = await getApi();
  if (!a) return null;

  const all: any[] = (await (a as any).ListCollectJobs(profile)) ?? [];

  // 1. The authoritative link.
  let existing = widgetId ? all.find(j => j.owner_widget_id === widgetId) : null;
  // 2. Legacy identity, for jobs created before owner_widget_id existed.
  if (!existing) {
    existing = all.find(j => j.name === label && j.command === command);
  }

  if (existing) {
    const needsBackfill = !existing.source || (widgetId && existing.owner_widget_id !== widgetId);
    if (needsBackfill) {
      try {
        await (a as any).SaveCollectJob({
          ...existing,
          source: 'widget',
          owner_widget_id: widgetId ?? '',
        });
      } catch {
        // Backfill is an optimisation — the id is already resolved, and the
        // next resolution will try again.
      }
    }
    return existing.id as string;
  }

  const job = await a.SaveCollectJob({
    id: '',
    profile,
    name: label,
    command,
    interval_s: intervalS ?? 60,
    enabled: true,
    default_actions: '[]',
    last_error: '',
    created_at: 0,
    source: 'widget',
    owner_widget_id: widgetId ?? '',
  } as any);
  return (job?.id as string) ?? null;
}

/**
 * Runners + pools, held at MODULE scope so the list survives panel navigation.
 *
 * This is the stale-while-revalidate half of the review's finding. Panel data
 * lived in panel-local `$state`, and AppShell renders panels under `{#if}` —
 * so leaving Runners and coming back destroyed the component, re-fetched from
 * scratch, and showed a spinner over rows that had been on screen moments
 * before. A module-scope resource outlives the mount: the retained rows render
 * immediately while `refreshing` drives a subtle marker, and the spinner is
 * reserved for a genuinely empty first load.
 *
 * `streamLive` is the panel that already got this right; this follows it.
 */

import { resource } from './resource.svelte';
import { runnerQueueHistory } from '../metricsHistory.svelte';

export interface Runner {
  name: string;
  capabilities: Record<string, boolean>;
  tags: string[];
  version: string;
  registered_at: number; // epoch ms
  last_seen: number; // epoch ms
  queue_depth: number;
  in_flight: number;
  max_concurrent: number;
  host: string;
}

export interface Pool {
  name: string;
  match_tags: string[];
  policy: string;
}

export const runnersRes = resource<Runner[]>(
  async (api) => {
    const list = ((await api.ListRunners()) ?? []) as Runner[];
    // Record a queue-depth sample per runner so the inline sparkline and the
    // dashboard peak card have history without any backend work.
    const ts = Date.now();
    const activeNames = new Set<string>();
    for (const r of list) {
      activeNames.add(r.name);
      runnerQueueHistory.update(r.name, {
        ts,
        depth: r.queue_depth || 0,
        inflight: r.in_flight || 0,
      });
    }
    runnerQueueHistory.pruneKeys(activeNames);
    return list;
  },
  { initial: [], label: 'Failed to load runners' },
);

export const poolsRes = resource<Pool[]>(
  async (api) => ((await api.ListPools()) ?? []) as Pool[],
  { initial: [], label: 'Failed to load pools' },
);

/** Refresh both. Errors stay on each resource rather than throwing. */
export async function refreshRunners(): Promise<void> {
  await Promise.all([runnersRes.refresh(), poolsRes.refresh()]);
}

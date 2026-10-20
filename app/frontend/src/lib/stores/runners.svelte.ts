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

/** One runner's derived task history. Mirrors main.RunnerRow's used fields. */
export interface RunnerThroughput {
  name: string;
  completed: number;
  failed: number;
  /** Left running by a runner that died; excluded from the mean. */
  stranded: number;
  mean_duration_seconds: number;
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

/**
 * Per-runner task throughput: completed, failed, stranded, mean duration.
 *
 * This was a section of the Work hub's Delivery tab, which was the wrong place
 * twice over. A runner is fleet infrastructure, not one profile's delivery
 * work; and that table silently mixed scopes, taking queue depth and in-flight
 * fleet-wide off ListRunners while counting completed/failed/stranded from one
 * profile's tasks only. Side by side they read as comparable. Here the whole
 * table is fleet-wide, which is the only scope a runner has.
 *
 * NOT keyed by profile, and deliberately so -- see RunnerMetrics in
 * app_dora.go, which dropped its profile argument for the same reason.
 */
export const runnerThroughputRes = resource<Record<string, RunnerThroughput>>(
  async (api) => {
    const res = await api.RunnerMetrics();
    const byName: Record<string, RunnerThroughput> = {};
    for (const row of res?.runners ?? []) byName[row.name] = row as RunnerThroughput;
    return byName;
  },
  // Quiet: throughput is history layered onto a live table. The hub being
  // unreachable must cost those four columns, never the runner list itself.
  { initial: {}, label: 'Failed to load runner throughput' },
);

/** Refresh all three. Errors stay on each resource rather than throwing. */
export async function refreshRunners(): Promise<void> {
  await Promise.all([runnersRes.refresh(), poolsRes.refresh(), runnerThroughputRes.refresh()]);
}

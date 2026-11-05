/**
 * Server-side schedules, at module scope so the list survives navigation.
 *
 * Loaded across ALL profiles deliberately — the panel has its own profile
 * filter, which is why there is no scope key here (same reasoning as
 * collectors.svelte.ts).
 *
 * Separate from collectJobsRes rather than merged: the two have independent
 * loading and error states, and one backend being down must not blank the
 * other's rows. The panel concatenates them for display.
 *
 * Typed from the GENERATED model — see services.svelte.ts.
 */

import { resource } from './resource.svelte';
import type { brainbox } from '../../../wailsjs/go/models';

export const schedulesRes = resource<brainbox.Schedule[]>(
  async (api) => ((await api.ListSchedules('')) ?? []) as brainbox.Schedule[],
  { initial: [], label: 'Failed to load server schedules' },
);

/** The server loop's own health, so the panel can say the clock is running
 * rather than leaving the operator to infer it from rows changing. */
export const schedulerStatusRes = resource<brainbox.SchedulerStatus | null>(
  async (api) => (await api.GetSchedulerStatus()) ?? null,
  { initial: null, label: 'Failed to load scheduler status' },
);

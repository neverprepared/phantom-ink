/**
 * Collect jobs, at module scope so the list survives navigation.
 *
 * Loaded across ALL profiles deliberately (the panel has its own profile
 * filter), which is why there is no scope key here.
 *
 * Typed from the GENERATED model — see services.svelte.ts.
 */

import { resource } from './resource.svelte';
import type { main } from '../../../wailsjs/go/models';

export const collectJobsRes = resource<main.CollectJob[]>(
  async (api) => ((await api.ListCollectJobs('')) ?? []) as main.CollectJob[],
  { initial: [], label: 'Failed to load collect jobs' },
);

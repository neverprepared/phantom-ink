/**
 * Backups + the disk overview, at module scope so they survive navigation.
 *
 * The disk overview is the one that matters most: GetDiskOverview walks the
 * profile tree, so re-running it on every visit to this panel was the most
 * expensive cold load in the app.
 *
 * Profile identity itself is NOT here — profileState owns that, and these
 * resources deliberately do not duplicate it.
 */

import { resource } from './resource.svelte';
import type { main } from '../../../wailsjs/go/models';

export const backupsRes = resource<string[]>(
  async (api) => ((await api.ListBackups()) ?? []) as string[],
  // Backups were previously swallowed into [] on failure, which reads as
  // "no backups" — keep the empty fallback but record why it is empty.
  { initial: [], label: 'Failed to load backups' },
);

export const diskOverviewRes = resource<main.DiskOverview | null>(
  async (api) => (await api.GetDiskOverview()) ?? null,
  { initial: null, label: 'Failed to load disk usage' },
);

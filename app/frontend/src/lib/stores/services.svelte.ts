/**
 * Local service rows, at module scope so the list survives navigation.
 *
 * Typed from the GENERATED model, not a hand-written mirror — the panels'
 * local interfaces are the drift this frontend already has 149 instances of.
 */

import { resource } from './resource.svelte';
import type { main } from '../../../wailsjs/go/models';

export const servicesRes = resource<main.ServiceStatus[]>(
  async (api) => ((await api.ListServices()) ?? []) as main.ServiceStatus[],
  { initial: [], label: 'Failed to load services' },
);

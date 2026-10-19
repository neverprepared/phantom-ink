/**
 * Profile tokens + the mint form's catalogs, at module scope so the list
 * survives panel navigation (see resource.svelte.ts).
 */

import { resource } from './resource.svelte';
import type { brainbox } from '../../../wailsjs/go/models';

// The GENERATED type, not a hand-written mirror of it. My first attempt here
// declared its own ProfileToken and immediately disagreed with the Go struct
// (it invented created_at, which ProfileTokenInfo does not have) — the exact
// drift that already leaves 149 hand-written types shadowing 177 generated
// ones in this frontend.
export const tokensRes = resource<brainbox.ProfileTokenInfo[]>(
  async (api) => ((await api.ListProfileTokens()) ?? []) as brainbox.ProfileTokenInfo[],
  { initial: [], label: 'Failed to load tokens' },
);

// Catalogs are best-effort: a failure here must not blank the page, because a
// free-text profile with no capabilities still mints. That was the original
// intent, expressed as two bare `try {} catch {}` blocks; a resource keeps the
// same fail-soft behaviour while still recording WHY it is empty.
export const tokenCapabilitiesRes = resource<string[]>(
  async (api) => ((await api.ProfileTokenCapabilities()) ?? []) as string[],
  { initial: [], label: 'Failed to load token capabilities' },
);

export const tokenProfilesRes = resource<string[]>(
  async (api) => ((await api.ProfileTokenProfiles()) ?? []) as string[],
  { initial: [], label: 'Failed to load token profiles' },
);

/** Load everything the Tokens panel needs. Catalog failures are non-fatal. */
export async function loadTokensPanel(): Promise<void> {
  await Promise.all([
    tokensRes.refresh(),
    tokenCapabilitiesRes.refresh(),
    tokenProfilesRes.refresh(),
  ]);
}

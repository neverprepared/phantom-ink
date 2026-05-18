/** Credential-authority health polling.
 *
 * Polls GET /api/credentials/authority/status every 5s. Drives the
 * status-bar dot (green/yellow/red) and the Credentials modal.
 */

import { getApi } from './utils/api';

export interface AuthorityInfo {
  name: string;
  version: string;
  tags: string[];
  online: boolean;
  last_seen: number;
  last_seen_age_ms: number;
  last_seal_at: number | null;
  last_seal_age_ms: number | null;
}

export interface SealFailure {
  when: number;
  status: number;
  error: string;
}

export interface AuthorityStatus {
  authorities: AuthorityInfo[];
  any_online: boolean;
  recent_failures: SealFailure[];
}

export type HealthState =
  | 'unknown'   // not loaded yet, or API unreachable
  | 'none'      // no authority registered at all
  | 'green'     // at least one authority online, no recent failures
  | 'yellow'    // at least one authority online but recent failures
  | 'red';      // an authority is registered but all stale

let _status = $state<AuthorityStatus | null>(null);
let _loadError = $state<string | null>(null);
let _loaded = $state(false);

export const authorityState = {
  get status() { return _status; },
  get loadError() { return _loadError; },
  get loaded() { return _loaded; },
  get health(): HealthState {
    if (!_loaded || !_status) return 'unknown';
    const a = _status.authorities;
    if (a.length === 0) return 'none';
    const anyOnline = _status.any_online;
    if (!anyOnline) return 'red';
    // Online + recent failures within last 5 min → yellow.
    const fiveMinAgo = Date.now() - 5 * 60_000;
    const recentFail = _status.recent_failures.some(f => f.when > fiveMinAgo);
    return recentFail ? 'yellow' : 'green';
  },
};

let pollHandle: number | undefined;

async function refreshOnce() {
  const a = await getApi();
  if (!a) {
    _loadError = 'API bindings unavailable';
    _loaded = true;
    return;
  }
  try {
    _status = (await a.GetAuthorityStatus()) as AuthorityStatus;
    _loadError = null;
  } catch (err: any) {
    _loadError = `${err?.message ?? err}`;
  } finally {
    _loaded = true;
  }
}

export function startAuthorityPolling(intervalMs = 5_000) {
  if (pollHandle !== undefined) return;
  void refreshOnce();
  pollHandle = window.setInterval(refreshOnce, intervalMs);
}

export function stopAuthorityPolling() {
  if (pollHandle !== undefined) {
    window.clearInterval(pollHandle);
    pollHandle = undefined;
  }
}

export function refreshAuthority() {
  return refreshOnce();
}

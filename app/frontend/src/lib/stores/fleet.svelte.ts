/**
 * Shared sessions + fleet reads, polled ONCE for the whole app.
 *
 * ## Why this exists
 *
 * GetSessions had three independent consumers: StatusBar (every 15s, for the
 * life of the app), DashboardPanel (on mount plus its own poll) and
 * SessionsPanel (on mount, on bus events, on refreshTick). ListRunners had
 * three too. None of them shared, so the same data was fetched several times
 * over, and because AppShell renders panels under `{#if}` both panels also
 * blanked to a spinner on every single visit.
 *
 * Those are two different problems with one fix: hold the data at module scope
 * and poll it in one place.
 *
 * ## Who owns the cadence
 *
 * StatusBar. It is the only consumer that is ALWAYS mounted — it lives in the
 * shell, not under `{#if}` — so it is the only one whose lifetime matches the
 * poll's. Panels read the values and may force a refresh in response to their
 * own events; they never own a timer for this data.
 *
 * ## Scope
 *
 * Sessions are keyed by profile, because GetSessions(profile) filters by it
 * server-side (app_brainbox.go). Runners are deliberately NOT keyed: a runner
 * is fleet infrastructure with no profile, which is the lesson from the
 * RunnerMetrics mixed-scope bug — see app_dora.go.
 */
import { keyedResource, resource } from './resource.svelte';
import { runnersRes } from './runners.svelte';

/** How often the always-on poll re-reads. Matches StatusBar's previous timer. */
export const FLEET_POLL_MS = 15_000;

/**
 * One profile's sessions.
 *
 * Consumers must NOT re-filter by workspace_profile. GetSessions already
 * filters server-side on an exact match, so both panels' client-side
 * case-insensitive re-filter was a no-op that implied a looser rule than the
 * one actually in force.
 */
export const sessionsRes = keyedResource<any[]>(
  (profile) => async (api) => ((await api.GetSessions(profile)) ?? []) as any[],
  { initial: [], label: (profile) => `Failed to load sessions (${profile || 'all profiles'})` },
);

/**
 * Local `claude` processes on this Mac. Host state, so not profile-keyed.
 *
 * Three callers wanted this independently: SessionsPanel's refresh AND its
 * refreshMetrics (so twice per mount, from the same onMount), plus
 * DashboardPanel's load. As a resource the concurrent-call collapse makes
 * those one request instead of three.
 */
export const localProcsRes = resource<any[]>(
  async (api) => ((await api.FindClaudeProcesses()) ?? []) as any[],
  { initial: [], label: 'Failed to scan local processes' },
);

/**
 * Per-container writable size. Host state, so not profile-keyed.
 *
 * Same story: SessionsPanel fetched this from both refresh and refreshMetrics,
 * both fired from onMount, so every visit made the same Docker round-trip
 * twice.
 */
export const containerDiskRes = resource<any[]>(
  async (api) => ((await api.GetContainerDiskUsage()) ?? []) as any[],
  { initial: [], label: 'Failed to read container disk usage' },
);

/**
 * Whole-disk breakdown behind the Sessions panel's disk card. Host state.
 *
 * Only one consumer, so this is not about de-duplication: it is here so the
 * card survives the panel being destroyed on navigation, like everything else
 * in this file.
 */
export const diskBreakdownRes = resource<any | null>(
  async (api) => (await api.GetDiskBreakdown()) ?? null,
  { initial: null, label: 'Failed to read disk breakdown' },
);

/**
 * This Mac's local runner. Host state, read by the Sessions and Settings
 * panels -- Settings also writes it (start/stop), which is why `refresh` is
 * exported rather than only polled.
 */
export const localRunnerRes = resource<any | null>(
  async (api) => (await api.GetLocalRunnerStatus()) ?? null,
  { initial: null, label: 'Failed to read local runner status' },
);

let pollHandle: ReturnType<typeof setInterval> | null = null;
let pollProfile = '';

/** Read everything the shared poll covers, for `profile`. */
export async function refreshFleet(profile: string, force = false): Promise<void> {
  pollProfile = profile;
  await Promise.all([
    sessionsRes.for(profile).refresh({ force }),
    runnersRes.refresh({ force }),
  ]);
}

/**
 * Start the single app-wide poll. Idempotent, and re-pointing it at a new
 * profile restarts it so the next tick reads the right one.
 *
 * Intended for StatusBar alone. Calling it from a panel would tie the cadence
 * to that panel's mount, which is the arrangement this replaces.
 */
export function startFleetPolling(profile: string): void {
  if (pollHandle !== null && profile === pollProfile) return;
  stopFleetPolling();
  pollProfile = profile;
  void refreshFleet(profile);
  pollHandle = setInterval(() => {
    // A hidden window has nobody to render for; skipping keeps a backgrounded
    // app off the hub entirely.
    if (typeof document !== 'undefined' && document.hidden) return;
    void refreshFleet(pollProfile);
  }, FLEET_POLL_MS);
}

export function stopFleetPolling(): void {
  if (pollHandle !== null) {
    clearInterval(pollHandle);
    pollHandle = null;
  }
}

/** Test seam: drop every cached profile and stop the poll. */
export function resetFleet(): void {
  stopFleetPolling();
  pollProfile = '';
  sessionsRes.clear();
  runnersRes.reset();
  localProcsRes.reset();
  containerDiskRes.reset();
  diskBreakdownRes.reset();
  localRunnerRes.reset();
}

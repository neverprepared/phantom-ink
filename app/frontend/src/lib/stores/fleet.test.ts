/**
 * Tests for the shared sessions/fleet store.
 *
 * Two properties matter and they are independent: the data must SURVIVE a
 * panel remount (AppShell destroys panels under `{#if}`), and it must be
 * fetched ONCE for all three consumers rather than once each.
 */

import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';

const mockGetApi = vi.fn();
vi.mock('../utils/api', () => ({ getApi: () => mockGetApi() }));
vi.mock('../metricsHistory.svelte', () => ({
  runnerQueueHistory: { value: {}, update: vi.fn(), pruneKeys: vi.fn() },
}));

const { sessionsRes, refreshFleet, startFleetPolling, stopFleetPolling, resetFleet, FLEET_POLL_MS } =
  await import('./fleet.svelte');
const { runnersRes } = await import('./runners.svelte');

let GetSessions: ReturnType<typeof vi.fn>;
let ListRunners: ReturnType<typeof vi.fn>;

beforeEach(() => {
  GetSessions = vi.fn(async (profile: string) => [
    { session_name: `${profile}-a`, active: true, workspace_profile: profile },
  ]);
  ListRunners = vi.fn(async () => [{ name: 'm3', queue_depth: 0, in_flight: 0 }]);
  mockGetApi.mockReset();
  mockGetApi.mockResolvedValue({ GetSessions, ListRunners });
  resetFleet();
});

afterEach(() => {
  stopFleetPolling();
  vi.useRealTimers();
});

describe('sessions', () => {
  it('are keyed by profile, so switching back paints without a refetch', async () => {
    await refreshFleet('work');
    await refreshFleet('personal');
    expect(sessionsRes.for('work').value[0].session_name).toBe('work-a');
    expect(sessionsRes.for('personal').value[0].session_name).toBe('personal-a');

    // 'work' still holds its rows, so a consumer coming back has data at once.
    expect(sessionsRes.for('work').loaded).toBe(true);
  });

  it('are served to a second consumer without a second fetch', async () => {
    await refreshFleet('work');
    expect(GetSessions).toHaveBeenCalledTimes(1);

    // A panel mounting now reads the retained value; it does not fetch.
    const r = sessionsRes.for('work');
    expect(r.value).toHaveLength(1);
    expect(r.loading).toBe(false);
    expect(GetSessions).toHaveBeenCalledTimes(1);
  });

  it('report loading only when that profile has nothing yet', async () => {
    await refreshFleet('work');
    // Cached profile: no spinner on re-read.
    expect(sessionsRes.for('work').loading).toBe(false);

    const p = refreshFleet('fresh');
    expect(sessionsRes.for('fresh').loading).toBe(true);
    // And the cached one is unaffected by the other's cold load.
    expect(sessionsRes.for('work').loading).toBe(false);
    await p;
  });

  it('keep the rows on screen when a refresh fails', async () => {
    await refreshFleet('work');
    GetSessions.mockRejectedValue(new Error('hub down'));
    await refreshFleet('work');

    expect(sessionsRes.for('work').value).toHaveLength(1);
    expect(sessionsRes.for('work').error).toContain('hub down');
  });
});

describe('runners', () => {
  it('are shared, not keyed by profile', async () => {
    await refreshFleet('work');
    await refreshFleet('personal');

    // A runner has no profile; both reads resolve to the same fleet list.
    expect(runnersRes.value).toHaveLength(1);
    expect(runnersRes.value[0].name).toBe('m3');
  });

  it('a sessions failure does not cost the runner list', async () => {
    GetSessions.mockRejectedValue(new Error('sessions down'));
    await refreshFleet('work');

    expect(runnersRes.value).toHaveLength(1);
    expect(runnersRes.error).toBeNull();
  });
});

describe('the single poll', () => {
  it('reads once on start and again each interval', async () => {
    vi.useFakeTimers();
    startFleetPolling('work');
    await vi.advanceTimersByTimeAsync(0);
    expect(GetSessions).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(FLEET_POLL_MS);
    expect(GetSessions).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(FLEET_POLL_MS);
    expect(GetSessions).toHaveBeenCalledTimes(3);
  });

  it('starting again for the same profile does not stack a second timer', async () => {
    vi.useFakeTimers();
    startFleetPolling('work');
    await vi.advanceTimersByTimeAsync(0);
    startFleetPolling('work');
    startFleetPolling('work');
    await vi.advanceTimersByTimeAsync(0);

    const before = GetSessions.mock.calls.length;
    await vi.advanceTimersByTimeAsync(FLEET_POLL_MS);
    // One tick must produce ONE read, not three.
    expect(GetSessions.mock.calls.length).toBe(before + 1);
  });

  it('re-points at a new profile rather than polling the old one', async () => {
    vi.useFakeTimers();
    startFleetPolling('work');
    await vi.advanceTimersByTimeAsync(0);
    startFleetPolling('personal');
    await vi.advanceTimersByTimeAsync(0);

    GetSessions.mockClear();
    await vi.advanceTimersByTimeAsync(FLEET_POLL_MS);
    expect(GetSessions).toHaveBeenCalledWith('personal');
    expect(GetSessions).not.toHaveBeenCalledWith('work');
  });

  it('stops cleanly', async () => {
    vi.useFakeTimers();
    startFleetPolling('work');
    await vi.advanceTimersByTimeAsync(0);
    stopFleetPolling();

    GetSessions.mockClear();
    await vi.advanceTimersByTimeAsync(FLEET_POLL_MS * 3);
    expect(GetSessions).not.toHaveBeenCalled();
  });
});

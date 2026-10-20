/**
 * Tests for the Delivery tab store.
 *
 * Two policies are under test, and the split is the point: metrics are cached
 * per profile AND per window, while runner health is never cached because it
 * is a liveness claim rather than history.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';

const mockGetApi = vi.fn();
vi.mock('../utils/api', () => ({ getApi: () => mockGetApi() }));
vi.mock('../notifications.svelte', () => ({
  notifications: { error: vi.fn(), success: vi.fn(), info: vi.fn() },
}));

const { doraState } = await import('./dora.svelte');

function overview(profile: string, days: number) {
  return {
    profile,
    token_missing: false,
    token_invalid: false,
    // deploy_count carries the window, so each window's cached payload is
    // distinguishable in the assertions below.
    metrics: { bands: {}, per_repo: { [`${profile}/r`]: { deploy_count: days } } },
  };
}

let DORAOverview: ReturnType<typeof vi.fn>;
let RunnerMetrics: ReturnType<typeof vi.fn>;

beforeEach(() => {
  DORAOverview = vi.fn(async (profile: string, days: number) => overview(profile, days));
  RunnerMetrics = vi.fn(async (profile: string) => ({ profile, runners: [], unreachable: '' }));
  mockGetApi.mockReset();
  mockGetApi.mockResolvedValue({ DORAOverview, RunnerMetrics });
  doraState.hardReset();
});

describe('metrics cache', () => {
  it('a tab switch back keeps the metrics and only revalidates', async () => {
    await doraState.refresh('work');
    const p = doraState.refresh('work');
    expect(doraState.loading).toBe(false);
    expect(doraState.refreshing).toBe(true);
    await p;
  });

  it('switching profile and back paints each profile cached metrics', async () => {
    await doraState.refresh('work');
    await doraState.refresh('personal');
    expect(doraState.perRepo[0][0]).toBe('personal/r');

    const back = doraState.refresh('work');
    expect(doraState.perRepo[0][0]).toBe('work/r');
    expect(doraState.loading).toBe(false);
    await back;
  });

  it('caches each window separately so toggling back is instant', async () => {
    await doraState.refresh('work');
    expect(doraState.windowDays).toBe(30);

    await doraState.setWindow('work', 90);
    expect(doraState.perRepo[0][1].deploy_count).toBe(90);

    const calls = DORAOverview.mock.calls.length;
    // Back to 30: already cached, so it paints before any refetch resolves.
    const p = doraState.setWindow('work', 30);
    expect(doraState.perRepo[0][1].deploy_count).toBe(30);
    expect(doraState.loading).toBe(false);
    await p;
    expect(DORAOverview.mock.calls.length).toBeGreaterThan(calls); // still revalidates
  });

  it('a window never seen for this profile loads cold', async () => {
    await doraState.refresh('work');
    const p = doraState.setWindow('work', 90);
    expect(doraState.loading).toBe(true);
    await p;
    expect(doraState.perRepo[0][1].deploy_count).toBe(90);
  });

  it('invalidate drops every window for that profile, not just the visible one', async () => {
    await doraState.refresh('work');
    await doraState.setWindow('work', 90);
    await doraState.setWindow('work', 30);
    await doraState.refresh('personal');

    doraState.invalidate('work');

    // A sync rewrites the underlying rows, so BOTH of work's windows must
    // refetch -- otherwise the toggle shows pre-sync numbers.
    doraState.windowDays = 30;
    const p30 = doraState.refresh('work');
    expect(doraState.loading).toBe(true);
    await p30;

    const p90 = doraState.setWindow('work', 90);
    expect(doraState.loading).toBe(true);
    await p90;
  });

  it('invalidate leaves other profiles cached', async () => {
    await doraState.refresh('work');
    await doraState.refresh('personal');
    doraState.invalidate('work');

    const p = doraState.refresh('personal');
    expect(doraState.loading).toBe(false);
    await p;
  });
});

describe('runner health', () => {
  // Runner health used to be a second section of this tab, cached under its
  // own policy. It moved to Settings -> runners: a runner is fleet
  // infrastructure, not one profile's delivery work, and that table mixed
  // fleet-wide queue depth with profile-scoped task counts in one row.
  it('is no longer part of this store', () => {
    expect('runners' in doraState).toBe(false);
    expect('runnersLoading' in doraState).toBe(false);
  });

  it('is not fetched by a delivery refresh', async () => {
    await doraState.refresh('work');
    expect(DORAOverview).toHaveBeenCalled();
    expect(RunnerMetrics).not.toHaveBeenCalled();
  });
});

describe('freshness label', () => {
  it('reports each window own read time, not the most recent read', async () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(new Date('2026-10-20T12:00:00Z'));
      await doraState.refresh('work');
      const at30 = doraState.fetchedAt;
      expect(at30).toBe('2026-10-20T12:00:00.000Z');

      vi.setSystemTime(new Date('2026-10-20T12:05:00Z'));
      await doraState.setWindow('work', 90);
      expect(doraState.fetchedAt).toBe('2026-10-20T12:05:00.000Z');

      // Switching BACK must show 30's own age on the first frame -- that is
      // the whole point of the label, so a cached window does not claim to
      // have been read just now.
      vi.setSystemTime(new Date('2026-10-20T12:09:00Z'));
      const p = doraState.setWindow('work', 30);
      expect(doraState.fetchedAt).toBe(at30);

      // Then the revalidate lands and the age catches up.
      await p;
      expect(doraState.fetchedAt).toBe('2026-10-20T12:09:00.000Z');
    } finally {
      vi.useRealTimers();
    }
  });

  it('a failed read leaves the age reporting the rows actually on screen', async () => {
    await doraState.refresh('work');
    const before = doraState.fetchedAt;

    DORAOverview.mockRejectedValue(new Error('db locked'));
    await doraState.refresh('work');

    // The rows are the old ones, so the label must still describe them.
    expect(doraState.fetchedAt).toBe(before);
  });

  it('is empty before anything has loaded', () => {
    expect(doraState.fetchedAt).toBe('');
  });
});

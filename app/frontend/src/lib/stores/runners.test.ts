/**
 * Tests for the runners store, specifically the throughput layer that moved
 * here from the Work hub's Delivery tab.
 *
 * The move was not cosmetic: on the Delivery tab the same table mixed
 * fleet-wide queue depth with profile-scoped task counts. Here the whole table
 * is fleet-wide, which is the only scope a runner has — so the binding takes
 * no profile, and that is what these pin.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';

const mockGetApi = vi.fn();
vi.mock('../utils/api', () => ({ getApi: () => mockGetApi() }));
vi.mock('../metricsHistory.svelte', () => ({
  runnerQueueHistory: { value: {}, update: vi.fn(), pruneKeys: vi.fn() },
}));

const { runnersRes, poolsRes, runnerThroughputRes, refreshRunners } = await import(
  './runners.svelte'
);

let ListRunners: ReturnType<typeof vi.fn>;
let ListPools: ReturnType<typeof vi.fn>;
let RunnerMetrics: ReturnType<typeof vi.fn>;

beforeEach(() => {
  ListRunners = vi.fn(async () => [{ name: 'm3', queue_depth: 2, in_flight: 1 }]);
  ListPools = vi.fn(async () => []);
  RunnerMetrics = vi.fn(async () => ({
    available: true,
    unreachable: '',
    runners: [
      { name: 'm3', completed: 7, failed: 1, stranded: 2, mean_duration_seconds: 90 },
      { name: 'm5', completed: 3, failed: 0, stranded: 0, mean_duration_seconds: 30 },
    ],
  }));
  mockGetApi.mockReset();
  mockGetApi.mockResolvedValue({ ListRunners, ListPools, RunnerMetrics });
  runnersRes.reset();
  poolsRes.reset();
  runnerThroughputRes.reset();
});

describe('runner throughput', () => {
  it('is requested with NO profile argument', async () => {
    await refreshRunners();
    expect(RunnerMetrics).toHaveBeenCalledWith();
  });

  it('is keyed by runner name for the table to join on', async () => {
    await refreshRunners();
    expect(runnerThroughputRes.value.m3.completed).toBe(7);
    expect(runnerThroughputRes.value.m3.stranded).toBe(2);
    expect(runnerThroughputRes.value.m5.completed).toBe(3);
  });

  it('is empty rather than absent when the hub lists no rows', async () => {
    RunnerMetrics.mockResolvedValue({ available: false, unreachable: '', runners: null });
    await refreshRunners();
    expect(runnerThroughputRes.value).toEqual({});
  });

  it('a hub outage costs the throughput columns but NOT the runner list', async () => {
    RunnerMetrics.mockRejectedValue(new Error('hub unreachable'));
    await refreshRunners();

    // This is the whole point of it being a separate resource: history is a
    // layer on a live table, so losing it must not blank the table.
    expect(runnersRes.value).toHaveLength(1);
    expect(runnersRes.error).toBeNull();
    expect(runnerThroughputRes.error).toContain('hub unreachable');
    expect(runnerThroughputRes.value).toEqual({});
  });

  it('a runner list outage does not stop throughput loading', async () => {
    ListRunners.mockRejectedValue(new Error('runners down'));
    await refreshRunners();

    expect(runnersRes.error).toContain('runners down');
    expect(runnerThroughputRes.value.m3.completed).toBe(7);
  });
});

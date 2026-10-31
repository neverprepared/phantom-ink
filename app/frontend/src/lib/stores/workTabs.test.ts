/**
 * Tests for the Work hub's optional-tab availability probe.
 *
 * The behaviour that matters is ORDERING: whether a profile has the Jira tab
 * is answered from local SQLite, which is instant, so the tab must not wait on
 * the gateway-env round trip that `JiraStatus` makes (measured at ~1.6s, and
 * it is the endpoint's own decryption cost, not the network).
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';

const mockGetApi = vi.fn();
vi.mock('../utils/api', () => ({ getApi: () => mockGetApi() }));
vi.mock('../utils/wailsTopic', () => ({ onWailsTopic: () => () => {} }));

const { workTabsState } = await import('./workTabs.svelte');
const { workState } = await import('../stores.svelte');

/** A never-resolving promise, to prove a caller did not wait on it. */
function pending<T>(): Promise<T> {
  return new Promise<T>(() => {});
}

beforeEach(() => {
  mockGetApi.mockReset();
  workTabsState.hardReset();
  workState.tab = 'code';
});

it('marks the jira tab available from the local reads alone', async () => {
  mockGetApi.mockResolvedValue({
    JiraGloballyEnabled: vi.fn(async () => true),
    JiraOptedIn: vi.fn(async () => true),
    // The slow one. If the probe waits on it, the tab never appears.
    JiraStatus: vi.fn(() => pending()),
    DORAOverview: vi.fn(async () => ({ token_missing: false })),
  });

  await workTabsState.probeLocal('work');

  expect(workTabsState.jira('work')).toBe(true);
});

it('does not offer the tab to a profile that has not opted in', async () => {
  mockGetApi.mockResolvedValue({
    JiraGloballyEnabled: vi.fn(async () => true),
    JiraOptedIn: vi.fn(async () => false),
    JiraStatus: vi.fn(() => pending()),
    DORAOverview: vi.fn(async () => ({ token_missing: false })),
  });

  await workTabsState.probeLocal('work');

  expect(workTabsState.jira('work')).toBe(false);
});

it('hides the tab when the full status says the credentials are missing', async () => {
  mockGetApi.mockResolvedValue({
    JiraGloballyEnabled: vi.fn(async () => true),
    JiraOptedIn: vi.fn(async () => true),
    JiraStatus: vi.fn(async () => ({ configured: false, opted_in: true })),
    DORAOverview: vi.fn(async () => ({ token_missing: false })),
  });

  await workTabsState.probeLocal('work');
  expect(workTabsState.jira('work')).toBe(true); // optimistic
  await workTabsState.confirm('work', 'work');

  expect(workTabsState.jira('work')).toBe(false);
});

it('moves the operator off a tab that just vanished under them', async () => {
  mockGetApi.mockResolvedValue({
    JiraGloballyEnabled: vi.fn(async () => true),
    JiraOptedIn: vi.fn(async () => true),
    JiraStatus: vi.fn(async () => ({ configured: false, opted_in: true })),
    DORAOverview: vi.fn(async () => ({ token_missing: false })),
  });

  await workTabsState.probeLocal('work');
  workState.tab = 'jira';
  await workTabsState.confirm('work', 'work');

  expect(workState.tab).toBe('code');
});

it('probes delivery even when the jira status never answers', async () => {
  const DORAOverview = vi.fn(async () => ({ token_missing: false }));
  mockGetApi.mockResolvedValue({
    JiraGloballyEnabled: vi.fn(async () => true),
    JiraOptedIn: vi.fn(async () => true),
    JiraStatus: vi.fn(() => pending()),
    DORAOverview,
  });

  await workTabsState.probeLocal('work');
  // Deliberately not awaited: the jira half never settles.
  void workTabsState.confirm('work', 'work');
  await vi.waitFor(() => expect(DORAOverview).toHaveBeenCalled());

  expect(workTabsState.dora('work')).toBe(true);
});

it('keeps availability per profile', async () => {
  mockGetApi.mockResolvedValue({
    JiraGloballyEnabled: vi.fn(async () => true),
    JiraOptedIn: vi.fn(async (p: string) => p === 'work'),
    JiraStatus: vi.fn(() => pending()),
    DORAOverview: vi.fn(async () => ({ token_missing: false })),
  });

  await workTabsState.probeLocal('work');
  await workTabsState.probeLocal('home');

  expect(workTabsState.jira('work')).toBe(true);
  expect(workTabsState.jira('home')).toBe(false);
});

it('survives a remount, so the strip paints on the first frame', async () => {
  mockGetApi.mockResolvedValue({
    JiraGloballyEnabled: vi.fn(async () => true),
    JiraOptedIn: vi.fn(async () => true),
    JiraStatus: vi.fn(() => pending()),
    DORAOverview: vi.fn(async () => ({ token_missing: false })),
  });

  await workTabsState.probeLocal('work');
  // A remount reads the same module-scoped store; nothing is re-probed.
  expect(workTabsState.jira('work')).toBe(true);
});

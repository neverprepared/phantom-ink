/**
 * Tests for the Jira tab store.
 *
 * The behaviour under test is the same as the Code tab's and for the same
 * reason: the hub renders tabs under `{#if}`, so a tab switch destroys the
 * component. Tickets must survive both a tab switch and a profile switch, and
 * a write that changes the result must drop the cache rather than leave a
 * stale list on screen.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';

const mockGetApi = vi.fn();
vi.mock('../utils/api', () => ({ getApi: () => mockGetApi() }));
const errorToast = vi.fn();
vi.mock('../notifications.svelte', () => ({
  notifications: { error: errorToast, success: vi.fn(), info: vi.fn() },
}));

const { jiraState } = await import('./jira.svelte');

function result(profile: string, extra: Record<string, unknown> = {}) {
  return {
    profile,
    jql: `assignee = ${profile}`,
    tickets: [{ key: `${profile.toUpperCase()}-1`, summary: 'a ticket' }],
    error: '',
    fetched_at: '2026-10-20T12:00:00Z',
    stale: false,
    ...extra,
  };
}

let JiraTickets: ReturnType<typeof vi.fn>;

beforeEach(() => {
  JiraTickets = vi.fn(async (profile: string) => result(profile));
  mockGetApi.mockReset();
  mockGetApi.mockResolvedValue({ JiraTickets });
  errorToast.mockReset();
  jiraState.hardReset();
});

it('cold load reports loading', async () => {
  const p = jiraState.refresh('work');
  expect(jiraState.loading).toBe(true);
  await p;
  expect(jiraState.rows).toHaveLength(1);
  expect(jiraState.jql).toBe('assignee = work');
});

it('a tab switch back keeps the tickets and only revalidates', async () => {
  await jiraState.refresh('work');
  const p = jiraState.refresh('work');
  expect(jiraState.rows).toHaveLength(1);
  expect(jiraState.loading).toBe(false);
  expect(jiraState.refreshing).toBe(true);
  await p;
});

it('switching profile and back paints each profile cached tickets', async () => {
  await jiraState.refresh('work');
  await jiraState.refresh('personal');
  expect(jiraState.rows[0].key).toBe('PERSONAL-1');

  const back = jiraState.refresh('work');
  expect(jiraState.rows[0].key).toBe('WORK-1');
  expect(jiraState.loading).toBe(false);
  await back;
});

it('a never-visited profile shows a cold spinner, not another profile tickets', async () => {
  await jiraState.refresh('work');
  const p = jiraState.refresh('fresh');
  expect(jiraState.rows).toHaveLength(0);
  expect(jiraState.loading).toBe(true);
  await p;
  expect(jiraState.rows[0].key).toBe('FRESH-1');
});

it('force reaches the binding so refresh bypasses the server memo', async () => {
  await jiraState.refresh('work');
  expect(JiraTickets).toHaveBeenLastCalledWith('work', false);
  await jiraState.refresh('work', true);
  expect(JiraTickets).toHaveBeenLastCalledWith('work', true);
});

it('invalidate forces the next read to refetch', async () => {
  await jiraState.refresh('work');
  jiraState.invalidate('work');

  // Dropped, so this is a cold load again rather than a silent cache hit.
  const p = jiraState.refresh('work');
  expect(jiraState.loading).toBe(true);
  await p;
  expect(jiraState.rows).toHaveLength(1);
});

it('invalidate leaves other profiles alone', async () => {
  await jiraState.refresh('work');
  await jiraState.refresh('personal');
  jiraState.invalidate('personal');

  const p = jiraState.refresh('work');
  expect(jiraState.loading).toBe(false); // work still cached
  await p;
});

it('surfaces jira own message without treating it as a load failure', async () => {
  JiraTickets.mockResolvedValue(
    result('work', { tickets: [], error: "field 'nope' does not exist" }),
  );
  await jiraState.refresh('work');
  expect(jiraState.jiraError).toContain('does not exist');
  expect(jiraState.loadError).toBeNull();
});

it('reports per-profile staleness', async () => {
  JiraTickets.mockImplementation(async (profile: string) =>
    result(profile, { stale: profile === 'work' }),
  );
  await jiraState.refresh('work');
  expect(jiraState.serverRevalidating).toBe(true);
  await jiraState.refresh('personal');
  expect(jiraState.serverRevalidating).toBe(false);
});

it('a failed refresh keeps the tickets already on screen', async () => {
  await jiraState.refresh('work');
  JiraTickets.mockRejectedValue(new Error('jira down'));
  await jiraState.refresh('work');

  expect(jiraState.rows).toHaveLength(1);
  expect(jiraState.loadError).toContain('jira down');
  expect(errorToast).toHaveBeenCalled();
});

it('profile names the tickets on screen immediately after a warm switch', async () => {
  await jiraState.refresh('work');
  await jiraState.refresh('personal');
  await jiraState.refresh('work');

  const pending = jiraState.refresh('personal');
  expect(jiraState.profile).toBe('personal');
  expect(jiraState.rows[0].key).toBe('PERSONAL-1');
  await pending;
});

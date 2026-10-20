/**
 * Tests for the shared agent-role catalog.
 *
 * Five call sites used to fetch this list and map it identically:
 * SessionsPanel, JobsPanel, CodeStore, RuleForm and TodoActions. The
 * properties that matter are that it is fetched ONCE, that each caller keeps
 * its own fallback (they differ), and that an actual catalog change can still
 * force a re-read.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';

const mockGetApi = vi.fn();
vi.mock('../utils/api', () => ({ getApi: () => mockGetApi() }));

const { agentRolesRes, agentRoleNames, ensureAgentRoles } = await import('./catalog.svelte');

let ListAgentRoles: ReturnType<typeof vi.fn>;

beforeEach(() => {
  ListAgentRoles = vi.fn(async () => [{ name: 'reviewer' }, { name: 'builder' }]);
  mockGetApi.mockReset();
  mockGetApi.mockResolvedValue({ ListAgentRoles });
  agentRolesRes.reset();
});

it('is fetched once however many consumers ask', async () => {
  await Promise.all([
    ensureAgentRoles(),
    ensureAgentRoles(),
    ensureAgentRoles(),
    ensureAgentRoles(),
    ensureAgentRoles(),
  ]);
  expect(ListAgentRoles).toHaveBeenCalledTimes(1);
  expect(agentRoleNames([])).toEqual(['reviewer', 'builder']);
});

it('does not re-read once held, so a remount is free', async () => {
  await ensureAgentRoles();
  expect(ListAgentRoles).toHaveBeenCalledTimes(1);
  await ensureAgentRoles();
  expect(ListAgentRoles).toHaveBeenCalledTimes(1);
});

it('keeps each caller own fallback, because they differ', async () => {
  // Nothing loaded: the session form wants 'assistant', the dispatch lanes
  // want 'worker'. One shared fetch must not flatten that.
  expect(agentRoleNames(['assistant'])).toEqual(['assistant']);
  expect(agentRoleNames(['worker'])).toEqual(['worker']);
  // A rule form legitimately wants an empty picker.
  expect(agentRoleNames([])).toEqual([]);
});

it('uses the fallback when the catalog is empty, not an empty picker', async () => {
  ListAgentRoles.mockResolvedValue([]);
  await ensureAgentRoles();
  expect(agentRoleNames(['worker'])).toEqual(['worker']);
});

it('drops entries with no usable name', async () => {
  ListAgentRoles.mockResolvedValue([{ name: 'ok' }, { name: '' }, {}, null]);
  await ensureAgentRoles();
  expect(agentRoleNames(['worker'])).toEqual(['ok']);
});

it('a forced refresh re-reads, for when a role is actually authored', async () => {
  await ensureAgentRoles();
  ListAgentRoles.mockResolvedValue([{ name: 'reviewer' }, { name: 'auditor' }]);

  // ensure() alone must NOT re-read...
  await ensureAgentRoles();
  expect(agentRoleNames([])).toEqual(['reviewer', 'builder']);

  // ...but an agent.created/updated/deleted event forces it.
  await agentRolesRes.refresh({ force: true });
  expect(agentRoleNames([])).toEqual(['reviewer', 'auditor']);
});

it('falls back rather than throwing when the hub is unreachable', async () => {
  ListAgentRoles.mockRejectedValue(new Error('hub down'));
  await ensureAgentRoles();

  expect(agentRoleNames(['worker'])).toEqual(['worker']);
  expect(agentRolesRes.error).toContain('hub down');
});

it('keeps the held catalog when a later refresh fails', async () => {
  await ensureAgentRoles();
  ListAgentRoles.mockRejectedValue(new Error('hub down'));
  await agentRolesRes.refresh({ force: true });

  // A failed poll must not empty a picker the user is looking at.
  expect(agentRoleNames([])).toEqual(['reviewer', 'builder']);
});

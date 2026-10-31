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

const { jiraState, jiraDispatchTarget, resolveTicketTarget } = await import('./jira.svelte');

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

describe('ref-first repo resolution', () => {
  const repos = [
    { provider: 'github', full_name: 'acme/api', clone_url: 'https://github.com/acme/api.git' },
    { provider: 'ado', full_name: 'acme/web', clone_url: 'https://dev.azure.com/acme/proj/_git/web' },
  ] as any[];

  const ticket = (extra: Record<string, unknown> = {}) =>
    ({
      key: 'PLAT-42',
      summary: 'Rate limit the webhook endpoint',
      url: 'https://acme.atlassian.net/browse/PLAT-42',
      ...extra,
    }) as any;

  it('takes the repo from the ticket first code ref', () => {
    const t = jiraDispatchTarget(
      ticket({ refs: [{ repo: 'acme/api', url: 'https://github.com/acme/api/pull/7' }] }),
      repos,
    );
    expect(t?.repoFullName).toBe('acme/api');
    expect(t?.repoURL).toBe('https://github.com/acme/api.git');
    expect(t?.issueKey).toBe('PLAT-42');
    // The ticket link rides along as htmlURL so the prompt can cite it.
    expect(t?.htmlURL).toBe('https://acme.atlassian.net/browse/PLAT-42');
  });

  it('resolves an ADO ref the same way', () => {
    const t = jiraDispatchTarget(
      ticket({ refs: [{ repo: 'acme/web', url: 'https://dev.azure.com/acme/proj/_git/web/pullrequest/3' }] }),
      repos,
    );
    expect(t?.repoURL).toBe('https://dev.azure.com/acme/proj/_git/web');
  });

  // A ref can point at a repo this profile does not list (a fork, another
  // org). Falling back to the PR url beats offering the operator nothing.
  it('falls back to the ref pull-request url when the repo is not listed', () => {
    const t = jiraDispatchTarget(
      ticket({ refs: [{ repo: 'other/thing', url: 'https://github.com/other/thing/pull/1' }] }),
      repos,
    );
    expect(t?.repoFullName).toBe('other/thing');
    expect(t?.repoURL).toBe('https://github.com/other/thing.git');
  });

  it('returns null with no refs, so the caller shows the repo picker', () => {
    expect(jiraDispatchTarget(ticket({ refs: [] }), repos)).toBeNull();
    expect(jiraDispatchTarget(ticket(), repos)).toBeNull();
  });

  it('an explicit repo choice overrides the ref', () => {
    const t = jiraDispatchTarget(
      ticket({ refs: [{ repo: 'acme/api', url: 'https://github.com/acme/api/pull/7' }] }),
      repos,
      'acme/web',
    );
    expect(t?.repoFullName).toBe('acme/web');
  });

  it('an explicit choice that is not in the repo list resolves to nothing', () => {
    expect(jiraDispatchTarget(ticket(), repos, 'nope/nope')).toBeNull();
  });
});

describe('JQL presets', () => {
  let GetJiraJQLPresets: ReturnType<typeof vi.fn>;
  let SetJiraJQLPresets: ReturnType<typeof vi.fn>;
  let ApplyJiraJQLPreset: ReturnType<typeof vi.fn>;

  const preset = (name: string) => ({ name, jql: `label = ${name}` });

  beforeEach(() => {
    GetJiraJQLPresets = vi.fn(async (profile: string) => [
      preset(`${profile}-a`),
      preset(`${profile}-b`),
    ]);
    SetJiraJQLPresets = vi.fn(async () => undefined);
    ApplyJiraJQLPreset = vi.fn(async () => undefined);
    mockGetApi.mockResolvedValue({
      JiraTickets,
      GetJiraJQLPresets,
      SetJiraJQLPresets,
      ApplyJiraJQLPreset,
    });
  });

  it('loads a profile presets and caches them per profile', async () => {
    await jiraState.loadPresets('work');
    expect(jiraState.presets.map((p) => p.name)).toEqual(['work-a', 'work-b']);

    await jiraState.loadPresets('personal');
    expect(jiraState.presets[0].name).toBe('personal-a');

    // Switching back paints from cache immediately and only revalidates —
    // the chip row must never blank out between two visits.
    const back = jiraState.loadPresets('work');
    expect(jiraState.presets[0].name).toBe('work-a');
    expect(jiraState.presetsLoading).toBe(false);
    await back;
    expect(jiraState.presets[0].name).toBe('work-a');
  });

  it('applying a preset drops the cached tickets so the new query is read', async () => {
    await jiraState.refresh('work');
    await jiraState.loadPresets('work');

    await jiraState.applyPreset('work', 'work-b');
    expect(ApplyJiraJQLPreset).toHaveBeenCalledWith('work', 'work-b');

    // The tickets on screen belonged to the PREVIOUS query — the next read
    // must be a cold one, not a cache hit.
    const p = jiraState.refresh('work');
    expect(jiraState.loading).toBe(true);
    await p;
  });

  it('a failed apply surfaces an error and leaves the tickets cached', async () => {
    await jiraState.refresh('work');
    ApplyJiraJQLPreset.mockRejectedValue(new Error('no preset named "nope"'));

    await expect(jiraState.applyPreset('work', 'nope')).rejects.toThrow('no preset');
    const p = jiraState.refresh('work');
    expect(jiraState.loading).toBe(false); // still cached
    await p;
  });

  it('saving the list writes it and refreshes the presets', async () => {
    await jiraState.loadPresets('work');
    const next = [preset('escalations')];

    await jiraState.savePresets('work', next);
    expect(SetJiraJQLPresets).toHaveBeenCalledWith('work', next);
    // Re-read, so a rejected or normalised list is what ends up on screen.
    expect(GetJiraJQLPresets).toHaveBeenCalledTimes(2);
  });

  it('saving does not drop the cached tickets — the active query is unchanged', async () => {
    await jiraState.refresh('work');
    await jiraState.savePresets('work', [preset('escalations')]);

    const p = jiraState.refresh('work');
    expect(jiraState.loading).toBe(false);
    await p;
  });
});

describe('late code refs', () => {
  it('reports refsPending from the payload', async () => {
    JiraTickets = vi.fn(async (profile: string) => result(profile, { refs_pending: true }));
    mockGetApi.mockResolvedValue({ JiraTickets });

    await jiraState.refresh('work');

    expect(jiraState.refsPending).toBe(true);
  });

  it('merges late refs onto the rows already on screen and clears pending', async () => {
    JiraTickets = vi.fn(async (profile: string) => result(profile, { refs_pending: true }));
    const JiraTicketRefs = vi.fn(async () => ({
      profile: 'work',
      refs: { 'WORK-1': [{ row_key: 'github:org/repo#7', number: 7, repo: 'org/repo' }] },
      orphan_rows: { 'WORK-1': ['github:org/repo#999'] },
    }));
    mockGetApi.mockResolvedValue({ JiraTickets, JiraTicketRefs });

    await jiraState.refresh('work');
    expect(jiraState.rows[0].refs ?? []).toHaveLength(0);

    await jiraState.loadRefs('work');

    expect(jiraState.refsPending).toBe(false);
    expect(jiraState.rows[0].refs).toHaveLength(1);
    expect(jiraState.rows[0].refs[0].number).toBe(7);
    expect(jiraState.rows[0].orphan_rows).toEqual(['github:org/repo#999']);
  });

  it('leaves a row with no refs alone rather than inventing an empty list', async () => {
    JiraTickets = vi.fn(async (profile: string) => result(profile, { refs_pending: true }));
    const JiraTicketRefs = vi.fn(async () => ({ profile: 'work', refs: {}, orphan_rows: {} }));
    mockGetApi.mockResolvedValue({ JiraTickets, JiraTicketRefs });

    await jiraState.refresh('work');
    await jiraState.loadRefs('work');

    expect(jiraState.rows).toHaveLength(1);
    expect(jiraState.rows[0].refs ?? []).toHaveLength(0);
    expect(jiraState.refsPending).toBe(false);
  });

  // The refs belong to the profile that asked for them. A profile switch
  // mid-flight must not paint one account's pull requests onto another's.
  it('discards refs that arrive for a profile no longer on screen', async () => {
    JiraTickets = vi.fn(async (profile: string) => result(profile, { refs_pending: true }));
    const JiraTicketRefs = vi.fn(async () => ({
      profile: 'work',
      refs: { 'WORK-1': [{ row_key: 'github:org/repo#7', number: 7, repo: 'org/repo' }] },
      orphan_rows: {},
    }));
    mockGetApi.mockResolvedValue({ JiraTickets, JiraTicketRefs });

    await jiraState.refresh('work');
    const pending = jiraState.loadRefs('work');
    await jiraState.refresh('home'); // operator switches profile
    await pending;

    expect(jiraState.profile).toBe('home');
    expect(jiraState.rows[0].key).toBe('HOME-1');
    expect(jiraState.rows[0].refs ?? []).toHaveLength(0);
  });
});

describe('resolving a ticket target while refs are still pending', () => {
  const repos = [
    { full_name: 'org/repo', clone_url: 'https://github.com/org/repo.git' },
  ] as any[];

  function pendingTickets() {
    return vi.fn(async (profile: string) =>
      result(profile, {
        refs_pending: true,
        tickets: [{ key: 'WORK-1', summary: 'a ticket', url: 'https://j/WORK-1' }],
      }),
    );
  }

  it('waits for the refs, then resolves the repo the ref points at', async () => {
    const JiraTicketRefs = vi.fn(async () => ({
      profile: 'work',
      refs: {
        'WORK-1': [
          { row_key: 'github:org/repo#7', number: 7, repo: 'org/repo', url: 'https://github.com/org/repo/pull/7' },
        ],
      },
      orphan_rows: {},
    }));
    mockGetApi.mockResolvedValue({ JiraTickets: pendingTickets(), JiraTicketRefs });

    await jiraState.refresh('work');
    const t = await resolveTicketTarget('work', 'WORK-1', repos);

    expect(JiraTicketRefs).toHaveBeenCalledOnce();
    expect(t?.repoFullName).toBe('org/repo');
    expect(t?.issueKey).toBe('WORK-1');
  });

  it('does not re-fetch refs that are already known', async () => {
    const JiraTicketRefs = vi.fn(async () => ({ profile: 'work', refs: {}, orphan_rows: {} }));
    const JiraTickets = vi.fn(async (profile: string) =>
      result(profile, {
        refs_pending: false,
        tickets: [
          {
            key: 'WORK-1',
            summary: 'a ticket',
            refs: [{ row_key: 'github:org/repo#7', number: 7, repo: 'org/repo' }],
          },
        ],
      }),
    );
    mockGetApi.mockResolvedValue({ JiraTickets, JiraTicketRefs });

    await jiraState.refresh('work');
    const t = await resolveTicketTarget('work', 'WORK-1', repos);

    expect(JiraTicketRefs).not.toHaveBeenCalled();
    expect(t?.repoFullName).toBe('org/repo');
  });

  // Null is the signal to fall back to the inline picker, and it must mean
  // "there is genuinely no code", never "we had not looked yet".
  it('returns null once the refs come back empty, so the picker is the answer', async () => {
    const JiraTicketRefs = vi.fn(async () => ({ profile: 'work', refs: {}, orphan_rows: {} }));
    mockGetApi.mockResolvedValue({ JiraTickets: pendingTickets(), JiraTicketRefs });

    await jiraState.refresh('work');

    expect(await resolveTicketTarget('work', 'WORK-1', repos)).toBeNull();
  });

  // The rows are REPLACED when refs land, so resolving against the object the
  // caller was holding would read the pre-refs copy and ask for a repo anyway.
  it('resolves against the row as it is after the refs landed', async () => {
    const JiraTicketRefs = vi.fn(async () => ({
      profile: 'work',
      refs: { 'WORK-1': [{ row_key: 'github:org/repo#7', number: 7, repo: 'org/repo' }] },
      orphan_rows: {},
    }));
    mockGetApi.mockResolvedValue({ JiraTickets: pendingTickets(), JiraTicketRefs });

    await jiraState.refresh('work');
    const staleRow = jiraState.rows[0];
    const t = await resolveTicketTarget('work', 'WORK-1', repos);

    expect(staleRow.refs ?? []).toHaveLength(0); // the copy the caller held
    expect(t?.repoFullName).toBe('org/repo'); // what it resolved anyway
  });

  // A fork, or another org's repo: the ref is real even though this profile
  // does not list it, and its PR url still normalises to a clone url.
  it('keeps a ref whose repo this profile does not list', async () => {
    const JiraTicketRefs = vi.fn(async () => ({
      profile: 'work',
      refs: {
        'WORK-1': [
          { row_key: 'github:other/fork#3', number: 3, repo: 'other/fork', url: 'https://github.com/other/fork/pull/3' },
        ],
      },
      orphan_rows: {},
    }));
    mockGetApi.mockResolvedValue({ JiraTickets: pendingTickets(), JiraTicketRefs });

    await jiraState.refresh('work');
    const t = await resolveTicketTarget('work', 'WORK-1', repos);

    expect(t?.repoFullName).toBe('other/fork');
    expect(t?.repoURL).toContain('other/fork');
  });

  it('returns null for a ticket that is gone after a profile switch', async () => {
    const JiraTicketRefs = vi.fn(async () => ({ profile: 'work', refs: {}, orphan_rows: {} }));
    mockGetApi.mockResolvedValue({ JiraTickets: pendingTickets(), JiraTicketRefs });

    await jiraState.refresh('work');
    await jiraState.refresh('home');

    expect(await resolveTicketTarget('home', 'WORK-1', repos)).toBeNull();
  });

  // An explicit pick must still win, and must not pay for a refs fetch.
  it('honours an explicit repo choice without consulting the refs', async () => {
    const JiraTicketRefs = vi.fn(async () => ({ profile: 'work', refs: {}, orphan_rows: {} }));
    mockGetApi.mockResolvedValue({ JiraTickets: pendingTickets(), JiraTicketRefs });

    await jiraState.refresh('work');
    const t = await resolveTicketTarget('work', 'WORK-1', repos, 'org/repo');

    expect(JiraTicketRefs).not.toHaveBeenCalled();
    expect(t?.repoFullName).toBe('org/repo');
  });
});

describe('when the refs fetch fails', () => {
  it('stops claiming the refs are still coming, and rethrows', async () => {
    const JiraTickets = vi.fn(async (profile: string) => result(profile, { refs_pending: true }));
    const JiraTicketRefs = vi.fn(async () => {
      throw new Error('providers down');
    });
    mockGetApi.mockResolvedValue({ JiraTickets, JiraTicketRefs });

    await jiraState.refresh('work');
    expect(jiraState.refsLookupFailed).toBe(false);

    await expect(jiraState.loadRefs('work')).rejects.toThrow('providers down');

    // The caller surfaces the error; the row must stop saying "checking…".
    expect(jiraState.refsLookupFailed).toBe(true);
  });

  it('clears the failure when the list is reloaded', async () => {
    const JiraTickets = vi.fn(async (profile: string) => result(profile, { refs_pending: true }));
    const JiraTicketRefs = vi.fn(async () => {
      throw new Error('providers down');
    });
    mockGetApi.mockResolvedValue({ JiraTickets, JiraTicketRefs });

    await jiraState.refresh('work');
    await expect(jiraState.loadRefs('work')).rejects.toThrow();
    expect(jiraState.refsLookupFailed).toBe(true);

    await jiraState.refresh('work', true);

    expect(jiraState.refsLookupFailed).toBe(false);
  });
});

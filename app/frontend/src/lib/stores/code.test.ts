/**
 * Tests for the Work panel's overview store.
 *
 * These pin the behaviour that made returning to the Work panel feel slow.
 * AppShell renders panels under `{#if}`, so every navigation destroys
 * CodePanel; the store is a module singleton that outlives it. The panel used
 * to call `reset()` on mount, throwing away rows that were still in memory and
 * dropping into a full-panel spinner. The contract below is the opposite:
 * a same-profile remount keeps its rows and revalidates behind them, and only
 * a genuine profile change clears them.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';

const mockGetApi = vi.fn();
vi.mock('../utils/api', () => ({
  getApi: () => mockGetApi(),
  safe: <T>(p: Promise<T>) => p,
}));
vi.mock('../notifications.svelte', () => ({
  notifications: { error: vi.fn(), success: vi.fn(), info: vi.fn() },
}));

const { codeState, httpsCloneURL, PROMPT_TEMPLATES } = await import('./code.svelte');

/** A minimal CodeOverview — only the fields a test asserts on are set. */
function overview(profile: string, extra: Record<string, unknown> = {}) {
  return {
    profile,
    token_missing: false,
    token_invalid: false,
    repos: [{ provider: 'github', full_name: `${profile}/one`, description: '' }],
    pull_requests: [],
    issues: [],
    notifications: [],
    repos_error: '',
    pull_requests_error: '',
    issues_error: '',
    notifications_error: '',
    issue_keys: {},
    jira_issues: {},
    jira_error: '',
    fetched_at: '2026-10-20T12:00:00Z',
    stale: false,
    ...extra,
  };
}

let CodeOverview: ReturnType<typeof vi.fn>;

beforeEach(() => {
  CodeOverview = vi.fn(async (profile: string) => overview(profile));
  mockGetApi.mockReset();
  mockGetApi.mockResolvedValue({ CodeOverview, ListAgentRoles: vi.fn(async () => []) });
  // hardReset covers reset() plus the rows, which is what a fresh test wants.
  codeState.hardReset();
});

describe('overview load', () => {
  it('cold load reports loading, not refreshing', async () => {
    const p = codeState.refresh('work');
    expect(codeState.loading).toBe(true);
    expect(codeState.refreshing).toBe(false);
    await p;
    expect(codeState.loading).toBe(false);
    expect(codeState.repos).toHaveLength(1);
  });

  it('a same-profile remount keeps the rows and flips refreshing', async () => {
    await codeState.refresh('work');
    expect(codeState.repos).toHaveLength(1);

    // Second mount: rows must stay on screen for the whole fetch.
    const p = codeState.refresh('work');
    expect(codeState.repos).toHaveLength(1);
    expect(codeState.loading).toBe(false);
    expect(codeState.refreshing).toBe(true);
    await p;
    expect(codeState.refreshing).toBe(false);
  });

  it('a profile change clears the previous profile rows before loading', async () => {
    await codeState.refresh('work');
    expect(codeState.repos[0].full_name).toBe('work/one');

    const p = codeState.refresh('personal');
    // Painting work's repositories under personal's header is the leak this
    // guards; an empty list plus a spinner is the correct intermediate state.
    expect(codeState.repos).toHaveLength(0);
    expect(codeState.loading).toBe(true);
    await p;
    expect(codeState.repos[0].full_name).toBe('personal/one');
  });

  it('force=true reaches the binding so Refresh bypasses the server memo', async () => {
    await codeState.refresh('work');
    await codeState.refresh('work', true);
    expect(CodeOverview).toHaveBeenLastCalledWith('work', true);
  });

  it('plain navigation passes force=false', async () => {
    await codeState.refresh('work');
    expect(CodeOverview).toHaveBeenLastCalledWith('work', false);
  });

  it('surfaces the payload freshness so the panel can show the data age', async () => {
    CodeOverview.mockResolvedValue(
      overview('work', { stale: true, fetched_at: '2026-10-20T11:59:00Z' }),
    );
    await codeState.refresh('work');
    expect(codeState.fetchedAt).toBe('2026-10-20T11:59:00Z');
    expect(codeState.serverRevalidating).toBe(true);
  });

  it('drops a response that lands after a profile switch', async () => {
    // Built up front: the resource awaits getApi() before it calls the
    // fetcher, so the resolver would not exist yet if it were assigned there.
    let settleWork!: (v: unknown) => void;
    const workPending = new Promise((r) => (settleWork = r));
    CodeOverview.mockImplementation((profile: string) =>
      profile === 'work' ? workPending : Promise.resolve(overview(profile)),
    );

    const slow = codeState.refresh('work');
    await codeState.refresh('personal');
    settleWork(overview('work'));
    await slow;

    expect(codeState.repos[0].full_name).toBe('personal/one');
  });

  it('a failed refresh keeps the rows that are already on screen', async () => {
    await codeState.refresh('work');
    CodeOverview.mockRejectedValue(new Error('github down'));
    await codeState.refresh('work');

    expect(codeState.repos).toHaveLength(1);
    expect(codeState.loadError).toContain('github down');
  });
});

describe('notifications', () => {
  it('marks one read optimistically and restores it on failure', async () => {
    const rows = [
      { id: 'n1', repo_full_name: 'work/one' },
      { id: 'n2', repo_full_name: 'work/two' },
    ];
    CodeOverview.mockResolvedValue(overview('work', { notifications: rows }));
    await codeState.refresh('work');

    const MarkNotificationRead = vi.fn(async () => {});
    mockGetApi.mockResolvedValue({ CodeOverview, MarkNotificationRead });
    await codeState.markNotificationRead('n1');
    expect(codeState.notifications.map((n) => n.id)).toEqual(['n2']);

    // A failure must put the row back rather than silently losing it.
    const failing = vi.fn(async () => {
      throw new Error('missing notifications scope');
    });
    mockGetApi.mockResolvedValue({ CodeOverview, MarkNotificationRead: failing });
    await codeState.markNotificationRead('n2');
    expect(codeState.notifications.map((n) => n.id)).toEqual(['n2']);
  });
});

describe('per-profile cache', () => {
  it('switching back to a visited profile paints its rows immediately', async () => {
    await codeState.refresh('work');
    await codeState.refresh('personal');
    expect(codeState.repos[0].full_name).toBe('personal/one');

    const calls = CodeOverview.mock.calls.length;
    // Back to work: its rows must be on screen BEFORE the refetch resolves.
    const back = codeState.refresh('work');
    expect(codeState.repos[0].full_name).toBe('work/one');
    expect(codeState.loading).toBe(false);
    expect(codeState.refreshing).toBe(true);
    await back;

    // It still revalidates — cached does not mean frozen.
    expect(CodeOverview.mock.calls.length).toBeGreaterThan(calls);
    expect(codeState.repos[0].full_name).toBe('work/one');
  });

  it('a profile never visited still shows a cold spinner, not another profile rows', async () => {
    await codeState.refresh('work');

    const p = codeState.refresh('fresh');
    // The cross-profile leak this whole design guards against: 'fresh' has no
    // cache, so it must render empty-and-loading rather than work's rows.
    expect(codeState.repos).toHaveLength(0);
    expect(codeState.loading).toBe(true);
    await p;
    expect(codeState.repos[0].full_name).toBe('fresh/one');
  });

  it('keeps each profile loading state separate', async () => {
    await codeState.refresh('work');

    // A cold profile loading must not report a spinner for the cached one.
    let settle!: (v: unknown) => void;
    const pending = new Promise((r) => (settle = r));
    CodeOverview.mockImplementation((profile: string) =>
      profile === 'cold' ? pending : Promise.resolve(overview(profile)),
    );

    const p = codeState.refresh('cold');
    expect(codeState.loading).toBe(true);

    // Back to work mid-flight: work is cached, so no cold spinner.
    await codeState.refresh('work');
    expect(codeState.loading).toBe(false);
    expect(codeState.repos[0].full_name).toBe('work/one');

    settle(overview('cold'));
    await p;
    // The cold response must not paint over the profile now being viewed.
    expect(codeState.repos[0].full_name).toBe('work/one');
  });

  it('a stale cached payload reports the backend as revalidating', async () => {
    CodeOverview.mockImplementation(async (profile: string) =>
      overview(profile, { stale: profile === 'work' }),
    );
    await codeState.refresh('work');
    expect(codeState.serverRevalidating).toBe(true);

    await codeState.refresh('personal');
    // Freshness is per profile: personal's payload is not stale.
    expect(codeState.serverRevalidating).toBe(false);
  });

  it('a mark-read after a warm switch targets the profile on screen', async () => {
    // The bug this guards: a cached switch paints the new profile's rows on
    // the first frame, so anything naming the profile from a field that
    // lagged until the revalidate landed wrote to the PREVIOUS account.
    CodeOverview.mockImplementation(async (profile: string) =>
      overview(profile, { notifications: [{ id: `${profile}-n1`, repo_full_name: `${profile}/one` }] }),
    );
    await codeState.refresh('work');
    await codeState.refresh('personal');
    await codeState.refresh('work');

    // Mid-switch to personal: its rows are cached, so they paint immediately
    // and the in-flight revalidate has not resolved yet.
    const pending = codeState.refresh('personal');
    expect(codeState.notifications[0].id).toBe('personal-n1');

    const MarkNotificationRead = vi.fn(async () => {});
    mockGetApi.mockResolvedValue({ CodeOverview, MarkNotificationRead });
    await codeState.markNotificationRead('personal-n1');
    expect(MarkNotificationRead).toHaveBeenCalledWith('personal', 'personal-n1');
    await pending;
  });

  it('hardReset drops every profile cache', async () => {
    await codeState.refresh('work');
    await codeState.refresh('personal');
    codeState.hardReset();

    expect(codeState.repos).toHaveLength(0);
    // And a profile visited before the reset is cold again, not restored.
    const p = codeState.refresh('work');
    expect(codeState.loading).toBe(true);
    await p;
  });
});

describe('jira dispatch kind', () => {
  // A ticket target carries the TICKET's url in htmlURL and the repo's clone
  // url in repoURL. httpsCloneURL prefers repoURL, which is the whole reason
  // that mapping is safe — if it ever flipped, a `git clone` of a Jira page
  // would reach the agent.
  const target = {
    kind: 'jira' as const,
    repoFullName: 'acme/api',
    repoURL: 'https://github.com/acme/api.git',
    number: 0,
    title: 'Rate limit the webhook endpoint',
    htmlURL: 'https://acme.atlassian.net/browse/PLAT-42',
    issueKey: 'PLAT-42',
  };

  it('resolves the clone url from the repo, never from the ticket link', () => {
    expect(httpsCloneURL(target)).toBe('https://github.com/acme/api.git');
  });

  it('has templates, each naming the key, the summary and the ticket link', () => {
    const tpls = PROMPT_TEMPLATES.jira;
    expect(tpls.length).toBeGreaterThan(0);
    for (const t of tpls) {
      const prompt = t.build(target);
      expect(prompt).toContain('PLAT-42');
      expect(prompt).toContain('Rate limit the webhook endpoint');
      expect(prompt).toContain('https://acme.atlassian.net/browse/PLAT-42');
      expect(prompt).toContain('acme/api');
    }
  });

  // The whole point of putting the key in the branch and PR title: the Jira
  // tab's derived link is parsed out of the PR TITLE, so an agent that omits
  // the key produces work this tab cannot see.
  it('the implement template tells the agent to carry the key into the PR title', () => {
    const prompt = PROMPT_TEMPLATES.jira[0].build(target);
    expect(prompt).toMatch(/PLAT-42-/); // branch convention
    expect(prompt.toLowerCase()).toContain('pull request title');
  });

  it('openDispatch on a ticket seeds the jira templates', () => {
    codeState.openDispatch(target);
    expect(codeState.templates).toBe(PROMPT_TEMPLATES.jira);
    expect(codeState.dispatchPrompt).toContain('PLAT-42');
    codeState.closeDispatch();
  });
});

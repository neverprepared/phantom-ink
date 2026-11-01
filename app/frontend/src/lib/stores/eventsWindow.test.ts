/**
 * Tests for the Events panel's "all events in a window" store.
 *
 * The behaviour that matters is the QUERY, not the rendering. Two of these
 * guard against silent wrongness rather than visible breakage:
 *
 *  - `since_ms`/`until_ms` must be NON-ZERO. The router treats 0 as unbounded
 *    (`if since_ms:` / `if until_ms:` in agent_store.search_events), so an
 *    off-by-one that yields 0 returns ALL history while still looking like a
 *    windowed query.
 *  - `workspace` must stay `''`. Scoping it to the active profile filters with
 *    `envelope LIKE '%"workspace":"X"%'`, which drops every envelope that has
 *    no workspace key at all — exactly the platform events this tab exists for.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';

const mockGetApi = vi.fn();
vi.mock('../utils/api', () => ({ getApi: () => mockGetApi() }));
vi.mock('../utils/wailsTopic', () => ({ onAgentEvent: () => () => {} }));

const { eventsWindow, WINDOW_RANGES, PAGE_LIMIT, shortEnvelopeId } =
  await import('./eventsWindow.svelte');

/** A never-resolving promise, to prove a caller did not wait on it. */
function pending<T>(): Promise<T> {
  return new Promise<T>(() => {});
}

function entry(seq: number, ts: number, over: Record<string, any> = {}) {
  return {
    seq,
    id: `hub-task:${seq}`,
    source: 'phantom_router-hub',
    type: 'task.completed',
    status: 'done',
    parent_id: '',
    ts,
    envelope: { title: `event ${seq}` },
    ...over,
  };
}

/** Capture the options object each SearchAgentEvents call was made with. */
function apiReturning(...pages: any[]) {
  const calls: any[] = [];
  const search = vi.fn(async (opts: any) => {
    calls.push(opts);
    const page = pages[calls.length - 1] ?? { items: [], backend: 'opensearch' };
    return page;
  });
  mockGetApi.mockResolvedValue({ SearchAgentEvents: search });
  return { calls, search };
}

beforeEach(() => {
  mockGetApi.mockReset();
  eventsWindow.hardReset();
});

describe('the window query', () => {
  it('sends a bounded, non-zero window derived from the selected range', async () => {
    const { calls } = apiReturning({ items: [entry(1, 1000)], backend: 'opensearch', total: 1 });

    const before = Date.now();
    await eventsWindow.setRange(3_600_000);

    expect(calls).toHaveLength(1);
    const { since_ms, until_ms } = calls[0];
    // 0 means "unbounded" to the backend, so a zero here is the silent failure.
    expect(since_ms).toBeGreaterThan(0);
    expect(until_ms).toBeGreaterThanOrEqual(before);
    expect(until_ms - since_ms).toBe(3_600_000);
  });

  it('defaults to a 24h window', async () => {
    const { calls } = apiReturning({ items: [], backend: 'opensearch' });
    await eventsWindow.load();
    expect(calls[0].until_ms - calls[0].since_ms).toBe(86_400_000);
  });

  it('asks for all workspaces, never the active profile', async () => {
    const { calls } = apiReturning({ items: [], backend: 'opensearch' });
    await eventsWindow.load();
    // Scoping this would silently drop workspace-less platform envelopes.
    expect(calls[0].workspace).toBe('');
  });

  it('requests the endpoint cap and no filters', async () => {
    const { calls } = apiReturning({ items: [], backend: 'opensearch' });
    await eventsWindow.load();
    expect(calls[0].limit).toBe(PAGE_LIMIT);
    expect(calls[0].q).toBe('');
    expect(calls[0].type).toBe('');
    expect(calls[0].status).toBe('');
    expect(calls[0].source).toBe('');
  });

  it('exposes every range the UI offers', () => {
    expect(WINDOW_RANGES.map((r) => r.label)).toEqual(['1h', '6h', '24h', '7d']);
  });
});

describe('paging older', () => {
  it('uses the oldest ts as an INCLUSIVE cursor and de-dupes the overlap', async () => {
    // The first page must be FULL, or the store correctly refuses to page at
    // all — a short page means the window is already exhausted.
    const page1 = Array.from({ length: PAGE_LIMIT }, (_, i) =>
      entry(PAGE_LIMIT - i, 10_000 - i),
    );
    const oldestTs = 10_000 - (PAGE_LIMIT - 1); // the cursor the store should send
    const { calls } = apiReturning(
      { items: page1, backend: 'opensearch' },
      // The oldest row comes back AGAIN because the cursor is inclusive.
      { items: [entry(1, oldestTs), entry(0, oldestTs - 1)], backend: 'opensearch' },
    );

    await eventsWindow.load();
    const firstSince = calls[0].since_ms;
    expect(eventsWindow.hasMore).toBe(true);

    await eventsWindow.loadOlder();

    // Inclusive, NOT min(ts) - 1: rows sharing that millisecond would be lost.
    expect(calls[1].until_ms).toBe(oldestTs);
    // The window floor does not move while paging back.
    expect(calls[1].since_ms).toBe(firstSince);

    // seq 1 was in both pages and appears once; seq 0 is the genuinely new row.
    expect(eventsWindow.items).toHaveLength(PAGE_LIMIT + 1);
    expect(eventsWindow.items.filter((i) => i.seq === 1)).toHaveLength(1);
    expect(eventsWindow.items[eventsWindow.items.length - 1].seq).toBe(0);
  });

  it('refuses to page when the first page came back short', async () => {
    const { calls } = apiReturning({ items: [entry(2, 2000), entry(1, 1000)], backend: 'opensearch' });
    await eventsWindow.load();
    await eventsWindow.loadOlder();
    // A short page means the window is exhausted; a second call would be waste.
    expect(calls).toHaveLength(1);
  });

  it('flags more to fetch when a page comes back exactly at the cap', async () => {
    const full = Array.from({ length: PAGE_LIMIT }, (_, i) => entry(PAGE_LIMIT - i, 10_000 - i));
    apiReturning({ items: full, backend: 'opensearch', total: 5000 });
    await eventsWindow.load();
    expect(eventsWindow.hasMore).toBe(true);
  });

  it('stops when a page yields nothing new, even at the cap', async () => {
    // Every row shares one millisecond, so the inclusive cursor cannot advance.
    const tied = Array.from({ length: PAGE_LIMIT }, (_, i) => entry(i + 1, 5000));
    apiReturning(
      { items: tied, backend: 'opensearch' },
      { items: tied, backend: 'opensearch' },
    );
    await eventsWindow.load();
    await eventsWindow.loadOlder();
    // Without this the UI would offer "load older" forever.
    expect(eventsWindow.hasMore).toBe(false);
    expect(eventsWindow.items).toHaveLength(PAGE_LIMIT);
  });

  it('does not page below the cap', async () => {
    apiReturning({ items: [entry(1, 1000)], backend: 'opensearch', total: 1 });
    await eventsWindow.load();
    expect(eventsWindow.hasMore).toBe(false);
  });
});

describe('ordering and staleness', () => {
  it('sorts newest first regardless of the order the backend returned', async () => {
    // The two backends disagree: Postgres sorts seq DESC, OpenSearch ts desc.
    apiReturning({
      items: [entry(1, 1000), entry(3, 3000), entry(2, 2000)],
      backend: 'postgres',
    });
    await eventsWindow.load();
    expect(eventsWindow.items.map((i) => i.ts)).toEqual([3000, 2000, 1000]);
  });

  it('breaks a ts tie by seq so the order is total', async () => {
    apiReturning({ items: [entry(5, 7000), entry(9, 7000)], backend: 'postgres' });
    await eventsWindow.load();
    expect(eventsWindow.items.map((i) => i.seq)).toEqual([9, 5]);
  });

  it('discards a slow response superseded by a newer range', async () => {
    const search = vi
      .fn()
      .mockReturnValueOnce(pending())
      .mockResolvedValueOnce({ items: [entry(1, 1000)], backend: 'opensearch' });
    mockGetApi.mockResolvedValue({ SearchAgentEvents: search });

    const slow = eventsWindow.setRange(604_800_000); // 7d, never resolves
    await eventsWindow.setRange(3_600_000); // 1h, wins

    expect(eventsWindow.rangeMs).toBe(3_600_000);
    expect(eventsWindow.items.map((i) => i.seq)).toEqual([1]);
    void slow;
  });

  it('replaces rather than accumulates when the range changes', async () => {
    apiReturning(
      { items: [entry(1, 1000), entry(2, 2000)], backend: 'opensearch' },
      { items: [entry(3, 3000)], backend: 'opensearch' },
    );
    await eventsWindow.load();
    await eventsWindow.setRange(3_600_000);
    expect(eventsWindow.items.map((i) => i.seq)).toEqual([3]);
  });
});

describe('filters', () => {
  it('sends the free-text query and the type prefix to the backend', async () => {
    const { calls } = apiReturning({ items: [], backend: 'opensearch' });
    await eventsWindow.setFilters({ q: 'orphan', typePrefix: 'task.' });

    // Server-side on purpose: the 1000-row budget is spent by the backend, so
    // filtering client-side would only sift the newest page.
    expect(calls[0].q).toBe('orphan');
    expect(calls[0].type).toBe('task.');
  });

  it('keeps the window while filtering', async () => {
    const { calls } = apiReturning(
      { items: [], backend: 'opensearch' },
      { items: [], backend: 'opensearch' },
    );
    await eventsWindow.setRange(21_600_000);
    await eventsWindow.setFilters({ q: 'x' });
    // A filter narrows WHAT, not WHEN.
    expect(calls[1].until_ms - calls[1].since_ms).toBe(21_600_000);
  });

  it('drops the previous results rather than merging into them', async () => {
    apiReturning(
      { items: [entry(2, 2000), entry(1, 1000)], backend: 'opensearch' },
      { items: [entry(9, 9000)], backend: 'opensearch' },
    );
    await eventsWindow.load();
    await eventsWindow.setFilters({ q: 'nine' });
    // The cursor and the seen-set are only meaningful against one result set;
    // merging would show rows that do not match the filter.
    expect(eventsWindow.items.map((i) => i.seq)).toEqual([9]);
  });

  it('reports whether a filter is active, so the empty state can say which', async () => {
    apiReturning({ items: [], backend: 'opensearch' }, { items: [], backend: 'opensearch' });
    await eventsWindow.load();
    expect(eventsWindow.filtered).toBe(false);
    await eventsWindow.setFilters({ typePrefix: 'task.' });
    // "No events in this window" and "no matches for this filter" are
    // different facts; conflating them makes a working filter look broken.
    expect(eventsWindow.filtered).toBe(true);
  });

  it('treats whitespace as no filter', async () => {
    const { calls } = apiReturning({ items: [], backend: 'opensearch' });
    await eventsWindow.setFilters({ q: '   ', typePrefix: '  ' });
    expect(calls[0].q).toBe('');
    expect(calls[0].type).toBe('');
    expect(eventsWindow.filtered).toBe(false);
  });

  it('keeps filters applied when paging older', async () => {
    const page1 = Array.from({ length: PAGE_LIMIT }, (_, i) =>
      entry(PAGE_LIMIT - i, 10_000 - i),
    );
    const { calls } = apiReturning(
      { items: page1, backend: 'opensearch' },
      { items: [entry(0, 1)], backend: 'opensearch' },
    );
    await eventsWindow.setFilters({ q: 'keepme', typePrefix: 'task.' });
    await eventsWindow.loadOlder();
    // A second page that dropped the filter would pull in unrelated rows.
    expect(calls[1].q).toBe('keepme');
    expect(calls[1].type).toBe('task.');
  });

  it('clears filters on hardReset', async () => {
    apiReturning({ items: [], backend: 'opensearch' });
    await eventsWindow.setFilters({ q: 'x', typePrefix: 'y.' });
    eventsWindow.hardReset();
    expect(eventsWindow.q).toBe('');
    expect(eventsWindow.typePrefix).toBe('');
    expect(eventsWindow.filtered).toBe(false);
  });
});

describe('correlating related events', () => {
  it('shortens only the opaque segments of an id', () => {
    // The id IS the correlation key: a session's created/stopped/deleted all
    // share `session:<name>`, and a task's queued/started/completed all share
    // `hub-task:<uuid>`. Shortening must keep the human-meaningful prefixes and
    // cut only the hex, or the key stops being recognisable.
    expect(shortEnvelopeId('session:bypass-repro')).toBe('session:bypass-repro');
    expect(shortEnvelopeId('rule-exec:227')).toBe('rule-exec:227');
    expect(shortEnvelopeId('hub-task:3b7ad79c-f98d-4156-9631-5c7ef3892122'))
      .toBe('hub-task:3b7ad79c');
    expect(shortEnvelopeId('runner-work:m3-64:a89bbcc26f410d2985006e09'))
      .toBe('runner-work:m3-64:a89bbcc2');
    // Nested ids are real: an action carries the hub-task it acted on.
    expect(shortEnvelopeId('action:hub-task:eed6e0e0-ec6a-409c-8b18-67ab100552ba:dismiss:1793534580074'))
      .toBe('action:hub-task:eed6e0e0:dismiss:17935345');
  });

  it('leaves a short host-like segment alone', () => {
    // m3-64 is hex-ish and dashed but only 5 chars; truncating it would be
    // worse than leaving it.
    expect(shortEnvelopeId('runner:m3-64')).toBe('runner:m3-64');
  });

  it('handles an empty id without throwing', () => {
    expect(shortEnvelopeId('')).toBe('');
  });

  it('correlates on the FULL id, not the shortened one', async () => {
    const { calls } = apiReturning({ items: [], backend: 'opensearch' });
    const full = 'hub-task:3b7ad79c-f98d-4156-9631-5c7ef3892122';
    await eventsWindow.correlate(full);
    // The display form is lossy; querying with it would match the wrong rows
    // or none.
    expect(calls[0].q).toBe(full);
  });

  it('clears the type prefix when correlating', async () => {
    const { calls } = apiReturning(
      { items: [], backend: 'opensearch' },
      { items: [], backend: 'opensearch' },
    );
    await eventsWindow.setFilters({ typePrefix: 'task.' });
    await eventsWindow.correlate('session:bypass-repro');

    // Correlating means "show me this whole lifecycle". Keeping a type prefix
    // would hide most of it — click a session id under a `task.` filter and
    // you would see nothing at all.
    expect(calls[1].type).toBe('');
    expect(eventsWindow.typePrefix).toBe('');
  });
});

describe('honest reporting', () => {
  it('keeps the backend name and total when OpenSearch answered', async () => {
    apiReturning({ items: [entry(1, 1000)], backend: 'opensearch', total: 4321 });
    await eventsWindow.load();
    expect(eventsWindow.backend).toBe('opensearch');
    expect(eventsWindow.total).toBe(4321);
  });

  it('reports no total when Postgres answered, rather than inventing one', async () => {
    // The Postgres path returns no count at all; a fabricated total would be a lie.
    apiReturning({ items: [entry(1, 1000)], backend: 'postgres' });
    await eventsWindow.load();
    expect(eventsWindow.backend).toBe('postgres');
    expect(eventsWindow.total).toBeNull();
  });

  it('freezes the window at load time so the footer can show what was asked', async () => {
    const { calls } = apiReturning({ items: [], backend: 'opensearch' });
    await eventsWindow.load();
    expect(eventsWindow.startMs).toBe(calls[0].since_ms);
    expect(eventsWindow.endMs).toBe(calls[0].until_ms);
  });

  it('surfaces a failure instead of showing an empty window', async () => {
    mockGetApi.mockResolvedValue({
      SearchAgentEvents: vi.fn(async () => {
        throw new Error('router unreachable');
      }),
    });
    await eventsWindow.load();
    expect(eventsWindow.error).toContain('router unreachable');
    expect(eventsWindow.loading).toBe(false);
  });

  it('counts live events without mutating the frozen list', async () => {
    apiReturning({ items: [entry(1, 1000)], backend: 'opensearch' });
    await eventsWindow.load();
    eventsWindow.noteLiveEvent();
    eventsWindow.noteLiveEvent();
    expect(eventsWindow.newSinceLoad).toBe(2);
    expect(eventsWindow.items).toHaveLength(1);
  });

  it('clears the new-event count on reload', async () => {
    apiReturning(
      { items: [entry(1, 1000)], backend: 'opensearch' },
      { items: [entry(1, 1000)], backend: 'opensearch' },
    );
    await eventsWindow.load();
    eventsWindow.noteLiveEvent();
    await eventsWindow.load();
    expect(eventsWindow.newSinceLoad).toBe(0);
  });

  it('does nothing when the api is unavailable', async () => {
    mockGetApi.mockResolvedValue(null);
    await eventsWindow.load();
    expect(eventsWindow.items).toEqual([]);
    expect(eventsWindow.loading).toBe(false);
  });
});

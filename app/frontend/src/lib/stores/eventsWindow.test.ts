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

const { eventsWindow, WINDOW_RANGES, PAGE_LIMIT } = await import('./eventsWindow.svelte');

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

/**
 * The Events panel's "all events in a window" tab — every envelope the bus
 * recorded between two timestamps, newest first, unfiltered.
 *
 * The other tabs answer "what is happening now": `live` lists envelopes whose
 * status is still active and `attention` the actionable ones, so anything that
 * reached a terminal status has already dropped out. This answers "what
 * happened", which needs a time range rather than a status filter.
 *
 * WHY THE LOGIC IS HERE AND NOT IN THE COMPONENT: `vite.config.ts` sets
 * `environment: 'node'` and `@testing-library` is not a dependency, so a
 * `.svelte` file cannot be tested at all. Everything worth asserting — the
 * window maths, the cursor, the de-dupe, the staleness guard — lives in this
 * module, and `EventsWindowTab.svelte` only renders it.
 *
 * PAGING WITHOUT A BACKEND CHANGE. `/api/agent_events/search` has no cursor,
 * no offset and a hard `limit` of 1000, but it does take `since_ms`/`until_ms`
 * and every row carries a unique `seq`. So page N+1 is the same query with
 * `until_ms` set to the oldest `ts` already held — INCLUSIVE, because many rows
 * share a millisecond and `-1` would skip the rest of that millisecond — and
 * the overlap is removed client-side by `seq`. A page that yields no new `seq`
 * ends the walk, which is also the guard against more than `PAGE_LIMIT` rows
 * inside a single millisecond.
 *
 * KNOWN LIMIT (the trigger for doing this properly later): the Postgres
 * fallback sorts `ORDER BY seq DESC` while this cursor is `ts`
 * (phantom_router/agent_store.py:395). A late or backfilled event — one whose
 * `ts` is older than its insert order — can be crowded off a page and then
 * excluded by the next page's narrower `until_ms`. It cannot happen on the
 * OpenSearch path, which sorts by `ts` (os_sink.py:387), and it only applies
 * past the first page. The real fix is a `before_seq` parameter on the
 * endpoint; `seq` is the only unique monotonic key in the table, and
 * `agent_events.id` is NOT a candidate — it is a repeating semantic key
 * (`hub-task:<id>`, `session:<name>`), so a `since_id` scheme is meaningless.
 */

import { getApi } from '../utils/api';
import type { AgentEventEntry } from './streamLive.svelte';

export const WINDOW_RANGES = [
  { label: '1h', ms: 3_600_000 },
  { label: '6h', ms: 21_600_000 },
  { label: '24h', ms: 86_400_000 },
  { label: '7d', ms: 604_800_000 },
] as const;

/** The endpoint's hard cap (phantom-router api.py, `limit: int = Query(100, ge=1, le=1000)`). */
export const PAGE_LIMIT = 1000;

/**
 * Display form of an envelope id, with the opaque parts cut.
 *
 * The id IS the correlation key, and it is a SEMANTIC repeating key rather
 * than a row identity: a session's created/stopped/deleted events all carry
 * `session:<name>`, and a task's queued/started/completed all carry
 * `hub-task:<uuid>`. Showing it is what makes a lifecycle legible in a flat
 * list — and the real shapes are long enough to need trimming:
 *
 *   session:bypass-repro                             (keep whole)
 *   rule-exec:227                                    (keep whole)
 *   hub-task:3b7ad79c-f98d-4156-9631-5c7ef3892122    -> hub-task:3b7ad79c
 *   runner-work:m3-64:a89bbcc26f410d2985006e09       -> runner-work:m3-64:a89bbcc2
 *   action:hub-task:<uuid>:dismiss:<epoch-ms>        -> action:hub-task:eed6e0e0:dismiss:17935345
 *
 * Only segments that are BOTH long and opaque (hex, dashes or digits) are cut,
 * so the meaningful prefixes and short host names like `m3-64` survive. This
 * is display only — correlation always queries the full id.
 */
export function shortEnvelopeId(id: string): string {
  if (!id) return '';
  return id
    .split(':')
    .map((seg) => (seg.length >= 12 && /^[0-9a-f-]+$/i.test(seg) ? seg.slice(0, 8) : seg))
    .join(':');
}

const DEFAULT_RANGE_MS = 86_400_000;

/** How many `load older` pages to allow before telling the reader to narrow the window. */
const MAX_PAGES = 5;

let _rangeMs = $state<number>(DEFAULT_RANGE_MS);
let _items = $state<AgentEventEntry[]>([]);
let _loading = $state(false);
let _loadingMore = $state(false);
let _error = $state<string | null>(null);
let _backend = $state<string>('');
let _total = $state<number | null>(null);
let _startMs = $state(0);
let _endMs = $state(0);
let _hasMore = $state(false);
let _newSinceLoad = $state(0);
let _pages = $state(0);
// Filters are applied SERVER-SIDE. The endpoint's 1000-row budget is spent by
// the backend, so filtering the returned page client-side would only sift the
// newest 1000 rows rather than the whole window.
let _q = $state('');
let _typePrefix = $state('');

// Not reactive: bookkeeping, never rendered.
let seen = new Set<number>();
let epoch = 0;

/**
 * One search call. Kept separate so `load` and `loadOlder` cannot drift apart
 * on the argument shape.
 *
 * The keys are snake_case behind an `as any` cast because the Go JSON tags are
 * snake_case while the generated `models.ts` fields are Go-cased — the same
 * cast and the same reason as EventLogPanel.svelte.
 */
async function search(api: any, sinceMs: number, untilMs: number) {
  return (await api.SearchAgentEvents({
    q: _q.trim(),
    // A PREFIX match, not an exact one, on both backends: `type LIKE 'x%'` on
    // Postgres and a `prefix` filter on OpenSearch. So `task.` selects every
    // task.* event, which is the operation worth having.
    type: _typePrefix.trim(),
    // ALL workspaces, deliberately. Scoping this to the active profile makes
    // the Postgres path filter on `envelope LIKE '%"workspace":"X"%'`
    // (agent_store.py:371), which silently excludes every envelope with no
    // workspace key — precisely the platform and lifecycle events a tab
    // labelled "all" exists to show. The row renders a [ws] chip instead.
    workspace: '',
    status: '',
    source: '',
    since_ms: sinceMs,
    until_ms: untilMs,
    limit: PAGE_LIMIT,
  } as any)) as { items?: AgentEventEntry[]; backend?: string; total?: number | null };
}

/** Newest first, with `seq` breaking a `ts` tie so the order is total. */
function newestFirst(rows: AgentEventEntry[]): AgentEventEntry[] {
  return [...rows].sort((a, b) => b.ts - a.ts || b.seq - a.seq);
}

/**
 * Fold a page in, ignoring it if a newer query has since started.
 * Returns how many rows were actually new.
 */
function merge(rows: AgentEventEntry[], mine: number): number {
  if (mine !== epoch) return 0;
  const fresh = rows.filter((r) => !seen.has(r.seq));
  for (const r of fresh) seen.add(r.seq);
  _items = newestFirst([..._items, ...fresh]);
  return fresh.length;
}

export const eventsWindow = {
  get rangeMs(): number {
    return _rangeMs;
  },
  get items(): AgentEventEntry[] {
    return _items;
  },
  get loading(): boolean {
    return _loading;
  },
  get loadingMore(): boolean {
    return _loadingMore;
  },
  get error(): string | null {
    return _error;
  },
  get backend(): string {
    return _backend;
  },
  /** The real match count, or null when Postgres answered and did not report one. */
  get total(): number | null {
    return _total;
  },
  get startMs(): number {
    return _startMs;
  },
  get endMs(): number {
    return _endMs;
  },
  get hasMore(): boolean {
    return _hasMore;
  },
  get newSinceLoad(): number {
    return _newSinceLoad;
  },
  get pagesLoaded(): number {
    return _pages;
  },
  /** True once `load older` has been used as many times as it is allowed. */
  get pageCapReached(): boolean {
    return _pages >= MAX_PAGES;
  },

  get q(): string {
    return _q;
  },
  get typePrefix(): string {
    return _typePrefix;
  },
  /** Whether anything is narrowing the result, so the empty state can say which. */
  get filtered(): boolean {
    return _q.trim() !== '' || _typePrefix.trim() !== '';
  },

  /**
   * Apply filters and refetch.
   *
   * A full `load()` rather than an incremental narrow: the ts cursor and the
   * seen-set are only meaningful against one result set, so keeping them would
   * leave rows on screen that do not match. The window itself is untouched —
   * a filter narrows WHAT, not WHEN.
   */
  async setFilters(next: { q?: string; typePrefix?: string }): Promise<void> {
    if (next.q !== undefined) _q = next.q;
    if (next.typePrefix !== undefined) _typePrefix = next.typePrefix;
    await this.load();
  },

  /**
   * Show every event sharing one envelope id — the lifecycle behind a row.
   *
   * Queries the FULL id: the shortened display form is lossy and would match
   * the wrong rows or none. The type prefix is CLEARED on purpose, because
   * correlating means "show me all of this", and clicking a session id while a
   * `task.` prefix was set would otherwise return nothing at all.
   *
   * `q` rather than a dedicated id filter because the search endpoint has no
   * id parameter; the id appears verbatim in the envelope JSON, so the
   * substring match on Postgres and the simple_query_string on OpenSearch both
   * find it.
   */
  async correlate(id: string): Promise<void> {
    _typePrefix = '';
    _q = id;
    await this.load();
  },

  async setRange(ms: number): Promise<void> {
    _rangeMs = ms;
    await this.load();
  },

  /**
   * Fetch the newest page of a FRESH window.
   *
   * The window is frozen here rather than recomputed per render, so `load
   * older` keeps a stable floor and the footer can state the range actually
   * queried — the `1h` label would otherwise drift from the truth the moment
   * the tab has been open a while.
   */
  async load(): Promise<void> {
    const api = await getApi();
    if (!api) return;
    const mine = ++epoch;

    const end = Date.now();
    const start = end - _rangeMs;
    _startMs = start;
    _endMs = end;
    _items = [];
    seen = new Set();
    _newSinceLoad = 0;
    _pages = 0;
    _total = null;
    _error = null;
    _loading = true;

    try {
      const res = await search(api, start, end);
      if (mine !== epoch) return;
      const rows = res.items ?? [];
      _backend = res.backend ?? '';
      _total = typeof res.total === 'number' ? res.total : null;
      const added = merge(rows, mine);
      _pages = 1;
      _hasMore = rows.length >= PAGE_LIMIT && added > 0;
    } catch (e) {
      if (mine === epoch) _error = String(e);
    } finally {
      if (mine === epoch) _loading = false;
    }
  },

  /**
   * Walk one page further back inside the same window.
   *
   * `until_ms` is the oldest `ts` held and is INCLUSIVE; the overlap it
   * re-fetches is dropped by `seq`. A page with nothing new clears `hasMore`,
   * which is what stops a millisecond holding more than `PAGE_LIMIT` rows from
   * looping forever.
   */
  async loadOlder(): Promise<void> {
    if (!_hasMore || _loading || _loadingMore) return;
    if (_pages >= MAX_PAGES) return;
    const api = await getApi();
    if (!api) return;
    const mine = epoch;

    const oldest = _items.length ? Math.min(..._items.map((i) => i.ts)) : _endMs;
    _loadingMore = true;
    try {
      const res = await search(api, _startMs, oldest);
      if (mine !== epoch) return;
      const rows = res.items ?? [];
      if (typeof res.total === 'number') _total = res.total;
      const added = merge(rows, mine);
      _pages += 1;
      _hasMore = rows.length >= PAGE_LIMIT && added > 0 && _pages < MAX_PAGES;
    } catch (e) {
      if (mine === epoch) _error = String(e);
    } finally {
      if (mine === epoch) _loadingMore = false;
    }
  },

  /**
   * A live envelope arrived. Counted only — never appended.
   *
   * Prepending would contradict the frozen window (its floor would stay put
   * while its ceiling moved) and would put a row outside the cursor's
   * invariant. The count drives a "N new - refresh" affordance instead, which
   * is honest about being a snapshot.
   */
  noteLiveEvent(): void {
    _newSinceLoad += 1;
  },

  /** Test seam; every store in this directory has one. */
  hardReset(): void {
    epoch = 0;
    seen = new Set();
    _rangeMs = DEFAULT_RANGE_MS;
    _items = [];
    _loading = false;
    _loadingMore = false;
    _error = null;
    _backend = '';
    _total = null;
    _startMs = 0;
    _endMs = 0;
    _hasMore = false;
    _newSinceLoad = 0;
    _pages = 0;
    _q = '';
    _typePrefix = '';
  },
};
